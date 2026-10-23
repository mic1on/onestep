from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from onestep.envelope import Envelope
from onestep.resilience import ConnectorOperation, ConnectorOperationError

from onestep.connectors.base import Delivery, Sink, Source
from onestep.connectors.codec import decode_envelope, encode_envelope

from .resilience import as_redis_connector_operation_error, collect_sensitive_tokens

try:  # pragma: no cover - optional dependency
    from redis.asyncio import Redis
    from redis.asyncio.connection import ConnectionPool
except ImportError:  # pragma: no cover - optional dependency
    Redis = None
    ConnectionPool = None


# Every mutation is fenced by the current PEL owner. JUSTID renewals do not
# increment Redis' delivery counter; explicit retries and crash claims do.
_OWNED_MESSAGE = """
local p = redis.call('XPENDING', KEYS[1], ARGV[1], ARGV[3], ARGV[3], 1)
if #p == 0 or p[1][2] ~= ARGV[2] then return 0 end
if tonumber(ARGV[5]) > 0 and p[1][4] ~= tonumber(ARGV[5]) then return 0 end
if ARGV[4] == 'ack' then
    return redis.call('XACK', KEYS[1], ARGV[1], ARGV[3])
elseif ARGV[4] == 'retry' then
    redis.call('XCLAIM', KEYS[1], ARGV[1], ARGV[2], 0, ARGV[3])
    return p[1][4] + 1
else
    redis.call('XCLAIM', KEYS[1], ARGV[1], ARGV[2], 0, ARGV[3], 'JUSTID')
    return p[1][4]
end
"""


class RedisOwnershipLostError(RuntimeError):
    pass


class RedisStreamDelivery(Delivery):
    """A PEL entry, retained locally until settlement or explicit release."""

    def __init__(self, redis: Any, stream: str, group: str,
                 message_id: bytes | str, envelope: Envelope,
                 *, queue: "RedisStreamQueue | None" = None) -> None:
        super().__init__(envelope)
        self._redis = redis
        self._stream = stream
        self._group = group
        self._message_id = message_id
        self._queue = queue
        self._settled = False
        self._delivery_count = 1

    async def start_processing(self) -> None:
        if self._queue is not None:
            async with self._queue._delivery_lock:
                self._queue._check_active(self)
                await self._queue._owned_action(self._message_id, "renew")

    async def ack(self) -> None:
        if self._queue is None:
            if not self._settled:
                await self._redis.xack(self._stream, self._group, self._message_id)
                self._settled = True
            return
        async with self._queue._delivery_lock:
            if self._settled:
                return
            self._queue._check_active(self)
            await self._queue._owned_action(self._message_id, "ack")
            self._queue._active.pop(self._message_id, None)
            self._settled = True

    async def retry(self, *, delay_s: float | None = None) -> None:
        if delay_s:
            await asyncio.sleep(delay_s)
        if self._queue is None:
            raise RuntimeError("Redis retry requires the owning RedisStreamQueue")
        await self._queue._requeue(self, increment=True)

    async def release_unstarted(self) -> None:
        if self._queue is not None:
            await self._queue._requeue(self, increment=False)

    async def fail(self, exc: Exception | None = None) -> None:
        await self.ack()


