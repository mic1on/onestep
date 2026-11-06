from __future__ import annotations

import asyncio

import pytest

from onestep import Delivery, Envelope, MemoryQueue, OneStepApp, Source
from onestep.runtime.runner import TaskRunner


def _stop(app: OneStepApp, control: str) -> None:
    if control == "drain":
        app.request_drain()
    elif control == "pause":
        app.request_task_pause("consume")
    else:
        app.request_shutdown()


class _RecordingDelivery(Delivery):
    def __init__(self, number: int) -> None:
        super().__init__(Envelope(body=number, attempts=4))
        self.releases = 0
        self.started = 0

    async def release_unstarted(self) -> None:
        self.releases += 1

    async def start_processing(self) -> None:
        self.started += 1

    async def ack(self) -> None:
        pytest.fail("unstarted delivery must not be acknowledged")

    async def retry(self, *, delay_s: float | None = None) -> None:
        pytest.fail("unstarted delivery must not be retried")

    async def fail(self, exc: Exception | None = None) -> None:
        pytest.fail("unstarted delivery must not be failed")


@pytest.mark.parametrize("control", ["drain", "pause", "shutdown"])
@pytest.mark.parametrize("mode", ["completed_safe", "completed_unsafe", "return_on_cancel"])
def test_stop_releases_completed_or_cancel_returned_batch(control, mode, monkeypatch) -> None:
    async def scenario() -> None:
        app = OneStepApp("stop-fetch-race")
        fetch_started = asyncio.Event()
        stop_observed = asyncio.Event()
        deliveries = [_RecordingDelivery(number) for number in range(3)]
        cancelled = False
        handled = []
        original_wait = app.wait_for_stop_fetching

        async def wait_for_stop_fetching(task_name=None):
            await original_wait(task_name)
            # Wake fetch before this task's completion wakes asyncio.wait.
            # Both tasks therefore complete in the same wait iteration.
            stop_observed.set()

        monkeypatch.setattr(app, "wait_for_stop_fetching", wait_for_stop_fetching)

        class RacingSource(Source):
            fetch_is_cancel_safe = mode != "completed_unsafe"
            poll_interval_s = 0.001

            async def fetch(self, limit):
                nonlocal cancelled
                fetch_started.set()
                _stop(app, control)
                if mode == "return_on_cancel":
                    try:
                        await asyncio.Future()
                    except asyncio.CancelledError:
                        cancelled = True
                else:
                    await stop_observed.wait()
                return deliveries

        @app.task(source=RacingSource("racing"), concurrency=3)
        async def consume(ctx, item):
            handled.append(item)

        serving = asyncio.create_task(app.serve())
        try:
            # startup replaces lifecycle events. Wait until it has completed
            # before registering drain/pause waiters on those events.
            await asyncio.wait_for(fetch_started.wait(), timeout=1)
            if control == "drain":
                await asyncio.wait_for(app.wait_for_drain(), timeout=1)
            elif control == "pause":
                await asyncio.wait_for(app.wait_for_task_pause("consume"), timeout=1)
            else:
                await asyncio.wait_for(serving, timeout=1)
            assert handled == []
            assert [delivery.releases for delivery in deliveries] == [1, 1, 1]
            assert [delivery.started for delivery in deliveries] == [0, 0, 0]
            assert cancelled is (mode == "return_on_cancel")
        finally:
            app.request_shutdown()
            await asyncio.wait_for(serving, timeout=1)

    asyncio.run(scenario())


@pytest.mark.parametrize("return_on_cancel", [False, True])
def test_runner_cancellation_collects_fetch_result(return_on_cancel) -> None:
    async def scenario() -> None:
        app = OneStepApp("cancel-fetch")
        entered = asyncio.Event()
        delivery = _RecordingDelivery(1)
        cancelled = asyncio.Event()

        class CancellableSource(Source):
            async def fetch(self, limit):
                entered.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    cancelled.set()
                    if return_on_cancel:
                        return [delivery]
                    raise

        @app.task(source=CancellableSource("cancellable"))
        async def consume(ctx, item):
            pytest.fail("cancelled fetch must not start a handler")

        runner = TaskRunner(app, app.tasks[0])
        fetching = asyncio.create_task(runner._fetch_deliveries(1))
        await asyncio.wait_for(entered.wait(), timeout=1)
        fetching.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(fetching, timeout=1)
        assert cancelled.is_set()
        assert delivery.releases == int(return_on_cancel)
        assert not runner.is_fetching

    asyncio.run(scenario())


