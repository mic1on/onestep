from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from threading import Lock
from time import monotonic
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.security import require_ingest_token
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db.models import Service, TaskCustomMetricWindow, TaskMetricWindow
from onestep_control_plane_api.db.session import get_db_session
from onestep_control_plane_api.ops.observability import (
    CounterSample,
    GaugeSample,
    HistogramSample,
    ObservabilitySnapshot,
    collect_prometheus_snapshot,
    ensure_event_loop_lag_sampler_started,
)

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

router = APIRouter(tags=["prometheus"], dependencies=[Depends(require_ingest_token)])


async def ensure_observability_samplers_started() -> None:
    """Lazily start the process-local observability samplers on the event loop.

    Declared as an ``async`` dependency so FastAPI runs it on the loop thread,
    where ``asyncio.Task`` creation is legal (the endpoint body itself runs in a
    threadpool because it performs blocking SQLAlchemy work). Idempotent, so it is
    safe on every scrape; the follow-up #197 wiring PR may also start the sampler
    from the application lifespan.
    """

    ensure_event_loop_lag_sampler_started()


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


@dataclass(frozen=True)
class _PrometheusCacheEntry:
    bind: object
    body: str
    expires_at: float


_prometheus_cache_lock = Lock()
_prometheus_cache: _PrometheusCacheEntry | None = None


@router.get("/metrics", include_in_schema=False)
def prometheus_metrics(
    db: Session = Depends(get_db_session),
    _: None = Depends(ensure_observability_samplers_started),
) -> Response:
    body = build_prometheus_metrics(db)
    return Response(content=body, media_type=PROMETHEUS_CONTENT_TYPE)


def reset_prometheus_metrics_cache() -> None:
    global _prometheus_cache
    with _prometheus_cache_lock:
        _prometheus_cache = None


def build_prometheus_metrics(db: Session) -> str:
    ttl = settings.prometheus_cache_ttl_s
    if ttl <= 0:
        return _compose_prometheus_metrics(_build_prometheus_metrics(db))

    global _prometheus_cache
    bind = db.get_bind()
    now = monotonic()
    with _prometheus_cache_lock:
        if (
            _prometheus_cache is not None
            and _prometheus_cache.bind is bind
            and _prometheus_cache.expires_at > now
        ):
            return _compose_prometheus_metrics(_prometheus_cache.body)
        body = _build_prometheus_metrics(db)
        _prometheus_cache = _PrometheusCacheEntry(
            bind=bind,
            body=body,
            expires_at=now + ttl,
        )
        return _compose_prometheus_metrics(body)


def _compose_prometheus_metrics(db_body: str) -> str:
    """Append the process-local observability section to the cached database body.

    Only the database-derived body is cached (``settings.prometheus_cache_ttl_s``,
    15 s by default) because it runs aggregation queries. Event-loop, pool and
    scan samples are free to read and would go stale behind that cache, so they
    are rendered fresh on every scrape.
    """

    return db_body + build_observability_metrics()


def _build_prometheus_metrics(db: Session) -> str:
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


def build_observability_metrics() -> str:
    """Render only the process-local observability metrics, with no database access.

    This is the reusable entry point for a diagnostics session: it returns the
    same text/plain 0.0.4 section that :func:`build_prometheus_metrics` embeds,
    so an operator can diff the two without scraping the authenticated endpoint.
    """

    lines: list[str] = []
    _append_observability_metrics(lines, collect_prometheus_snapshot())
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def _append_observability_metrics(lines: list[str], snapshot: ObservabilitySnapshot) -> None:
    """Append event-loop, pool and scan metric families from ``snapshot``.

    Samples are grouped per metric name so each family carries exactly one HELP
    and one TYPE line, matching the Prometheus 0.0.4 text format.
    """

    _append_grouped_families(lines, metric_type="gauge", samples=snapshot.gauges)
    _append_grouped_families(lines, metric_type="counter", samples=snapshot.counters)
    _append_histogram_families(lines, histograms=snapshot.histograms)


def _append_grouped_families(
    lines: list[str],
    *,
    metric_type: str,
    samples: Iterable[GaugeSample | CounterSample],
) -> None:
    grouped: dict[str, list[tuple[dict[str, str], float | int | None]]] = {}
    help_texts: dict[str, str] = {}
    for sample in samples:
        grouped.setdefault(sample.name, []).append((dict(sample.labels), sample.value))
        help_texts.setdefault(sample.name, sample.help_text)
    for name in sorted(grouped):
        _append_metric_family(
            lines,
            name=name,
            help_text=help_texts[name],
            metric_type=metric_type,
            samples=grouped[name],
        )


def _append_histogram_families(lines: list[str], *, histograms: Iterable[HistogramSample]) -> None:
    grouped: dict[str, list[HistogramSample]] = {}
    for histogram in histograms:
        grouped.setdefault(histogram.name, []).append(histogram)
    for name in sorted(grouped):
        family = sorted(grouped[name], key=lambda sample: sorted(sample.labels))
        lines.append(f"# HELP {name} {family[0].help_text}")
        lines.append(f"# TYPE {name} histogram")
        for histogram in family:
            for bucket, cumulative in histogram.buckets:
                labels = dict(histogram.labels)
                labels["le"] = "+Inf" if math.isinf(bucket) else _format_number(bucket)
                lines.append(
                    f"{name}_bucket{_format_labels(labels)} {cumulative}"
                )
            lines.append(
                f"{name}_sum{_format_labels(dict(histogram.labels))} "
                f"{_format_number(histogram.total)}"
            )
            lines.append(
                f"{name}_count{_format_labels(dict(histogram.labels))} {histogram.count}"
            )