@dataclass
class RedisConnector:
    """Redis connection manager for onestep connectors.
    
    Usage:
        redis = RedisConnector("redis://localhost:6379")
        source = redis.stream("jobs", group="workers")
    """
    url: str
    options: dict[str, Any] | None = None
    _pool: Any | None = field(default=None, init=False, repr=False)
    _redis: Any | None = field(default=None, init=False, repr=False)
    _ref_count: int = field(default=0, init=False, repr=False)
    _lock: asyncio.Lock | None = field(default=None, init=False, repr=False)
    _loop: asyncio.AbstractEventLoop | None = field(default=None, init=False, repr=False)

    def stream(
        self,
        name: str,
        *,
        group: str = "onestep",
        consumer: str | None = None,
        batch_size: int = 100,
        poll_interval_s: float = 1.0,
        block_ms: int | None = None,
        start_id: str = "$",
        create_group: bool = True,
        maxlen: int | None = None,
        approximate_trim: bool = True,
        reclaim_idle_ms: int = 60000,
    ) -> "RedisStreamQueue":
        """Create a Redis Streams queue.
        
        Args:
            name: Stream name
            group: Consumer group name
            consumer: Consumer name (defaults to auto-generated)
            batch_size: Max messages to fetch per batch
            poll_interval_s: Poll interval for blocking reads
            block_ms: Redis BLOCK parameter in ms (defaults to poll_interval_s * 1000)
            start_id: Start ID for consumer group ("$" = new messages only)
            create_group: Create group if not exists
            maxlen: Max stream length (for XADD TRIM)
            approximate_trim: Use approximate trimming (~)
        """
        return RedisStreamQueue(
            connector=self,
            name=name,
            group=group,
            consumer=consumer,
            batch_size=batch_size,
            poll_interval_s=poll_interval_s,
            block_ms=block_ms if block_ms is not None else int(poll_interval_s * 1000),
            start_id=start_id,
            create_group=create_group,
            maxlen=maxlen,
            approximate_trim=approximate_trim,
            reclaim_idle_ms=reclaim_idle_ms,
        )

    async def close(self) -> None:
        """Close Redis connection."""
        lock = self._runtime_lock()
        async with lock:
            await self._close_connection_locked()

    def _driver(self) -> Any:
        if Redis is None:
            raise RuntimeError(
                "RedisConnector requires redis>=4.2.0. Install onestep-redis."
            )
        return Redis

    def _runtime_lock(self) -> asyncio.Lock:
        current_loop = asyncio.get_running_loop()
        if self._lock is None or self._loop is not current_loop:
            self._lock = asyncio.Lock()
            self._loop = current_loop
            self._pool = None
            self._redis = None
            self._ref_count = 0
        return self._lock

    async def acquire(self) -> Any:
        """Acquire Redis connection."""
        lock = self._runtime_lock()
        async with lock:
            if self._redis is None:
                driver = self._driver()
                self._pool = ConnectionPool.from_url(self.url, **(self.options or {}))
                self._redis = driver(connection_pool=self._pool)
            self._ref_count += 1
            return self._redis

    async def release(self) -> None:
        """Release Redis connection reference."""
        lock = self._runtime_lock()
        async with lock:
            if self._ref_count > 0:
                self._ref_count -= 1
            if self._ref_count == 0:
                await self._close_connection_locked()

    async def _close_connection_locked(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
        if self._pool is not None:
            await self._pool.aclose()
            self._pool = None


class RedisStreamQueue(Source, Sink):
    """Redis Streams source and sink (Redis >= 6.2).
    
    Uses consumer groups for reliable message processing:
    - XADD for publishing
    - XREADGROUP for consuming
    - XACK for acknowledging
    
    Messages that are not acked remain in PEL (Pending Entries List)
    and can be reclaimed via XCLAIM/XAUTOCLAIM.
    """

    fetch_is_cancel_safe = False

    def __init__(
        self,
        *,
        connector: RedisConnector,
        name: str,
        group: str,
        consumer: str | None,
        batch_size: int,
        poll_interval_s: float,
        block_ms: int,
        start_id: str,
        create_group: bool,
        maxlen: int | None,
        approximate_trim: bool,
        reclaim_idle_ms: int = 60000,
    ) -> None:
        Source.__init__(self, name)
        Sink.__init__(self, name)
        self.connector = connector
        self.group = group
        self.consumer = consumer or f"consumer-{uuid.uuid4().hex}"
        self.batch_size = batch_size
        self.poll_interval_s = poll_interval_s
        self.block_ms = block_ms
        self.start_id = start_id
        self.create_group = create_group
        self.maxlen = maxlen
        self.approximate_trim = approximate_trim
        self._redis: Any | None = None
        self._opened = False
        if isinstance(reclaim_idle_ms, bool) or not isinstance(reclaim_idle_ms, int) or reclaim_idle_ms < 30:
            raise ValueError("reclaim_idle_ms must be an integer >= 30")
        self.reclaim_idle_ms = reclaim_idle_ms
        self._claim_cursor: bytes | str = "0-0"
        self._active: dict[bytes | str, RedisStreamDelivery] = {}
        self._ready: deque[RedisStreamDelivery] = deque()
        self._heartbeat: asyncio.Task | None = None
        self._fetch_lock = asyncio.Lock()
        self._delivery_lock = asyncio.Lock()
        self._transport_lock = asyncio.Lock()
        self._acquired = False

    def _connection_secrets(self) -> list[str]:
        """Secret-bearing config tokens used to scrub error messages."""
        return collect_sensitive_tokens(
            self.connector.url, self.connector.options
        )

    async def open(self) -> None:
        """Open connection and create consumer group if needed."""
        async with self._transport_lock:
            await self._open_connection()

    async def _open_connection(self) -> None:
        if self._opened:
            return
        try:
            self._redis = await self.connector.acquire()
            self._acquired = True
            
            if self.create_group:
                # Create consumer group if not exists
                # XGROUP CREATE stream group start_id MKSTREAM
                try:
                    await self._redis.xgroup_create(
                        self.name,
                        self.group,
                        id=self.start_id,
                        mkstream=True,
                    )
                except Exception as e:
                    # Group already exists is OK
                    error_msg = str(e).lower()
                    if "busygroup" not in error_msg and "already exist" not in error_msg:
                        raise
            
            self._opened = True
        except BaseException as exc:
            await self._reset_transport_locked()
            if not isinstance(exc, Exception):
                raise
            connector_error = as_redis_connector_operation_error(
                operation=ConnectorOperation.OPEN,
                exc=exc,
                source_name=self.name,
                retry_delay_s=self.poll_interval_s,
                secrets=self._connection_secrets(),
            )
            if connector_error is None:
                raise
            raise connector_error from None

    async def close(self) -> None:
        """Stop renewal even if a prior transport error already closed the client."""
        if self._heartbeat is not None:
            self._heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await self._heartbeat
            self._heartbeat = None
        self._active.clear()
        self._ready.clear()
        await self._reset_transport_state()

    async def fetch(self, limit: int) -> list[Delivery]:
        # A single cursor and local reservation set protect concurrent fetch calls.
        async with self._fetch_lock:
            return await self._fetch(limit)

    async def _fetch(self, limit: int) -> list[Delivery]:
        try:
            await self.open()
            fetch_limit = max(1, min(limit, self.batch_size))
            if self._heartbeat is None:
                self._heartbeat = asyncio.create_task(self._renew_pending())
            if self._ready:
                return [self._ready.popleft() for _ in range(min(fetch_limit, len(self._ready)))]
            # One bounded scan per fetch, continuing the server's scan cursor.
            claimed = await self._redis.xautoclaim(
                self.name, self.group, self.consumer, self.reclaim_idle_ms,
                start_id=self._claim_cursor, count=fetch_limit,
            )
            self._claim_cursor = claimed[0]
            deliveries = await self._process_messages([(self.name, claimed[1])], reclaimed=True)
            if deliveries:
                return deliveries
            messages = await self._redis.xreadgroup(
                groupname=self.group, consumername=self.consumer,
                streams={self.name: ">"}, count=fetch_limit,
                # Fetch is not cancel-safe: bound every broker wait so stop
                # controls can join it, including Redis BLOCK 0 (infinite).
                block=min(self.block_ms, 1000) if self.block_ms > 0 else 1000,
            )
            return await self._process_messages(messages or [])
        except ConnectorOperationError:
            raise
        except Exception as exc:
            connector_error = as_redis_connector_operation_error(
                operation=ConnectorOperation.FETCH,
                exc=exc,
                source_name=self.name,
                retry_delay_s=self.poll_interval_s,
                secrets=self._connection_secrets(),
            )
            if connector_error is None:
                raise
            await self._reset_transport_state()
            raise connector_error from None

    async def _process_messages(self, messages: list, *, reclaimed: bool = False) -> list[Delivery]:
        async with self._delivery_lock:
            return await self._process_messages_locked(messages, reclaimed=reclaimed)

    async def _process_messages_locked(self, messages: list, *, reclaimed: bool) -> list[Delivery]:
        """Process raw xreadgroup response into Delivery objects."""
        # xreadgroup returns: [[stream_name, [(id, data), ...]]]
        deliveries: list[Delivery] = []
        for stream_name, stream_messages in messages:
            for message_id, message_data in stream_messages:
                if not message_data:
                    continue
                if message_id in self._active:
                    if not reclaimed:
                        continue
                    try:
                        await self._owned_action(message_id, "renew")
                    except RedisOwnershipLostError:
                        # Even our own XAUTOCLAIM changes the delivery generation.
                        # Fence the expired handler before exposing its replacement.
                        self._active.pop(message_id, None)
                        self._ready = deque(d for d in self._ready if d._message_id != message_id)
                    else:
                        continue
                # message_data is dict of field -> value
                # We expect {"body": json_encoded_envelope}
                body = message_data.get(b"body") or message_data.get("body")
                if body is None:
                    # Skip malformed messages
                    continue
                
                try:
                    envelope = decode_envelope(body)
                except Exception:
                    # Skip messages we can't decode
                    continue
                
                count = await self._owned_action(message_id, "renew")
                envelope.attempts += count - 1
                delivery = RedisStreamDelivery(
                    redis=self._redis, stream=self.name, group=self.group,
                    message_id=message_id, envelope=envelope, queue=self,
                )
                delivery._delivery_count = count
                self._active[message_id] = delivery
                deliveries.append(delivery)
        
        return deliveries

    async def _owned_action(self, message_id: bytes | str, action: str) -> int:
        await self.open()
        active = self._active.get(message_id)
        count = active._delivery_count if active is not None else 0
        result = await self._redis.eval(
            _OWNED_MESSAGE, 1, self.name, self.group, self.consumer, message_id, action, count,
        )
        if not result:
            raise RedisOwnershipLostError("Redis pending message ownership lost")
        return int(result)

    def _check_active(self, delivery: RedisStreamDelivery) -> None:
        if self._active.get(delivery._message_id) is not delivery:
            raise RedisOwnershipLostError("Redis delivery no longer active")

    async def _requeue(self, delivery: RedisStreamDelivery, *, increment: bool) -> None:
        async with self._delivery_lock:
            await self._requeue_locked(delivery, increment=increment)

    async def _requeue_locked(self, delivery: RedisStreamDelivery, *, increment: bool) -> None:
        if delivery._settled:
            return
        self._check_active(delivery)
        count = await self._owned_action(delivery._message_id, "retry" if increment else "renew")
        envelope = delivery.envelope
        retry = RedisStreamDelivery(
            self._redis, self.name, self.group, delivery._message_id,
            Envelope(body=envelope.body, meta=dict(envelope.meta),
                     attempts=envelope.attempts + int(increment)), queue=self,
        )
        retry._delivery_count = count
        self._active[delivery._message_id] = retry
        self._ready.append(retry)
        delivery._settled = True

    async def _renew_pending(self) -> None:
        while True:
            await asyncio.sleep(self.reclaim_idle_ms / 3000)
            for message_id in list(self._active):
                async with self._delivery_lock:
                    if message_id not in self._active:
                        continue
                    try:
                        await self._owned_action(message_id, "renew")
                    except RedisOwnershipLostError:
                        self._active.pop(message_id, None)
                        self._ready = deque(d for d in self._ready if d._message_id != message_id)
                    except Exception:
                        # Never silently end renewal for every other delivery.
                        logging.getLogger(__name__).warning(
                            "Redis pending renewal failed for %r", message_id,
                        )

    async def send(self, envelope: Envelope) -> None:
        """Send message using XADD."""
        try:
            await self.open()
            if self._redis is None:
                raise RuntimeError("Redis stream is not open")
            
            # XADD stream * body json_encoded_envelope
            add_kwargs: dict[str, Any] = {
                "name": self.name,
                "fields": {"body": encode_envelope(envelope)},
            }
            
            if self.maxlen is not None:
                add_kwargs["maxlen"] = self.maxlen
                if self.approximate_trim:
                    add_kwargs["approximate"] = True
            
            await self._redis.xadd(**add_kwargs)
        except ConnectorOperationError:
            raise
        except Exception as exc:
            connector_error = as_redis_connector_operation_error(
                operation=ConnectorOperation.SEND,
                exc=exc,
                source_name=self.name,
                retry_delay_s=self.poll_interval_s,
                secrets=self._connection_secrets(),
            )
            if connector_error is None:
                raise
            await self._reset_transport_state()
            raise connector_error from None

    async def _reset_transport_state(self) -> None:
        """Reset transport state on error."""
        async with self._transport_lock:
            await self._reset_transport_locked()

    async def _reset_transport_locked(self) -> None:
        self._redis = None
        self._opened = False
        if self._acquired:
            self._acquired = False
            await self.connector.release()
