from __future__ import annotations

import asyncio
from typing import Optional

from onestep.events import TaskEvent, TaskEventKind
from onestep.metrics import CustomMetricsRegistry
from onestep.observability import PrometheusExporter
from onestep.retry import FailureInfo, FailureKind


def make_event(
    kind: TaskEventKind,
    *,
    app: str = "billing",
    task: str = "sync",
    source: str = "incoming",
    attempts: int = 0,
    duration_s: Optional[float] = None,
    failure: Optional[FailureInfo] = None,
) -> TaskEvent:
    return TaskEvent(
        kind=kind,
        app=app,
        task=task,
        source=source,
        attempts=attempts,
        duration_s=duration_s,
        failure=failure,
    )


def render_lines(exporter: PrometheusExporter) -> list[str]:
    return [
        line
        for line in exporter.render().splitlines()
        if line and not line.startswith("#")
    ]


def test_exporter_counts_succeeded_tasks_with_status_label() -> None:
    exporter = PrometheusExporter(app_name="billing")

    exporter(make_event(TaskEventKind.SUCCEEDED, duration_s=0.25))

    assert (
        'onestep_tasks_processed_total{app="billing",task="sync",status="succeeded"} 1'
        in render_lines(exporter)
    )


def test_exporter_declares_counter_type_metadata() -> None:
    exporter = PrometheusExporter(app_name="billing")

    exporter(make_event(TaskEventKind.SUCCEEDED, duration_s=0.25))
    body = exporter.render()

    assert "# TYPE onestep_tasks_processed_total counter" in body
    assert "# HELP onestep_tasks_processed_total" in body


def test_exporter_observes_duration_histogram() -> None:
    exporter = PrometheusExporter(app_name="billing")

    exporter(make_event(TaskEventKind.SUCCEEDED, duration_s=0.3))
    lines = render_lines(exporter)

    assert (
        'onestep_task_duration_seconds_bucket{app="billing",task="sync",le="0.25"} 0'
        in lines
    )
    assert (
        'onestep_task_duration_seconds_bucket{app="billing",task="sync",le="0.5"} 1'
        in lines
    )
    assert (
        'onestep_task_duration_seconds_bucket{app="billing",task="sync",le="+Inf"} 1'
        in lines
    )
    assert 'onestep_task_duration_seconds_count{app="billing",task="sync"} 1' in lines
    assert 'onestep_task_duration_seconds_sum{app="billing",task="sync"} 0.3' in lines


def test_exporter_tracks_inflight_gauge_from_lifecycle_events() -> None:
    exporter = PrometheusExporter(app_name="billing")

    exporter(make_event(TaskEventKind.STARTED))
    exporter(make_event(TaskEventKind.STARTED))
    assert 'onestep_inflight_tasks{app="billing",task="sync"} 2' in render_lines(exporter)

    exporter(make_event(TaskEventKind.SUCCEEDED, duration_s=0.1))
    assert 'onestep_inflight_tasks{app="billing",task="sync"} 1' in render_lines(exporter)


def test_exporter_inflight_gauge_never_goes_negative() -> None:
    exporter = PrometheusExporter(app_name="billing")

    exporter(make_event(TaskEventKind.SUCCEEDED, duration_s=0.1))

    assert 'onestep_inflight_tasks{app="billing",task="sync"} 0' in render_lines(exporter)


def test_exporter_counts_retries_dead_letters_and_failure_kinds() -> None:
    exporter = PrometheusExporter(app_name="billing")

    exporter(make_event(TaskEventKind.RETRIED, duration_s=0.2))
    exporter(make_event(TaskEventKind.DEAD_LETTERED))
    exporter(
        make_event(
            TaskEventKind.FAILED,
            duration_s=0.2,
            failure=FailureInfo(
                kind=FailureKind.TIMEOUT,
                exception_type="TimeoutError",
                message="slow",
            ),
        )
    )
    exporter(make_event(TaskEventKind.CANCELLED))
    exporter(make_event(TaskEventKind.FETCHED))
    lines = render_lines(exporter)

    assert 'onestep_tasks_retried_total{app="billing",task="sync"} 1' in lines
    assert 'onestep_tasks_dead_lettered_total{app="billing",task="sync"} 1' in lines
    assert 'onestep_tasks_cancelled_total{app="billing",task="sync"} 1' in lines
    assert 'onestep_deliveries_fetched_total{app="billing",task="sync"} 1' in lines
    assert (
        'onestep_tasks_processed_total{app="billing",task="sync",status="failed"} 1'
        in lines
    )
    assert (
        'onestep_task_failures_total{app="billing",task="sync",failure_kind="timeout"} 1'
        in lines
    )


