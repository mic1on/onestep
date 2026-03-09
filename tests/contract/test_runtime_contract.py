from __future__ import annotations

import asyncio
import logging
import signal

from onestep import (
    ConnectorErrorKind,
    ConnectorOperation,
    ConnectorOperationError,
    FailureKind,
    InMemoryMetrics,
    InMemoryStateStore,
    MaxAttempts,
    MemoryQueue,
    NoRetry,
    OneStepApp,
    RetryAction,
    RetryDecision,
    Sink,
    StructuredEventLogger,
    TaskEventKind,
)
from onestep.connectors.base import Delivery, Sink, Source
from onestep.envelope import Envelope


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


def test_task_timeout_retries_once_then_fails() -> None:
    async def scenario() -> None:
        source = MemoryQueue("incoming", poll_interval_s=0.01)
        app = OneStepApp("timeout-app")
        attempts: list[int] = []

        @app.task(source=source, retry=MaxAttempts(2, delay_s=0), timeout_s=0.01)
        async def slow(ctx, item):
            attempts.append(ctx.current.attempts)
            await asyncio.sleep(0.05)

        await source.publish({"value": 1})
        app_task = asyncio.create_task(app.serve())
        await asyncio.sleep(0.15)
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

        dead_batch = await dead.fetch(1)
        assert len(dead_batch) == 1
        assert dead_batch[0].payload == {
            "payload": {"value": 1},
            "failure": {
                "kind": "timeout",
                "exception_type": "TimeoutError",
                "message": "",
            },
        }
        assert dead_batch[0].envelope.meta == {
            "app": "dlq-app",
            "task": "slow",
            "source": "incoming",
            "original_meta": {"trace_id": "abc"},
            "original_attempts": 0,
        }
        assert await source.fetch(1) == []

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
