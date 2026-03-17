from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from onestep.envelope import Envelope
from onestep.resilience import ConnectorOperation, ConnectorOperationError, as_connector_operation_error

from .base import Delivery, Sink, Source
from .codec import decode_envelope, encode_envelope

try:  # pragma: no cover - optional dependency
    from redis.asyncio import Redis
    from redis.asyncio.connection import ConnectionPool
except ImportError:  # pragma: no cover - optional dependency
    Redis = None
    ConnectionPool = None


class RedisStreamDelivery(Delivery):
    """Delivery wrapper for Redis Streams messages."""

    def __init__(
        self,
        redis: Any,
        stream: str,
        group: str,
        message_id: bytes | str,
        envelope: Envelope,
    ) -> None:
        super().__init__(envelope)
        self._redis = redis
        self._stream = stream
        self._group = group
        self._message_id = message_id

    async def ack(self) -> None:
        """Acknowledge message: XACK stream group message_id"""
        await self._redis.xack(self._stream, self._group, self._message_id)

    async def retry(self, *, delay_s: float | None = None) -> None:
        """Retry message: leave in PEL for redelivery via next fetch().
        
        The message stays in PEL (Pending Entries List) and will be
        retrieved again on the next fetch() call (which checks pending first).
        """
        if delay_s:
            await asyncio.sleep(delay_s)
        # Message remains in PEL - fetch() will pick it up on next cycle

    async def fail(self, exc: Exception | None = None) -> None:
        """Fail message: acknowledge to remove from PEL.
        
        Called when a message should not be retried:
        - NoRetry policy exhausted
        - RetryDecision.FAIL
        - After successful dead-letter delivery
        
        We XACK the message to remove it from PEL, preventing infinite
        reprocessing loops. If dead-letter was configured, the message
        has already been sent there before fail() is called.
        """
        await self._redis.xack(self._stream, self._group, self._message_id)


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
        )

    async def close(self) -> None:
        """Close Redis connection."""
        lock = self._runtime_lock()
        async with lock:
            await self._close_connection_locked()

    def _driver(self) -> Any:
        if Redis is None:
            raise RuntimeError(
                "RedisConnector requires redis>=4.2.0. Install onestep[redis]."
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
    """Redis Streams source and sink.
    
    Uses consumer groups for reliable message processing:
    - XADD for publishing
    - XREADGROUP for consuming
    - XACK for acknowledging
    
    Messages that are not acked remain in PEL (Pending Entries List)
    and can be reclaimed via XCLAIM/XAUTOCLAIM.
    """

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
    ) -> None:
        Source.__init__(self, name)
        Sink.__init__(self, name)
        self.connector = connector
        self.group = group
        self.consumer = consumer or f"consumer-{id(self)}"
        self.batch_size = batch_size
        self.poll_interval_s = poll_interval_s
        self.block_ms = block_ms
        self.start_id = start_id
        self.create_group = create_group
        self.maxlen = maxlen
        self.approximate_trim = approximate_trim
        self._redis: Any | None = None
        self._opened = False

    async def open(self) -> None:
        """Open connection and create consumer group if needed."""
        if self._opened:
            return
        try:
            self._redis = await self.connector.acquire()
            
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
        except Exception as exc:
            connector_error = as_connector_operation_error(
                backend="redis",
                operation=ConnectorOperation.OPEN,
                exc=exc,
                source_name=self.name,
                retry_delay_s=self.poll_interval_s,
            )
            if connector_error is None:
                raise
            raise connector_error from exc

    async def close(self) -> None:
        """Close connection."""
        if not self._opened:
            return
        self._redis = None
        self._opened = False
        await self.connector.release()

    async def fetch(self, limit: int) -> list[Delivery]:
        """Fetch messages using XREADGROUP.
        
        Strategy:
        1. First, check for pending messages (id="0") - messages in PEL
        2. If no pending messages, fetch new messages (id=">")
        
        This ensures retried/failed messages get reprocessed.
        """
        try:
            await self.open()
            if self._redis is None:
                return []
            
            fetch_limit = max(1, min(limit, self.batch_size))
            
            # First, try to fetch pending messages (in PEL)
            # XREADGROUP GROUP group consumer COUNT n STREAMS stream 0
            pending_messages = await self._redis.xreadgroup(
                groupname=self.group,
                consumername=self.consumer,
                streams={self.name: "0"},  # "0" = pending messages
                count=fetch_limit,
                block=0,  # Non-blocking for pending check
            )
            
            if pending_messages:
                return self._process_messages(pending_messages)
            
            # No pending messages, fetch new ones
            # XREADGROUP GROUP group consumer COUNT n BLOCK ms STREAMS stream >
            messages = await self._redis.xreadgroup(
                groupname=self.group,
                consumername=self.consumer,
                streams={self.name: ">"},  # ">" = new messages only
                count=fetch_limit,
                block=self.block_ms,
            )
            
            if not messages:
                return []
            
            return self._process_messages(messages)
        except ConnectorOperationError:
            raise
        except Exception as exc:
            connector_error = as_connector_operation_error(
                backend="redis",
                operation=ConnectorOperation.FETCH,
                exc=exc,
                source_name=self.name,
                retry_delay_s=self.poll_interval_s,
            )
            if connector_error is None:
                raise
            await self._reset_transport_state()
            raise connector_error from exc

    def _process_messages(self, messages: list) -> list[Delivery]:
        """Process raw xreadgroup response into Delivery objects."""
        # xreadgroup returns: [[stream_name, [(id, data), ...]]]
        deliveries: list[Delivery] = []
        for stream_name, stream_messages in messages:
            for message_id, message_data in stream_messages:
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
                
                deliveries.append(
                    RedisStreamDelivery(
                        redis=self._redis,
                        stream=self.name,
                        group=self.group,
                        message_id=message_id,
                        envelope=envelope,
                    )
                )
        
        return deliveries

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
            connector_error = as_connector_operation_error(
                backend="redis",
                operation=ConnectorOperation.SEND,
                exc=exc,
                source_name=self.name,
                retry_delay_s=self.poll_interval_s,
            )
            if connector_error is None:
                raise
            await self._reset_transport_state()
            raise connector_error from exc

    async def _reset_transport_state(self) -> None:
        """Reset transport state on error."""
        self._redis = None
        self._opened = False
        await self.connector.release()