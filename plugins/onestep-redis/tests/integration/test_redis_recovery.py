"""Recovery and lease tests against an isolated real Redis >= 6.2."""
import asyncio
import os
import uuid

import pytest
import pytest_asyncio

from onestep import MaxAttempts, OneStepApp
from onestep.runtime.executor import DeliveryExecutor
from onestep_redis import RedisConnector
from onestep_redis.connector import RedisOwnershipLostError

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
if not os.environ.get('REDIS_URL'):
    pytest.skip('set REDIS_URL for live Redis tests', allow_module_level=True)


@pytest_asyncio.fixture
async def queues():
    connector = RedisConnector(os.environ['REDIS_URL'])
    name = 'onestep:recovery:' + uuid.uuid4().hex
    a = connector.stream(name, group='workers', block_ms=1, poll_interval_s=.01, reclaim_idle_ms=150)
    b = connector.stream(name, group='workers', block_ms=1, poll_interval_s=.01, reclaim_idle_ms=150)
    await a.open()
    yield a, b
    await a.close()
    await b.close()
    client = await connector.acquire()
    await client.delete(name)
    await connector.release()


async def test_healthy_inflight_and_retry_delay_are_not_reclaimed(queues):
    a, b = queues
    await a.publish({'id': 1})
    first = (await a.fetch(1))[0]
    await first.start_processing()
    retrying = asyncio.create_task(first.retry(delay_s=.45))
    for _ in range(4):
        assert await a.fetch(1) == []
        assert await b.fetch(1) == []
        await asyncio.sleep(.04)
    await retrying
    retry = (await a.fetch(1))[0]
    assert retry._message_id == first._message_id
    assert retry.envelope.attempts == 1
    assert (await a._redis.xpending_range(a.name, a.group, '-', '+', 1))[0]['times_delivered'] == 2
    await retry.ack()
    assert (await a._redis.xpending(a.name, a.group))['pending'] == 0


async def test_new_consumer_recovers_crashed_owner_and_fences_old_ack(queues):
    a, b = queues
    await a.publish({'id': 1})
    first = (await a.fetch(1))[0]
    await first.retry()
    second = (await a.fetch(1))[0]
    assert second.envelope.attempts == 1
    # Abruptly stop only renewal, retaining the stale delivery to test its ACK.
    a._heartbeat.cancel()
    with pytest.raises(asyncio.CancelledError):
        await a._heartbeat
    a._heartbeat = None
    await asyncio.sleep(.18)
    recovered = (await b.fetch(1))[0]
    assert recovered._message_id == first._message_id
    assert recovered.envelope.attempts == 2
    with pytest.raises(RedisOwnershipLostError):
        await second.ack()
    assert (await b._redis.xpending(b.name, b.group))['pending'] == 1
    await recovered.ack()
    assert (await b._redis.xpending(b.name, b.group))['pending'] == 0


async def test_reclaim_scan_progresses_past_healthy_pel_head(queues):
    a, b = queues
    for i in range(21):
        await a.publish({'id': i})
    active = await a.fetch(20)
    client = a._redis
    # Another owner dies behind twenty healthy entries. COUNT 1 examines only 10.
    claimed = await client.xreadgroup(a.group, 'dead-owner', {a.name: '>'}, count=1)
    dead_id = claimed[0][1][0][0]
    await asyncio.sleep(.18)
    deliveries = []
    for _ in range(6):
        deliveries = await b.fetch(1)
        if deliveries:
            break
    assert len(deliveries) == 1 and deliveries[0]._message_id == dead_id
    await deliveries[0].ack()
    for delivery in active:
        await delivery.ack()


async def test_concurrent_reclaim_has_single_owner(queues):
    a, b = queues
    await a.publish({'id': 1})
    old = (await a.fetch(1))[0]
    await a.close()
    await asyncio.sleep(.18)
    c = b.connector.stream(b.name, group=b.group, block_ms=1, poll_interval_s=.01, reclaim_idle_ms=150)
    try:
        results = await asyncio.gather(b.fetch(1), c.fetch(1))
        found = [d for batch in results for d in batch]
        assert len(found) == 1 and found[0]._message_id == old._message_id
        await found[0].ack()
    finally:
        await c.close()


