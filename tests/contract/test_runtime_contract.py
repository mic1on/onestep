from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from onestep import (
    ConnectorErrorKind,
    ConnectorOperation,
    ConnectorOperationError,
    FailureKind,
    InMemoryMetrics,
    InMemoryStateStore,
    IntervalSource,
    MaxAttempts,
    MemoryQueue,
    NoRetry,
    OneStepApp,
    RetryAction,
    RetryDecision,
    StructuredEventLogger,
    TaskEventKind,
)
from onestep.connectors.base import Delivery, Sink, Source
from onestep.envelope import Envelope
from onestep.task import EmitRoute


class _StubDelivery(Delivery):
    def __init__(self, payload):
        super().__init__(Envelope(body=payload))
        self.acked = False

    async def ack(self) -> None:
        self.acked = True

    async def retry(self, *, delay_s: float | None = None) -> None:
        return None

    async def fail(self, exc: Exception | None = None) -> None:
        return None


class _FlakySource(Source):
    def __init__(self, name: str, *, retryable: bool) -> None:
        super().__init__(name)
        self.poll_interval_s = 0.01
        self._retryable = retryable
        self._calls = 0
        self.delivery = _StubDelivery({"value": 1})

    async def fetch(self, limit: int) -> list[Delivery]:
        self._calls += 1
        if self._calls == 1:
            raise ConnectorOperationError(
                backend="fake",
                operation=ConnectorOperation.FETCH,
                kind=ConnectorErrorKind.DISCONNECTED if self._retryable else ConnectorErrorKind.MISCONFIGURED,
                source_name=self.name,
                retry_delay_s=0,
            )
        if self._calls == 2:
            return [self.delivery]
        return []


class _FlakySink(Sink):
    def __init__(self, name: str, *, failures: int, retryable: bool) -> None:
        super().__init__(name)
        self.failures = failures
        self.retryable = retryable
        self.calls = 0
        self.items = []

    async def send(self, envelope: Envelope) -> None:
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectorOperationError(
                backend="fake",
                operation=ConnectorOperation.SEND,
                kind=ConnectorErrorKind.DISCONNECTED if self.retryable else ConnectorErrorKind.MISCONFIGURED,
                source_name=self.name,
                retry_delay_s=0,
            )
        self.items.append(envelope.body)


class _ManagedSource(Source):
    def __init__(self, name: str, *, fail_open: bool = False) -> None:
        super().__init__(name)
        self.fail_open = fail_open
        self.opened = False
        self.closed = False
        self.fetch_calls = 0

    async def open(self) -> None:
        self.opened = True
        if self.fail_open:
            raise RuntimeError(f"{self.name} failed to open")

    async def fetch(self, limit: int) -> list[Delivery]:
        self.fetch_calls += 1
        return []

    async def close(self) -> None:
        self.closed = True


class _AckFailsOnceDelivery(Delivery):
    def __init__(self, source: "_AckFailsOnceSource", envelope: Envelope) -> None:
        super().__init__(envelope)
        self._source = source

    async def ack(self) -> None:
        self._source.ack_calls += 1
        if self.envelope.attempts == 0:
            raise RuntimeError("ack failed after sink send")
        self._source.acked_attempts.append(self.envelope.attempts)

    async def retry(self, *, delay_s: float | None = None) -> None:
        self._source.retry_calls += 1
        if delay_s:
            await asyncio.sleep(delay_s)
        await self._source.put(
            Envelope(
                body=self.envelope.body,
                meta=dict(self.envelope.meta),
                attempts=self.envelope.attempts + 1,
            )
        )

    async def fail(self, exc: Exception | None = None) -> None:
        self._source.fail_calls += 1


class _AckFailsOnceSource(Source):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.poll_interval_s = 0.01
        self._queue: asyncio.Queue[Envelope] = asyncio.Queue()
        self.ack_calls = 0
        self.retry_calls = 0
        self.fail_calls = 0
        self.acked_attempts: list[int] = []

    async def put(self, envelope: Envelope) -> None:
        await self._queue.put(envelope)

    async def fetch(self, limit: int) -> list[Delivery]:
        try:
            envelope = await asyncio.wait_for(self._queue.get(), timeout=self.poll_interval_s)
        except asyncio.TimeoutError:
            return []
        return [_AckFailsOnceDelivery(self, envelope)]


class _AlwaysFailSink(Sink):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.calls = 0

    async def send(self, envelope: Envelope) -> None:
        self.calls += 1
        raise RuntimeError("sink failed after previous sink succeeded")


class _ReleaseTrackingDelivery(Delivery):
    def __init__(self, envelope: Envelope) -> None:
        super().__init__(envelope)
        self.released = False
        self.acked = False
        self.retried = False
        self.failed = False

    async def release_unstarted(self) -> None:
        self.released = True

    async def ack(self) -> None:
        self.acked = True

    async def retry(self, *, delay_s: float | None = None) -> None:
        self.retried = True

    async def fail(self, exc: Exception | None = None) -> None:
        self.failed = True


class _NonCancelSafeSource(Source):
    fetch_is_cancel_safe = False

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.poll_interval_s = 0.01
        self.fetch_started = asyncio.Event()
        self.release_fetch = asyncio.Event()
        self.delivery = _ReleaseTrackingDelivery(Envelope(body={"value": 1}))

    async def fetch(self, limit: int) -> list[Delivery]:
        self.fetch_started.set()
        await self.release_fetch.wait()
        return [self.delivery]


class _FakeClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs) -> None:
        self.current += timedelta(**kwargs)


def test_return_value_publishes_to_default_sink_contract() -> None:
    async def scenario() -> None:
        source = MemoryQueue("incoming")
        sink = MemoryQueue("processed")
        app = OneStepApp("return-contract")

        @app.task(source=source, emit=sink)
        async def transform(ctx, item):
            ctx.app.request_shutdown()
            return {"value": item["value"] * 2}

        await source.publish({"value": 21})
        await app.serve()

        deliveries = await sink.fetch(1)
        assert len(deliveries) == 1
        assert deliveries[0].payload == {"value": 42}

    asyncio.run(scenario())


def test_retryable_source_fetch_error_does_not_exit_worker_contract() -> None:
    async def scenario() -> None:
        source = _FlakySource("flaky-source", retryable=True)
        app = OneStepApp("retryable-source")
        seen: list[dict[str, int]] = []

        @app.task(source=source)
        async def consume(ctx, item):
            seen.append(item)
            ctx.app.request_shutdown()

        await app.serve()

        assert seen == [{"value": 1}]
        assert source.delivery.acked is True

    asyncio.run(scenario())


