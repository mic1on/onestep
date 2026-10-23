from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from onestep.envelope import Envelope
from onestep_kafka import KafkaConnector

pytestmark = pytest.mark.integration

async def _fetch_count(source, count):
    deliveries = []
    while len(deliveries) < count:
        deliveries.extend(await source.fetch(count - len(deliveries)))
    return deliveries



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


def test_kafka_transaction_gaps_commit_and_restart_without_replay() -> None:
    """Aborted records/control markers create real gaps, just like compaction."""
    async def scenario() -> None:
        from aiokafka import AIOKafkaProducer
        from aiokafka.structs import TopicPartition

        servers = os.environ['ONESTEP_KAFKA_BOOTSTRAP_SERVERS']
        name = 'onestep.gaps.' + uuid.uuid4().hex
        group = 'onestep-gaps-' + uuid.uuid4().hex
        producer = AIOKafkaProducer(bootstrap_servers=servers, transactional_id=uuid.uuid4().hex)
        await producer.start()
        source = KafkaConnector(servers).topic(
            name, group_id=group, poll_timeout_ms=100,
            consumer_options={'auto_offset_reset': 'earliest', 'isolation_level': 'read_committed'},
        )
        try:
            async with producer.transaction():
                await producer.send_and_wait(name, b'{"id": 1}', partition=0)
            await producer.begin_transaction()
            await producer.send_and_wait(name, b'{"id": "aborted"}', partition=0)
            await producer.abort_transaction()
            async with producer.transaction():
                await producer.send_and_wait(name, b'{"id": 2}', partition=0)
                await producer.send_and_wait(name, b'{"id": 3}', partition=0)
            deliveries = await asyncio.wait_for(_fetch_count(source, 3), 20)
            offsets = [d._record.offset for d in deliveries]
            assert offsets[1] > offsets[0] + 1
            assert [d.payload['id'] for d in deliveries] == [1, 2, 3]
            # Later completions must not jump the first visible unfinished record.
            await deliveries[2].ack()
            await deliveries[1].ack()
            tp = TopicPartition(name, 0)
            assert await source._consumer.committed(tp) is None
            await deliveries[0].retry()
            retry = (await source.fetch(1))[0]
            assert retry.envelope.attempts == 1
            await retry.ack()
            assert await source._consumer.committed(tp) == offsets[-1] + 1
            await source.close()
            for _ in range(5):
                assert await source.fetch(3) == []
        finally:
            await source.close()
            await producer.stop()
    asyncio.run(scenario())


def test_kafka_real_rebalance_fences_revoked_handler() -> None:
    async def scenario() -> None:
        from aiokafka import AIOKafkaProducer
        from aiokafka.admin import AIOKafkaAdminClient, NewTopic
        from aiokafka.structs import TopicPartition

        servers = os.environ['ONESTEP_KAFKA_BOOTSTRAP_SERVERS']
        name = 'onestep.rebalance.' + uuid.uuid4().hex
        group = 'onestep-rebalance-' + uuid.uuid4().hex
        admin = AIOKafkaAdminClient(bootstrap_servers=servers)
        await admin.start()
        await admin.create_topics([NewTopic(name, num_partitions=2, replication_factor=1)])
        await admin.close()
        producer = AIOKafkaProducer(bootstrap_servers=servers)
        await producer.start()
        for partition in (0, 1):
            await producer.send_and_wait(name, b'{"id": 1}', partition=partition)
        await producer.stop()
        connector = KafkaConnector(servers)
        options = {'auto_offset_reset': 'earliest', 'session_timeout_ms': 6000, 'heartbeat_interval_ms': 1000}
        a = connector.topic(name, group_id=group, client_id='first', consumer_options=options, poll_timeout_ms=100)
        b = connector.topic(name, group_id=group, client_id='second', consumer_options=options, poll_timeout_ms=100)
        try:
            original = await asyncio.wait_for(_fetch_count(a, 2), 20)
            for d in original:
                await d.start_processing()
            transferred = await asyncio.wait_for(_fetch_count(b, 1), 30)
            replacement = transferred[0]
            stale = next(d for d in original if d._record.partition == replacement._record.partition)
            assert not a.owns(stale)
            await stale.ack()
            await stale.retry()
            tp = TopicPartition(name, replacement._record.partition)
            assert await b._consumer.committed(tp) is None
            assert not a._retries
            await replacement.ack()
            assert await b._consumer.committed(tp) == replacement._record.offset + 1
        finally:
            await a.close()
            await b.close()
    asyncio.run(scenario())
