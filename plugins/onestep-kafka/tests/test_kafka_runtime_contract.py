from __future__ import annotations

import asyncio
from typing import Any

import pytest

from onestep.testing import (
    AcknowledgedSinkHarness,
    ClaimedSourceHarness,
    ConnectorCapability,
    ConnectorConformanceProfile,
    StopControl,
    run_acknowledged_sink_contract,
    run_claimed_source_stop_contract,
)
from onestep_kafka import KafkaConnector

from test_kafka_connector import FakeDriver, FakeProducer, FakeRecord, FakeTopicPartition


KAFKA_CONFORMANCE_PROFILE = ConnectorConformanceProfile(
    name="kafka",
    contracts={
        ConnectorCapability.BASIC_SOURCE: (
            "test_kafka_topic_fetch_decodes_envelopes_and_injects_metadata",
        ),
        ConnectorCapability.CHECKPOINT_SOURCE: (
            "test_ack_commits_only_after_contiguous_offsets_complete",
        ),
        ConnectorCapability.CLAIMED_SOURCE: (
            "test_stop_controls_release_fetched_unstarted_kafka_delivery",
        ),
        ConnectorCapability.ACKNOWLEDGED_SINK: (
            "test_runtime_ack_follows_kafka_producer_acknowledgement",
        ),
        ConnectorCapability.PUBLIC_ERRORS: (
            "test_kafka_topic_redacts_consumer_and_producer_passwords",
        ),
    },
)


def test_kafka_topic_fetch_is_not_cancel_safe() -> None:
    topic = KafkaConnector("localhost:9092", driver=FakeDriver()).topic("orders", group_id="workers")

    assert topic.fetch_is_cancel_safe is False


def test_kafka_conformance_profile() -> None:
    assert KAFKA_CONFORMANCE_PROFILE.capabilities == {
        ConnectorCapability.BASIC_SOURCE,
        ConnectorCapability.CHECKPOINT_SOURCE,
        ConnectorCapability.CLAIMED_SOURCE,
        ConnectorCapability.ACKNOWLEDGED_SINK,
        ConnectorCapability.PUBLIC_ERRORS,
    }


@pytest.mark.parametrize("control", list(StopControl))
def test_stop_controls_release_fetched_unstarted_kafka_delivery(control: StopControl) -> None:
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

        assert consumer.getmany_started is not None
        assert consumer.release_getmany is not None

        def assert_released() -> None:
            assert consumer.commits == []
            assert consumer.seeks == [(tp, 10)]

        await run_claimed_source_stop_contract(
            ClaimedSourceHarness(
                source=topic,
                wait_for_fetch_started=consumer.getmany_started.wait,
                release_fetch=consumer.release_getmany.set,
                assert_released=assert_released,
            ),
            control,
        )

    asyncio.run(scenario())


def test_runtime_ack_follows_kafka_producer_acknowledgement() -> None:
    async def scenario() -> None:
        producer = _BlockingProducer()
        driver = _BlockingProducerDriver(producer)
        topic = KafkaConnector("localhost:9092", driver=driver).topic("orders.out")

        await run_acknowledged_sink_contract(
            AcknowledgedSinkHarness(
                sink=topic,
                wait_for_send_started=producer.send_started.wait,
                release_send=producer.release_send.set,
            ),
            body={"id": 1},
        )

        assert len(producer.sent) == 1
        assert producer.sent[0]["topic"] == "orders.out"

    asyncio.run(scenario())


class _BlockingProducer(FakeProducer):
    def __init__(self) -> None:
        super().__init__()
        self.send_started = asyncio.Event()
        self.release_send = asyncio.Event()

    async def send_and_wait(self, topic: str, **kwargs: Any) -> None:
        self.send_started.set()
        await self.release_send.wait()
        await super().send_and_wait(topic, **kwargs)


class _BlockingProducerDriver(FakeDriver):
    def __init__(self, producer: _BlockingProducer) -> None:
        super().__init__()
        self.producer = producer

    def AIOKafkaProducer(self, **kwargs: Any) -> FakeProducer:
        self.producer.kwargs = kwargs
        self.producers.append(self.producer)
        return self.producer