def test_exporter_keeps_processed_total_free_of_double_counting() -> None:
    """A dead-lettered delivery emits DEAD_LETTERED and then FAILED (see
    DeliveryExecutor._handle_failure). processed_total must count that as one
    terminal outcome, not two."""
    exporter = PrometheusExporter(app_name="billing")

    exporter(make_event(TaskEventKind.STARTED))
    exporter(make_event(TaskEventKind.DEAD_LETTERED))
    exporter(
        make_event(
            TaskEventKind.FAILED,
            duration_s=0.1,
            failure=FailureInfo(
                kind=FailureKind.ERROR,
                exception_type="ValueError",
                message="boom",
            ),
        )
    )
    lines = render_lines(exporter)
    processed = [line for line in lines if line.startswith("onestep_tasks_processed_total")]

    assert processed == [
        'onestep_tasks_processed_total{app="billing",task="sync",status="failed"} 1'
    ]


def test_exporter_exposes_build_info() -> None:
    from onestep import __version__

    exporter = PrometheusExporter(app_name="billing")

    assert (
        f'onestep_build_info{{version="{__version__}"}} 1' in render_lines(exporter)
    )


def test_exporter_escapes_label_values() -> None:
    exporter = PrometheusExporter(app_name='we"ird\napp')

    exporter(make_event(TaskEventKind.STARTED, app='we"ird\napp', task="back\\slash"))
    body = exporter.render()

    assert 'app="we\\"ird\\napp"' in body
    assert 'task="back\\\\slash"' in body


def test_exporter_renders_custom_metrics_with_task_label() -> None:
    registry = CustomMetricsRegistry()
    exporter = PrometheusExporter(app_name="billing", custom_metrics=registry)
    metrics = registry.for_task("sync")
    metrics.counter("rows_synced", labels={"table": "orders"}).inc(3)
    metrics.gauge("queue_depth").set(7)

    lines = render_lines(exporter)

    assert 'rows_synced{task="sync",table="orders"} 3' in lines
    assert 'queue_depth{task="sync"} 7' in lines


def test_custom_metric_counters_stay_monotonic_across_control_plane_rotation() -> None:
    """rotate_task() resets counters because the control-plane reporter reports
    per-window deltas. Prometheus counters must not go backwards, so the
    non-destructive snapshot has to keep cumulative totals."""
    registry = CustomMetricsRegistry()
    exporter = PrometheusExporter(app_name="billing", custom_metrics=registry)
    counter = registry.for_task("sync").counter("rows_synced")

    counter.inc(2)
    registry.rotate_task("sync")
    counter.inc(3)

    assert 'rows_synced{task="sync"} 5' in render_lines(exporter)


def test_custom_metrics_snapshot_does_not_reset_reporter_window() -> None:
    registry = CustomMetricsRegistry()
    registry.for_task("sync").counter("rows_synced").inc(4)

    registry.snapshot()

    assert registry.rotate_task("sync") == [
        {"name": "rows_synced", "kind": "counter", "value": 4, "labels": {}}
    ]


def test_exporter_ignores_unknown_event_kinds_without_raising() -> None:
    exporter = PrometheusExporter(app_name="billing")

    async def scenario() -> None:
        await asyncio.sleep(0)

    asyncio.run(scenario())
    exporter(make_event(TaskEventKind.FETCHED))

    assert exporter.render().endswith("\n")
