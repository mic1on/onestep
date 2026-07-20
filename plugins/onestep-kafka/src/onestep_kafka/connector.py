from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from onestep.envelope import Envelope
from onestep.resilience import ConnectorOperation, ConnectorOperationError

from onestep.connectors.base import Delivery, Sink, Source
from onestep.connectors.codec import decode_envelope, encode_envelope

from .resilience import as_kafka_connector_operation_error

try:  # pragma: no cover - optional dependency
    from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
    from aiokafka.structs import TopicPartition
except ImportError:  # pragma: no cover - optional dependency
    AIOKafkaConsumer = None
    AIOKafkaProducer = None
    TopicPartition = None


@dataclass
class _PartitionOffsetState:
    next_commit_offset: int | None = None
    fetched_offsets: set[int] = field(default_factory=set)
    started_offsets: set[int] = field(default_factory=set)
    completed_offsets: set[int] = field(default_factory=set)


class KafkaOffsetTracker:
    def __init__(self) -> None:
        self._states: dict[tuple[str, int], _PartitionOffsetState] = {}

    def mark_fetched(self, topic: str, partition: int, offset: int) -> None:
        state = self._state(topic, partition, offset)
        state.fetched_offsets.add(offset)

    def mark_started(self, topic: str, partition: int, offset: int) -> None:
        state = self._state(topic, partition, offset)
        state.started_offsets.add(offset)

    def mark_completed(self, topic: str, partition: int, offset: int) -> int | None:
        state = self._state(topic, partition, offset)
        state.completed_offsets.add(offset)
        return self._next_committable_offset(state)

    def mark_committed(self, topic: str, partition: int, next_offset: int) -> None:
        state = self._state(topic, partition, next_offset)
        for offset in list(state.completed_offsets):
            if offset < next_offset:
                state.completed_offsets.discard(offset)
        for offset in list(state.fetched_offsets):
            if offset < next_offset:
                state.fetched_offsets.discard(offset)
        for offset in list(state.started_offsets):
            if offset < next_offset:
                state.started_offsets.discard(offset)
        state.next_commit_offset = next_offset

    def mark_released_unstarted(self, topic: str, partition: int, offset: int) -> int | None:
        state = self._state(topic, partition, offset)
        if offset in state.started_offsets or offset in state.completed_offsets:
            return None
        state.fetched_offsets.discard(offset)
        return offset

    def next_committed_offset(self, topic: str, partition: int) -> int | None:
        state = self._states.get((topic, partition))
        return None if state is None else state.next_commit_offset

    def _state(self, topic: str, partition: int, offset: int) -> _PartitionOffsetState:
        key = (topic, partition)
        state = self._states.get(key)
        if state is None:
            state = _PartitionOffsetState(next_commit_offset=offset)
            self._states[key] = state
        elif state.next_commit_offset is None or offset < state.next_commit_offset:
            state.next_commit_offset = offset
        return state

    def _next_committable_offset(self, state: _PartitionOffsetState) -> int | None:
        if state.next_commit_offset is None:
            return None
        next_offset = state.next_commit_offset
        while next_offset in state.completed_offsets:
            next_offset += 1
        if next_offset == state.next_commit_offset:
            return None
        return next_offset


class KafkaDelivery(Delivery):
    def __init__(self, topic: "KafkaTopic", record: Any) -> None:
        self._topic = topic
        self._record = record
        super().__init__(_decode_record_envelope(record))

    async def start_processing(self) -> None:
        await self._topic.mark_started(self._record)

    async def release_unstarted(self) -> None:
        await self._topic.release_unstarted_record(self._record)

    async def ack(self) -> None:
        await self._topic.ack_record(self._record)

    async def retry(self, *, delay_s: float | None = None) -> None:
        if delay_s:
            await asyncio.sleep(delay_s)
        await self._topic.seek_record(self._record)

    async def fail(self, exc: Exception | None = None) -> None:
        await self._topic.ack_record(self._record)


class KafkaConnector:
    def __init__(
        self,
        bootstrap_servers: str | list[str],
        options: dict[str, Any] | None = None,
        *,
        driver: Any | None = None,
    ) -> None:
        self.bootstrap_servers = bootstrap_servers
        self.options = options or {}
        self._driver_override = driver

    def topic(
        self,
        topic: str,
        *,
        group_id: str | None = None,
        client_id: str | None = None,
        batch_size: int = 100,
        poll_timeout_ms: int = 1000,
        key: str | bytes | None = None,
        headers: dict[str, Any] | list[tuple[str, Any]] | None = None,
        consumer_options: dict[str, Any] | None = None,
        producer_options: dict[str, Any] | None = None,
    ) -> "KafkaTopic":
        return KafkaTopic(
            connector=self,
            topic=topic,
            group_id=group_id,
            client_id=client_id,
            batch_size=batch_size,
            poll_timeout_ms=poll_timeout_ms,
            key=key,
            headers=headers,
            consumer_options=consumer_options or {},
            producer_options=producer_options or {},
        )

    def driver(self) -> Any:
        if self._driver_override is not None:
            return self._driver_override
        if AIOKafkaConsumer is None or AIOKafkaProducer is None or TopicPartition is None:
            raise RuntimeError("KafkaConnector requires aiokafka. Install onestep-kafka.")
        return _AiokafkaDriver


