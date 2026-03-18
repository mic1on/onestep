from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from onestep import (
    ControlPlaneReporter,
    ControlPlaneReporterConfig,
    FailureInfo,
    FailureKind,
    IntervalSource,
    MaxAttempts,
    MemoryQueue,
    OneStepApp,
    TaskEvent,
    TaskEventKind,
)


@dataclass
class SenderRecorder:
    calls: list[tuple[str, dict]] = field(default_factory=list)

    async def __call__(self, channel: str, payload: dict) -> None:
        self.calls.append((channel, payload))


def _make_config() -> ControlPlaneReporterConfig:
    return ControlPlaneReporterConfig(
        base_url="https://control-plane.example.com",
        token="secret-token",
        environment="prod",
        service_name="billing-sync",
        node_name="vm-prod-3",
        deployment_version="1.0.0+c435c99",
        instance_id=UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df"),
        heartbeat_interval_s=3600.0,
        metrics_interval_s=3600.0,
        event_flush_interval_s=3600.0,
        event_batch_size=10,
        max_pending_events=100,
        max_pending_metric_batches=10,
        timeout_s=3.0,
        reconnect_base_delay_s=0.01,
        reconnect_max_delay_s=0.02,
    )


def test_reporter_startup_sends_heartbeat() -> None:
    recorder = SenderRecorder()
    app = OneStepApp("billing-sync")
    reporter = ControlPlaneReporter(_make_config(), sender=recorder)
    reporter.attach(app)

    async def scenario() -> None:
        await app.startup()
        await app.shutdown()

    asyncio.run(scenario())

    assert [channel for channel, _ in recorder.calls[:2]] == [
        "heartbeat",
        "sync",
    ]
    heartbeat_payload = recorder.calls[0][1]
    sync_payload = recorder.calls[1][1]

    assert heartbeat_payload["service"] == {
        "name": "billing-sync",
        "environment": "prod",
        "node_name": "vm-prod-3",
        "instance_id": UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df"),
        "deployment_version": "1.0.0+c435c99",
    }
    assert heartbeat_payload["health"]["status"] == "ok"
    assert heartbeat_payload["health"]["task_controls"] == []
    assert heartbeat_payload["sequence"] == 1
    assert heartbeat_payload["runtime"]["pid"] > 0
    assert sync_payload["app"] == {
        "name": "billing-sync",
        "shutdown_timeout_s": 30.0,
        "tasks": [],
        "topology_hash": sync_payload["app"]["topology_hash"],
    }
    assert sync_payload["sequence"] == 2
    assert sync_payload["app"]["topology_hash"].startswith("sha256:")


def test_reporter_sync_payload_includes_task_topology() -> None:
    recorder = SenderRecorder()
    source = IntervalSource.every(hours=1, immediate=True, overlap="skip")
    sink = MemoryQueue("results", maxsize=50, batch_size=25, poll_interval_s=0.2)
    app = OneStepApp("billing-sync")

    @app.task(
        source=source,
        emit=sink,
        concurrency=16,
        timeout_s=30.0,
        retry=MaxAttempts(max_attempts=5, delay_s=10.0),
        description="Sync billing users from the scheduled source into the results queue.",
    )
    async def sync_users(ctx, payload):
        return payload

    reporter = ControlPlaneReporter(_make_config(), sender=recorder)
    reporter.attach(app)

    async def scenario() -> None:
        await app.startup()
        await app.shutdown()

    asyncio.run(scenario())

    sync_calls = [payload for channel, payload in recorder.calls if channel == "sync"]
    assert len(sync_calls) == 1
    sync_payload = sync_calls[0]
    assert sync_payload["app"]["name"] == "billing-sync"
    assert sync_payload["app"]["shutdown_timeout_s"] == 30.0
    assert sync_payload["app"]["topology_hash"].startswith("sha256:")
    assert sync_payload["app"]["tasks"] == [
        {
            "name": "sync_users",
            "description": "Sync billing users from the scheduled source into the results queue.",
            "source": {
                "kind": "interval",
                "name": "interval:3600s",
                "config": {
                    "seconds": 3600,
                    "immediate": True,
                    "overlap": "skip",
                    "timezone": str(source.timezone),
                    "poll_interval_s": source.poll_interval_s,
                },
            },
            "emit": [
                {
                    "kind": "memory_queue",
                    "name": "results",
                    "config": {
                        "maxsize": 50,
                        "batch_size": 25,
                        "poll_interval_s": 0.2,
                    },
                }
            ],
            "concurrency": 16,
            "timeout_s": 30.0,
            "retry": {
                "kind": "max_attempts",
                "config": {
                    "max_attempts": 5,
                    "delay_s": 10.0,
                },
            },
        }
    ]