def test_non_retryable_source_fetch_error_still_fails_fast_contract() -> None:
    async def scenario() -> None:
        source = _FlakySource("broken-source", retryable=False)
        app = OneStepApp("broken-source")

        @app.task(source=source)
        async def consume(ctx, item):
            raise AssertionError("should not run")

        try:
            await app.serve()
        except ConnectorOperationError as exc:
            assert exc.kind is ConnectorErrorKind.MISCONFIGURED
        else:
            raise AssertionError("expected ConnectorOperationError")

    asyncio.run(scenario())


def test_retryable_sink_send_error_retries_and_succeeds_contract() -> None:
    async def scenario() -> None:
        source = MemoryQueue("incoming")
        sink = _FlakySink("flaky-sink", failures=1, retryable=True)
        app = OneStepApp("retryable-sink")

        @app.task(source=source, emit=sink)
        async def consume(ctx, item):
            ctx.app.request_shutdown()
            return {"value": item["value"] + 1}

        await source.publish({"value": 1})
        await app.serve()

        assert sink.calls == 2
        assert sink.items == [{"value": 2}]

    asyncio.run(scenario())


def test_ack_failure_after_sink_send_retries_and_may_duplicate_output_contract() -> None:
    async def scenario() -> None:
        source = _AckFailsOnceSource("ack-fails")
        sink = MemoryQueue("processed", poll_interval_s=0.01)
        app = OneStepApp("ack-failure-contract")
        attempts: list[int] = []

        @app.task(source=source, emit=sink, retry=MaxAttempts(2, delay_s=0))
        async def consume(ctx, item):
            attempts.append(ctx.current.attempts)
            if ctx.current.attempts == 1:
                ctx.app.request_shutdown()
            return {"value": item["value"], "attempt": ctx.current.attempts}

        await source.put(Envelope(body={"value": 3}))
        await asyncio.wait_for(app.serve(), timeout=1.0)

        first = await sink.fetch(1)
        second = await sink.fetch(1)

        assert attempts == [0, 1]
        assert source.ack_calls == 2
        assert source.retry_calls == 1
        assert source.fail_calls == 0
        assert source.acked_attempts == [1]
        assert [delivery.payload for delivery in [*first, *second]] == [
            {"value": 3, "attempt": 0},
            {"value": 3, "attempt": 1},
        ]

    asyncio.run(scenario())


def test_startup_failure_closes_resources_opened_before_failing_resource_contract() -> None:
    async def scenario() -> None:
        healthy = _ManagedSource("healthy")
        broken = _ManagedSource("broken", fail_open=True)
        app = OneStepApp("startup-resource-failure")

        @app.task(source=healthy)
        async def consume_healthy(ctx, item):
            return None

        @app.task(source=broken)
        async def consume_broken(ctx, item):
            return None

        try:
            await app.startup()
        except RuntimeError as exc:
            assert "failed to open" in str(exc)
        else:
            raise AssertionError("expected startup failure")

        assert healthy.opened is True
        assert healthy.closed is True
        assert broken.opened is True
        assert app._resources == []

    asyncio.run(scenario())


def test_startup_hook_failure_closes_opened_resources_contract() -> None:
    async def scenario() -> None:
        source = _ManagedSource("source")
        app = OneStepApp("startup-hook-failure")

        @app.task(source=source)
        async def consume(ctx, item):
            return None

        @app.on_startup
        async def broken_startup(app):
            raise RuntimeError("startup hook broke")

        try:
            await app.startup()
        except RuntimeError as exc:
            assert str(exc) == "startup hook broke"
        else:
            raise AssertionError("expected startup hook failure")

        assert source.opened is True
        assert source.closed is True
        assert app._resources == []

    asyncio.run(scenario())


def test_cancelled_stop_fetching_waiter_does_not_leak_child_tasks_contract() -> None:
    async def scenario() -> None:
        app = OneStepApp("stop-fetching-cancellation")

        for _ in range(100):
            wait_task = asyncio.create_task(app.wait_for_stop_fetching("missing-task"))
            await asyncio.sleep(0)
            wait_task.cancel()
            await asyncio.gather(wait_task, return_exceptions=True)

        current_task = asyncio.current_task()
        leaked_tasks = [
            task
            for task in asyncio.all_tasks()
            if task is not current_task and not task.done()
        ]

        try:
            assert leaked_tasks == []
        finally:
            for task in leaked_tasks:
                task.cancel()
            await asyncio.gather(*leaked_tasks, return_exceptions=True)

    asyncio.run(scenario())


def test_app_created_outside_running_loop_still_serves_contract() -> None:
    source = MemoryQueue("outside-loop.incoming")
    sink = MemoryQueue("outside-loop.processed")
    app = OneStepApp("outside-loop-app")

    @app.task(source=source, emit=sink)
    async def transform(ctx, item):
        ctx.app.request_shutdown()
        return {"value": item["value"] + 1}

    async def scenario() -> None:
        await source.publish({"value": 1})
        await app.serve()

        deliveries = await sink.fetch(1)
        assert len(deliveries) == 1
        assert deliveries[0].payload == {"value": 2}

    asyncio.run(scenario())


def test_run_installs_sigterm_handler_contract(monkeypatch) -> None:
    app = OneStepApp("signal-contract")
    handlers = {}

    def fake_getsignal(sig):
        return f"previous-{sig}"

    def fake_signal(sig, handler):
        handlers[sig] = handler

    monkeypatch.setattr(signal, "getsignal", fake_getsignal)
    monkeypatch.setattr(signal, "signal", fake_signal)

    with app._install_signal_handlers():
        assert callable(handlers[signal.SIGTERM])
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        assert app.is_stopping is True

    assert handlers[signal.SIGTERM] == f"previous-{signal.SIGTERM}"


def test_drain_stops_new_fetch_and_waits_for_inflight_contract() -> None:
    async def scenario() -> None:
        source = MemoryQueue("incoming", batch_size=1, poll_interval_s=0.01)
        sink = MemoryQueue("processed", batch_size=1, poll_interval_s=0.01)
        app = OneStepApp("drain-contract")
        started = asyncio.Event()
        release = asyncio.Event()
        seen: list[int] = []

        @app.task(source=source, emit=sink, concurrency=1)
        async def consume(ctx, item):
            seen.append(item["value"])
            started.set()
            await release.wait()
            return {"value": item["value"]}

        await source.publish({"value": 1})
        await source.publish({"value": 2})
        serve_task = asyncio.create_task(app.serve())
        await asyncio.wait_for(started.wait(), timeout=1.0)

        app.request_drain()
        release.set()
        drain_status = await asyncio.wait_for(app.wait_for_drain(), timeout=1.0)

        assert drain_status == {
            "operation": "drain",
            "requested": True,
            "completion": "complete",
            "drained": True,
            "accepting_new_work": False,
            "runner_count": 1,
            "parked_runner_count": 1,
            "fetching_runner_count": 0,
            "inflight_task_count": 0,
        }
        assert seen == [1]
        assert source.size() == 1

        app.request_shutdown()
        await asyncio.wait_for(serve_task, timeout=1.0)

        deliveries = await sink.fetch(1)
        assert len(deliveries) == 1
        assert deliveries[0].payload == {"value": 1}

    asyncio.run(scenario())