class _AiokafkaDriver:
    AIOKafkaConsumer = AIOKafkaConsumer
    AIOKafkaProducer = AIOKafkaProducer
    TopicPartition = TopicPartition


class KafkaTopic(Source, Sink):
    fetch_is_cancel_safe = False

    def __init__(
        self,
        *,
        connector: KafkaConnector,
        topic: str,
        group_id: str | None,
        client_id: str | None,
        batch_size: int,
        poll_timeout_ms: int,
        key: str | bytes | None,
        headers: dict[str, Any] | list[tuple[str, Any]] | None,
        consumer_options: dict[str, Any],
        producer_options: dict[str, Any],
    ) -> None:
        Source.__init__(self, topic)
        Sink.__init__(self, topic)
        self.connector = connector
        self.topic = topic
        self.group_id = group_id
        self.client_id = client_id
        self.batch_size = batch_size
        self.poll_timeout_ms = poll_timeout_ms
        self.key = key
        self.headers = headers
        self.consumer_options = consumer_options
        self.producer_options = producer_options
        self._consumer: Any | None = None
        self._producer: Any | None = None
        self._consumer_lock: asyncio.Lock | None = None
        self._producer_lock: asyncio.Lock | None = None
        self._offset_lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._offset_tracker = KafkaOffsetTracker()

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        errors: list[BaseException] = []
        if self._consumer is not None:
            try:
                await self._consumer.stop()
            except Exception as exc:
                errors.append(exc)
            finally:
                self._consumer = None
        if self._producer is not None:
            try:
                await self._producer.stop()
            except Exception as exc:
                errors.append(exc)
            finally:
                self._producer = None
        if errors:
            raise errors[0]

    async def fetch(self, limit: int) -> list[Delivery]:
        try:
            consumer = await self._open_consumer()
            max_records = max(1, min(limit, self.batch_size))
            batches = await consumer.getmany(timeout_ms=self.poll_timeout_ms, max_records=max_records)
            deliveries: list[Delivery] = []
            async with self._runtime_offset_lock():
                for records in batches.values():
                    for record in records:
                        if len(deliveries) >= max_records:
                            break
                        self._offset_tracker.mark_fetched(record.topic, record.partition, record.offset)
                        deliveries.append(KafkaDelivery(self, record))
            return deliveries
        except ConnectorOperationError:
            raise
        except Exception as exc:
            connector_error = as_kafka_connector_operation_error(
                operation=ConnectorOperation.FETCH,
                exc=exc,
                source_name=self.name,
                retry_delay_s=self.poll_timeout_ms / 1000,
            )
            if connector_error is None:
                raise
            raise connector_error from exc

    async def send(self, envelope: Envelope) -> None:
        try:
            producer = await self._open_producer()
            await producer.send_and_wait(
                self.topic,
                value=encode_envelope(envelope),
                key=_normalize_key(self.key),
                headers=_normalize_headers(self.headers),
            )
        except ConnectorOperationError:
            raise
        except Exception as exc:
            connector_error = as_kafka_connector_operation_error(
                operation=ConnectorOperation.SEND,
                exc=exc,
                source_name=self.name,
                retry_delay_s=self.poll_timeout_ms / 1000,
            )
            if connector_error is None:
                raise
            raise connector_error from exc

    async def mark_started(self, record: Any) -> None:
        async with self._runtime_offset_lock():
            self._offset_tracker.mark_started(record.topic, record.partition, record.offset)

    async def ack_record(self, record: Any) -> None:
        async with self._runtime_offset_lock():
            next_offset = self._offset_tracker.mark_completed(record.topic, record.partition, record.offset)
            if next_offset is None:
                return
            await self._commit_offset_unlocked(record.topic, record.partition, next_offset)
            self._offset_tracker.mark_committed(record.topic, record.partition, next_offset)

    async def release_unstarted_record(self, record: Any) -> None:
        async with self._runtime_offset_lock():
            seek_offset = self._offset_tracker.mark_released_unstarted(
                record.topic,
                record.partition,
                record.offset,
            )
            if seek_offset is not None:
                await self._seek_to_offset_unlocked(record.topic, record.partition, seek_offset)

    async def seek_record(self, record: Any) -> None:
        async with self._runtime_offset_lock():
            await self._seek_to_offset_unlocked(record.topic, record.partition, record.offset)

    async def commit_offset(self, topic: str, partition: int, next_offset: int) -> None:
        async with self._runtime_offset_lock():
            await self._commit_offset_unlocked(topic, partition, next_offset)
            self._offset_tracker.mark_committed(topic, partition, next_offset)

    async def seek_to_offset(self, topic: str, partition: int, offset: int) -> None:
        async with self._runtime_offset_lock():
            await self._seek_to_offset_unlocked(topic, partition, offset)

    async def _commit_offset_unlocked(self, topic: str, partition: int, next_offset: int) -> None:
        consumer = await self._open_consumer()
        topic_partition = self.connector.driver().TopicPartition(topic, partition)
        await consumer.commit({topic_partition: next_offset})

    async def _seek_to_offset_unlocked(self, topic: str, partition: int, offset: int) -> None:
        consumer = await self._open_consumer()
        topic_partition = self.connector.driver().TopicPartition(topic, partition)
        consumer.seek(topic_partition, offset)

    async def _open_consumer(self) -> Any:
        self._ensure_runtime_state()
        assert self._consumer_lock is not None
        async with self._consumer_lock:
            if self._consumer is not None:
                return self._consumer
            if not self.group_id:
                raise ValueError("kafka_topic requires group_id when used as a source")
            driver = self.connector.driver()
            options = {
                **self.connector.options,
                **self.consumer_options,
                "bootstrap_servers": self.connector.bootstrap_servers,
                "group_id": self.group_id,
                "enable_auto_commit": False,
            }
            if self.client_id is not None:
                options["client_id"] = self.client_id
            self._consumer = driver.AIOKafkaConsumer(self.topic, **options)
            await self._consumer.start()
            return self._consumer

    async def _open_producer(self) -> Any:
        self._ensure_runtime_state()
        assert self._producer_lock is not None
        async with self._producer_lock:
            if self._producer is not None:
                return self._producer
            driver = self.connector.driver()
            options = {
                **self.connector.options,
                **self.producer_options,
                "bootstrap_servers": self.connector.bootstrap_servers,
            }
            if self.client_id is not None:
                options["client_id"] = self.client_id
            self._producer = driver.AIOKafkaProducer(**options)
            await self._producer.start()
            return self._producer

    def _runtime_offset_lock(self) -> asyncio.Lock:
        self._ensure_runtime_state()
        assert self._offset_lock is not None
        return self._offset_lock

    def _ensure_runtime_state(self) -> None:
        current_loop = asyncio.get_running_loop()
        if self._loop is not current_loop:
            self._loop = current_loop
            self._consumer_lock = asyncio.Lock()
            self._producer_lock = asyncio.Lock()
            self._offset_lock = asyncio.Lock()
            self._consumer = None
            self._producer = None
            self._offset_tracker = KafkaOffsetTracker()


