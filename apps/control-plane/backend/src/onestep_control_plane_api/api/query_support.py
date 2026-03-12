from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.constants import HEALTH_STATUS_VALUES, TASK_EVENT_KIND_VALUES
from onestep_control_plane_api.api.schemas import (
    ConnectorDescriptor,
    Environment,
    InstanceConnectivity,
    InstanceConnectivityCounts,
    InstanceStatusCounts,
    InstanceSummary,
    RetryDescriptor,
    ServiceSummary,
    TaskDashboardSummary,
    TaskEventCounts,
    TaskEventSummary,
    TaskMetricWindowSummary,
)
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db.models import (
    Instance,
    Service,
    TaskDefinition,
    TaskEvent,
    TaskMetricWindow,
)


def online_cutoff(now: datetime) -> datetime:
    return now - timedelta(seconds=settings.instance_offline_after_s)


def get_service_by_name_and_environment(
    db: Session,
    *,
    service_name: str,
    environment: Environment,
) -> Service | None:
    return db.scalar(
        select(Service).where(
            Service.name == service_name,
            Service.environment == environment,
        )
    )


def get_service_or_404(
    db: Session,
    *,
    service_name: str,
    environment: Environment,
) -> Service:
    service = get_service_by_name_and_environment(
        db,
        service_name=service_name,
        environment=environment,
    )
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"service {service_name}/{environment} was not found",
        )
    return service


def get_service_instance_or_404(
    db: Session,
    *,
    service: Service,
    service_name: str,
    environment: Environment,
    instance_id: UUID,
) -> Instance:
    instance = db.scalar(
        select(Instance).where(
            Instance.service_id == service.id,
            Instance.instance_id == instance_id,
        )
    )
    if instance is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"instance {instance_id} was not found for service "
                f"{service_name}/{environment}"
            ),
        )
    return instance


def online_instance_count_expression(cutoff: datetime):
    return func.coalesce(
        func.sum(
            case(
                (
                    and_(Instance.last_seen_at.is_not(None), Instance.last_seen_at >= cutoff),
                    1,
                ),
                else_=0,
            )
        ),
        0,
    )


def build_service_stats_subquery(cutoff: datetime):
    return (
        select(
            Instance.service_id.label("service_id"),
            func.count(Instance.id).label("instance_count"),
            online_instance_count_expression(cutoff).label("online_instance_count"),
            func.max(Instance.last_seen_at).label("last_seen_at"),
        )
        .group_by(Instance.service_id)
        .subquery()
    )


def build_service_summary(
    service: Service,
    *,
    instance_count: int,
    online_instance_count: int,
    last_seen_at: datetime | None,
) -> ServiceSummary:
    return ServiceSummary(
        name=service.name,
        environment=service.environment,
        latest_deployment_version=service.latest_deployment_version,
        latest_topology_hash=service.latest_topology_hash,
        latest_sync_at=service.latest_sync_at,
        instance_count=instance_count,
        online_instance_count=online_instance_count,
        last_seen_at=last_seen_at,
        created_at=service.created_at,
        updated_at=service.updated_at,
    )


def get_instance_connectivity(
    instance: Instance,
    *,
    cutoff: datetime,
) -> InstanceConnectivity:
    if instance.last_seen_at is None:
        return "never_reported"
    if instance.last_seen_at >= cutoff:
        return "online"
    return "offline"


def build_instance_summary(
    instance: Instance,
    *,
    cutoff: datetime,
) -> InstanceSummary:
    return InstanceSummary(
        instance_id=instance.instance_id,
        node_name=instance.node_name,
        hostname=instance.hostname,
        pid=instance.pid,
        deployment_version=instance.deployment_version,
        onestep_version=instance.onestep_version,
        python_version=instance.python_version,
        started_at=instance.started_at,
        last_sync_at=instance.last_sync_at,
        last_topology_hash=instance.last_topology_hash,
        last_heartbeat_sent_at=instance.last_heartbeat_sent_at,
        last_heartbeat_sequence=instance.last_heartbeat_sequence,
        last_seen_at=instance.last_seen_at,
        status=instance.status,
        connectivity=get_instance_connectivity(instance, cutoff=cutoff),
        created_at=instance.created_at,
        updated_at=instance.updated_at,
    )


def build_metric_window_summary(metric_window: TaskMetricWindow) -> TaskMetricWindowSummary:
    return TaskMetricWindowSummary(
        instance_id=metric_window.instance_id,
        task_name=metric_window.task_name,
        window_id=metric_window.window_id,
        window_started_at=metric_window.window_started_at,
        window_ended_at=metric_window.window_ended_at,
        fetched=metric_window.fetched,
        started=metric_window.started,
        succeeded=metric_window.succeeded,
        retried=metric_window.retried,
        failed=metric_window.failed,
        dead_lettered=metric_window.dead_lettered,
        cancelled=metric_window.cancelled,
        timeouts=metric_window.timeouts,
        inflight=metric_window.inflight,
        avg_duration_ms=metric_window.avg_duration_ms,
        p95_duration_ms=metric_window.p95_duration_ms,
        received_at=metric_window.received_at,
        created_at=metric_window.created_at,
    )