def test_non_cancel_safe_fetch_releases_unstarted_deliveries_on_drain_contract() -> None:
    async def scenario() -> None:
        source = _NonCancelSafeSource("non-cancel-safe")
        app = OneStepApp("non-cancel-safe-contract")
        seen: list[dict[str, int]] = []

        @app.task(source=source)
        async def consume(ctx, item):
            seen.append(item)

        serve_task = asyncio.create_task(app.serve())
        await asyncio.wait_for(source.fetch_started.wait(), timeout=1.0)

        app.request_drain()
        source.release_fetch.set()
        drain_status = await asyncio.wait_for(app.wait_for_drain(), timeout=1.0)

        assert drain_status["drained"] is True
        assert seen == []
        assert source.delivery.released is True
        assert source.delivery.acked is False
        assert source.delivery.retried is False
        assert source.delivery.failed is False

        app.request_shutdown()
        await asyncio.wait_for(serve_task, timeout=1.0)

    asyncio.run(scenario())


def test_task_pause_and_resume_control_intake_contract() -> None:
    async def scenario() -> None:
        source = MemoryQueue("task-control.incoming", batch_size=1, poll_interval_s=0.01)
        sink = MemoryQueue("task-control.processed", batch_size=1, poll_interval_s=0.01)
        app = OneStepApp("task-control-contract")
        first_started = asyncio.Event()
        second_started = asyncio.Event()
        release_first = asyncio.Event()
        seen: list[int] = []

        @app.task(source=source, emit=sink, concurrency=1)
        async def consume(ctx, item):
            seen.append(item["value"])
            if item["value"] == 1:
                first_started.set()
                await release_first.wait()
            if item["value"] == 2:
                second_started.set()
                ctx.app.request_shutdown()
            return {"value": item["value"]}

        await source.publish({"value": 1})
        await source.publish({"value": 2})
        serve_task = asyncio.create_task(app.serve())
        await asyncio.wait_for(first_started.wait(), timeout=1.0)

        app.request_task_pause("consume")
        release_first.set()
        pause_status = await asyncio.wait_for(app.wait_for_task_pause("consume"), timeout=1.0)

        assert pause_status == {
            "operation": "pause_task",
            "task_name": "consume",
            "requested": True,
            "completion": "complete",
            "paused": True,
            "accepting_new_work": False,
            "runner_count": 1,
            "parked_runner_count": 1,
            "fetching_runner_count": 0,
            "inflight_task_count": 0,
        }
        assert seen == [1]
        assert source.size() == 1

        app.request_task_resume("consume")
        resume_status = await asyncio.wait_for(app.wait_for_task_resume("consume"), timeout=1.0)
        assert resume_status["operation"] == "resume_task"
        assert resume_status["task_name"] == "consume"
        assert resume_status["requested"] is True
        assert resume_status["completion"] == "complete"
        assert resume_status["paused"] is False
        assert resume_status["accepting_new_work"] is True
        assert resume_status["runner_count"] == 1
        assert resume_status["parked_runner_count"] == 0

        await asyncio.wait_for(second_started.wait(), timeout=1.0)
        await asyncio.wait_for(serve_task, timeout=1.0)

        deliveries = await sink.fetch(1)
        deliveries.extend(await sink.fetch(1))
        assert len(deliveries) == 2
        assert [delivery.payload for delivery in deliveries] == [
            {"value": 1},
            {"value": 2},
        ]

    asyncio.run(scenario())


def test_interval_task_resume_does_not_backfill_paused_ticks_contract() -> None:
    async def scenario() -> None:
        tz = ZoneInfo("Asia/Shanghai")
        clock = _FakeClock(datetime(2026, 3, 7, 10, 0, tzinfo=tz))
        source = IntervalSource.every(
            seconds=5,
            immediate=True,
            timezone=tz,
            clock=clock,
            poll_interval_s=0.01,
        )
        app = OneStepApp("interval-resume-contract")
        first_started = asyncio.Event()
        second_started = asyncio.Event()
        scheduled_runs: list[str] = []

        @app.task(source=source, concurrency=1)
        async def tick(ctx, item):
            scheduled_runs.append(ctx.current.meta["scheduled_at"])
            if len(scheduled_runs) == 1:
                first_started.set()
            if len(scheduled_runs) == 2:
                second_started.set()

        serve_task = asyncio.create_task(app.serve())
        await asyncio.wait_for(first_started.wait(), timeout=1.0)

        app.request_task_pause("tick")
        pause_status = await asyncio.wait_for(app.wait_for_task_pause("tick"), timeout=1.0)
        assert pause_status["paused"] is True

        clock.advance(seconds=13)
        app.request_task_resume("tick")
        resume_status = await asyncio.wait_for(app.wait_for_task_resume("tick"), timeout=1.0)
        assert resume_status["accepting_new_work"] is True

        await asyncio.sleep(0.05)
        assert scheduled_runs == ["2026-03-07T10:00:00+08:00"]

        clock.advance(seconds=4)
        await asyncio.sleep(0.05)
        assert scheduled_runs == ["2026-03-07T10:00:00+08:00"]

        clock.advance(seconds=1)
        await asyncio.wait_for(second_started.wait(), timeout=1.0)
        assert scheduled_runs == [
            "2026-03-07T10:00:00+08:00",
            "2026-03-07T10:00:18+08:00",
        ]

        app.request_shutdown()
        await asyncio.wait_for(serve_task, timeout=1.0)

    asyncio.run(scenario())


