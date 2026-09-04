from __future__ import annotations

import asyncio
import base64
import binascii
import json
from dataclasses import dataclass, field
from typing import Any

from onestep.connectors.base import Delivery, Sink, Source
from onestep.connectors.codec import decode_envelope, encode_envelope
from onestep.envelope import Envelope
from onestep.resilience import ConnectorOperation, ConnectorOperationError

from .resilience import as_cf_connector_operation_error, collect_sensitive_tokens

try:  # pragma: no cover - optional dependency
    from cloudflare import AsyncCloudflare
except ImportError:  # pragma: no cover - optional dependency
    AsyncCloudflare = None

# Cloudflare Queues limits (see docs/platform/limits).
_MAX_BATCH_SIZE = 100
_MAX_VISIBILITY_TIMEOUT_MS = 12 * 60 * 60 * 1000  # 12 hours
_MAX_DELAY_SECONDS = 24 * 60 * 60  # 24 hours


def _try_base64_decode(value: str) -> bytes | None:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None


def _decode_message_body(raw: Any, content_type: str | None = None) -> Envelope:
    """Decode a Cloudflare pull message ``body`` into an onestep Envelope.

    Pull consumers may receive the body as a structured JSON value, a plain
    UTF-8 string (``text`` content type), or a base64-encoded string (``json``
    or ``bytes`` content types). This normalizes all of those into the envelope
    codec input.
    """
    if isinstance(raw, (dict, list)):
        return decode_envelope(raw)
    if isinstance(raw, (bytes, bytearray)):
        data = bytes(raw)
        try:
            return decode_envelope(data)
        except UnicodeDecodeError:
            # Undecodable binary payload: keep the decoded bytes as the body.
            return Envelope(body=data)
    if isinstance(raw, str):
        if content_type == "text":
            return decode_envelope(raw)
        if content_type in ("json", "bytes"):
            # These content types always travel base64-encoded, so decode
            # unconditionally instead of inferring from JSON-parsability:
            # a non-JSON ``bytes`` payload must keep its decoded body rather
            # than degrade to the raw base64 string.
            decoded = _try_base64_decode(raw)
            if decoded is None:
                # Malformed base64: fall back to plain-string handling.
                return decode_envelope(raw)
            try:
                return decode_envelope(decoded)
            except UnicodeDecodeError:
                return Envelope(body=decoded)
        # Unknown content type: accept base64 only when the decoded bytes
        # parse as JSON, otherwise treat the string as-is.
        decoded = _try_base64_decode(raw)
        if decoded is not None:
            try:
                json.loads(decoded.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                pass
            else:
                return decode_envelope(decoded)
        return decode_envelope(raw)
    return decode_envelope(raw)


def _message_field(message: Any, key: str) -> Any:
    """Read a field from an SDK model or a plain mapping."""
    if isinstance(message, dict):
        return message.get(key)
    return getattr(message, key, None)


class CFQueuesDelivery(Delivery):
    def __init__(self, queue: "CFQueue", message: Any) -> None:
        body = _message_field(message, "body")
        metadata = _message_field(message, "metadata")
        content_type = (
            metadata.get("CF-Content-Type") if isinstance(metadata, dict) else None
        )
        envelope = _decode_message_body(body, content_type)
        existing_meta = envelope.meta.get("cf_queues")
        cf_meta = dict(existing_meta) if isinstance(existing_meta, dict) else {}
        for key in ("id", "timestamp_ms", "attempts", "metadata"):
            cf_meta.pop(key, None)
        message_id = _message_field(message, "id")
        if message_id is not None:
            cf_meta["id"] = message_id
        timestamp_ms = _message_field(message, "timestamp_ms")
        if timestamp_ms is not None:
            cf_meta["timestamp_ms"] = timestamp_ms
        attempts = _message_field(message, "attempts")
        if attempts is not None:
            cf_meta["attempts"] = attempts
        if isinstance(metadata, dict):
            cf_meta["metadata"] = dict(metadata)
        envelope.meta["cf_queues"] = cf_meta
        super().__init__(envelope)
        self._queue = queue
        self._message = message
        lease_id = _message_field(message, "lease_id")
        # A missing lease_id would poison the ack API forever (Cloudflare
        # permanently rejects {"lease_id": null} payloads, and the staged
        # entry is only dropped on success). Fail fast at delivery build time.
        if not isinstance(lease_id, str) or not lease_id:
            raise ValueError(
                "Cloudflare Queues message is missing a non-empty lease_id; "
                "refusing to build a delivery that could poison the ack queue"
            )
        self._lease_id = lease_id

    async def ack(self) -> None:
        await self._queue.stage_ack(self._lease_id)

    async def retry(self, *, delay_s: float | None = None) -> None:
        delay = None if delay_s is None else max(0, int(delay_s))
        await self._queue.stage_retry(self._lease_id, delay_seconds=delay)

    async def fail(self, exc: Exception | None = None) -> None:
        if self._queue.on_fail == "ack":
            await self._queue.stage_ack(self._lease_id)
            return
        if self._queue.on_fail == "retry":
            await self._queue.stage_retry(self._lease_id, delay_seconds=None)
        # on_fail == "leave": do nothing; visibility_timeout re-delivers later.

    async def release_unstarted(self) -> None:
        # Immediately return the message to the queue instead of waiting for the
        # visibility timeout to expire.
        await self._queue.stage_retry(self._lease_id, delay_seconds=0)
        await self._queue.flush_acks()


@dataclass
class CFQueuesConnector:
    account_id: str
    # Secret-bearing: excluded from repr so logging/exception rendering of the
    # connector can never leak the token (see _secret_tokens).
    api_token: str = field(repr=False)
    base_url: str | None = None
    timeout_s: float = 10.0
    client: Any | None = None
    _client: Any | None = field(default=None, init=False, repr=False)

    def _secret_tokens(self) -> list[str]:
        """Secret-bearing config tokens used to scrub error messages."""
        return collect_sensitive_tokens(
            self.api_token,
            {"authorization": f"Bearer {self.api_token}"},
        )

    def queue(
        self,
        queue_id: str,
        *,
        batch_size: int = 5,
        visibility_timeout_ms: int | None = None,
        poll_interval_s: float = 1.0,
        on_fail: str = "leave",
        ack_batch_size: int = 100,
        ack_flush_interval_s: float = 0.5,
    ) -> "CFQueue":
        if on_fail not in {"leave", "retry", "ack"}:
            raise ValueError("on_fail must be one of: leave, retry, ack")
        return CFQueue(
            connector=self,
            queue_id=queue_id,
            batch_size=batch_size,
            visibility_timeout_ms=visibility_timeout_ms,
            poll_interval_s=poll_interval_s,
            on_fail=on_fail,
            ack_batch_size=ack_batch_size,
            ack_flush_interval_s=ack_flush_interval_s,
        )

    def get_client(self) -> Any:
        if self.client is not None:
            return self.client
        if self._client is None:
            if AsyncCloudflare is None:
                raise RuntimeError(
                    "CFQueuesConnector requires the cloudflare SDK. "
                    "Install onestep-cf-queues."
                )
            kwargs: dict[str, Any] = {
                "api_token": self.api_token,
                "timeout": self.timeout_s,
            }
            if self.base_url is not None:
                kwargs["base_url"] = self.base_url
            self._client = AsyncCloudflare(**kwargs)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None


class CFQueue(Source, Sink):
    # Cloudflare pull is short-polling (returns immediately), so fetch is safe
    # to cancel: no message is claimed until the response is parsed.
    fetch_is_cancel_safe = True

    def __init__(
        self,
        *,
        connector: CFQueuesConnector,
        queue_id: str,
        batch_size: int,
        visibility_timeout_ms: int | None,
        poll_interval_s: float,
        on_fail: str,
        ack_batch_size: int,
        ack_flush_interval_s: float,
    ) -> None:
        Source.__init__(self, queue_id)
        Sink.__init__(self, queue_id)
        if batch_size < 1 or batch_size > _MAX_BATCH_SIZE:
            raise ValueError(
                f"batch_size must be between 1 and {_MAX_BATCH_SIZE} for Cloudflare Queues"
            )
        if ack_batch_size < 1 or ack_batch_size > _MAX_BATCH_SIZE:
            raise ValueError(
                f"ack_batch_size must be between 1 and {_MAX_BATCH_SIZE} for Cloudflare Queues"
            )
        if (
            visibility_timeout_ms is not None
            and not 0 <= visibility_timeout_ms <= _MAX_VISIBILITY_TIMEOUT_MS
        ):
            raise ValueError(
                "visibility_timeout_ms must be between 0 and "
                f"{_MAX_VISIBILITY_TIMEOUT_MS} (12 hours) for Cloudflare Queues"
            )
        self.connector = connector
        self.queue_id = queue_id
        self.batch_size = batch_size
        self.visibility_timeout_ms = visibility_timeout_ms
        self.poll_interval_s = poll_interval_s
        self.on_fail = on_fail
        self.ack_batch_size = ack_batch_size
        self.ack_flush_interval_s = ack_flush_interval_s
        self.client: Any | None = None
        self._closed = False
        self._pending_acks: list[dict[str, str]] = []
        self._pending_retries: list[dict[str, Any]] = []
        self._ack_lock: asyncio.Lock | None = None
        self._ack_flusher_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def open(self) -> None:
        try:
            if self.client is None:
                self.client = self.connector.get_client()
            self._ensure_runtime_state()
            # A late delivery.ack() after close() routes through stage_ack ->
            # open(). Restarting the flusher here would orphan the task: after
            # close() cancelled it, nothing would ever cancel the new one.
            # Stage the ack and leave it to the next explicit open()/close()
            # cycle (the runtime's reopen path) to flush it.
            if self._closed:
                return
            if self._ack_flusher_task is None and self.ack_flush_interval_s > 0:
                self._ack_flusher_task = asyncio.create_task(self._ack_flush_loop())
        except Exception as exc:
            raise self._normalize(ConnectorOperation.OPEN, exc) from None

    async def close(self) -> None:
        if self._ack_flusher_task is not None:
            self._ack_flusher_task.cancel()
            try:
                await self._ack_flusher_task
            except asyncio.CancelledError:
                pass
            self._ack_flusher_task = None
        if self._ack_lock is not None:
            async with self._ack_lock:
                await self._flush_locked(ConnectorOperation.ACK)
        # Mark the queue closed so a late delivery.ack()/retry() after close
        # cannot restart an unmanaged flusher task (an orphan nobody cancels).
        # Runtime state (pending entries, lock) is intentionally preserved so
        # pending acks staged before close still flush on a later reopen.
        self._closed = True
        self.client = None

    async def fetch(self, limit: int) -> list[Delivery]:
        try:
            await self.open()
            params: dict[str, Any] = {
                "account_id": self.connector.account_id,
                "batch_size": max(1, min(limit, self.batch_size, _MAX_BATCH_SIZE)),
            }
            if self.visibility_timeout_ms is not None:
                params["visibility_timeout_ms"] = self.visibility_timeout_ms
            response = await self.client.queues.messages.pull(self.queue_id, **params)
            messages = getattr(response, "messages", None) or []
            deliveries: list[Delivery] = []
            for message in messages:
                try:
                    deliveries.append(CFQueuesDelivery(self, message))
                except ValueError:
                    # Unusable message (e.g. missing lease_id): it can never be
                    # acked or retried, so staging it would poison the ack
                    # queue forever. Skip it; the lease simply expires and
                    # Cloudflare redelivers.
                    continue
            return deliveries
        except ConnectorOperationError:
            raise
        except Exception as exc:
            raise self._normalize(ConnectorOperation.FETCH, exc) from None

    async def send(self, envelope: Envelope) -> None:
        try:
            await self.open()
            await self.client.queues.messages.push(
                self.queue_id,
                account_id=self.connector.account_id,
                body=encode_envelope(envelope).decode("utf-8"),
                content_type="text",
            )
        except ConnectorOperationError:
            raise
        except Exception as exc:
            raise self._normalize(ConnectorOperation.SEND, exc) from None

    def control_plane_descriptor(self) -> dict[str, Any]:
        """Stable, secret-free descriptor for the control-plane topology sync.

        The account id and API token live on the connector and are never
        included; only queue topology fields are reported.
        """
        return {
            "kind": "cf_queue",
            "name": self.name,
            "config": {
                "queue_id": self.queue_id,
                "batch_size": self.batch_size,
                "visibility_timeout_ms": self.visibility_timeout_ms,
                "poll_interval_s": self.poll_interval_s,
                "on_fail": self.on_fail,
                "ack_batch_size": self.ack_batch_size,
                "ack_flush_interval_s": self.ack_flush_interval_s,
            },
        }

    async def stage_ack(self, lease_id: str) -> None:
        self._validate_lease_id(lease_id)
        await self.open()
        lock = self._ensure_runtime_state()
        async with lock:
            self._pending_acks.append({"lease_id": lease_id})
            if self._should_flush():
                await self._flush_locked(ConnectorOperation.ACK)

    async def stage_retry(self, lease_id: str, *, delay_seconds: int | None) -> None:
        self._validate_lease_id(lease_id)
        await self.open()
        lock = self._ensure_runtime_state()
        async with lock:
            entry: dict[str, Any] = {"lease_id": lease_id}
            if delay_seconds is not None:
                entry["delay_seconds"] = min(delay_seconds, _MAX_DELAY_SECONDS)
            self._pending_retries.append(entry)
            if self._should_flush():
                await self._flush_locked(ConnectorOperation.ACK)

    async def flush_acks(self) -> None:
        await self.open()
        lock = self._ensure_runtime_state()
        async with lock:
            await self._flush_locked(ConnectorOperation.ACK)

    def _should_flush(self) -> bool:
        total = len(self._pending_acks) + len(self._pending_retries)
        return total >= self.ack_batch_size or self.ack_flush_interval_s <= 0

    @staticmethod
    def _validate_lease_id(lease_id: Any) -> None:
        # Defense in depth: the delivery constructor already rejects missing
        # lease ids, but a direct stage_ack/stage_retry call must also fail
        # fast instead of staging a {"lease_id": None} entry that Cloudflare
        # would reject forever while the flusher keeps resending it.
        if not isinstance(lease_id, str) or not lease_id:
            raise ValueError("lease_id must be a non-empty string")

    def _ensure_runtime_state(self) -> asyncio.Lock:
        current_loop = asyncio.get_running_loop()
        if self._ack_lock is None or self._loop is not current_loop:
            # Preserve entries staged on a previous loop: they still reference
            # live leases and dropping them silently turns acks into
            # redeliveries. The flusher task is loop-bound, so it is discarded.
            self._ack_lock = asyncio.Lock()
            self._loop = current_loop
            self._ack_flusher_task = None
        return self._ack_lock

    async def _ack_flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self.ack_flush_interval_s)
            try:
                await self.flush_acks()
            except Exception:
                # Keep the flusher alive; the fetch/ack path surfaces errors.
                continue

    async def _flush_locked(self, operation: ConnectorOperation) -> None:
        while self._pending_acks or self._pending_retries:
            acks = self._pending_acks[: self.ack_batch_size]
            remaining = self.ack_batch_size - len(acks)
            retries = self._pending_retries[:remaining] if remaining > 0 else []
            try:
                await self.client.queues.messages.ack(
                    self.queue_id,
                    account_id=self.connector.account_id,
                    acks=list(acks),
                    retries=list(retries),
                )
            except ConnectorOperationError:
                raise
            except Exception as exc:
                # Entries stay staged so a later flush retries them.
                raise self._normalize(operation, exc) from None
            self._pending_acks = self._pending_acks[len(acks) :]
            self._pending_retries = self._pending_retries[len(retries) :]

    def _normalize(
        self, operation: ConnectorOperation, exc: Exception
    ) -> ConnectorOperationError:
        connector_error = as_cf_connector_operation_error(
            operation=operation,
            exc=exc,
            source_name=self.name,
            retry_delay_s=max(self.poll_interval_s, 1.0),
            secrets=self.connector._secret_tokens(),
        )
        if connector_error is None:
            raise exc
        return connector_error