def _decode_record_envelope(record: Any) -> Envelope:
    envelope = decode_envelope(record.value)
    existing_kafka_meta = envelope.meta.get("kafka")
    kafka_meta = dict(existing_kafka_meta) if isinstance(existing_kafka_meta, Mapping) else {}
    kafka_meta.update(
        {
            "topic": record.topic,
            "partition": record.partition,
            "offset": record.offset,
            "timestamp": getattr(record, "timestamp", None),
            "key": _decode_bytes(getattr(record, "key", None)),
            "headers": _decode_headers(getattr(record, "headers", None)),
        }
    )
    envelope.meta["kafka"] = kafka_meta
    return envelope


def _normalize_key(value: str | bytes | None) -> bytes | None:
    if value is None or isinstance(value, bytes):
        return value
    return value.encode("utf-8")


def _normalize_headers(headers: dict[str, Any] | list[tuple[str, Any]] | None) -> list[tuple[str, bytes | None]] | None:
    if headers is None:
        return None
    items = headers.items() if isinstance(headers, Mapping) else headers
    return [(str(key), _normalize_header_value(value)) for key, value in items]


def _normalize_header_value(value: Any) -> bytes | None:
    if value is None or isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return str(value).encode("utf-8")


def _decode_headers(headers: Sequence[tuple[str, bytes | None]] | None) -> dict[str, str | None]:
    if not headers:
        return {}
    return {key: _decode_bytes(value) for key, value in headers}


def _decode_bytes(value: bytes | None) -> str | None:
    if value is None:
        return None
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return repr(value)