def test_task_restart_runner_resets_one_task_without_disturbing_others_contract() -> None:
    """restart_task_runner tears down and respawns a single task's runner
    (cancel coroutine, close+reopen private source, spawn fresh coroutine) while
    another task on the same app keeps running. Pause+resume never actually
    restarts; this is the real primitive."""

    async def scenario() -> None:
        # Private sources per task so close/reopen is observable and safe.
        restart_source = MemoryQueue("restart.incoming", batch_size=1, poll_interval_s=0.01)
        restart_sink = MemoryQueue("restart.processed", batch_size=1, poll_interval_s=0.01)
        other_source = MemoryQueue("other.incoming", batch_size=1, poll_interval_s=0.01)
        other_sink = MemoryQueue("other.processed", batch_size=1, poll_interval_s=0.01)
        app = OneStepApp("task-restart-contract")

        other_seen: list[int] = []
        restart_seen: list[int] = []

        @app.task(name="other", source=other_source, emit=other_sink, concurrency=1)
        async def other(ctx, item):
            other_seen.append(item["value"])
            if item["value"] == 99:
                ctx.app.request_shutdown()
            return {"value": item["value"]}

        @app.task(name="restartable", source=restart_source, emit=restart_sink, concurrency=1)
        async def restartable(ctx, item):
            restart_seen.append(item["value"])
            return {"value": item["value"]}

        await other_source.publish({"value": 1})
        serve_task = asyncio.create_task(app.serve())
        # Let the other task process its first item so we know runners are live.
        for _ in range(100):
            if other_seen:
                break
            await asyncio.sleep(0.005)
        assert other_seen == [1]

        # Sanity: both runners are registered and have task handles.
        assert len(app._runners) == 2
        assert set(app._runner_tasks) == {"other", "restartable"}
        original_handle = app._runner_tasks["restartable"]
        other_handle_before = app._runner_tasks["other"]

        # Restart the "restartable" task. (Closing/reopening the private source
        # discards any in-queue deliveries, so publish AFTER restart to verify
        # the fresh runner consumes from the reopened source.)
        status = await asyncio.wait_for(app.restart_task_runner("restartable"), timeout=2.0)

        # restart_task_runner returns the task_control_snapshot of the fresh runner.
        assert status["task_name"] == "restartable"
        assert status["runner_count"] == 1
        assert status["pause_requested"] is False
        # Fresh runner: a new asyncio.Task handle, the old one cancelled.
        assert original_handle.done()
        assert app._runner_tasks["restartable"] is not original_handle
        assert not app._runner_tasks["restartable"].done()
        # The other task's handle was untouched.
        assert app._runner_tasks["other"] is other_handle_before
        assert not other_handle_before.done()
        # Both runners still present.
        assert len(app._runners) == 2

        # The restarted runner consumes from its reopened private source.
        await restart_source.publish({"value": 7})
        for _ in range(200):
            if restart_seen:
                break
            await asyncio.sleep(0.005)
        assert restart_seen == [7]

        # Shut down cleanly.
        await other_source.publish({"value": 99})
        await asyncio.wait_for(serve_task, timeout=2.0)

    asyncio.run(scenario())


def test_task_restart_runner_does_not_close_shared_source_contract() -> None:
    """When two tasks share one source instance, restarting one must NOT close
    the source out from under the other (shared-resource safety). MemoryQueue is
    a competing-consumer source, so the two tasks race for messages; we assert
    safety by checking the non-restarted runner's handle stays alive and keeps
    consuming after the restart, and that the shared source object is the same
    instance on both tasks before and after."""

    async def scenario() -> None:
        shared_source = MemoryQueue("shared.incoming", batch_size=1, poll_interval_s=0.01)
        sink_a = MemoryQueue("a.processed", batch_size=1, poll_interval_s=0.01)
        sink_b = MemoryQueue("b.processed", batch_size=1, poll_interval_s=0.01)
        app = OneStepApp("shared-restart-contract")

        b_seen: list[int] = []

        @app.task(name="task_a", source=shared_source, emit=sink_a, concurrency=1)
        async def task_a(ctx, item):
            if item["value"] == 99:
                ctx.app.request_shutdown()
            return {"value": item["value"]}

        @app.task(name="task_b", source=shared_source, emit=sink_b, concurrency=1)
        async def task_b(ctx, item):
            b_seen.append(item["value"])
            if item["value"] == 99:
                ctx.app.request_shutdown()
            return {"value": item["value"]}

        serve_task = asyncio.create_task(app.serve())
        # Let runners spawn.
        for _ in range(100):
            if len(app._runners) == 2:
                break
            await asyncio.sleep(0.005)
        assert len(app._runner_tasks) == 2
        task_b_handle_before = app._runner_tasks["task_b"]
        assert not task_b_handle_before.done()

        # Restart task_a. The shared source must NOT be closed (task_b still
        # references it), and task_b's runner must keep running.
        await asyncio.wait_for(app.restart_task_runner("task_a"), timeout=2.0)
        assert len(app._runners) == 2
        # task_b's handle survived the restart of task_a.
        assert app._runner_tasks["task_b"] is task_b_handle_before
        assert not task_b_handle_before.done()
        # The shared source object identity is unchanged on both tasks.
        task_a_spec = next(t for t in app._tasks if t.name == "task_a")
        task_b_spec = next(t for t in app._tasks if t.name == "task_b")
        assert task_a_spec.source is shared_source
        assert task_b_spec.source is shared_source

        # task_b can still consume a freshly published item from the (still-open)
        # shared source. Publish several since task_a also competes for them.
        for value in (1, 2, 3, 4, 5, 6, 7, 8):
            await shared_source.publish({"value": value})
        for _ in range(300):
            if b_seen:
                break
            await asyncio.sleep(0.005)
        assert b_seen, "task_b could not consume after task_a was restarted; shared source likely closed"

        await shared_source.publish({"value": 99})
        await asyncio.wait_for(serve_task, timeout=2.0)

    asyncio.run(scenario())


def test_ctx_emit_and_return_follow_separate_contracts() -> None:
    async def scenario() -> None:
        source = MemoryQueue("incoming")
        default_sink = MemoryQueue("default")
        explicit_sink = MemoryQueue("explicit")
        app = OneStepApp("emit-contract")

        @app.task(source=source, emit=default_sink)
        async def fanout(ctx, item):
            await ctx.emit({"kind": "side", "value": item["value"]}, sink=explicit_sink)
            ctx.app.request_shutdown()
            return {"kind": "main", "value": item["value"] * 2}

        await source.publish({"value": 3})
        await app.serve()

        default_batch = await default_sink.fetch(1)
        explicit_batch = await explicit_sink.fetch(1)
        assert len(default_batch) == 1
        assert len(explicit_batch) == 1
        assert default_batch[0].payload == {"kind": "main", "value": 6}
        assert explicit_batch[0].payload == {"kind": "side", "value": 3}

    asyncio.run(scenario())


def test_multi_sink_send_is_not_transactional_when_later_sink_fails_contract() -> None:
    async def scenario() -> None:
        source = MemoryQueue("incoming", poll_interval_s=0.01)
        first_sink = MemoryQueue("first", poll_interval_s=0.01)
        failing_sink = _AlwaysFailSink("second")
        app = OneStepApp("multi-sink-contract")

        @app.task(source=source, emit=[first_sink, failing_sink], retry=NoRetry())
        async def consume(ctx, item):
            ctx.app.request_shutdown()
            return {"value": item["value"] + 1}

        await source.publish({"value": 1})
        await asyncio.wait_for(app.serve(), timeout=1.0)

        first_batch = await first_sink.fetch(1)
        assert failing_sink.calls == 1
        assert [delivery.payload for delivery in first_batch] == [{"value": 2}]

    asyncio.run(scenario())