def _collect_task_runtime_metrics(db: Session) -> dict[_TaskSeries, _TaskRuntimeAggregate]:
    aggregates: dict[_TaskSeries, _TaskRuntimeAggregate] = {}
    series_columns = (
        Service.name.label("service"),
        Service.environment.label("environment"),
        TaskMetricWindow.task_name.label("task"),
        TaskMetricWindow.instance_id.label("instance_id"),
    )
    totals = db.execute(
        select(
            *series_columns,
            func.sum(TaskMetricWindow.succeeded).label("succeeded"),
            func.sum(TaskMetricWindow.failed).label("failed"),
        )
        .join(Service, TaskMetricWindow.service_id == Service.id)
        .group_by(*series_columns)
    ).mappings()
    for row in totals:
        series = _TaskSeries(
            service=row["service"],
            environment=row["environment"],
            task=row["task"],
            instance_id=row["instance_id"],
        )
        aggregates[series] = _TaskRuntimeAggregate(
            succeeded=float(row["succeeded"]),
            failed=float(row["failed"]),
        )

    latest_windows = (
        select(
            *series_columns,
            TaskMetricWindow.inflight.label("inflight"),
            TaskMetricWindow.avg_duration_ms.label("avg_duration_ms"),
            func.row_number()
            .over(
                partition_by=(
                    TaskMetricWindow.service_id,
                    TaskMetricWindow.task_name,
                    TaskMetricWindow.instance_id,
                ),
                order_by=(
                    TaskMetricWindow.window_ended_at.desc(),
                    TaskMetricWindow.created_at.desc(),
                ),
            )
            .label("series_rank"),
        )
        .join(Service, TaskMetricWindow.service_id == Service.id)
        .subquery()
    )
    latest_rows = db.execute(
        select(latest_windows).where(latest_windows.c.series_rank == 1)
    ).mappings()
    for row in latest_rows:
        series = _TaskSeries(
            service=row["service"],
            environment=row["environment"],
            task=row["task"],
            instance_id=row["instance_id"],
        )
        aggregate = aggregates.setdefault(series, _TaskRuntimeAggregate())
        aggregate.latest_inflight = float(row["inflight"])
        aggregate.latest_avg_duration_ms = row["avg_duration_ms"]
    return aggregates


def _collect_custom_metrics(db: Session) -> dict[_CustomSeries, _CustomAggregate]:
    aggregates: dict[_CustomSeries, _CustomAggregate] = {}
    series_columns = (
        Service.name.label("service"),
        Service.environment.label("environment"),
        TaskCustomMetricWindow.task_name.label("task"),
        TaskCustomMetricWindow.instance_id.label("instance_id"),
        TaskCustomMetricWindow.metric_name.label("metric"),
        TaskCustomMetricWindow.metric_kind.label("kind"),
        TaskCustomMetricWindow.labels_json.label("labels_json"),
    )
    counter_totals = db.execute(
        select(
            *series_columns,
            func.sum(TaskCustomMetricWindow.metric_value).label("total"),
        )
        .join(Service, TaskCustomMetricWindow.service_id == Service.id)
        .where(TaskCustomMetricWindow.metric_kind == "counter")
        .group_by(*series_columns)
    ).mappings()
    for row in counter_totals:
        labels = tuple(
            sorted(
                (str(key), str(value)) for key, value in row["labels_json"].items()
            )
        )
        series = _CustomSeries(
            service=row["service"],
            environment=row["environment"],
            task=row["task"],
            instance_id=row["instance_id"],
            metric=row["metric"],
            kind=row["kind"],
            labels=labels,
        )
        aggregates[series] = _CustomAggregate(kind="counter", total=float(row["total"]))

    latest_windows = (
        select(
            *series_columns,
            TaskCustomMetricWindow.metric_value.label("metric_value"),
            func.row_number()
            .over(
                partition_by=(
                    TaskCustomMetricWindow.service_id,
                    TaskCustomMetricWindow.task_name,
                    TaskCustomMetricWindow.instance_id,
                    TaskCustomMetricWindow.metric_name,
                    TaskCustomMetricWindow.metric_kind,
                    TaskCustomMetricWindow.labels_json,
                ),
                order_by=(
                    TaskCustomMetricWindow.window_ended_at.desc(),
                    TaskCustomMetricWindow.created_at.desc(),
                ),
            )
            .label("series_rank"),
        )
        .join(Service, TaskCustomMetricWindow.service_id == Service.id)
        .where(TaskCustomMetricWindow.metric_kind == "gauge")
        .subquery()
    )
    latest_rows = db.execute(
        select(latest_windows).where(latest_windows.c.series_rank == 1)
    ).mappings()
    for row in latest_rows:
        labels = tuple(
            sorted((str(key), str(value)) for key, value in row["labels_json"].items())
        )
        series = _CustomSeries(
            service=row["service"],
            environment=row["environment"],
            task=row["task"],
            instance_id=row["instance_id"],
            metric=row["metric"],
            kind=row["kind"],
            labels=labels,
        )
        aggregates[series] = _CustomAggregate(
            kind="gauge",
            latest_value=float(row["metric_value"]),
        )
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
