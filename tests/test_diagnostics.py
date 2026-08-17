from __future__ import annotations

import asyncio
import threading
import time
from copy import deepcopy
from pathlib import Path

import pytest

from onestep import MaxAttempts, NoRetry, OneStepApp
from onestep.connectors.base import Delivery, Sink, Source
from onestep.diagnostics.connectivity import check_connectivity
from onestep.diagnostics.ipc import (
    FrameValidator,
    IPCProtocolError,
    decode_frame,
    encode_frame,
)
from onestep.diagnostics.models import DiagnosticReport, DiagnosticRequest
from onestep.diagnostics.runner import DiagnosticRunner
from onestep.diagnostics.supervisor import supervise_diagnostic
from onestep.envelope import Envelope
from onestep.events import TaskEventKind
from onestep.task import EmitBinding, EmitRoute, TaskHooks


class RecordingSink(Sink):
    def __init__(
        self,
        name: str,
        calls: list[str],
        *,
        broken: bool = False,
    ) -> None:
        super().__init__(name)
        self.calls = calls
        self.broken = broken

    async def open(self) -> None:
        self.calls.append(f"{self.name}:open")

    async def send(self, envelope: Envelope) -> None:
        self.calls.append(f"{self.name}:send")
        if self.broken:
            raise RuntimeError("secret sink details")

    async def close(self) -> None:
        self.calls.append(f"{self.name}:close")


def test_diagnostic_dry_run_executes_handler_hooks_and_routes_without_sink_io() -> None:
    async def scenario() -> None:
        calls: list[str] = []
        app = OneStepApp("diagnostic")
        sink = RecordingSink("selected", calls)

        async def before(ctx, payload):
            calls.append("before")

        async def after(ctx, payload, result):
            calls.append("after")

        async def predicate(ctx, payload, result):
            calls.append("route")
            return True

        @app.task(
            emit=EmitRoute(predicate=predicate, then_sinks=(sink,)),
            hooks=TaskHooks(before=(before,), after_success=(after,)),
        )
        async def consume(ctx, payload):
            calls.append("handler")
            return {"output": payload["input"]}

        report = await DiagnosticRunner(app).run(
            task_name="consume",
            envelope=Envelope(body={"input": 1}),
            send=False,
        )

        assert calls == ["before", "handler", "after", "route"]
        assert report.completion == "succeeded"
        assert report.mode == "dry-run"
        assert report.selected_sinks == ("selected",)
        assert report.delivery_action == "would_ack"
        assert report.delivery_action_basis == "predicted"
        assert report.side_effect_outcome == "not_attempted"
        assert report.outputs[0]["envelope"]["body"] == {"output": 1}
        assert [event.kind for event in report.events] == [
            TaskEventKind.STARTED,
            TaskEventKind.SUCCEEDED,
        ]

    asyncio.run(scenario())


def test_diagnostic_reports_transform_stage_before_sink_send() -> None:
    async def scenario() -> None:
        calls: list[str] = []
        app = OneStepApp("transform-diagnostic")
        sink = RecordingSink("projected", calls)

        def fail_transform(ctx, payload, result):
            raise RuntimeError("transform failed")

        @app.task(emit=EmitBinding(sink=sink, transform=fail_transform), retry=MaxAttempts(2))
        async def consume(ctx, payload):
            return payload

        report = await DiagnosticRunner(app).run(
            task_name="consume",
            envelope=Envelope(body={"id": 1}),
            send=False,
        )

        assert calls == []
        assert report.completion == "failed"
        assert report.failure_stage == "transform"
        assert report.selected_sinks == ("projected",)
        assert report.delivery_action == "would_retry"

    asyncio.run(scenario())