def test_task_spec_normalizes_unconditional_sink_to_emit_route() -> None:
    source = MemoryQueue("incoming")
    sink = MemoryQueue("processed")
    app = OneStepApp("emit-route-model")

    @app.task(source=source, emit=sink)
    async def consume(ctx, item):
        return item

    task = app.tasks[0]
    assert task.sinks == (sink,)
    assert task.emit_routes == (EmitRoute(then_sinks=(sink,)),)


def test_task_spec_flattens_conditional_route_sinks_for_compatibility() -> None:
    source = MemoryQueue("incoming")
    active = MemoryQueue("active")
    inactive = MemoryQueue("inactive")
    app = OneStepApp("emit-route-flatten")

    def is_active(ctx, payload, result):
        return True

    route = EmitRoute(predicate=is_active, then_sinks=(active,), otherwise_sinks=(inactive,))

    @app.task(source=source, emit=[route])
    async def consume(ctx, item):
        return item

    task = app.tasks[0]
    assert task.emit_routes == (route,)
    assert task.sinks == (active, inactive)


def test_conditional_emit_routes_select_then_and_otherwise_sinks() -> None:
    async def scenario() -> None:
        source = MemoryQueue("incoming", poll_interval_s=0.01)
        active = MemoryQueue("active", poll_interval_s=0.01)
        inactive = MemoryQueue("inactive", poll_interval_s=0.01)
        app = OneStepApp("conditional-emit")
        seen: list[int] = []

        def is_active(ctx, payload, result):
            return result["active"]

        @app.task(
            source=source,
            emit=[EmitRoute(predicate=is_active, then_sinks=(active,), otherwise_sinks=(inactive,))],
        )
        async def consume(ctx, item):
            seen.append(item["id"])
            if len(seen) == 2:
                ctx.app.request_shutdown()
            return item

        await source.publish({"id": 1, "active": True})
        await source.publish({"id": 2, "active": False})
        await app.serve()

        active_batch = await active.fetch(1)
        inactive_batch = await inactive.fetch(1)
        assert [delivery.payload for delivery in active_batch] == [{"id": 1, "active": True}]
        assert [delivery.payload for delivery in inactive_batch] == [{"id": 2, "active": False}]

    asyncio.run(scenario())


def test_conditional_emit_route_without_otherwise_skips_false_result() -> None:
    async def scenario() -> None:
        source = MemoryQueue("incoming", poll_interval_s=0.01)
        sink = MemoryQueue("processed", poll_interval_s=0.01)
        app = OneStepApp("conditional-emit-skip")

        @app.task(
            source=source,
            emit=[EmitRoute(predicate=lambda ctx, payload, result: False, then_sinks=(sink,))],
        )
        async def consume(ctx, item):
            ctx.app.request_shutdown()
            return item

        await source.publish({"value": 1})
        await app.serve()

        assert await sink.fetch(1) == []

    asyncio.run(scenario())


def test_conditional_emit_route_awaits_async_predicate() -> None:
    async def scenario() -> None:
        source = MemoryQueue("incoming", poll_interval_s=0.01)
        sink = MemoryQueue("processed", poll_interval_s=0.01)
        app = OneStepApp("conditional-emit-async")

        async def should_emit(ctx, payload, result):
            await asyncio.sleep(0)
            return result["value"] == 3

        @app.task(source=source, emit=[EmitRoute(predicate=should_emit, then_sinks=(sink,))])
        async def consume(ctx, item):
            ctx.app.request_shutdown()
            return {"value": item["value"] + 1}

        await source.publish({"value": 2})
        await app.serve()

        batch = await sink.fetch(1)
        assert [delivery.payload for delivery in batch] == [{"value": 3}]

    asyncio.run(scenario())


def test_conditional_emit_predicate_failure_uses_retry_policy() -> None:
    async def scenario() -> None:
        source = MemoryQueue("incoming", poll_interval_s=0.01)
        sink = MemoryQueue("processed", poll_interval_s=0.01)
        app = OneStepApp("conditional-emit-retry")
        attempts: list[int] = []
        predicate_calls = 0

        def flaky_predicate(ctx, payload, result):
            nonlocal predicate_calls
            predicate_calls += 1
            if predicate_calls == 1:
                raise RuntimeError("predicate failed")
            return True

        @app.task(
            source=source,
            emit=[EmitRoute(predicate=flaky_predicate, then_sinks=(sink,))],
            retry=MaxAttempts(2, delay_s=0),
        )
        async def consume(ctx, item):
            attempts.append(ctx.current.attempts)
            if len(attempts) == 2:
                ctx.app.request_shutdown()
            return item

        await source.publish({"value": 1})
        await app.serve()

        batch = await sink.fetch(1)
        assert attempts == [0, 1]
        assert predicate_calls == 2
        assert [delivery.payload for delivery in batch] == [{"value": 1}]

    asyncio.run(scenario())


def test_task_timeout_retries_once_then_fails() -> None:
    async def scenario() -> None:
        source = MemoryQueue("incoming", poll_interval_s=0.01)
        app = OneStepApp("timeout-app")
        attempts: list[int] = []
        second_attempt_seen = asyncio.Event()

        @app.task(source=source, retry=MaxAttempts(2, delay_s=0), timeout_s=0.01)
        async def slow(ctx, item):
            attempts.append(ctx.current.attempts)
            if len(attempts) == 2:
                second_attempt_seen.set()
            await asyncio.sleep(0.05)

        await source.publish({"value": 1})
        app_task = asyncio.create_task(app.serve())
        await asyncio.wait_for(second_attempt_seen.wait(), timeout=1.0)
        app.request_shutdown()
        await asyncio.wait_for(app_task, timeout=1.0)

        assert attempts == [0, 1]
        assert await source.fetch(1) == []

    asyncio.run(scenario())