def test_memory_release_is_idempotent_and_preserves_envelope() -> None:
    async def scenario() -> None:
        source = MemoryQueue("memory", poll_interval_s=0.001)
        original = Envelope(body={"nested": []}, meta={"trace": []}, attempts=7)
        await source.send(original)
        delivery, = await source.fetch(1)
        await asyncio.gather(*(delivery.release_unstarted() for _ in range(4)))
        assert source.size() == 1
        recovered, = await source.fetch(1)
        assert recovered.envelope is original
        assert recovered.envelope.attempts == 7
        await delivery.release_unstarted()
        assert source.size() == 0
        await recovered.release_unstarted()
        assert source.size() == 1
        again, = await source.fetch(1)
        await again.start_processing()
        await again.release_unstarted()
        assert source.size() == 0
        assert await source.fetch(1) == []

    asyncio.run(scenario())


def test_memory_release_overflows_capacity_without_unblocking_producers() -> None:
    async def scenario() -> None:
        source = MemoryQueue("bounded", maxsize=1, batch_size=1)
        original = Envelope(body="original", attempts=3)
        await source.send(original)
        delivery, = await source.fetch(1)
        await source.publish("second")
        sending = asyncio.create_task(source.publish("third"))
        await asyncio.sleep(0)
        assert not sending.done()
        await asyncio.wait_for(delivery.release_unstarted(), timeout=1)
        await delivery.release_unstarted()
        assert source.size() == 2
        recovered, = await source.fetch(1)
        assert recovered.envelope is original
        await asyncio.sleep(0)
        assert not sending.done()
        assert source.size() == 1
        second, = await source.fetch(1)
        assert second.payload == "second"
        await asyncio.wait_for(sending, timeout=1)
        third, = await source.fetch(1)
        assert third.payload == "third"
        assert source.size() == 0

    asyncio.run(scenario())


def test_memory_return_wakes_waiting_fetch_and_preserves_batch_order(monkeypatch) -> None:
    async def scenario() -> None:
        source = MemoryQueue("waiting", poll_interval_s=60)
        originals = [Envelope(body=number) for number in range(3)]
        for envelope in originals:
            await source.send(envelope)
        deliveries = await source.fetch(3)
        waiting = asyncio.Event()
        buffer = source._ensure_queue()
        original_get = buffer.get

        async def observed_get():
            waiting.set()
            return await original_get()

        monkeypatch.setattr(buffer, "get", observed_get)
        fetching = asyncio.create_task(source.fetch(4))
        await asyncio.wait_for(waiting.wait(), timeout=1)
        for delivery in deliveries:
            await delivery.release_unstarted()
        await source.publish("new")
        recovered = await asyncio.wait_for(fetching, timeout=1)
        assert [item.payload for item in recovered] == [0, 1, 2, "new"]
        assert all(item.envelope is original for item, original in zip(recovered, originals))
        assert source.size() == 0

    asyncio.run(scenario())


def test_memory_cancelled_waiter_does_not_steal_returned_work(monkeypatch) -> None:
    async def scenario() -> None:
        source = MemoryQueue("shared", poll_interval_s=60)
        await source.publish("original")
        delivery, = await source.fetch(1)
        waiting = asyncio.Event()
        buffer = source._ensure_queue()
        original_get = buffer.get
        getters = 0

        async def observed_get():
            nonlocal getters
            getters += 1
            if getters == 2:
                waiting.set()
            return await original_get()

        monkeypatch.setattr(buffer, "get", observed_get)
        first = asyncio.create_task(source.fetch(1))
        second = asyncio.create_task(source.fetch(1))
        await asyncio.wait_for(waiting.wait(), timeout=1)
        first.cancel()
        await delivery.release_unstarted()
        with pytest.raises(asyncio.CancelledError):
            await first
        recovered, = await asyncio.wait_for(second, timeout=1)
        assert recovered.envelope is delivery.envelope
        assert source.size() == 0

    asyncio.run(scenario())


