from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from onestep.envelope import Envelope
from onestep_kafka import KafkaConnector


@dataclass(frozen=True)
class FakeTopicPartition:
    topic: str
    partition: int


@dataclass
class FakeRecord:
    topic: str
    partition: int
    offset: int
    value: bytes
    key: bytes | None = None
    headers: list[tuple[str, bytes | None]] | None = None
    timestamp: int | None = None


class FakeConsumer:
    def __init__(self, *topics: str, **kwargs: Any) -> None:
        self.topics = topics
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.records: dict[FakeTopicPartition, list[FakeRecord]] = {}
        self.commits: list[dict[FakeTopicPartition, int]] = []
        self.seeks: list[tuple[FakeTopicPartition, int]] = []
        self.block_getmany = False
        self.getmany_started: asyncio.Event | None = None
        self.release_getmany: asyncio.Event | None = None

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def getmany(self, *, timeout_ms: int, max_records: int):
        if self.block_getmany:
            assert self.getmany_started is not None
            assert self.release_getmany is not None
            self.getmany_started.set()
            await self.release_getmany.wait()
            self.block_getmany = False
        selected: dict[FakeTopicPartition, list[FakeRecord]] = {}
        remaining = max_records
        for topic_partition, records in list(self.records.items()):
            if remaining <= 0:
                break
            batch = records[:remaining]
            self.records[topic_partition] = records[remaining:]
            if batch:
                selected[topic_partition] = batch
                remaining -= len(batch)
        return selected

    async def commit(self, offsets):
        self.commits.append(dict(offsets))

    def seek(self, topic_partition, offset: int) -> None:
        self.seeks.append((topic_partition, offset))

    def block_next_getmany(self) -> None:
        self.block_getmany = True
        self.getmany_started = asyncio.Event()
        self.release_getmany = asyncio.Event()


class FakeProducer:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.sent: list[dict[str, Any]] = []

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def send_and_wait(self, topic: str, **kwargs: Any) -> None:
        self.sent.append({"topic": topic, **kwargs})


class FakeDriver:
    TopicPartition = FakeTopicPartition

    def __init__(self) -> None:
        self.consumers: list[FakeConsumer] = []
        self.producers: list[FakeProducer] = []

    def AIOKafkaConsumer(self, *topics: str, **kwargs: Any) -> FakeConsumer:
        consumer = FakeConsumer(*topics, **kwargs)
        self.consumers.append(consumer)
        return consumer

    def AIOKafkaProducer(self, **kwargs: Any) -> FakeProducer:
        producer = FakeProducer(**kwargs)
        self.producers.append(producer)
        return producer


def test_kafka_topic_sends_encoded_envelope_with_key_and_headers() -> None:
    async def scenario() -> None:
        driver = FakeDriver()
        connector = KafkaConnector("localhost:9092", options={"security_protocol": "PLAINTEXT"}, driver=driver)
        topic = connector.topic(
            "orders.out",
            key="tenant-a",
            headers={"source": "onestep"},
            producer_options={"compression_type": "gzip"},
        )

        await topic.send(Envelope(body={"id": 1}, meta={"trace": "abc"}, attempts=2))

        producer = driver.producers[0]
        assert producer.kwargs["bootstrap_servers"] == "localhost:9092"
        assert producer.kwargs["security_protocol"] == "PLAINTEXT"
        assert producer.kwargs["compression_type"] == "gzip"
        assert producer.sent[0]["topic"] == "orders.out"
        assert producer.sent[0]["key"] == b"tenant-a"
        assert producer.sent[0]["headers"] == [("source", b"onestep")]
        assert producer.sent[0]["value"] == b'{"body": {"id": 1}, "meta": {"trace": "abc"}, "attempts": 2}'

    asyncio.run(scenario())