def test_diagnostic_events_do_not_reach_app_event_handlers() -> None:
    async def scenario() -> None:
        app = OneStepApp("local-events")
        external_events = []
        app.on_event(external_events.append)

        @app.task()
        async def consume(ctx, payload):
            return None

        report = await DiagnosticRunner(app).run(
            task_name="consume",
            envelope=Envelope(body={"id": 1}),
            send=False,
        )
        assert external_events == []
        assert [event.kind for event in report.events] == [
            TaskEventKind.STARTED,
            TaskEventKind.SUCCEEDED,
        ]

    asyncio.run(scenario())


def test_diagnostic_send_opens_and_reverse_closes_selected_sinks() -> None:
    async def scenario() -> None:
        calls: list[str] = []
        first = RecordingSink("first", calls)
        second = RecordingSink("second", calls)
        app = OneStepApp("send")

        @app.task(emit=(first, second))
        async def consume(ctx, payload):
            return {"ok": True}

        report = await DiagnosticRunner(app).run(
            task_name="consume",
            envelope=Envelope(body={"id": 1}),
            send=True,
        )
        assert calls == [
            "first:open",
            "first:send",
            "second:open",
            "second:send",
            "second:close",
            "first:close",
        ]
        assert report.side_effect_outcome == "completed"
        assert report.cleanup == "complete"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "retry,dead_letter,expected",
    [
        (MaxAttempts(max_attempts=3, delay_s=30), None, "would_retry"),
        (NoRetry(), "dead", "would_dead_letter"),
        (NoRetry(), None, "would_fail"),
    ],
)
def test_diagnostic_runs_exactly_one_failed_attempt(retry, dead_letter, expected) -> None:
    async def scenario() -> None:
        calls = 0
        sink_calls: list[str] = []
        app = OneStepApp("failed-diagnostic")
        sink = RecordingSink("dead", sink_calls) if dead_letter else None

        @app.task(retry=retry, dead_letter=sink)
        async def consume(ctx, payload):
            nonlocal calls
            calls += 1
            raise ValueError("boom")

        report = await DiagnosticRunner(app).run(
            task_name="consume",
            envelope=Envelope(body={"id": 1}, attempts=0),
            send=False,
        )
        assert calls == 1
        assert sink_calls == []
        assert report.delivery_action == expected
        assert report.delivery_action_basis == "predicted"
        assert report.failure == {
            "failure_kind": "error",
            "exception_type": "ValueError",
        }
        assert "boom" not in str(report.to_dict())
        if expected == "would_dead_letter":
            assert report.dead_letter == {"attempted": False, "published": None}

    asyncio.run(scenario())


def test_diagnostic_send_dead_letter_observes_success_and_failure() -> None:
    async def run_case(*, broken: bool):
        calls: list[str] = []
        app = OneStepApp("dead-send")

        @app.task(retry=NoRetry(), dead_letter=RecordingSink("dead", calls, broken=broken))
        async def consume(ctx, payload):
            raise ValueError("boom")

        report = await DiagnosticRunner(app).run(
            task_name="consume",
            envelope=Envelope(body={"id": 1}),
            send=True,
        )
        return calls, report

    calls, success = asyncio.run(run_case(broken=False))
    assert calls == ["dead:open", "dead:send", "dead:close"]
    assert success.delivery_action == "would_dead_letter"
    assert success.dead_letter == {"attempted": True, "published": True}

    calls, failure = asyncio.run(run_case(broken=True))
    assert calls == ["dead:open", "dead:send", "dead:close"]
    assert failure.delivery_action == "would_retry"
    assert failure.dead_letter == {"attempted": True, "published": False}


def test_diagnostic_report_json_round_trip_hides_failure_message() -> None:
    async def scenario() -> None:
        app = OneStepApp("roundtrip")

        @app.task()
        async def consume(ctx, payload):
            raise RuntimeError("password=secret")

        report = await DiagnosticRunner(app).run(
            task_name="consume",
            envelope=Envelope(body={"id": 1}),
            send=False,
        )
        encoded = report.to_dict()
        assert "password=secret" not in str(encoded)
        assert DiagnosticReport.from_dict(encoded).to_dict() == encoded

    asyncio.run(scenario())


