from __future__ import annotations

import asyncio

from onestep import MaxAttempts, OneStepApp
from onestep.runtime.executor import DeliveryExecutor
from onestep_kafka import KafkaConnector, KafkaOffsetTracker
from test_kafka_connector import FakeDriver, FakeRecord, FakeTopicPartition


def test_compacted_offsets_advance_only_completed_prefix():
    tracker = KafkaOffsetTracker()
    for offset in (10, 12, 13):
        tracker.mark_fetched('orders', 0, offset)
    assert tracker.mark_completed('orders', 0, 13) is None
    assert tracker.mark_completed('orders', 0, 12) is None
    assert tracker.mark_completed('orders', 0, 10) == 14
    tracker.mark_committed('orders', 0, 14)
    tracker.mark_fetched('orders', 0, 20)
    assert tracker.mark_completed('orders', 0, 20) == 21
    tracker.mark_committed('orders', 0, 21)
    assert not tracker._states['orders', 0].fetched_offsets


def test_release_remains_barrier_across_offset_gap():
    tracker = KafkaOffsetTracker()
    for offset in (10, 12):
        tracker.mark_fetched('orders', 0, offset)
    tracker.mark_released_unstarted('orders', 0, 10)
    assert tracker.mark_completed('orders', 0, 12) is None
    assert tracker.mark_completed('orders', 0, 10) == 13


def test_local_retries_preserve_attempts_and_commit_barrier():
    async def scenario():
        topic = KafkaConnector('localhost:9092', driver=FakeDriver()).topic('orders', group_id='g')
        consumer = await topic._open_consumer()
        tp = FakeTopicPartition('orders', 0)
        consumer.records[tp] = [FakeRecord('orders', 0, n, b'{}') for n in (10, 12, 13)]
        first, second, third = await topic.fetch(3)
        await second.ack()
        await third.ack()
        for attempt in range(3):
            assert first.envelope.attempts == attempt
            assert not consumer.commits
            if attempt < 2:
                await first.retry()
                first = (await topic.fetch(1))[0]
            else:
                await first.fail()
        assert consumer.commits == [{tp: 14}]
        assert consumer.seeks == []
        await topic.close()
    asyncio.run(scenario())


def test_rebalance_fences_old_ack_and_retry_and_resets_offset_barrier():
    async def scenario():
        topic = KafkaConnector('localhost:9092', driver=FakeDriver()).topic('orders', group_id='g')
        consumer = await topic._open_consumer()
        tp = FakeTopicPartition('orders', 0)
        consumer.records[tp] = [FakeRecord('orders', 0, 10, b'{}')]
        old = (await topic.fetch(1))[0]
        await old.start_processing()
        await consumer.listener.on_partitions_revoked({tp})
        await consumer.listener.on_partitions_assigned({tp})
        consumer.records[tp] = [FakeRecord('orders', 0, 12, b'{}')]
        new = (await topic.fetch(1))[0]
        await old.ack()
        await old.retry()
        assert not consumer.commits and not topic._retries
        await new.ack()
        assert consumer.commits == [{tp: 13}]
        await topic.close()
    asyncio.run(scenario())


def test_rebalance_during_fetch_rewinds_under_new_assignment():
    async def scenario():
        topic = KafkaConnector('localhost:9092', driver=FakeDriver()).topic('orders', group_id='g')
        consumer = await topic._open_consumer()
        tp = FakeTopicPartition('orders', 0)
        consumer.records[tp] = [FakeRecord('orders', 0, 10, b'{}')]
        consumer.block_next_getmany()
        pending = asyncio.create_task(topic.fetch(1))
        await consumer.getmany_started.wait()
        await consumer.listener.on_partitions_revoked({tp})
        await consumer.listener.on_partitions_assigned({tp})
        consumer.release_getmany.set()
        assert await pending == []
        assert consumer.seeks == [(tp, 10)]
        assert not consumer.commits
        await topic.close()
    asyncio.run(scenario())


def test_max_attempts_terminates_across_distinct_deliveries():
    async def scenario():
        topic = KafkaConnector('localhost:9092', driver=FakeDriver()).topic('orders', group_id='g')
        consumer = await topic._open_consumer()
        tp = FakeTopicPartition('orders', 0)
        consumer.records[tp] = [FakeRecord('orders', 0, 10, b'{}')]
        app = OneStepApp('retry-test')
        attempts = []

        @app.task(source=topic, retry=MaxAttempts(3))
        async def broken(ctx, payload):
            attempts.append(ctx.delivery.envelope.attempts)
            raise ValueError('poison')

        executor = DeliveryExecutor(app, app.tasks[0])
        for _ in range(3):
            await executor.execute((await topic.fetch(1))[0])
        assert attempts == [0, 1, 2]
        assert consumer.commits == [{tp: 11}]
        assert await topic.fetch(1) == []
        await topic.close()
    asyncio.run(scenario())