async def test_runner_concurrency_does_not_start_same_message_twice(queues):
    a, _ = queues
    await a.publish({'id': 1})
    app = OneStepApp('redis-concurrency')
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    @app.task(source=a, concurrency=2)
    async def handler(ctx, payload):
        calls.append(payload)
        started.set()
        await release.wait()

    serving = asyncio.create_task(app.serve())
    try:
        await asyncio.wait_for(started.wait(), 3)
        await asyncio.sleep(.35)
        assert len(calls) == 1
        # New traffic must still make progress behind the active PEL head.
        await a.publish({'id': 2})
        for _ in range(50):
            if len(calls) == 2:
                break
            await asyncio.sleep(.01)
        assert calls == [{'id': 1}, {'id': 2}]
    finally:
        release.set()
        app.request_shutdown()
        await asyncio.wait_for(serving, 3)


async def test_max_attempts_terminates_across_deliveries(queues):
    a, _ = queues
    await a.publish({'id': 1})
    app = OneStepApp('redis-retry')
    seen = []

    @app.task(source=a, retry=MaxAttempts(3))
    async def broken(ctx, payload):
        seen.append(ctx.delivery.envelope.attempts)
        raise ValueError('poison')

    executor = DeliveryExecutor(app, app.tasks[0])
    for _ in range(3):
        await executor.execute((await a.fetch(1))[0])
    assert seen == [0, 1, 2]
    assert (await a._redis.xpending(a.name, a.group))['pending'] == 0
    assert await a.fetch(1) == []


async def test_release_unstarted_preserves_attempt_and_commit_obligation(queues):
    a, _ = queues
    await a.publish({'id': 1})
    first = (await a.fetch(1))[0]
    await first.release_unstarted()
    again = (await a.fetch(1))[0]
    assert again._message_id == first._message_id
    assert again.envelope.attempts == 0
    assert (await a._redis.xpending(a.name, a.group))['pending'] == 1
    await again.ack()


async def test_retry_waits_for_in_progress_renewal_before_swapping_generation(queues, monkeypatch):
    a, _ = queues
    await a.publish({'id': 1})
    first = (await a.fetch(1))[0]
    entered = asyncio.Event()
    release = asyncio.Event()
    retried = asyncio.Event()
    original = a._redis.eval
    blocked = False

    async def eval_with_block(*args, **kwargs):
        nonlocal blocked
        action = args[-2]
        if action == 'renew' and not blocked:
            blocked = True
            entered.set()
            await release.wait()
        if action == 'retry':
            retried.set()
        return await original(*args, **kwargs)

    monkeypatch.setattr(a._redis, 'eval', eval_with_block)
    await asyncio.wait_for(entered.wait(), 1)
    retrying = asyncio.create_task(first.retry())
    try:
        await asyncio.sleep(.03)
        assert not retried.is_set()
    finally:
        release.set()
        await retrying
    again = (await a.fetch(1))[0]
    await asyncio.sleep(.06)
    assert a._active[again._message_id] is again
    assert again.envelope.attempts == 1
    await again.ack()


async def test_own_expired_claim_fences_previous_generation(queues):
    a, _ = queues
    await a.publish({'id': 1})
    old = (await a.fetch(1))[0]
    a._heartbeat.cancel()
    with pytest.raises(asyncio.CancelledError):
        await a._heartbeat
    # Keep the cancelled handle so fetch does not restart renewal for this test.
    await asyncio.sleep(.18)
    replacement = (await a.fetch(1))[0]
    assert replacement._message_id == old._message_id
    assert replacement.envelope.attempts == 1
    with pytest.raises(RedisOwnershipLostError):
        await old.ack()
    await replacement.ack()
