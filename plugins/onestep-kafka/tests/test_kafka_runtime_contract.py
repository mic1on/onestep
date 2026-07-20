from __future__ import annotations

import asyncio

from onestep import OneStepApp
from onestep_kafka import KafkaConnector

from test_kafka_connector import FakeDriver, FakeRecord, FakeTopicPartition


def test_kafka_topic_fetch_is_not_cancel_safe() -> None:
    topic = KafkaConnector("localhost:9092", driver=FakeDriver()).topic("orders", group_id="workers")

    assert topic.fetch_is_cancel_safe is False


def test_shutdown_releases_fetched_unstarted_kafka_delivery() -> None:
    async def scenario() -> None:
        driver = FakeDriver()
        topic = KafkaConnector("localhost:9092", driver=driver).topic(
            "orders",
            group_id="workers",
            poll_timeout_ms=0,
        )
        consumer = await topic._open_consumer()
        tp = FakeTopicPartition("orders", 0)
        consumer.records[tp] = [FakeRecord("orders", 0, 10, b'{"id": 10}')]
        consumer.block_next_getmany()

        app = OneStepApp("kafka-shutdown", shutdown_timeout_s=1.0)
        handled: list[dict[str, int]] = []

        @app.task(source=topic, concurrency=1)
        async def consume(ctx, item):
            handled.append(item)

        serve_task = asyncio.create_task(app.serve())
        assert consumer.getmany_started is not None
        assert consumer.release_getmany is not None
        await asyncio.wait_for(consumer.getmany_started.wait(), timeout=1.0)
        app.request_shutdown()
        await asyncio.sleep(0.02)
        assert serve_task.done() is False
        consumer.release_getmany.set()
        await asyncio.wait_for(serve_task, timeout=2.0)

        assert handled == []
        assert consumer.commits == []
        assert consumer.seeks == [(tp, 10)]

    asyncio.run(scenario())