def test_failure_classification_and_dead_letter_payload() -> None:
    class CapturePolicy:
        def __init__(self) -> None:
            self.failures = []

        def on_error(self, envelope, exc, failure):
            self.failures.append(failure)
            return RetryAction(RetryDecision.FAIL)

    async def scenario() -> None:
        source = MemoryQueue("incoming", poll_interval_s=0.01)
        dead = MemoryQueue("dead", poll_interval_s=0.01)
        policy = CapturePolicy()
        app = OneStepApp("dlq-app")

        @app.task(source=source, retry=policy, timeout_s=0.01, dead_letter=dead)
        async def slow(ctx, item):
            await asyncio.sleep(0.05)

        await source.publish({"value": 1}, meta={"trace_id": "abc"})
        app_task = asyncio.create_task(app.serve())
        await asyncio.sleep(0.1)
        app.request_shutdown()
        await asyncio.wait_for(app_task, timeout=1.0)

        assert len(policy.failures) == 1
        assert policy.failures[0].kind is FailureKind.TIMEOUT
        assert policy.failures[0].traceback is not None
        assert "TimeoutError" in policy.failures[0].traceback

        dead_batch = await dead.fetch(1)
        assert len(dead_batch) == 1
        assert dead_batch[0].payload["payload"] == {"value": 1}
        assert dead_batch[0].payload["failure"]["kind"] == "timeout"
        assert dead_batch[0].payload["failure"]["exception_type"] == "TimeoutError"
        assert dead_batch[0].payload["failure"]["message"] == ""
        assert "TimeoutError" in dead_batch[0].payload["failure"]["traceback"]
        assert dead_batch[0].envelope.meta == {
            "app": "dlq-app",
            "task": "slow",
            "source": "incoming",
            "original_meta": {"trace_id": "abc"},
            "original_attempts": 0,
        }
        assert await source.fetch(1) == []

    asyncio.run(scenario())


def test_replay_task_dead_letters_restores_original_payload_and_meta() -> None:
    async def scenario() -> None:
        source = MemoryQueue("incoming", poll_interval_s=0.01)
        dead = MemoryQueue("dead", poll_interval_s=0.01)
        app = OneStepApp("dlq-replay-app")

        @app.task(source=source, retry=NoRetry(), dead_letter=dead)
        async def consume(ctx, item):
            return item

        await app.startup()
        await dead.publish(
            {
                "payload": {"value": 7},
                "failure": {
                    "kind": "error",
                    "exception_type": "RuntimeError",
                    "message": "boom",
                    "traceback": "Traceback",
                },
            },
            meta={
                "app": "dlq-replay-app",
                "task": "consume",
                "source": "incoming",
                "original_meta": {"trace_id": "abc"},
                "original_attempts": 3,
            },
        )

        result = await app.replay_task_dead_letters("consume", limit=5)
        replayed_batch = await source.fetch(1)
        remaining_dead = await dead.fetch(1)
        await app.shutdown()

        assert result == {
            "operation": "replay_dead_letters",
            "task_name": "consume",
            "requested": True,
            "completion": "complete",
            "requested_limit": 5,
            "attempted_count": 1,
            "replayed_count": 1,
            "failed_count": 0,
            "empty": False,
        }
        assert len(replayed_batch) == 1
        assert replayed_batch[0].payload == {"value": 7}
        assert replayed_batch[0].envelope.meta == {"trace_id": "abc"}
        assert replayed_batch[0].envelope.attempts == 3
        assert remaining_dead == []

    asyncio.run(scenario())


def test_discard_task_dead_letters_acknowledges_dead_letter_batch() -> None:
    async def scenario() -> None:
        source = MemoryQueue("incoming", poll_interval_s=0.01)
        dead = MemoryQueue("dead", poll_interval_s=0.01)
        app = OneStepApp("dlq-discard-app")

        @app.task(source=source, retry=NoRetry(), dead_letter=dead)
        async def consume(ctx, item):
            return item

        await app.startup()
        await dead.publish(
            {
                "payload": {"value": 11},
                "failure": {
                    "kind": "error",
                    "exception_type": "RuntimeError",
                    "message": "boom",
                    "traceback": "Traceback",
                },
            },
            meta={
                "app": "dlq-discard-app",
                "task": "consume",
                "source": "incoming",
                "original_meta": {"trace_id": "abc"},
                "original_attempts": 1,
            },
        )

        result = await app.discard_task_dead_letters("consume", limit=5)
        remaining_dead = await dead.fetch(1)
        await app.shutdown()

        assert result == {
            "operation": "discard_dead_letters",
            "task_name": "consume",
            "requested": True,
            "completion": "complete",
            "requested_limit": 5,
            "attempted_count": 1,
            "discarded_count": 1,
            "failed_count": 0,
            "empty": False,
        }
        assert remaining_dead == []

    asyncio.run(scenario())


def test_run_task_once_executes_generic_manual_delivery_contract() -> None:
    async def scenario() -> None:
        source = MemoryQueue("incoming", poll_interval_s=0.01)
        sink = MemoryQueue("processed", poll_interval_s=0.01)
        app = OneStepApp("manual-run-contract")

        @app.task(source=source, emit=sink)
        async def consume(ctx, item):
            return {
                "value": item["value"] + 1,
                "manual_run": bool(ctx.delivery.envelope.meta.get("manual_run")),
            }

        await app.startup()
        result = await app.run_task_once("consume", payload={"value": 9})
        processed = await sink.fetch(1)
        await app.shutdown()

        assert result == {
            "operation": "run_task_once",
            "task_name": "consume",
            "requested": True,
            "completion": "complete",
            "attempted_count": 1,
            "manual_run": True,
        }
        assert len(processed) == 1
        assert processed[0].payload == {
            "value": 10,
            "manual_run": True,
        }

    asyncio.run(scenario())


def test_task_events_and_metrics_for_success_and_timeout_dead_letter() -> None:
    async def scenario() -> None:
        source = MemoryQueue("incoming", poll_interval_s=0.01)
        dead = MemoryQueue("dead", poll_interval_s=0.01)
        metrics = InMemoryMetrics()
        events = []
        app = OneStepApp("events-app")
        app.on_event(metrics)

        @app.on_event
        def capture(event):
            events.append(event.kind)

        @app.on_startup
        async def seed(app):
            await source.publish({"kind": "ok"})
            await source.publish({"kind": "slow"})

        @app.task(source=source, retry=NoRetry(), timeout_s=0.01, dead_letter=dead)
        async def consume(ctx, item):
            if item["kind"] == "slow":
                await asyncio.sleep(0.05)
                return None
            if item["kind"] == "ok":
                return {"done": True}
            raise AssertionError("unexpected payload")

        app_task = asyncio.create_task(app.serve())
        await asyncio.sleep(0.1)
        app.request_shutdown()
        await asyncio.wait_for(app_task, timeout=1.0)

        assert metrics.count(TaskEventKind.FETCHED) == 2
        assert metrics.count(TaskEventKind.STARTED) == 2
        assert metrics.count(TaskEventKind.SUCCEEDED) == 1
        assert metrics.count(TaskEventKind.DEAD_LETTERED) == 1
        assert metrics.count(TaskEventKind.FAILED) == 1
        assert metrics.count(TaskEventKind.FAILED, failure_kind=FailureKind.TIMEOUT) == 1
        assert events.count(TaskEventKind.SUCCEEDED) == 1
        assert events.count(TaskEventKind.DEAD_LETTERED) == 1

        dead_batch = await dead.fetch(1)
        assert len(dead_batch) == 1
        assert dead_batch[0].payload["payload"] == {"kind": "slow"}
        assert dead_batch[0].payload["failure"]["kind"] == "timeout"

    asyncio.run(scenario())


