from datetime import UTC, datetime
from uuid import UUID

from onestep_control_plane_api.api.schemas import TaskMetricWindowSummary
from onestep_control_plane_api.core.settings import settings


def build_metric_window_summary() -> TaskMetricWindowSummary:
    return TaskMetricWindowSummary(
        instance_id=UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df"),
        task_name="sync_user",
        window_id="sync_user:2026-03-17T05:35:19+00:00:2026-03-17T05:35:49+00:00",
        window_started_at=datetime(2026, 3, 17, 5, 35, 19, 678056, tzinfo=UTC),
        window_ended_at=datetime(2026, 3, 17, 5, 35, 49, 771325, tzinfo=UTC),
        fetched=1,
        started=1,
        succeeded=1,
        retried=0,
        failed=0,
        dead_lettered=0,
        cancelled=0,
        timeouts=0,
        inflight=0,
        avg_duration_ms=57123.0,
        p95_duration_ms=57123.0,
        received_at=datetime(2026, 3, 17, 5, 35, 50, tzinfo=UTC),
        created_at=datetime(2026, 3, 17, 5, 35, 50, 100000, tzinfo=UTC),
    )


def test_api_model_serializes_datetimes_using_configured_response_timezone(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_response_timezone", "Asia/Shanghai")
    summary = build_metric_window_summary()

    payload = summary.model_dump(mode="json")

    assert payload["window_started_at"] == "2026-03-17T13:35:19.678056+08:00"
    assert payload["window_ended_at"] == "2026-03-17T13:35:49.771325+08:00"
    assert payload["received_at"] == "2026-03-17T13:35:50+08:00"
    assert payload["created_at"] == "2026-03-17T13:35:50.100000+08:00"


def test_api_model_falls_back_to_tz_env_when_response_timezone_is_empty(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_response_timezone", "")
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    summary = build_metric_window_summary()

    payload = summary.model_dump(mode="json")

    assert payload["window_started_at"] == "2026-03-17T13:35:19.678056+08:00"
