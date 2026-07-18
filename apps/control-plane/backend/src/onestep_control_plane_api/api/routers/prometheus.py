from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.security import require_ingest_token
from onestep_control_plane_api.db.models import Service, TaskCustomMetricWindow, TaskMetricWindow
from onestep_control_plane_api.db.session import get_db_session

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

router = APIRouter(tags=["prometheus"], dependencies=[Depends(require_ingest_token)])


@dataclass(frozen=True)
class _TaskSeries:
    service: str
    environment: str
    task: str
    instance_id: UUID


@dataclass
class _TaskRuntimeAggregate:
    succeeded: float = 0.0
    failed: float = 0.0
    latest_inflight: float = 0.0
    latest_avg_duration_ms: float | None = None
    latest_sort_key: tuple[object, object] | None = None


@dataclass(frozen=True)
class _CustomSeries:
    service: str
    environment: str
    task: str
    instance_id: UUID
    metric: str
    kind: str
    labels: tuple[tuple[str, str], ...]


@dataclass
class _CustomAggregate:
    kind: str
    total: float = 0.0
    latest_value: float = 0.0
    latest_sort_key: tuple[object, object] | None = None


@router.get("/metrics", include_in_schema=False)
def prometheus_metrics(db: Session = Depends(get_db_session)) -> Response:
    body = build_prometheus_metrics(db)
    return Response(content=body, media_type=PROMETHEUS_CONTENT_TYPE)


def build_prometheus_metrics(db: Session) -> str:
    runtime_metrics = _collect_task_runtime_metrics(db)
    custom_metrics = _collect_custom_metrics(db)
    lines: list[str] = []
    _append_metric_family(
        lines,
        name="onestep_task_succeeded_total",
        help_text="OneStep task deliveries that completed successfully.",
        metric_type="counter",
        samples=[
            (_runtime_labels(series), aggregate.succeeded)
            for series, aggregate in runtime_metrics.items()
        ],
    )
    _append_metric_family(
        lines,
        name="onestep_task_failed_total",
        help_text="OneStep task deliveries that failed after retry policy handling.",
        metric_type="counter",
        samples=[
            (_runtime_labels(series), aggregate.failed)
            for series, aggregate in runtime_metrics.items()
        ],
    )
    _append_metric_family(
        lines,
        name="onestep_task_inflight",
        help_text="Latest OneStep inflight delivery count reported by a task instance.",
        metric_type="gauge",
        samples=[
            (_runtime_labels(series), aggregate.latest_inflight)
            for series, aggregate in runtime_metrics.items()
        ],
    )
    _append_metric_family(
        lines,
        name="onestep_task_duration_avg_ms",
        help_text="Latest OneStep average task duration in milliseconds.",
        metric_type="gauge",
        samples=[
            (_runtime_labels(series), aggregate.latest_avg_duration_ms)
            for series, aggregate in runtime_metrics.items()
            if aggregate.latest_avg_duration_ms is not None
        ],
    )
    _append_metric_family(
        lines,
        name="onestep_task_custom_counter_total",
        help_text="Custom OneStep counter metrics reported by task handlers.",
        metric_type="counter",
        samples=[
            (_custom_labels(series), aggregate.total)
            for series, aggregate in custom_metrics.items()
            if aggregate.kind == "counter"
        ],
    )
    _append_metric_family(
        lines,
        name="onestep_task_custom_gauge",
        help_text="Latest custom OneStep gauge metrics reported by task handlers.",
        metric_type="gauge",
        samples=[
            (_custom_labels(series), aggregate.latest_value)
            for series, aggregate in custom_metrics.items()
            if aggregate.kind == "gauge"
        ],
    )
    return "\n".join(lines) + "\n"


def _collect_task_runtime_metrics(db: Session) -> dict[_TaskSeries, _TaskRuntimeAggregate]:
    aggregates: dict[_TaskSeries, _TaskRuntimeAggregate] = {}
    rows = db.execute(
        select(TaskMetricWindow, Service).join(Service, TaskMetricWindow.service_id == Service.id)
    )
    for metric_window, service in rows:
        series = _TaskSeries(
            service=service.name,
            environment=service.environment,
            task=metric_window.task_name,
            instance_id=metric_window.instance_id,
        )
        aggregate = aggregates.setdefault(series, _TaskRuntimeAggregate())
        aggregate.succeeded += metric_window.succeeded
        aggregate.failed += metric_window.failed
        sort_key = (metric_window.window_ended_at, metric_window.created_at)
        if aggregate.latest_sort_key is None or sort_key > aggregate.latest_sort_key:
            aggregate.latest_sort_key = sort_key
            aggregate.latest_inflight = metric_window.inflight
            aggregate.latest_avg_duration_ms = metric_window.avg_duration_ms
    return aggregates


def _collect_custom_metrics(db: Session) -> dict[_CustomSeries, _CustomAggregate]:
    aggregates: dict[_CustomSeries, _CustomAggregate] = {}
    rows = db.execute(
        select(TaskCustomMetricWindow, Service).join(
            Service,
            TaskCustomMetricWindow.service_id == Service.id,
        )
    )
    for metric_window, service in rows:
        labels = tuple(
            sorted(
                (str(key), str(value))
                for key, value in metric_window.labels_json.items()
            )
        )
        series = _CustomSeries(
            service=service.name,
            environment=service.environment,
            task=metric_window.task_name,
            instance_id=metric_window.instance_id,
            metric=metric_window.metric_name,
            kind=metric_window.metric_kind,
            labels=labels,
        )
        aggregate = aggregates.setdefault(
            series,
            _CustomAggregate(kind=metric_window.metric_kind),
        )
        if metric_window.metric_kind == "counter":
            aggregate.total += metric_window.metric_value
        sort_key = (metric_window.window_ended_at, metric_window.created_at)
        if aggregate.latest_sort_key is None or sort_key > aggregate.latest_sort_key:
            aggregate.latest_sort_key = sort_key
            aggregate.latest_value = metric_window.metric_value
    return aggregates


def _runtime_labels(series: _TaskSeries) -> dict[str, str]:
    return {
        "service": series.service,
        "environment": series.environment,
        "task": series.task,
        "instance_id": str(series.instance_id),
    }


def _custom_labels(series: _CustomSeries) -> dict[str, str]:
    labels = _runtime_labels(
        _TaskSeries(
            service=series.service,
            environment=series.environment,
            task=series.task,
            instance_id=series.instance_id,
        )
    )
    labels["metric"] = series.metric
    labels.update(dict(series.labels))
    return labels


def _append_metric_family(
    lines: list[str],
    *,
    name: str,
    help_text: str,
    metric_type: str,
    samples: Iterable[tuple[dict[str, str], float | int | None]],
) -> None:
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} {metric_type}")
    for labels, value in sorted(samples, key=lambda sample: sorted(sample[0].items())):
        if value is None:
            continue
        lines.append(f"{name}{_format_labels(labels)} {_format_number(float(value))}")


def _format_labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    formatted = ",".join(
        f'{key}="{_escape_label_value(value)}"' for key, value in sorted(labels.items())
    )
    return f"{{{formatted}}}"


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return repr(value)
