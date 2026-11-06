from __future__ import annotations

import asyncio
import threading
from collections import deque
from types import SimpleNamespace

import pytest
from onestep_sql.mysql import MySQLConnector, connector

from onestep import MaxAttempts, MemoryQueue, OneStepApp
from onestep.runtime.executor import DeliveryAction, DeliveryExecutor
from onestep.state import InMemoryCursorStore

FILE = "mysql-bin.000001"
KEY = "binlog-recovery"


class Stream:
    def __init__(self, events):
        self.events = deque(events)
        self.log_file = FILE
        self.log_pos = 100
        self.read_count = 0

    def fetchone(self):
        self.read_count += 1
        if not self.events:
            return None
        pos, ids = self.events.popleft()
        self.log_pos = pos
        return SimpleNamespace(
            rows=[{"values": {"id": value}} for value in ids],
            schema="app",
            table="items",
        )

    def close(self):
        pass


@pytest.fixture(autouse=True)
def recognize_fake_event(monkeypatch):
    monkeypatch.setattr(connector, "_event_name", lambda event: "insert")


async def make_source(events, state=None, **options):
    db = MySQLConnector("sqlite:///:memory:")
    state = state or InMemoryCursorStore()
    if await state.load(KEY) is None:
        await state.save(KEY, {"file": FILE, "pos": 100})
    source = db.binlog(server_id=18491, state=state, state_key=KEY, **options)
    source._stream = Stream(events)
    return db, source, state


@pytest.mark.parametrize("action,attempts", [("retry", 1), ("release_unstarted", 0)])
@pytest.mark.parametrize("ids,target", [([1], 0), ([1, 2, 3], 0), ([1, 2, 3], 2)])
def test_requeue_preserves_barrier_and_old_delivery_cannot_settle(
    action, attempts, ids, target
):
    async def scenario():
        db, source, state = await make_source(
            [(100 + value * 100, [value]) for value in ids]
        )
        batch = await source.fetch(len(ids))
        failed = batch[target]
        await getattr(failed, action)()
        # Stale calls (including concurrent retry) cannot enqueue twice or ACK
        # the replacement delivery before its handler has run.
        await asyncio.gather(
            failed.retry(), failed.ack(), failed.fail(), failed.release_unstarted()
        )
        for index, delivery in enumerate(batch):
            if index != target:
                await delivery.ack()
        assert (await state.load(KEY))["pos"] == (
            100 if target == 0 else 100 + target * 100
        )
        (replay,) = await source.fetch(10)
        assert replay.payload["values"]["id"] == ids[target]
        assert replay.envelope.attempts == attempts
        assert replay.envelope.meta == failed.envelope.meta
        await replay.ack()
        assert (await state.load(KEY))["pos"] == 100 + ids[-1] * 100
        assert await source.fetch(10) == []
        assert not source._pending
        await db.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("target", [0, 1, 2])
def test_terminal_fail_settles_contiguous_prefix(target):
    async def scenario():
        db, source, state = await make_source([(200, [1]), (300, [2]), (400, [3])])
        batch = await source.fetch(3)
        for index in reversed(range(3)):
            if index != target:
                await batch[index].ack()
        await batch[target].fail(RuntimeError("poison row"))
        assert await state.load(KEY) == {"file": FILE, "pos": 400}
        assert not source._pending
        await db.close()

    asyncio.run(scenario())


def test_multirow_events_across_fetches_only_commit_complete_prefix():
    async def scenario():
        db, source, state = await make_source(
            [(200, [1]), (300, [2, 3, 4]), (400, [5])]
        )
        first, second = await source.fetch(2)
        await second.ack()
        await first.ack()
        # The completed event at 200 must be saved even though the following
        # acknowledged row is only a partial event.
        assert (await state.load(KEY))["pos"] == 200
        (third,) = await source.fetch(1)
        await third.retry()
        (replay,) = await source.fetch(1)
        fourth, fifth = await source.fetch(2)
        await fifth.ack()
        await fourth.ack()
        assert (await state.load(KEY))["pos"] == 200
        await replay.ack()
        assert (await state.load(KEY))["pos"] == 400
        assert not source._pending
        await db.close()

    asyncio.run(scenario())


class FailingState(InMemoryCursorStore):
    failure = None

    async def save(self, key, value):
        if self.failure is not None:
            raise self.failure
        await super().save(key, value)