def build_task_event_summary(event: TaskEvent) -> TaskEventSummary:
    return TaskEventSummary(
        event_id=event.event_id,
        instance_id=event.instance_id,
        task_name=event.task_name,
        kind=event.kind,
        occurred_at=event.occurred_at,
        attempts=event.attempts,
        duration_ms=event.duration_ms,
        failure_kind=event.failure_kind,
        exception_type=event.exception_type,
        message=event.message,
        meta=event.meta_json,
        received_at=event.received_at,
        created_at=event.created_at,
    )


def parse_emit_descriptors(
    emit_json: list[dict[str, object]] | dict[str, object] | None,
) -> list[ConnectorDescriptor]:
    if emit_json is None:
        return []
    if isinstance(emit_json, dict):
        return [ConnectorDescriptor.model_validate(emit_json)]
    return [ConnectorDescriptor.model_validate(item) for item in emit_json]


def parse_retry_descriptor(retry_policy: dict[str, object] | None) -> RetryDescriptor | None:
    if retry_policy is None:
        return None
    return RetryDescriptor.model_validate(retry_policy)


def apply_task_definition_to_summary(
    summary: TaskDashboardSummary,
    task_definition: TaskDefinition,
) -> None:
    summary.description = task_definition.description
    summary.source_name = task_definition.source_name
    summary.source_kind = task_definition.source_kind
    summary.source_config = task_definition.source_config_json
    summary.emit = parse_emit_descriptors(task_definition.emit_json)
    summary.concurrency = task_definition.concurrency
    summary.timeout_s = task_definition.timeout_s
    summary.retry_policy = parse_retry_descriptor(task_definition.retry_policy)
    summary.topology_hash = task_definition.topology_hash


def get_service_summary_data(
    db: Session,
    *,
    service: Service,
    cutoff: datetime,
) -> tuple[ServiceSummary, InstanceConnectivityCounts]:
    total, online, never_reported, last_seen_at = db.execute(
        select(
            func.count(Instance.id),
            online_instance_count_expression(cutoff),
            func.coalesce(
                func.sum(case((Instance.last_seen_at.is_(None), 1), else_=0)),
                0,
            ),
            func.max(Instance.last_seen_at),
        ).where(Instance.service_id == service.id)
    ).one()
    service_summary = build_service_summary(
        service,
        instance_count=total,
        online_instance_count=online,
        last_seen_at=last_seen_at,
    )
    connectivity = build_instance_connectivity_counts(
        total=total,
        online=online,
        never_reported=never_reported,
    )
    return service_summary, connectivity


def build_instance_status_counts(rows: list[tuple[str, int]]) -> InstanceStatusCounts:
    counts = {status_value: 0 for status_value in HEALTH_STATUS_VALUES}
    for status_value, count in rows:
        if status_value in counts:
            counts[status_value] = count
    return InstanceStatusCounts(**counts)


def build_instance_connectivity_counts(
    *,
    total: int,
    online: int,
    never_reported: int,
) -> InstanceConnectivityCounts:
    offline = max(total - online - never_reported, 0)
    return InstanceConnectivityCounts(
        total=total,
        online=online,
        offline=offline,
        never_reported=never_reported,
    )


def build_task_event_counts(*, counts_by_kind: dict[str, int]) -> TaskEventCounts:
    return TaskEventCounts(
        failed=counts_by_kind.get("failed", 0),
        retried=counts_by_kind.get("retried", 0),
        dead_lettered=counts_by_kind.get("dead_lettered", 0),
        cancelled=counts_by_kind.get("cancelled", 0),
        succeeded=counts_by_kind.get("succeeded", 0),
    )


def service_has_task_data(
    db: Session,
    *,
    service_id: UUID,
    task_name: str,
) -> bool:
    task_definition = db.scalar(
        select(TaskDefinition.id)
        .where(
            TaskDefinition.service_id == service_id,
            TaskDefinition.task_name == task_name,
        )
        .limit(1)
    )
    if task_definition is not None:
        return True

    metric_task = db.scalar(
        select(TaskMetricWindow.id)
        .where(
            TaskMetricWindow.service_id == service_id,
            TaskMetricWindow.task_name == task_name,
        )
        .limit(1)
    )
    if metric_task is not None:
        return True

    event_task = db.scalar(
        select(TaskEvent.id)
        .where(
            TaskEvent.service_id == service_id,
            TaskEvent.task_name == task_name,
        )
        .limit(1)
    )
    return event_task is not None