class _SharedResource(Source, Sink):
    def __init__(self, calls: list[str]) -> None:
        Source.__init__(self, "shared")
        Sink.__init__(self, "shared")
        self.calls = calls

    async def open(self) -> None:
        self.calls.append("open")

    async def close(self) -> None:
        self.calls.append("close")

    async def fetch(self, limit: int) -> list[Delivery]:
        raise AssertionError("fetch must not run")

    async def send(self, envelope: Envelope) -> None:
        raise AssertionError("send must not run")


class _NoProbeStore:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def load(self, key: str):
        self.calls.append("load")
        raise AssertionError("load must not run")

    async def save(self, key: str, value) -> None:
        self.calls.append("save")
        raise AssertionError("save must not run")

    async def delete(self, key: str) -> None:
        self.calls.append("delete")
        raise AssertionError("delete must not run")


def test_connectivity_deduplicates_aliases_and_never_probes_store_methods() -> None:
    async def scenario() -> None:
        calls: list[str] = []
        shared = _SharedResource(calls)
        store = _NoProbeStore()
        app = OneStepApp("connectivity", state=store)
        app.register_resource("queue", shared)

        @app.task(source=shared, emit=shared)
        async def consume(ctx, payload):
            return payload

        report = await check_connectivity(app, timeout_s=0.1)
        shared_result = next(item for item in report.resources if "queue" in item.aliases)
        store_result = next(item for item in report.resources if "app.state" in item.aliases)
        assert shared_result.aliases == (
            "queue",
            "consume.source",
            "consume.emit[0]",
        )
        assert shared_result.roles == ("named", "source", "sink")
        assert shared_result.status == "connected"
        assert store_result.probe_kind == "none"
        assert store_result.status == "not_probeable"
        assert store.calls == []
        assert calls == ["open", "close"]
        assert report.ok is True

    asyncio.run(scenario())


class _LifecycleResource:
    def __init__(
        self,
        name: str,
        calls: list[str],
        *,
        open_mode: str = "ok",
        close_broken: bool = False,
    ) -> None:
        self.name = name
        self.calls = calls
        self.open_mode = open_mode
        self.close_broken = close_broken

    async def open(self) -> None:
        self.calls.append(f"{self.name}:open")
        if self.open_mode == "broken":
            raise RuntimeError("dsn=secret")
        if self.open_mode == "slow":
            await asyncio.sleep(1)

    async def close(self) -> None:
        self.calls.append(f"{self.name}:close")
        if self.close_broken:
            raise RuntimeError("close secret")


def test_connectivity_continues_after_failures_and_attempts_cleanup() -> None:
    async def scenario() -> None:
        calls: list[str] = []
        app = OneStepApp("partial")
        app.register_resource("good", _LifecycleResource("good", calls))
        app.register_resource(
            "broken",
            _LifecycleResource("broken", calls, open_mode="broken"),
        )
        app.register_resource(
            "slow",
            _LifecycleResource("slow", calls, open_mode="slow"),
        )
        report = await check_connectivity(app, timeout_s=0.01)
        lifecycle = [item for item in report.resources if item.probe_kind == "lifecycle"]
        assert calls == [
            "good:open",
            "good:close",
            "broken:open",
            "broken:close",
            "slow:open",
            "slow:close",
        ]
        assert [item.status for item in lifecycle] == [
            "connected",
            "failed",
            "failed",
        ]
        assert lifecycle[1].open == {
            "status": "failed",
            "exception_type": "RuntimeError",
        }
        assert "secret" not in str(report.to_dict())
        assert report.ok is False

    asyncio.run(scenario())