def test_reporter_heartbeat_includes_task_control_states() -> None:
    recorder = SenderRecorder()
    source = MemoryQueue("incoming")
    sink = MemoryQueue("processed")
    app = OneStepApp("billing-sync")

    @app.task(source=source, emit=sink)
    async def sync_users(ctx, payload):
        return payload

    reporter = ControlPlaneReporter(_make_config(), sender=recorder)
    reporter.attach(app)

    async def scenario() -> None:
        await app.startup()
        app.request_task_pause("sync_users")
        await reporter.send_heartbeat_now()
        await app.shutdown()

    asyncio.run(scenario())

    heartbeat_payload = [payload for channel, payload in recorder.calls if channel == "heartbeat"][-1]
    assert heartbeat_payload["health"]["task_controls"] == [
        {
            "task_name": "sync_users",
            "supported_commands": [
                "pause_task",
                "resume_task",
            ],
            "pause_requested": True,
            "paused": True,
            "accepting_new_work": False,
            "runner_count": 0,
            "parked_runner_count": 0,
            "fetching_runner_count": 0,
            "inflight_task_count": 0,
        }
    ]


def test_reporter_flushes_metrics_and_events() -> None:
    recorder = SenderRecorder()
    app = OneStepApp("billing-sync")
    reporter = ControlPlaneReporter(_make_config(), sender=recorder)
    reporter.attach(app)

    async def scenario() -> None:
        await app.startup()
        await app.emit_event(
            TaskEvent(
                kind=TaskEventKind.FETCHED,
                app=app.name,
                task="sync_users",
                source="interval:3600s",
                attempts=0,
            )
        )
        await app.emit_event(
            TaskEvent(
                kind=TaskEventKind.STARTED,
                app=app.name,
                task="sync_users",
                source="interval:3600s",
                attempts=0,
            )
        )
        await app.emit_event(
            TaskEvent(
                kind=TaskEventKind.RETRIED,
                app=app.name,
                task="sync_users",
                source="interval:3600s",
                attempts=1,
                duration_s=0.25,
                failure=FailureInfo(
                    kind=FailureKind.TIMEOUT,
                    exception_type="TimeoutError",
                    message="task exceeded timeout",
                    traceback=(
                        "Traceback (most recent call last):\n"
                        "TimeoutError: task exceeded timeout\n"
                    ),
                ),
                meta={"trace_id": "trace-1"},
            )
        )
        reporter._rotate_metrics_window(datetime(2026, 3, 8, 17, 31, 0, tzinfo=timezone.utc))
        await reporter._flush_metric_batches()
        await reporter._flush_event_batches()
        await reporter._safe_send_heartbeat()
        await app.shutdown()

    asyncio.run(scenario())

    metrics_calls = [payload for channel, payload in recorder.calls if channel == "metrics"]
    events_calls = [payload for channel, payload in recorder.calls if channel == "events"]
    heartbeat_calls = [payload for channel, payload in recorder.calls if channel == "heartbeat"]

    assert len(metrics_calls) == 1
    assert len(events_calls) == 1
    assert len(heartbeat_calls) >= 2

    metrics_payload = metrics_calls[0]
    assert metrics_payload["tasks"] == [
        {
            "task_name": "sync_users",
            "window_id": metrics_payload["tasks"][0]["window_id"],
            "fetched": 1,
            "started": 1,
            "succeeded": 0,
            "retried": 1,
            "failed": 0,
            "dead_lettered": 0,
            "cancelled": 0,
            "timeouts": 1,
            "inflight": 0,
            "avg_duration_ms": 250.0,
            "p95_duration_ms": 250.0,
        }
    ]
    assert metrics_payload["window"]["ended_at"] == datetime(
        2026, 3, 8, 17, 31, 0, tzinfo=timezone.utc
    )

    event_payload = events_calls[0]["events"][0]
    assert event_payload["kind"] == "retried"
    assert event_payload["task_name"] == "sync_users"
    assert event_payload["failure"] == {
        "kind": "timeout",
        "exception_type": "TimeoutError",
        "message": "task exceeded timeout",
        "traceback": (
            "Traceback (most recent call last):\n"
            "TimeoutError: task exceeded timeout\n"
        ),
    }
    assert event_payload["meta"] == {
        "trace_id": "trace-1",
        "source": "interval:3600s",
    }
    assert event_payload["event_id"].startswith("8f9f0d7c4b4a4a588a6f52d6735f44df-")

    assert heartbeat_calls[-1]["health"]["status"] == "degraded"


def test_reporter_drops_oldest_events_when_pending_buffer_overflows() -> None:
    recorder = SenderRecorder()
    config = _make_config()
    config.max_pending_events = 2
    app = OneStepApp("billing-sync")
    reporter = ControlPlaneReporter(config, sender=recorder)
    reporter.attach(app)

    async def scenario() -> None:
        await app.startup()
        for attempts in (1, 2, 3):
            await app.emit_event(
                TaskEvent(
                    kind=TaskEventKind.FAILED,
                    app=app.name,
                    task="sync_users",
                    source="interval:3600s",
                    attempts=attempts,
                    failure=FailureInfo(
                        kind=FailureKind.ERROR,
                        exception_type="RuntimeError",
                        message=f"failed-{attempts}",
                        traceback="traceback",
                    ),
                )
            )
        await app.shutdown()

    asyncio.run(scenario())

    assert len(reporter._pending_events) == 0
    events_payload = next(payload for channel, payload in recorder.calls if channel == "events")
    assert [event["attempts"] for event in events_payload["events"]] == [2, 3]


