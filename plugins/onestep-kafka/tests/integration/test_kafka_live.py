from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from onestep.envelope import Envelope
from onestep_kafka import KafkaConnector

pytestmark = pytest.mark.integration


def test_kafka_live_send_fetch_ack_and_no_redelivery() -> None:
    async def scenario() -> None:
        bootstrap_servers = os.environ["ONESTEP_KAFKA_BOOTSTRAP_SERVERS"]
        topic_name = f"{os.environ.get('ONESTEP_KAFKA_TOPIC_PREFIX', 'onestep.integration')}.{uuid.uuid4().hex}"
        group_id = f"onestep-{uuid.uuid4().hex}"

        connector = KafkaConnector(bootstrap_servers)
        sink = connector.topic(topic_name)
        source = connector.topic(
            topic_name,
            group_id=group_id,
            consumer_options={"auto_offset_reset": "earliest"},
            poll_timeout_ms=500,
        )

        try:
            await sink.send(Envelope(body={"id": 1}, meta={"trace": "live"}))
            deliveries = []
            for _ in range(20):
                deliveries = await source.fetch(1)
                if deliveries:
                    break
                await asyncio.sleep(0.1)
            assert len(deliveries) == 1
            assert deliveries[0].payload == {"id": 1}
            assert deliveries[0].envelope.meta["kafka"]["topic"] == topic_name
            await deliveries[0].start_processing()
            await deliveries[0].ack()

            redelivered = await source.fetch(1)
            assert redelivered == []
        finally:
            await source.close()
            await sink.close()

    asyncio.run(scenario())