def build_task_summary_map(
    db: Session,
    *,
    service_id: UUID,
    lookback_started_at: datetime,
) -> dict[str, TaskDashboardSummary]:
    task_definitions = db.scalars(
        select(TaskDefinition)
        .where(TaskDefinition.service_id == service_id)
        .order_by(TaskDefinition.task_name)
    ).all()
    summaries = {
        task_definition.task_name: TaskDashboardSummary(
            task_name=task_definition.task_name,
            event_counts=TaskEventCounts(),
        )
        for task_definition in task_definitions
    }
    for task_definition in task_definitions:
        apply_task_definition_to_summary(summaries[task_definition.task_name], task_definition)

    weighted_avg_duration_ms = (
        func.sum(func.coalesce(TaskMetricWindow.avg_duration_ms, 0.0) * TaskMetricWindow.started)
        / func.nullif(func.sum(TaskMetricWindow.started), 0)
    )
    metric_rows = db.execute(
        select(
            TaskMetricWindow.task_name,
            func.count(TaskMetricWindow.id),
            func.max(TaskMetricWindow.window_started_at),
            func.max(TaskMetricWindow.window_ended_at),
            func.sum(TaskMetricWindow.fetched),
            func.sum(TaskMetricWindow.started),
            func.sum(TaskMetricWindow.succeeded),
            func.sum(TaskMetricWindow.retried),
            func.sum(TaskMetricWindow.failed),
            func.sum(TaskMetricWindow.dead_lettered),
            func.sum(TaskMetricWindow.cancelled),
            func.sum(TaskMetricWindow.timeouts),
            weighted_avg_duration_ms,
            func.max(TaskMetricWindow.p95_duration_ms),
        )
        .where(
            TaskMetricWindow.service_id == service_id,
            TaskMetricWindow.window_ended_at >= lookback_started_at,
        )
        .group_by(TaskMetricWindow.task_name)
    ).all()

    for (
        task_name,
        metric_window_count,
        latest_window_started_at,
        latest_window_ended_at,
        fetched,
        started,
        succeeded,
        retried,
        failed,
        dead_lettered,
        cancelled,
        timeouts,
        weighted_avg_duration_ms_value,
        max_p95_duration_ms,
    ) in metric_rows:
        summary = summaries.get(task_name)
        if summary is None:
            summary = TaskDashboardSummary(
                task_name=task_name,
                event_counts=TaskEventCounts(),
            )
            summaries[task_name] = summary

        summary.metric_window_count = metric_window_count
        summary.latest_window_started_at = latest_window_started_at
        summary.latest_window_ended_at = latest_window_ended_at
        summary.fetched = fetched or 0
        summary.started = started or 0
        summary.succeeded = succeeded or 0
        summary.retried = retried or 0
        summary.failed = failed or 0
        summary.dead_lettered = dead_lettered or 0
        summary.cancelled = cancelled or 0
        summary.timeouts = timeouts or 0
        summary.weighted_avg_duration_ms = weighted_avg_duration_ms_value
        summary.max_p95_duration_ms = max_p95_duration_ms

    event_count_expressions = [
        func.sum(case((TaskEvent.kind == kind, 1), else_=0)).label(f"{kind}_count")
        for kind in TASK_EVENT_KIND_VALUES
    ]
    event_rows = db.execute(
        select(
            TaskEvent.task_name,
            func.max(TaskEvent.occurred_at),
            *event_count_expressions,
        )
        .where(
            TaskEvent.service_id == service_id,
            TaskEvent.occurred_at >= lookback_started_at,
        )
        .group_by(TaskEvent.task_name)
    ).all()

    for row in event_rows:
        task_name = row[0]
        counts_by_kind = {
            kind: row[index + 2] or 0 for index, kind in enumerate(TASK_EVENT_KIND_VALUES)
        }
        summary = summaries.get(task_name)
        if summary is None:
            summary = TaskDashboardSummary(
                task_name=task_name,
                event_counts=build_task_event_counts(counts_by_kind=counts_by_kind),
                last_event_at=row[1],
            )
            summaries[task_name] = summary
            continue

        summary.last_event_at = row[1]
        summary.event_counts = build_task_event_counts(counts_by_kind=counts_by_kind)

    return summaries


def get_service_topology_hashes(
    db: Session,
    *,
    service_id: UUID,
) -> list[str]:
    return list(
        db.scalars(
            select(Instance.last_topology_hash)
            .where(
                Instance.service_id == service_id,
                Instance.last_topology_hash.is_not(None),
            )
            .distinct()
            .order_by(Instance.last_topology_hash)
        ).all()
    )


def sort_task_summaries(task_summaries: list[TaskDashboardSummary]) -> list[TaskDashboardSummary]:
    def activity_key(summary: TaskDashboardSummary) -> datetime:
        candidates = [
            candidate
            for candidate in (summary.latest_window_ended_at, summary.last_event_at)
            if candidate is not None
        ]
        if not candidates:
            return datetime.min.replace(tzinfo=UTC)
        return max(candidates)

    return sorted(
        task_summaries,
        key=lambda summary: (activity_key(summary), summary.task_name),
        reverse=True,
    )