@pytest.mark.parametrize(
    "failure", [OSError("disk unavailable"), asyncio.CancelledError()]
)
@pytest.mark.parametrize("action", ["ack", "fail"])
def test_checkpoint_failure_preserves_prefix_and_delivery_can_retry(failure, action):
    async def scenario():
        state = FailingState()
        db, source, state = await make_source([(200, [1]), (300, [2])], state)
        first, second = await source.fetch(2)
        await second.ack()
        state.failure = failure
        with pytest.raises(type(failure)):
            await getattr(first, action)()
        assert (await state.load(KEY))["pos"] == 100
        assert source._committed.pos == 100
        assert len(source._pending) == 2
        # A failed ACK must not leave its own token acknowledged: retrying it
        # cannot let another ACK advance over the retry barrier.
        state.failure = None
        await first.retry()
        await source.ack_token(second._token)
        assert (await state.load(KEY))["pos"] == 100
        (replacement,) = await source.fetch(1)
        await replacement.ack()
        assert (await state.load(KEY))["pos"] == 300
        await db.close()

    asyncio.run(scenario())


def test_cancelled_save_with_ambiguous_success_is_safe_to_repeat():
    class AmbiguousState(InMemoryCursorStore):
        entered = None
        release = None

        async def save(self, key, value):
            await super().save(key, value)
            if self.entered is not None:
                self.entered.set()
                await self.release.wait()

    async def scenario():
        state = AmbiguousState()
        db, source, state = await make_source([(200, [1])], state)
        (delivery,) = await source.fetch(1)
        state.entered, state.release = asyncio.Event(), asyncio.Event()
        pending = asyncio.create_task(delivery.ack())
        await state.entered.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert source._committed.pos == 100
        assert len(source._pending) == 1
        state.release.set()
        await delivery.ack()
        assert source._committed.pos == 200
        assert not source._pending
        await db.close()

    asyncio.run(scenario())


def test_restart_replays_entire_uncommitted_event():
    async def scenario():
        events = [(200, [1]), (300, [2, 3]), (400, [4])]
        db, source, state = await make_source(events)
        first, second, third, fourth = await source.fetch(4)
        await first.ack()
        await second.ack()
        await third.retry()
        await fourth.ack()
        await source.close()
        await db.close()
        # A fresh process has no local retries; the durable position must replay
        # the incomplete event, including its already successful first row.
        saved = await state.load(KEY)
        assert saved["pos"] == 200
        db, restarted, _ = await make_source(
            [event for event in events if event[0] > saved["pos"]], state
        )
        batch = await restarted.fetch(10)
        assert [item.payload["values"]["id"] for item in batch] == [2, 3, 4]
        assert all(item.envelope.attempts == 0 for item in batch)
        for item in batch:
            await item.ack()
        assert (await state.load(KEY))["pos"] == 400
        await db.close()

    asyncio.run(scenario())


def test_max_attempts_reaches_dlq_and_commits_without_stalling():
    async def scenario():
        db, source, state = await make_source([(200, [1])])
        app = OneStepApp("binlog-max-attempts")
        dlq = MemoryQueue("dlq")
        attempts = []

        @app.task(source=source, retry=MaxAttempts(3), dead_letter=dlq)
        async def poison(ctx, payload):
            attempts.append(ctx.delivery.envelope.attempts)
            raise ValueError("poison")

        executor = DeliveryExecutor(app, app.tasks[0])
        for _ in range(3):
            (delivery,) = await source.fetch(1)
            outcome = await executor.execute(delivery)
        assert attempts == [0, 1, 2]
        assert outcome.terminal
        assert outcome.delivery_action == DeliveryAction.DEAD_LETTER
        assert len(await dlq.fetch(10)) == 1
        assert (await state.load(KEY))["pos"] == 200
        assert await source.fetch(1) == []
        await db.close()

    asyncio.run(scenario())


def test_delayed_retry_preserves_delivery_until_deadline(monkeypatch):
    async def scenario():
        db, source, _state = await make_source([(200, [1])])
        (delivery,) = await source.fetch(1)
        now = 1000.0
        monkeypatch.setattr(connector.time, "monotonic", lambda: now)
        await delivery.retry(delay_s=10)
        assert await source.fetch(1) == []
        now += 10
        (replacement,) = await source.fetch(1)
        assert replacement.envelope.attempts == 1
        await replacement.ack()
        await db.close()

    asyncio.run(scenario())


def test_cancelled_read_is_retained_and_can_resume_with_smaller_limit():
    async def scenario():
        db, source, state = await make_source([(200, [1, 2])], blocking=True)
        entered, release = threading.Event(), threading.Event()
        fetch_sync = source._fetch_sync
        calls = 0

        def blocked(limit):
            nonlocal calls
            calls += 1
            rows = fetch_sync(limit)
            entered.set()
            assert release.wait(5)
            return rows

        source._fetch_sync = blocked
        task = asyncio.create_task(source.fetch(2))
        await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        (first,) = await asyncio.wait_for(source.fetch(1), 2)
        (second,) = await source.fetch(1)
        assert calls == 1
        assert [first.payload["values"]["id"], second.payload["values"]["id"]] == [1, 2]
        await first.ack()
        assert (await state.load(KEY))["pos"] == 100
        await second.ack()
        assert (await state.load(KEY))["pos"] == 200
        await db.close()

    asyncio.run(scenario())