def test_succeeded_event_includes_notification_metadata_when_task_returns_it() -> None:
    async def scenario() -> None:
        source = MemoryQueue("incoming", poll_interval_s=0.01)
        captured = []
        app = OneStepApp("events-app")

        @app.on_event
        def observe(event):
            captured.append(event)

        @app.on_startup
        async def seed(app):
            await source.publish({"kind": "ok"})

        @app.task(source=source)
        async def consume(ctx, item):
            ctx.app.request_shutdown()
            return {
                "done": True,
                "notification": {
                    "summary": "processed one item",
                    "metrics": [
                        {"label": "processed", "value": 1},
                    ],
                },
            }

        await asyncio.wait_for(app.serve(), timeout=1.0)

        succeeded = [event for event in captured if event.kind is TaskEventKind.SUCCEEDED]
        assert len(succeeded) == 1
        assert succeeded[0].meta == {
            "notification": {
                "summary": "processed one item",
                "metrics": [
                    {"label": "processed", "value": 1},
                ],
            }
        }

    asyncio.run(scenario())


def test_task_context_records_custom_metrics_from_handler() -> None:
    async def scenario() -> None:
        source = MemoryQueue("incoming", poll_interval_s=0.01)
        app = OneStepApp("custom-metrics-app")

        @app.on_startup
        async def seed(app):
            await source.publish({"rows": [1, 2, 3, 4, 5]})

        @app.task(source=source)
        async def consume(ctx, item):
            rows = item["rows"]
            ctx.metrics.counter("rows_success").inc(3)
            ctx.metrics.counter("rows_failed", labels={"reason": "validation"}).inc(2)
            ctx.metrics.gauge("batch_size").set(len(rows))
            ctx.metrics.gauge("batch_size").set(len(rows) + 1)
            ctx.app.request_shutdown()

        await asyncio.wait_for(app.serve(), timeout=1.0)
        return app.custom_metrics.rotate_task("consume")

    samples = asyncio.run(scenario())

    assert samples == [
        {"name": "batch_size", "kind": "gauge", "value": 6, "labels": {}},
        {
            "name": "rows_failed",
            "kind": "counter",
            "value": 2,
            "labels": {"reason": "validation"},
        },
        {"name": "rows_success", "kind": "counter", "value": 3, "labels": {}},
    ]


def test_custom_metrics_reject_invalid_series() -> None:
    app = OneStepApp("custom-metrics-validation")
    metrics = app.custom_metrics.for_task("consume")

    with pytest.raises(ValueError, match="invalid custom metric name"):
        metrics.counter("bad metric").inc()

    with pytest.raises(ValueError, match="reserved"):
        metrics.counter("rows_success", labels={"service": "billing"}).inc()

    with pytest.raises(ValueError, match="counter increment must be >= 0"):
        metrics.counter("rows_success").inc(-1)

    with pytest.raises(ValueError, match="finite"):
        metrics.gauge("batch_size").set(float("nan"))

    metrics.counter("same_name").inc()
    with pytest.raises(ValueError, match="already exists as counter"):
        metrics.gauge("same_name").set(1)


def test_succeeded_event_ignores_or_sanitizes_invalid_notification_payload() -> None:
    async def scenario() -> None:
        source = MemoryQueue("incoming", poll_interval_s=0.01)
        captured = []
        app = OneStepApp("notification-sanitize")

        @app.on_event
        def observe(event):
            captured.append(event)

        @app.on_startup
        async def seed(app):
            await source.publish({"kind": "invalid"})

        @app.task(source=source)
        async def consume(ctx, item):
            ctx.app.request_shutdown()
            return {
                "done": True,
                "notification": {
                    "summary": "processed one item",
                    "metrics": [
                        {"label": "processed", "value": 1, "extra": object()},
                        {"label": "ratio", "value": float("nan")},
                        object(),
                    ],
                    "details": {
                        "kept": True,
                        "dropped": object(),
                        1: "not-a-string-key",
                    },
                    "bad_root_value": object(),
                },
            }

        await asyncio.wait_for(app.serve(), timeout=1.0)

        succeeded = [event for event in captured if event.kind is TaskEventKind.SUCCEEDED]
        assert len(succeeded) == 1
        assert succeeded[0].meta == {
            "notification": {
                "summary": "processed one item",
                "metrics": [
                    {"label": "processed", "value": 1},
                    {"label": "ratio"},
                ],
                "details": {
                    "kept": True,
                },
            }
        }

    asyncio.run(scenario())