def test_reporter_drops_oldest_metric_batches_when_pending_buffer_overflows() -> None:
    recorder = SenderRecorder()
    config = _make_config()
    config.max_pending_metric_batches = 2
    app = OneStepApp("billing-sync")
    reporter = ControlPlaneReporter(config, sender=recorder)
    reporter.attach(app)

    async def scenario() -> None:
        await app.startup()
        await app.emit_event(
            TaskEvent(
                kind=TaskEventKind.FETCHED,
                app=app.name,
                task="sync_users",
                source="interval:3600s",
                attempts=0,
            )
        )
        reporter._rotate_metrics_window(datetime(2026, 3, 8, 17, 31, 0, tzinfo=timezone.utc))
        await app.emit_event(
            TaskEvent(
                kind=TaskEventKind.FETCHED,
                app=app.name,
                task="sync_users",
                source="interval:3600s",
                attempts=0,
            )
        )
        reporter._rotate_metrics_window(datetime(2026, 3, 8, 17, 32, 0, tzinfo=timezone.utc))
        await app.emit_event(
            TaskEvent(
                kind=TaskEventKind.FETCHED,
                app=app.name,
                task="sync_users",
                source="interval:3600s",
                attempts=0,
            )
        )
        reporter._rotate_metrics_window(datetime(2026, 3, 8, 17, 33, 0, tzinfo=timezone.utc))

    asyncio.run(scenario())

    assert len(reporter._pending_metric_batches) == 2
    assert reporter._pending_metric_batches[0].window_ended_at == datetime(
        2026, 3, 8, 17, 32, 0, tzinfo=timezone.utc
    )
    assert reporter._pending_metric_batches[1].window_ended_at == datetime(
        2026, 3, 8, 17, 33, 0, tzinfo=timezone.utc
    )


def test_reporter_direct_sync_raises_when_sender_fails() -> None:
    class FailingSender:
        async def __call__(self, channel: str, payload: dict) -> None:
            raise RuntimeError(f"failed-{channel}")

    app = OneStepApp("billing-sync")
    reporter = ControlPlaneReporter(_make_config(), sender=FailingSender())
    reporter.attach(app)

    async def scenario() -> None:
        await app.startup()
        try:
            await reporter.send_sync_now()
        finally:
            await app.shutdown()

    try:
        asyncio.run(scenario())
    except RuntimeError as exc:
        assert str(exc) == "failed-sync"
    else:  # pragma: no cover - defensive assertion for the async wrapper
        raise AssertionError("expected reporter.send_sync_now() to propagate sender failures")


def test_reporter_config_from_env_uses_fallback_names(monkeypatch) -> None:
    monkeypatch.setenv("ONESTEP_CONTROL_URL", "https://control-plane.example.com")
    monkeypatch.setenv("ONESTEP_CONTROL_TOKEN", "secret-token")
    monkeypatch.setenv("ONESTEP_ENV", "staging")
    monkeypatch.setenv("ONESTEP_SERVICE_NAME", "payments-sync")
    monkeypatch.setenv("ONESTEP_NODE_NAME", "node-a")
    monkeypatch.setenv("ONESTEP_DEPLOYMENT_VERSION", "2026.03.08")
    monkeypatch.setenv("ONESTEP_INSTANCE_ID", "f13b655a-b4c9-4b69-a8d4-3027f3fa7415")
    monkeypatch.setenv("ONESTEP_CONTROL_PLANE_HEARTBEAT_INTERVAL_S", "15")
    monkeypatch.setenv("ONESTEP_CONTROL_PLANE_METRICS_INTERVAL_S", "45")
    monkeypatch.setenv("ONESTEP_CONTROL_PLANE_EVENT_FLUSH_INTERVAL_S", "7")
    monkeypatch.setenv("ONESTEP_CONTROL_PLANE_EVENT_BATCH_SIZE", "50")
    monkeypatch.setenv("ONESTEP_CONTROL_PLANE_TIMEOUT_S", "4")

    config = ControlPlaneReporterConfig.from_env(app_name="ignored-app")

    assert config.base_url == "https://control-plane.example.com"
    assert config.token == "secret-token"
    assert config.environment == "staging"
    assert config.service_name == "payments-sync"
    assert config.node_name == "node-a"
    assert config.deployment_version == "2026.03.08"
    assert config.instance_id == UUID("f13b655a-b4c9-4b69-a8d4-3027f3fa7415")
    assert config.heartbeat_interval_s == 15.0
    assert config.metrics_interval_s == 45.0
    assert config.event_flush_interval_s == 7.0
    assert config.event_batch_size == 50
    assert config.timeout_s == 4.0