def test_kafka_topic_fetch_decodes_envelopes_and_injects_metadata() -> None:
    async def scenario() -> None:
        driver = FakeDriver()
        connector = KafkaConnector("localhost:9092", driver=driver)
        topic = connector.topic(
            "orders.in",
            group_id="workers",
            client_id="worker-1",
            batch_size=10,
            poll_timeout_ms=5,
            consumer_options={"auto_offset_reset": "earliest"},
        )
        consumer = await topic._open_consumer()
        tp = FakeTopicPartition("orders.in", 0)
        consumer.records[tp] = [
            FakeRecord(
                topic="orders.in",
                partition=0,
                offset=10,
                value=b'{"body": {"id": 1}, "meta": {"trace": "abc", "kafka": {"old": "kept", "offset": 1}}, "attempts": 3}',
                key=b"tenant-a",
                headers=[("source", b"upstream")],
                timestamp=1720000000000,
            )
        ]

        deliveries = await topic.fetch(10)

        assert len(deliveries) == 1
        delivery = deliveries[0]
        assert delivery.payload == {"id": 1}
        assert delivery.envelope.attempts == 3
        assert delivery.envelope.meta["trace"] == "abc"
        assert delivery.envelope.meta["kafka"] == {
            "old": "kept",
            "topic": "orders.in",
            "partition": 0,
            "offset": 10,
            "timestamp": 1720000000000,
            "key": "tenant-a",
            "headers": {"source": "upstream"},
        }
        assert driver.consumers[0].kwargs["enable_auto_commit"] is False
        assert driver.consumers[0].kwargs["group_id"] == "workers"
        assert driver.consumers[0].kwargs["client_id"] == "worker-1"
        assert driver.consumers[0].kwargs["auto_offset_reset"] == "earliest"

    asyncio.run(scenario())


def test_ack_commits_only_after_contiguous_offsets_complete() -> None:
    async def scenario() -> None:
        driver = FakeDriver()
        connector = KafkaConnector("localhost:9092", driver=driver)
        topic = connector.topic("orders.in", group_id="workers")
        consumer = await topic._open_consumer()
        tp = FakeTopicPartition("orders.in", 0)
        consumer.records[tp] = [
            FakeRecord("orders.in", 0, 10, b'{"id": 10}'),
            FakeRecord("orders.in", 0, 11, b'{"id": 11}'),
        ]

        first, second = await topic.fetch(2)
        await first.start_processing()
        await second.start_processing()
        await second.ack()
        assert consumer.commits == []

        await first.ack()
        assert consumer.commits == [{tp: 12}]

    asyncio.run(scenario())


def test_retry_does_not_commit_and_seeks_to_offset() -> None:
    async def scenario() -> None:
        driver = FakeDriver()
        connector = KafkaConnector("localhost:9092", driver=driver)
        topic = connector.topic("orders.in", group_id="workers")
        consumer = await topic._open_consumer()
        tp = FakeTopicPartition("orders.in", 0)
        consumer.records[tp] = [FakeRecord("orders.in", 0, 10, b'{"id": 10}')]

        delivery = (await topic.fetch(1))[0]
        await delivery.start_processing()
        await delivery.retry()

        assert consumer.commits == []
        assert consumer.seeks == [(tp, 10)]

    asyncio.run(scenario())


def test_fail_advances_commit_tracker() -> None:
    async def scenario() -> None:
        driver = FakeDriver()
        connector = KafkaConnector("localhost:9092", driver=driver)
        topic = connector.topic("orders.in", group_id="workers")
        consumer = await topic._open_consumer()
        tp = FakeTopicPartition("orders.in", 0)
        consumer.records[tp] = [FakeRecord("orders.in", 0, 10, b'{"id": 10}')]

        delivery = (await topic.fetch(1))[0]
        await delivery.start_processing()
        await delivery.fail(RuntimeError("poison"))

        assert consumer.commits == [{tp: 11}]

    asyncio.run(scenario())


def test_release_unstarted_does_not_commit_and_seeks_to_offset() -> None:
    async def scenario() -> None:
        driver = FakeDriver()
        connector = KafkaConnector("localhost:9092", driver=driver)
        topic = connector.topic("orders.in", group_id="workers")
        consumer = await topic._open_consumer()
        tp = FakeTopicPartition("orders.in", 0)
        consumer.records[tp] = [FakeRecord("orders.in", 0, 10, b'{"id": 10}')]

        delivery = (await topic.fetch(1))[0]
        await delivery.release_unstarted()

        assert consumer.commits == []
        assert consumer.seeks == [(tp, 10)]

    asyncio.run(scenario())
