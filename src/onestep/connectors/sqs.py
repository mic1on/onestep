from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from onestep.envelope import Envelope
from onestep.resilience import ConnectorOperation, ConnectorOperationError, as_connector_operation_error

from .base import Delivery, Sink, Source
from .codec import decode_envelope, encode_envelope

try:  # pragma: no cover - optional dependency
    import boto3
except ImportError:  # pragma: no cover - optional dependency
    boto3 = None


class SQSDelivery(Delivery):
    def __init__(self, queue: "SQSQueue", message: dict[str, Any]) -> None:
        super().__init__(decode_envelope(message["Body"]))
        self._queue = queue
        self._message = message
        self._heartbeat_task: asyncio.Task[None] | None = None

    async def start_processing(self) -> None:
        if self._queue.heartbeat_interval_s is None or self._heartbeat_task is not None:
            return
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def ack(self) -> None:
        await self._stop_heartbeat()
        await self._queue.stage_delete(self._message)

    async def retry(self, *, delay_s: float | None = None) -> None:
        await self._stop_heartbeat()
        timeout = 0 if delay_s is None else max(0, int(delay_s))
        await asyncio.to_thread(
            self._queue.client.change_message_visibility,
            QueueUrl=self._queue.url,
            ReceiptHandle=self._message["ReceiptHandle"],
            VisibilityTimeout=timeout,
        )

    async def fail(self, exc: Exception | None = None) -> None:
        await self._stop_heartbeat()
        if self._queue.on_fail == "delete":
            await self._queue.stage_delete(self._message)
            return
        if self._queue.on_fail == "release":
            await asyncio.to_thread(
                self._queue.client.change_message_visibility,
                QueueUrl=self._queue.url,
                ReceiptHandle=self._message["ReceiptHandle"],
                VisibilityTimeout=0,
            )

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self._queue.heartbeat_interval_s)
            await asyncio.to_thread(
                self._queue.client.change_message_visibility,
                QueueUrl=self._queue.url,
                ReceiptHandle=self._message["ReceiptHandle"],
                VisibilityTimeout=self._queue.heartbeat_visibility_timeout,
            )

    async def _stop_heartbeat(self) -> None:
        if self._heartbeat_task is None:
            return
        self._heartbeat_task.cancel()
        try:
            await self._heartbeat_task
        except asyncio.CancelledError:
            pass
        self._heartbeat_task = None


@dataclass
class SQSConnector:
    region_name: str | None = None
    options: dict[str, Any] | None = None
    client: Any | None = None
    _client: Any | None = field(default=None, init=False, repr=False)

    def queue(
        self,
        url: str,
        *,
        wait_time_s: int = 20,
        visibility_timeout: int | None = None,
        batch_size: int = 10,
        poll_interval_s: float = 0.0,
        message_group_id: str | None = None,
        deduplication_id_factory: Any | None = None,
        on_fail: str = "leave",
        delete_batch_size: int = 10,
        delete_flush_interval_s: float = 0.5,
        heartbeat_interval_s: float | None = None,
        heartbeat_visibility_timeout: int | None = None,
    ) -> "SQSQueue":
        if on_fail not in {"leave", "release", "delete"}:
            raise ValueError("on_fail must be one of: leave, release, delete")
        return SQSQueue(
            connector=self,
            url=url,
            wait_time_s=wait_time_s,
            visibility_timeout=visibility_timeout,
            batch_size=batch_size,
            poll_interval_s=poll_interval_s,
            message_group_id=message_group_id,
            deduplication_id_factory=deduplication_id_factory,
            on_fail=on_fail,
            delete_batch_size=delete_batch_size,
            delete_flush_interval_s=delete_flush_interval_s,
            heartbeat_interval_s=heartbeat_interval_s,
            heartbeat_visibility_timeout=heartbeat_visibility_timeout,
        )

    def get_client(self) -> Any:
        if self.client is not None:
            return self.client
        if self._client is None:
            if boto3 is None:
                raise RuntimeError("SQSConnector requires boto3. Install onestep[sqs].")
            self._client = boto3.client("sqs", region_name=self.region_name, **(self.options or {}))
        return self._client

    async def close(self) -> None:
        self._client = None