def test_structured_event_logger_emits_uniform_log_fields() -> None:
    class ListHandler(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.records = []

        def emit(self, record) -> None:
            self.records.append(record)

    async def scenario() -> None:
        source = MemoryQueue("incoming")
        logger = logging.getLogger("tests.structured-events")
        handler = ListHandler()
        logger.handlers = [handler]
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        app = OneStepApp("structured-events")
        app.on_event(StructuredEventLogger(logger=logger))

        @app.on_startup
        async def seed(app):
            await source.publish({"value": 4})

        @app.task(source=source)
        async def consume(ctx, item):
            ctx.app.request_shutdown()
            return None

        await asyncio.wait_for(app.serve(), timeout=1.0)

        succeeded = [record for record in handler.records if getattr(record, "event_kind", None) == "succeeded"]
        assert len(succeeded) == 1
        record = succeeded[0]
        assert record.levelno == logging.INFO
        assert record.app_name == "structured-events"
        assert record.task_name == "consume"
        assert record.source_name == "incoming"
        assert record.attempts == 0
        assert isinstance(record.task_event_meta, dict)
        assert record.failure_kind is None

    asyncio.run(scenario())


def test_structured_event_logger_includes_failure_fields() -> None:
    class ListHandler(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.records = []

        def emit(self, record) -> None:
            self.records.append(record)

    async def scenario() -> None:
        source = MemoryQueue("incoming", poll_interval_s=0.01)
        logger = logging.getLogger("tests.structured-events.failure")
        handler = ListHandler()
        logger.handlers = [handler]
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        app = OneStepApp("structured-events-failure")
        app.on_event(StructuredEventLogger(logger=logger))

        @app.on_startup
        async def seed(app):
            await source.publish({"value": 1})

        @app.task(source=source, retry=NoRetry(), timeout_s=0.01)
        async def consume(ctx, item):
            await asyncio.sleep(0.05)

        app_task = asyncio.create_task(app.serve())
        await asyncio.sleep(0.1)
        app.request_shutdown()
        await asyncio.wait_for(app_task, timeout=1.0)

        failed = [record for record in handler.records if getattr(record, "event_kind", None) == "failed"]
        assert len(failed) == 1
        record = failed[0]
        assert record.levelno == logging.ERROR
        assert record.failure_kind == "timeout"
        assert record.failure_exception_type == "TimeoutError"
        assert record.app_name == "structured-events-failure"
        assert record.task_name == "consume"

    asyncio.run(scenario())


def test_sink_success_logs_at_debug_level() -> None:
    class ListHandler(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.records = []

        def emit(self, record) -> None:
            self.records.append(record)

    async def scenario() -> None:
        source = MemoryQueue("incoming")
        sink = MemoryQueue("processed")
        logger = logging.getLogger("onestep.logging-contract.consume")
        handler = ListHandler()
        previous_handlers = list(logger.handlers)
        previous_level = logger.level
        previous_propagate = logger.propagate
        logger.handlers = [handler]
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        app = OneStepApp("logging-contract")

        @app.on_startup
        async def seed(app):
            await source.publish({"value": 1})

        @app.task(source=source, emit=sink)
        async def consume(ctx, item):
            ctx.app.request_shutdown()
            return {"value": item["value"] + 1}

        try:
            await asyncio.wait_for(app.serve(), timeout=1.0)
        finally:
            logger.handlers = previous_handlers
            logger.setLevel(previous_level)
            logger.propagate = previous_propagate

        succeeded = [record for record in handler.records if record.getMessage() == "sink send succeeded"]
        assert len(succeeded) == 1
        record = succeeded[0]
        assert record.levelno == logging.DEBUG
        assert record.sink_name == "processed"
        assert record.sink_kind == "MemoryQueue"
        assert record.delivery_attempts == 0

    asyncio.run(scenario())


def test_sink_success_debug_log_respects_logger_level() -> None:
    class ListHandler(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.records = []

        def emit(self, record) -> None:
            self.records.append(record)

    async def scenario() -> None:
        source = MemoryQueue("incoming")
        sink = MemoryQueue("processed")
        logger = logging.getLogger("onestep.logging-contract-quiet.consume")
        handler = ListHandler()
        previous_handlers = list(logger.handlers)
        previous_level = logger.level
        previous_propagate = logger.propagate
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False

        app = OneStepApp("logging-contract-quiet")

        @app.on_startup
        async def seed(app):
            await source.publish({"value": 1})

        @app.task(source=source, emit=sink)
        async def consume(ctx, item):
            ctx.app.request_shutdown()
            return {"value": item["value"] + 1}

        try:
            await asyncio.wait_for(app.serve(), timeout=1.0)
        finally:
            logger.handlers = previous_handlers
            logger.setLevel(previous_level)
            logger.propagate = previous_propagate

        succeeded = [record for record in handler.records if record.getMessage() == "sink send succeeded"]
        assert succeeded == []

    asyncio.run(scenario())


def test_shutdown_timeout_cancels_inflight_and_retries_delivery() -> None:
    async def scenario() -> None:
        source = MemoryQueue("incoming", poll_interval_s=0.01)
        app = OneStepApp("shutdown-app", shutdown_timeout_s=0.05)
        started = asyncio.Event()
        cancelled = asyncio.Event()

        @app.task(source=source)
        async def slow(ctx, item):
            started.set()
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()

        await source.publish({"value": 1})
        app_task = asyncio.create_task(app.serve())
        await asyncio.wait_for(started.wait(), timeout=1.0)
        app.request_shutdown()
        await asyncio.wait_for(app_task, timeout=1.0)

        assert cancelled.is_set()
        redelivered = await source.fetch(1)
        assert len(redelivered) == 1
        assert redelivered[0].payload == {"value": 1}
        assert redelivered[0].envelope.attempts == 1

    asyncio.run(scenario())


def test_event_handler_failure_does_not_break_runtime() -> None:
    async def scenario() -> None:
        source = MemoryQueue("incoming")
        app = OneStepApp("event-handler-failure")
        seen = []

        @app.on_event
        def broken(event):
            raise RuntimeError("observer broke")

        @app.on_event
        def capture(event):
            seen.append(event.kind)

        @app.on_startup
        async def seed(app):
            await source.publish({"value": 1})

        @app.task(source=source)
        async def consume(ctx, item):
            ctx.app.request_shutdown()
            return None

        await asyncio.wait_for(app.serve(), timeout=1.0)
        assert TaskEventKind.SUCCEEDED in seen

    asyncio.run(scenario())


def test_dead_letter_publish_failure_retries_original_delivery() -> None:
    class BrokenSink(Sink):
        def __init__(self) -> None:
            super().__init__("broken")

        async def send(self, envelope) -> None:
            raise RuntimeError("dead-letter offline")

    async def scenario() -> None:
        source = MemoryQueue("incoming", poll_interval_s=0.01)
        app = OneStepApp("broken-dlq")

        @app.task(source=source, retry=NoRetry(), dead_letter=BrokenSink())
        async def fail_now(ctx, item):
            ctx.app.request_shutdown()
            raise RuntimeError("boom")

        await source.publish({"value": 1})
        await asyncio.wait_for(app.serve(), timeout=1.0)

        redelivered = await source.fetch(1)
        assert len(redelivered) == 1
        assert redelivered[0].payload == {"value": 1}
        assert redelivered[0].envelope.attempts == 1

    asyncio.run(scenario())


def test_lifecycle_hooks_and_context_state_config() -> None:
    async def scenario() -> None:
        source = MemoryQueue("incoming")
        state = InMemoryStateStore()
        app = OneStepApp("hooked-app", config={"prefix": "v1"}, state=state)
        events: list[str] = []

        @app.on_startup
        async def startup(app):
            events.append("startup")
            await source.publish({"value": 2})

        @app.on_shutdown
        def shutdown():
            events.append("shutdown")

        @app.task(source=source)
        async def consume(ctx, item):
            events.append("handler")
            assert ctx.config["prefix"] == "v1"
            current = await ctx.state.get("count", 0)
            await ctx.state.set("count", current + item["value"])
            ctx.app.request_shutdown()

        await app.serve()

        assert events == ["startup", "handler", "shutdown"]
        assert await state.load("hooked-app:consume:count") == 2

    asyncio.run(scenario())