def test_local_retry_wakes_a_blocking_read():
    async def scenario():
        db, source, state = await make_source([(200, [1])], blocking=True)
        (first,) = await source.fetch(1)
        entered, release = threading.Event(), threading.Event()

        def blocked(limit):
            entered.set()
            assert release.wait(5)
            return []

        source._fetch_sync = blocked
        pending = asyncio.create_task(source.fetch(1))
        await asyncio.to_thread(entered.wait, 5)
        try:
            await first.retry()
            (replacement,) = await asyncio.wait_for(pending, 1)
            assert replacement.payload == first.payload
            await replacement.ack()
        finally:
            release.set()
            await source._read_task
        assert (await state.load(KEY))["pos"] == 200
        await db.close()

    asyncio.run(scenario())


def test_later_stream_error_does_not_drop_rows_already_consumed():
    async def scenario():
        db, source, state = await make_source([(200, [1])])
        stream = source._stream
        fetchone = stream.fetchone

        def failing():
            if stream.read_count:
                raise OSError("connection lost")
            return fetchone()

        stream.fetchone = failing
        (delivery,) = await source.fetch(10)
        await delivery.ack()
        assert (await state.load(KEY))["pos"] == 200
        with pytest.raises(OSError, match="connection lost"):
            await source.fetch(10)
        await db.close()

    asyncio.run(scenario())


def test_close_waits_for_reader_before_closing_stream():
    async def scenario():
        db, source, state = await make_source([(200, [1])], blocking=True)
        stream = source._stream
        entered, release = threading.Event(), threading.Event()
        fetched = source._fetch_sync
        closed = []
        stream.close = lambda: closed.append(True)

        def late_read(limit):
            entered.set()
            assert release.wait(5)
            assert not closed
            return fetched(limit)

        source._fetch_sync = late_read
        reading = asyncio.create_task(source.fetch(1))
        await asyncio.to_thread(entered.wait, 5)
        closing = asyncio.create_task(source.close())
        try:
            # close wakes the fetch waiter without cancelling its owned thread.
            assert await asyncio.wait_for(reading, 1) == []
            assert not closing.done()
            assert not closed
        finally:
            release.set()
            await asyncio.wait_for(closing, 2)
        assert closed == [True]
        assert source._read_task is None
        assert source._stream is None
        assert not source._ready and not source._buffered
        assert (await state.load(KEY))["pos"] == 100
        await db.close()

    asyncio.run(scenario())


def test_close_reopen_replays_from_durable_position_and_fences_old_delivery(
    monkeypatch,
):
    async def scenario():
        events = [(200, [1]), (300, [2])]
        db, source, state = await make_source(events)
        first, old = await source.fetch(2)
        await first.ack()
        await source.close()

        def reopen():
            if source._stream is None:
                source._stream = Stream(
                    [event for event in events if event[0] > source._committed.pos]
                )
            return source._stream

        monkeypatch.setattr(source, "_ensure_stream", reopen)
        (current,) = await source.fetch(1)
        await old.ack()
        await old.retry()
        await old.release_unstarted()
        await old.fail()
        assert not source._retries
        assert (await state.load(KEY))["pos"] == 200
        assert current.payload["values"]["id"] == 2
        await current.ack()
        assert (await state.load(KEY))["pos"] == 300
        await source.close()
        await db.close()

    asyncio.run(scenario())


def test_blocking_fetch_can_be_cancelled_and_closed_without_new_events():
    async def scenario():
        db, source, _state = await make_source([], blocking=True, poll_interval_s=30)
        waiting = asyncio.create_task(source.fetch(1))
        await asyncio.sleep(0.02)
        assert not waiting.done()
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        # Wake the long async poll directly through close, too.
        waiting = asyncio.create_task(source.fetch(1))
        await asyncio.sleep(0.02)
        await asyncio.wait_for(source.close(), 1)
        assert await waiting == []
        assert source._read_task is None
        await db.close()

    asyncio.run(scenario())


def test_source_and_delivery_can_be_constructed_without_current_event_loop():
    asyncio.run(asyncio.sleep(0))  # Deliberately clears the policy's current loop.
    db = MySQLConnector("sqlite:///:memory:")
    source = db.binlog(server_id=18491)
    delivery = connector.BinlogDelivery(
        source, connector.Envelope(body={}), connector._BinlogToken(FILE, 200)
    )
    assert source._fetch_lock is None
    assert source._wake_fetch is None
    assert delivery._settle_lock is None
    asyncio.run(db.close())