class SQSQueue(Source, Sink):
    def __init__(
        self,
        *,
        connector: SQSConnector,
        url: str,
        wait_time_s: int,
        visibility_timeout: int | None,
        batch_size: int,
        poll_interval_s: float,
        message_group_id: str | None,
        deduplication_id_factory: Any | None,
        on_fail: str,
        delete_batch_size: int,
        delete_flush_interval_s: float,
        heartbeat_interval_s: float | None,
        heartbeat_visibility_timeout: int | None,
    ) -> None:
        Source.__init__(self, url)
        Sink.__init__(self, url)
        if batch_size < 1 or batch_size > 10:
            raise ValueError("batch_size must be between 1 and 10 for SQS")
        if delete_batch_size < 1 or delete_batch_size > 10:
            raise ValueError("delete_batch_size must be between 1 and 10 for SQS")
        self.connector = connector
        self.url = url
        self.wait_time_s = wait_time_s
        self.visibility_timeout = visibility_timeout
        self.batch_size = batch_size
        self.poll_interval_s = poll_interval_s
        self.message_group_id = message_group_id
        self.deduplication_id_factory = deduplication_id_factory
        self.on_fail = on_fail
        self.delete_batch_size = delete_batch_size
        self.delete_flush_interval_s = delete_flush_interval_s
        self.heartbeat_interval_s = heartbeat_interval_s
        self.heartbeat_visibility_timeout = (
            heartbeat_visibility_timeout
            if heartbeat_visibility_timeout is not None
            else visibility_timeout or 60
        )
        self.client: Any | None = None
        self._pending_delete: list[dict[str, str]] = []
        self._delete_lock: asyncio.Lock | None = None
        self._delete_flusher_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._delete_counter = 0

    async def open(self) -> None:
        try:
            if self.client is None:
                self.client = self.connector.get_client()
            self._ensure_runtime_state()
            if self._delete_flusher_task is None and self.delete_flush_interval_s > 0:
                self._delete_flusher_task = asyncio.create_task(self._delete_flush_loop())
        except Exception as exc:
            connector_error = as_connector_operation_error(
                backend="sqs",
                operation=ConnectorOperation.OPEN,
                exc=exc,
                source_name=self.name,
                retry_delay_s=max(self.poll_interval_s, float(self.wait_time_s)),
            )
            if connector_error is None:
                raise
            raise connector_error from exc

    async def close(self) -> None:
        if self._delete_flusher_task is not None:
            self._delete_flusher_task.cancel()
            try:
                await self._delete_flusher_task
            except asyncio.CancelledError:
                pass
            self._delete_flusher_task = None
        if self._delete_lock is not None:
            async with self._delete_lock:
                await self._flush_locked()
        self.client = None

    async def fetch(self, limit: int) -> list[Delivery]:
        try:
            await self.open()
            params = {
                "QueueUrl": self.url,
                "MaxNumberOfMessages": max(1, min(limit, self.batch_size, 10)),
                "WaitTimeSeconds": self.wait_time_s,
                "MessageAttributeNames": ["All"],
                "AttributeNames": ["All"],
            }
            if self.visibility_timeout is not None:
                params["VisibilityTimeout"] = self.visibility_timeout
            response = await asyncio.to_thread(self.client.receive_message, **params)
            messages = response.get("Messages", [])
            return [SQSDelivery(self, message) for message in messages]
        except ConnectorOperationError:
            raise
        except Exception as exc:
            connector_error = as_connector_operation_error(
                backend="sqs",
                operation=ConnectorOperation.FETCH,
                exc=exc,
                source_name=self.name,
                retry_delay_s=max(self.poll_interval_s, float(self.wait_time_s)),
            )
            if connector_error is None:
                raise
            raise connector_error from exc

    async def send(self, envelope: Envelope) -> None:
        try:
            await self.open()
            params = {
                "QueueUrl": self.url,
                "MessageBody": encode_envelope(envelope).decode("utf-8"),
            }
            if self.url.endswith(".fifo"):
                group_id = self.message_group_id
                if not group_id:
                    raise ValueError("FIFO SQS queues require message_group_id")
                params["MessageGroupId"] = group_id
                if self.deduplication_id_factory is not None:
                    params["MessageDeduplicationId"] = self.deduplication_id_factory(envelope)
            await asyncio.to_thread(self.client.send_message, **params)
        except ConnectorOperationError:
            raise
        except Exception as exc:
            connector_error = as_connector_operation_error(
                backend="sqs",
                operation=ConnectorOperation.SEND,
                exc=exc,
                source_name=self.name,
                retry_delay_s=max(self.poll_interval_s, float(self.wait_time_s)),
            )
            if connector_error is None:
                raise
            raise connector_error from exc

    async def stage_delete(self, message: dict[str, Any]) -> None:
        await self.open()
        lock = self._ensure_runtime_state()
        async with lock:
            self._delete_counter += 1
            entry = {
                "Id": f"msg_{self._delete_counter}",
                "ReceiptHandle": message["ReceiptHandle"],
            }
            self._pending_delete.append(entry)
            if len(self._pending_delete) >= self.delete_batch_size or self.delete_flush_interval_s <= 0:
                await self._flush_locked()

    async def flush_deletes(self) -> None:
        await self.open()
        lock = self._ensure_runtime_state()
        async with lock:
            await self._flush_locked()

    def _ensure_runtime_state(self) -> asyncio.Lock:
        current_loop = asyncio.get_running_loop()
        if self._delete_lock is None or self._loop is not current_loop:
            self._delete_lock = asyncio.Lock()
            self._loop = current_loop
            self._pending_delete = []
            self._delete_flusher_task = None
            self._delete_counter = 0
        return self._delete_lock

    async def _delete_flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self.delete_flush_interval_s)
            try:
                await self.flush_deletes()
            except Exception:
                # Keep the flusher alive; fetch/ack path will surface client errors.
                continue

    async def _flush_locked(self) -> None:
        if not self._pending_delete:
            return
        entries = self._pending_delete[:10]
        self._pending_delete = self._pending_delete[10:]
        await asyncio.to_thread(
            self.client.delete_message_batch,
            QueueUrl=self.url,
            Entries=entries,
        )
        if self._pending_delete:
            await self._flush_locked()