def test_connectivity_timeout_bounds_synchronously_blocking_lifecycle() -> None:
    class BlockingLifecycle:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self.connected = False
            self.cleaned_up = threading.Event()

        def open(self) -> None:
            self.calls.append("open")
            time.sleep(0.1)
            self.connected = True

        def close(self) -> None:
            self.calls.append("close")
            self.connected = False
            self.cleaned_up.set()

    resource = BlockingLifecycle()
    app = OneStepApp("sync-blocking-connectivity")
    app.register_resource("blocking", resource)

    started = time.monotonic()
    report = asyncio.run(check_connectivity(app, timeout_s=0.02))
    elapsed = time.monotonic() - started

    result = report.resources[0]
    assert elapsed < 0.15
    assert result.open == {"status": "timed_out"}
    assert result.close == {"status": "timed_out"}
    assert resource.calls == ["open"]
    assert report.ok is False

    assert resource.cleaned_up.wait(timeout=0.5)
    assert resource.calls == ["open", "close"]
    assert resource.connected is False


def test_connectivity_only_not_probeable_is_success_with_warning() -> None:
    app = OneStepApp("stores-only", state=_NoProbeStore())
    report = asyncio.run(check_connectivity(app, timeout_s=0.1))
    assert report.ok is True
    assert "no connection was verified" in report.warnings[0]


def test_ipc_rejects_non_monotonic_and_unknown_frames() -> None:
    validator = FrameValidator(direction="status")
    validator.accept(
        decode_frame(
            encode_frame(
                "checkpoint",
                sequence=1,
                payload={
                    "phase": "child_start",
                    "transition": "entered",
                    "elapsed_s": 0.0,
                },
            )
        )
    )
    with pytest.raises(IPCProtocolError, match="sequence"):
        validator.accept(
            decode_frame(
                encode_frame(
                    "checkpoint",
                    sequence=1,
                    payload={
                        "phase": "handler",
                        "transition": "entered",
                        "elapsed_s": 0.1,
                    },
                )
            )
        )
    with pytest.raises(IPCProtocolError, match="kind"):
        decode_frame(
            b'{"schema":"onestep/diagnostic-ipc","version":1,'
            b'"kind":"other","sequence":2,"payload":{}}'
        )


@pytest.mark.parametrize(
    "data,match",
    [
        (b"\xff", "malformed"),
        (b"{", "malformed"),
        (
            b'{"schema":"wrong","version":1,"kind":"cancel",'
            b'"sequence":1,"payload":{}}',
            "schema",
        ),
    ],
)
def test_ipc_rejects_malformed_frames(data: bytes, match: str) -> None:
    with pytest.raises(IPCProtocolError, match=match):
        decode_frame(data)


def test_ipc_rejects_invalid_checkpoint_and_final_payloads() -> None:
    checkpoint_validator = FrameValidator(direction="status")
    with pytest.raises(IPCProtocolError, match="fields"):
        checkpoint_validator.accept(
            decode_frame(
                encode_frame(
                    "checkpoint",
                    sequence=1,
                    payload={
                        "phase": "handler",
                        "transition": "entered",
                        "elapsed_s": 0.0,
                        "secret": "must not cross IPC",
                    },
                )
            )
        )
    with pytest.raises(IPCProtocolError, match="diagnostic result"):
        checkpoint_validator.accept(
            decode_frame(encode_frame("final", sequence=1, payload={}))
        )
    frame = decode_frame(
        encode_frame(
            "checkpoint",
            sequence=True,
            payload={
                "phase": "handler",
                "transition": "entered",
                "elapsed_s": 0.0,
            },
        )
    )
    with pytest.raises(IPCProtocolError, match="sequence"):
        checkpoint_validator.accept(frame)


def _run_request(target: str, task: str, *, send: bool = False) -> DiagnosticRequest:
    return DiagnosticRequest(
        operation="run",
        target=target,
        task=task,
        envelope=Envelope(body={"value": 1}),
        send=send,
    )