def test_memory_cancellation_after_inner_get_returns_preserves_envelope(monkeypatch) -> None:
    async def scenario() -> None:
        source = MemoryQueue("get-race", poll_interval_s=0.01)
        original = Envelope(body="original", attempts=5)
        await source.send(original)
        buffer = source._ensure_queue()
        original_get = buffer.get

        async def get_then_cancel_fetch():
            envelope = await original_get()
            fetching.cancel()
            return envelope

        monkeypatch.setattr(buffer, "get", get_then_cancel_fetch)
        fetching = asyncio.create_task(source.fetch(1))
        try:
            deliveries = await fetching
        except asyncio.CancelledError:
            pass
        else:
            # Some Python versions let the finished get win cancellation.
            for delivery in deliveries:
                await delivery.release_unstarted()
        monkeypatch.setattr(buffer, "get", original_get)
        assert source.size() == 1
        recovered, = await source.fetch(1)
        assert recovered.envelope is original
        assert source.size() == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("stop", ["cancel", "timeout"])
def test_memory_get_returning_after_cancellation_is_returned_once(stop, monkeypatch) -> None:
    async def scenario() -> None:
        source = MemoryQueue("get-cancel-return", poll_interval_s=0.01)
        original = Envelope(body="original", attempts=5)
        await source.send(original)
        buffer = source._ensure_queue()
        original_get = buffer.get
        entered = asyncio.Event()

        async def get_returning_on_cancel():
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                return await original_get()

        monkeypatch.setattr(buffer, "get", get_returning_on_cancel)
        fetching = asyncio.create_task(source.fetch(1))
        await asyncio.wait_for(entered.wait(), timeout=1)
        if stop == "cancel":
            fetching.cancel()
            with pytest.raises(asyncio.CancelledError):
                await fetching
        else:
            assert await asyncio.wait_for(fetching, timeout=1) == []
        monkeypatch.setattr(buffer, "get", original_get)
        assert source.size() == 1
        recovered, = await source.fetch(1)
        assert recovered.envelope is original
        assert source.size() == 0
        assert await source.fetch(1) == []

    asyncio.run(scenario())


@pytest.mark.parametrize("control", ["drain", "pause", "shutdown"])
def test_memory_stop_then_resume_processes_each_envelope_once(control, monkeypatch) -> None:
    async def scenario() -> None:
        app = OneStepApp("memory-stop-resume")
        fetch_started = asyncio.Event()
        stop_observed = asyncio.Event()
        original_wait = app.wait_for_stop_fetching
        handled = []
        released = []

        async def wait_for_stop_fetching(task_name=None):
            await original_wait(task_name)
            stop_observed.set()

        monkeypatch.setattr(app, "wait_for_stop_fetching", wait_for_stop_fetching)

        class StopOnceMemoryQueue(MemoryQueue):
            stopped_once = False

            async def fetch(self, limit):
                fetch_started.set()
                deliveries = await super().fetch(limit)
                if not self.stopped_once:
                    self.stopped_once = True
                    # Refill all slots before the runner returns this batch.
                    await self.publish("new-0")
                    await self.publish("new-1")
                    released.extend(deliveries)
                    _stop(app, control)
                    await stop_observed.wait()
                return deliveries

        source = StopOnceMemoryQueue("memory", maxsize=2, batch_size=2, poll_interval_s=0.001)
        originals = [Envelope(body=number, attempts=number + 2) for number in range(2)]
        for envelope in originals:
            await source.send(envelope)

        async def consume(ctx, item):
            handled.append((item, ctx.current.attempts))
            if len(handled) == 4:
                ctx.app.request_shutdown()

        app.task(source=source, concurrency=2)(consume)
        serving = asyncio.create_task(app.serve())
        try:
            await asyncio.wait_for(fetch_started.wait(), timeout=1)
            if control == "drain":
                await asyncio.wait_for(app.wait_for_drain(), timeout=1)
            elif control == "pause":
                await asyncio.wait_for(app.wait_for_task_pause("consume"), timeout=1)
            else:
                await asyncio.wait_for(serving, timeout=1)
            assert handled == []
            assert source.size() == 4
            for delivery in released:
                await delivery.release_unstarted()
            assert source.size() == 4
            if control == "pause":
                app.request_task_resume("consume")
                await asyncio.wait_for(serving, timeout=1)
            else:
                app.request_shutdown()
                await asyncio.wait_for(serving, timeout=1)
                # Drain/shutdown is terminal for the app, but the queue remains
                # usable by a new app in the same event loop/process.
                resumed = OneStepApp("resumed")
                resumed.task(source=source, concurrency=2)(consume)
                await asyncio.wait_for(resumed.serve(), timeout=1)
            assert handled == [(0, 2), (1, 3), ("new-0", 0), ("new-1", 0)]
            assert source.size() == 0
            assert await source.fetch(2) == []
        finally:
            app.request_shutdown()
            await asyncio.wait_for(serving, timeout=1)

    asyncio.run(scenario())