def test_spawned_python_and_yaml_targets_have_matching_results(
    tmp_path: Path,
) -> None:
    yaml_target = tmp_path / "worker.yaml"
    yaml_target.write_text(
        """apiVersion: onestep/v1alpha1
kind: App
app:
  name: diagnostic-yaml
tasks:
  - name: success
    handler:
      ref: tests.assets.diagnostic_app:yaml_handler
""",
        encoding="utf-8",
    )
    python_report = supervise_diagnostic(
        _run_request("tests.assets.diagnostic_app:app", "success"),
        timeout_s=3,
        grace_s=0.2,
    )
    yaml_report = supervise_diagnostic(
        _run_request(str(yaml_target), "success"),
        timeout_s=3,
        grace_s=0.2,
    )
    assert python_report.completion == yaml_report.completion == "succeeded"
    assert python_report.selected_sinks == yaml_report.selected_sinks == ()
    assert python_report.delivery_action == yaml_report.delivery_action == "would_ack"


@pytest.mark.parametrize(
    "target,phase",
    [
        ("tests.assets.diagnostic_app:blocking_app", "handler"),
        ("tests.assets.diagnostic_app:hook_blocking_app", "before_hook"),
        ("tests.assets.diagnostic_app:async_cancel_app", "handler"),
    ],
)
def test_supervisor_bounds_blocking_diagnostics(target: str, phase: str) -> None:
    task = {
        "tests.assets.diagnostic_app:blocking_app": "block",
        "tests.assets.diagnostic_app:hook_blocking_app": "blocked_by_hook",
        "tests.assets.diagnostic_app:async_cancel_app": "wait_forever",
    }[target]
    started = time.monotonic()
    report = supervise_diagnostic(
        _run_request(target, task),
        timeout_s=0.3,
        grace_s=0.2,
    )
    assert time.monotonic() - started < 2
    assert report.completion == "timed_out"
    assert report.cleanup in {"complete", "incomplete"}
    assert report.last_checkpoint is not None
    assert report.last_checkpoint["phase"] == phase


def test_spawned_child_output_is_forwarded_only_to_stderr(capsys) -> None:
    report = supervise_diagnostic(
        _run_request("tests.assets.diagnostic_app:output_app", "write_output"),
        timeout_s=3,
        grace_s=0.2,
    )
    captured = capsys.readouterr()
    assert report.completion == "succeeded"
    assert captured.out == ""
    assert "child stdout marker" in captured.err
    assert "child stderr marker" in captured.err


def test_supervisor_marks_forced_send_outcome_unknown(
    tmp_path: Path,
    monkeypatch,
) -> None:
    marker = tmp_path / "send-entered"
    monkeypatch.setenv("ONESTEP_DIAGNOSTIC_SEND_MARKER", str(marker))
    report = supervise_diagnostic(
        _run_request(
            "tests.assets.diagnostic_app:send_blocking_app",
            "block_during_send",
            send=True,
        ),
        timeout_s=0.4,
        grace_s=0.2,
    )
    assert marker.read_text(encoding="utf-8") == "entered"
    assert report.completion == "timed_out"
    assert report.side_effect_outcome == "unknown"
    assert "partial" in report.warning
    assert "duplicate" in report.warning


def test_supervisor_synthesizes_child_failure_without_final() -> None:
    report = supervise_diagnostic(
        _run_request("tests.assets.diagnostic_app:exit_app", "exit_without_final"),
        timeout_s=3,
        grace_s=0.2,
    )
    assert report.completion == "child_failed"
    assert report.last_checkpoint is not None
    assert report.last_checkpoint["phase"] in {"child_start", "handler"}


def test_final_frame_requires_strict_nested_report_types() -> None:
    app = OneStepApp("strict-final")

    @app.task()
    async def task(ctx, payload):
        return None

    report = asyncio.run(
        DiagnosticRunner(app).run(
            task_name="task",
            envelope=Envelope(body={}),
            send=False,
        )
    ).to_dict()
    invalid = deepcopy(report)
    invalid["events"][0]["attempts"] = True
    validator = FrameValidator(direction="status")
    with pytest.raises(IPCProtocolError, match="diagnostic result"):
        validator.accept(
            decode_frame(encode_frame("final", sequence=1, payload=invalid))
        )
