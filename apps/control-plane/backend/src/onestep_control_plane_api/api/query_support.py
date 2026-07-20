from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.agent_command_service import expire_stale_commands
from onestep_control_plane_api.api.common import as_utc, utcnow
from onestep_control_plane_api.api.constants import (
    HEALTH_STATUS_VALUES,
    RETRY_ATTEMPTS_CONFIG_KEYS,
    SINK_LABEL_CONFIG_KEYS,
    SOURCE_LABEL_CONFIG_KEYS,
    TASK_EVENT_KIND_VALUES,
    TASK_FAILING_EVENT_KINDS,
)
from onestep_control_plane_api.api.schemas import (
    AgentCommandOverview,
    AgentCommandStatusCounts,
    AgentSessionSummary,
    ConnectorDescriptor,
    Environment,
    EventLogLevel,
    InstanceConnectivity,
    InstanceConnectivityCounts,
    InstanceStatusCounts,
    InstanceSummary,
    InstanceViewStatus,
    RecentEventSummary,
    RetryDescriptor,
    ServiceListStatus,
    ServiceListSummary,
    ServiceSummary,
    ServiceViewStatus,
    TaskControlStateSummary,
    TaskDashboardSummary,
    TaskEventCounts,
    TaskEventHistoryItem,
    TaskEventKind,
    TaskEventSummary,
    TaskInstanceControlState,
    TaskMetricChartPointSummary,
    TaskMetricWindowSummary,
    TaskViewStatus,
)
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db.models import (
    AgentCommand,
    AgentSession,
    Instance,
    Service,
    TaskDefinition,
    TaskEvent,
    TaskMetricWindow,
)


TASK_EVENT_HISTORY_COMMAND_KINDS = (
    "pause_task",
    "resume_task",
    "restart_task",
    "discard_dead_letters",
    "replay_dead_letters",
    "run_task_once",
)


@dataclass(frozen=True, slots=True)
class ServiceMetricAggregates:
    """Per-service lookback metric totals used to derive service-list fields."""

    fetched: int
    succeeded: int
    failed: int
    dead_lettered: int
    cancelled: int
    timeouts: int


def online_cutoff(now: datetime) -> datetime:
    return now - timedelta(seconds=settings.instance_offline_after_s)


def health_participation_cutoff(now: datetime) -> datetime:
    return now - timedelta(seconds=settings.instance_health_participation_window_s)


def instance_last_activity_expression():
    return case(
        (
            and_(Instance.last_seen_at.is_(None), Instance.last_sync_at.is_(None)),
            Instance.created_at,
        ),
        (
            Instance.last_seen_at.is_(None),
            Instance.last_sync_at,
        ),
        (
            Instance.last_sync_at.is_(None),
            Instance.last_seen_at,
        ),
        (
            Instance.last_seen_at >= Instance.last_sync_at,
            Instance.last_seen_at,
        ),
        else_=Instance.last_sync_at,
    )


def instance_participates_in_health_expression(cutoff: datetime):
    return instance_last_activity_expression() >= cutoff


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


def health_participation_instance_count_expression(cutoff: datetime):
    return func.coalesce(
        func.sum(
            case(
                (
                    instance_participates_in_health_expression(cutoff),
                    1,
                ),
                else_=0,
            )
        ),
        0,
    )


def never_reported_instance_count_expression(cutoff: datetime):
    return func.coalesce(
        func.sum(
            case(
                (
                    and_(
                        Instance.last_seen_at.is_(None),
                        instance_participates_in_health_expression(cutoff),
                    ),
                    1,
                ),
                else_=0,
            )
        ),
        0,
    )


def build_service_stats_subquery(
    *,
    online_cutoff: datetime,
    health_cutoff: datetime,
):
    return (
        select(
            Instance.service_id.label("service_id"),
            health_participation_instance_count_expression(health_cutoff).label("instance_count"),
            online_instance_count_expression(online_cutoff).label("online_instance_count"),
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
    source_kinds: list[str] | None = None,
    task_count: int = 0,
    failing_task_count: int = 0,
    success_rate: float = 100.0,
    throughput_per_min: int = 0,
    error_count: int = 0,
) -> ServiceSummary:
    service_list_status = derive_service_list_status(
        instance_count=instance_count,
        online_instance_count=online_instance_count,
    )
    view_status = derive_service_view_status(
        service_list_status=service_list_status,
        online_instance_count=online_instance_count,
    )
    return ServiceSummary(
        name=service.name,
        environment=service.environment,
        latest_deployment_version=service.latest_deployment_version,
        service_status=service_list_status,
        latest_topology_hash=service.latest_topology_hash,
        latest_sync_at=service.latest_sync_at,
        instance_count=instance_count,
        online_instance_count=online_instance_count,
        last_seen_at=last_seen_at,
        source_kinds=source_kinds or [],
        task_count=task_count,
        failing_task_count=failing_task_count,
        view_status=view_status,
        success_rate=success_rate,
        throughput_per_min=throughput_per_min,
        error_count=error_count,
        uptime_reference_at=last_seen_at,
        online_task_count=max(task_count - failing_task_count, 0)
        if online_instance_count > 0
        else 0,
        standby_instance_count=max(instance_count - online_instance_count, 0),
        created_at=service.created_at,
        updated_at=service.updated_at,
    )


def derive_service_list_status(
    *,
    instance_count: int,
    online_instance_count: int,
) -> ServiceListStatus:
    if online_instance_count == 0:
        return "offline"
    if online_instance_count < instance_count:
        return "attention"
    return "online"


# ---- View-facing derived status / metric helpers ----
# These centralise the business rules that the frontend used to apply, so the
# wire contract carries a single canonical view status and scalar metrics.

def derive_service_view_status(
    *,
    service_list_status: ServiceListStatus,
    online_instance_count: int,
) -> ServiceViewStatus:
    if online_instance_count == 0:
        return "stopped"
    if service_list_status == "online":
        return "running"
    if service_list_status == "attention":
        return "degraded"
    return "stopped"


def derive_task_view_status(
    *,
    online_instance_count: int,
    pause_requested: bool | None,
    error_count: int,
    started: int,
    succeeded: int,
) -> TaskViewStatus:
    if pause_requested is True:
        return "paused"
    if error_count > 0:
        return "failed"
    if started > 0 or succeeded > 0:
        return "running"
    if online_instance_count == 0:
        return "offline"
    return "idle"


def derive_instance_view_status(
    *,
    status: str,
    connectivity: InstanceConnectivity,
) -> InstanceViewStatus:
    if connectivity != "online":
        return "stopped"
    if status == "ok":
        return "running"
    if status == "starting":
        return "starting"
    if status in ("error", "degraded"):
        return "failed"
    return "stopped"


def derive_event_level(kind: TaskEventKind) -> EventLogLevel:
    if kind in ("failed", "dead_lettered"):
        return "error"
    if kind == "retried":
        return "warn"
    return "info"


def compute_success_rate(
    *,
    succeeded: int,
    failed: int,
    dead_lettered: int,
    cancelled: int,
) -> float:
    total = succeeded + failed + dead_lettered + cancelled
    if total == 0:
        return 100.0
    return round(succeeded / total * 100, 2)


def compute_throughput_per_min(*, fetched: int, lookback_minutes: int) -> int:
    if lookback_minutes <= 0:
        return 0
    return int(fetched // lookback_minutes)


def compute_error_count(*, failed: int, dead_lettered: int, timeouts: int) -> int:
    return failed + dead_lettered + timeouts


def extract_retry_attempts(retry_policy: RetryDescriptor | None) -> int:
    if retry_policy is None:
        return 0
    config = retry_policy.config or {}
    for key in RETRY_ATTEMPTS_CONFIG_KEYS:
        value = config.get(key)
        if isinstance(value, int) and value >= 0:
            return value
    # Retry policy present but no recognised attempts key: assume the default
    # of one attempt (matches the previous frontend fallback).
    return 1


def _first_non_empty_str(config: dict[str, Any] | None, keys: tuple[str, ...]) -> str | None:
    if not config:
        return None
    for key in keys:
        value = config.get(key)
        if isinstance(value, str) and value.strip() != "":
            return value
    return None


def extract_source_label(
    *,
    source_name: str | None,
    source_config: dict[str, Any] | None,
) -> str | None:
    if source_name:
        return source_name
    return _first_non_empty_str(source_config, SOURCE_LABEL_CONFIG_KEYS)


def extract_sink_label(emit: list[ConnectorDescriptor]) -> str | None:
    sink = emit[0] if emit else None
    if sink is None:
        return None
    if sink.name:
        return sink.name
    return _first_non_empty_str(sink.config, SINK_LABEL_CONFIG_KEYS)


def build_task_config_yaml(summary: TaskDashboardSummary) -> str:
    emit = summary.emit[0] if summary.emit else None
    retry_attempts = extract_retry_attempts(summary.retry_policy)
    return (
        "task_config:\n"
        f'  id: "{summary.task_name}"\n'
        f'  topology_hash: "{summary.topology_hash or "unknown"}"\n'
        "\n"
        "  execution:\n"
        f"    concurrency: {summary.concurrency or 1}\n"
        f"    timeout_s: {summary.timeout_s if summary.timeout_s is not None else 'null'}\n"
        "    retry_policy:\n"
        f'      kind: "{summary.retry_policy.kind if summary.retry_policy else "none"}"\n'
        f"      attempts: {retry_attempts}\n"
        "\n"
        "  source:\n"
        f'    type: "{summary.source_kind or "unknown"}"\n'
        f'    name: "{summary.source_name or "default"}"\n'
        "\n"
        "  sink:\n"
        f'    type: "{emit.kind if emit else "handler"}"\n'
        f'    name: "{emit.name if emit else "default"}"'
    )


def build_service_list_summary(items: list[ServiceSummary]) -> ServiceListSummary:
    total_instances = sum(item.instance_count for item in items)
    online_instances = sum(item.online_instance_count for item in items)
    online_services = sum(1 for item in items if item.service_status == "online")
    attention_services = sum(1 for item in items if item.service_status == "attention")
    offline_services = sum(1 for item in items if item.service_status == "offline")
    return ServiceListSummary(
        total_services=len(items),
        online_services=online_services,
        attention_services=attention_services,
        offline_services=offline_services,
        ready_services=online_services,
        total_instances=total_instances,
        online_instances=online_instances,
        total_tasks=sum(item.task_count for item in items),
        failing_tasks=sum(item.failing_task_count for item in items),
    )


def build_source_kinds_map(db: Session) -> dict[UUID, list[str]]:
    """Return a mapping of service_id -> sorted distinct source_kinds."""
    rows = db.execute(
        select(
            TaskDefinition.service_id,
            TaskDefinition.source_kind,
        )
        .where(TaskDefinition.source_kind.is_not(None))
        .distinct()
        .order_by(TaskDefinition.service_id, TaskDefinition.source_kind)
    ).all()
    result: dict[UUID, list[str]] = {}
    for service_id, source_kind in rows:
        result.setdefault(service_id, []).append(source_kind)
    return result


def build_source_kind_counts_map(
    db: Session,
    *,
    environment: Environment | None = None,
) -> dict[str, int]:
    """Return a mapping of source_kind -> count of distinct services using it."""
    stmt = (
        select(
            TaskDefinition.source_kind,
            func.count(func.distinct(TaskDefinition.service_id)),
        )
        .where(TaskDefinition.source_kind.is_not(None))
        .group_by(TaskDefinition.source_kind)
    )
    if environment is not None:
        stmt = stmt.join(Service, TaskDefinition.service_id == Service.id).where(
            Service.environment == environment
        )
    rows = db.execute(stmt).all()
    return {source_kind: count for source_kind, count in rows}


def build_task_counts_map(db: Session) -> dict[UUID, int]:
    """Return a mapping of service_id -> count of distinct task definitions."""
    rows = db.execute(
        select(
            TaskDefinition.service_id,
            func.count(func.distinct(TaskDefinition.task_name)),
        ).group_by(TaskDefinition.service_id)
    ).all()
    return {service_id: count for service_id, count in rows}


def build_failing_task_counts_map(
    db: Session,
    *,
    lookback_started_at: datetime,
) -> dict[UUID, int]:
    """Count distinct tasks with failed/dead_lettered events per service in the lookback window."""
    failing_kinds = TASK_FAILING_EVENT_KINDS
    rows = db.execute(
        select(
            TaskEvent.service_id,
            func.count(func.distinct(TaskEvent.task_name)),
        )
        .where(
            TaskEvent.kind.in_(failing_kinds),
            TaskEvent.occurred_at >= lookback_started_at,
        )
        .group_by(TaskEvent.service_id)
    ).all()
    return {service_id: count for service_id, count in rows}


def build_service_metric_aggregates_map(
    db: Session,
    *,
    lookback_started_at: datetime,
) -> dict[UUID, ServiceMetricAggregates]:
    """Aggregate TaskMetricWindow counts per service for the service-list view.

    Returns summed fetched/succeeded/failed/dead_lettered/cancelled/timeouts per
    service, used to derive the service-level success_rate / throughput_per_min /
    error_count. Lookback scope matches the per-task summary path.
    """
    rows = db.execute(
        select(
            TaskMetricWindow.service_id,
            func.coalesce(func.sum(TaskMetricWindow.fetched), 0),
            func.coalesce(func.sum(TaskMetricWindow.succeeded), 0),
            func.coalesce(func.sum(TaskMetricWindow.failed), 0),
            func.coalesce(func.sum(TaskMetricWindow.dead_lettered), 0),
            func.coalesce(func.sum(TaskMetricWindow.cancelled), 0),
            func.coalesce(func.sum(TaskMetricWindow.timeouts), 0),
        )
        .where(TaskMetricWindow.window_ended_at >= lookback_started_at)
        .group_by(TaskMetricWindow.service_id)
    ).all()
    result: dict[UUID, ServiceMetricAggregates] = {}
    for (
        service_id,
        fetched,
        succeeded,
        failed,
        dead_lettered,
        cancelled,
        timeouts,
    ) in rows:
        result[service_id] = ServiceMetricAggregates(
            fetched=int(fetched or 0),
            succeeded=int(succeeded or 0),
            failed=int(failed or 0),
            dead_lettered=int(dead_lettered or 0),
            cancelled=int(cancelled or 0),
            timeouts=int(timeouts or 0),
        )
    return result


def build_service_failing_task_counts_map(
    db: Session,
    *,
    lookback_started_at: datetime,
) -> dict[UUID, int]:
    """Count distinct tasks per service with failed/dead_lettered events.

    Event-sourced (TaskEvent) so a task that failed but never recorded a failed
    metric window still counts as failing — matches the dashboard path.
    Timeouts are folded into the per-task error_count only (no TaskEvent kind).
    """
    rows = db.execute(
        select(
            TaskEvent.service_id,
            func.count(func.distinct(TaskEvent.task_name)),
        )
        .where(
            TaskEvent.kind.in_(TASK_FAILING_EVENT_KINDS),
            TaskEvent.occurred_at >= lookback_started_at,
        )
        .group_by(TaskEvent.service_id)
    ).all()
    return {service_id: int(count or 0) for service_id, count in rows}


def build_service_error_counts_map(
    db: Session,
    *,
    lookback_started_at: datetime,
) -> dict[UUID, int]:
    """Per-service error_count for the service list.

    error_count = event-sourced failed + dead_lettered TaskEvents + metric-sourced
    timeouts, matching the per-task error_count definition. Used so the list and
    dashboard present the same error totals.
    """
    event_rows = db.execute(
        select(
            TaskEvent.service_id,
            func.count(TaskEvent.id),
        )
        .where(
            TaskEvent.kind.in_(TASK_FAILING_EVENT_KINDS),
            TaskEvent.occurred_at >= lookback_started_at,
        )
        .group_by(TaskEvent.service_id)
    ).all()
    event_counts: dict[UUID, int] = {
        service_id: int(count or 0) for service_id, count in event_rows
    }
    timeout_rows = db.execute(
        select(
            TaskMetricWindow.service_id,
            func.coalesce(func.sum(TaskMetricWindow.timeouts), 0),
        )
        .where(
            TaskMetricWindow.window_ended_at >= lookback_started_at,
        )
        .group_by(TaskMetricWindow.service_id)
    ).all()
    service_ids = set(event_counts) | {sid for sid, _ in timeout_rows}
    result: dict[UUID, int] = {}
    timeout_by_service = {service_id: int(total or 0) for service_id, total in timeout_rows}
    for service_id in service_ids:
        result[service_id] = event_counts.get(service_id, 0) + timeout_by_service.get(service_id, 0)
    return result


def augment_service_summary_from_tasks(
    service_summary: ServiceSummary,
    *,
    task_summaries: list[TaskDashboardSummary],
    lookback_minutes: int,
) -> None:
    """Fill the service-level derived fields on a ServiceSummary in place.

    Used by endpoints that already load per-task summaries (dashboard,
    task list, task detail) so the embedded ServiceSummary carries the same
    success_rate / throughput_per_min / error_count / failing_task_count as the
    service list endpoint, without a second metric aggregation query.
    """
    fetched = sum(task.fetched for task in task_summaries)
    succeeded = sum(task.succeeded for task in task_summaries)
    failed = sum(task.failed for task in task_summaries)
    dead_lettered = sum(task.dead_lettered for task in task_summaries)
    cancelled = sum(task.cancelled for task in task_summaries)
    service_summary.success_rate = compute_success_rate(
        succeeded=succeeded,
        failed=failed,
        dead_lettered=dead_lettered,
        cancelled=cancelled,
    )
    # error_count reuses the per-task error_count (already finalised in
    # build_task_summary_map), which blends event-sourced failures with
    # metric-sourced timeouts — keeping list and dashboard totals consistent.
    service_summary.error_count = sum(task.error_count for task in task_summaries)
    failing_task_count = sum(1 for task in task_summaries if task.error_count > 0)
    service_summary.failing_task_count = failing_task_count
    task_count = len(task_summaries)
    # Keep service_summary.task_count in sync with the loaded task set so the
    # online_task_count derivation (and any client reading task_count) matches.
    service_summary.task_count = task_count
    service_summary.online_task_count = (
        max(task_count - failing_task_count, 0)
        if service_summary.online_instance_count > 0
        else 0
    )
    service_summary.throughput_per_min = compute_throughput_per_min(
        fetched=fetched,
        lookback_minutes=lookback_minutes,
    )


def build_agent_session_summary(
    session: AgentSession,
    *,
    instance: Instance | None = None,
) -> AgentSessionSummary:
    return AgentSessionSummary(
        session_id=session.session_id,
        instance_id=session.instance_id,
        node_name=instance.node_name if instance is not None else None,
        hostname=instance.hostname if instance is not None else None,
        status=session.status,
        protocol_version=session.protocol_version,
        capabilities=session.capabilities_json,
        accepted_capabilities=session.accepted_capabilities_json,
        connected_at=session.connected_at,
        last_hello_at=session.last_hello_at,
        last_message_at=session.last_message_at,
        superseded_at=session.superseded_at,
        disconnected_at=session.disconnected_at,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def get_active_sessions_by_instance_id(
    db: Session,
    *,
    service_id: UUID,
) -> dict[UUID, AgentSession]:
    sessions = db.scalars(
        select(AgentSession)
        .where(
            AgentSession.service_id == service_id,
            AgentSession.status == "active",
        )
        .order_by(AgentSession.connected_at.desc(), AgentSession.session_id.desc())
    ).all()
    active_by_instance_id: dict[UUID, AgentSession] = {}
    for session in sessions:
        active_by_instance_id.setdefault(session.instance_id, session)
    return active_by_instance_id


def get_latest_session_for_instance(
    db: Session,
    *,
    instance_id: UUID,
) -> AgentSession | None:
    return db.scalar(
        select(AgentSession)
        .where(AgentSession.instance_id == instance_id)
        .order_by(AgentSession.connected_at.desc(), AgentSession.session_id.desc())
        .limit(1)
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
    active_session: AgentSessionSummary | None = None,
) -> InstanceSummary:
    connectivity = get_instance_connectivity(instance, cutoff=cutoff)
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
        connectivity=connectivity,
        active_session=active_session,
        view_status=derive_instance_view_status(status=instance.status, connectivity=connectivity),
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


def build_task_metric_chart_points(
    metric_windows: list[TaskMetricWindow],
    *,
    lookback_started_at: datetime,
    lookback_minutes: int,
) -> list[TaskMetricChartPointSummary]:
    if lookback_minutes <= 0:
        return []

    bucket_start = as_utc(lookback_started_at)
    bucket_end = bucket_start + timedelta(minutes=lookback_minutes)
    buckets = [
        {
            "bucket_started_at": bucket_start + timedelta(minutes=index),
            "bucket_ended_at": bucket_start + timedelta(minutes=index + 1),
            "reported_window_count": 0,
            "fetched": 0,
            "started": 0,
            "succeeded": 0,
            "retried": 0,
            "failed": 0,
            "dead_lettered": 0,
            "cancelled": 0,
            "timeouts": 0,
            "inflight": 0,
            "avg_duration_weighted_sum": 0.0,
            "avg_duration_weight": 0,
            "p95_duration_ms": None,
        }
        for index in range(lookback_minutes)
    ]

    for metric_window in metric_windows:
        ended_at = as_utc(metric_window.window_ended_at)
        if ended_at < bucket_start or ended_at > bucket_end:
            continue
        bucket_index = min(
            int((ended_at - bucket_start).total_seconds() // 60),
            lookback_minutes - 1,
        )
        bucket = buckets[bucket_index]
        bucket["reported_window_count"] += 1
        bucket["fetched"] += metric_window.fetched
        bucket["started"] += metric_window.started
        bucket["succeeded"] += metric_window.succeeded
        bucket["retried"] += metric_window.retried
        bucket["failed"] += metric_window.failed
        bucket["dead_lettered"] += metric_window.dead_lettered
        bucket["cancelled"] += metric_window.cancelled
        bucket["timeouts"] += metric_window.timeouts
        bucket["inflight"] = max(bucket["inflight"], metric_window.inflight)
        if metric_window.avg_duration_ms is not None and metric_window.started > 0:
            bucket["avg_duration_weighted_sum"] += (
                metric_window.avg_duration_ms * metric_window.started
            )
            bucket["avg_duration_weight"] += metric_window.started
        if metric_window.p95_duration_ms is not None:
            current_p95 = bucket["p95_duration_ms"]
            bucket["p95_duration_ms"] = (
                metric_window.p95_duration_ms
                if current_p95 is None
                else max(current_p95, metric_window.p95_duration_ms)
            )

    points: list[TaskMetricChartPointSummary] = []
    for bucket in buckets:
        avg_duration_weight = bucket["avg_duration_weight"]
        avg_duration_ms = (
            bucket["avg_duration_weighted_sum"] / avg_duration_weight
            if avg_duration_weight
            else None
        )
        points.append(
            TaskMetricChartPointSummary(
                bucket_started_at=bucket["bucket_started_at"],
                bucket_ended_at=bucket["bucket_ended_at"],
                reported_window_count=bucket["reported_window_count"],
                fetched=bucket["fetched"],
                started=bucket["started"],
                succeeded=bucket["succeeded"],
                retried=bucket["retried"],
                failed=bucket["failed"],
                dead_lettered=bucket["dead_lettered"],
                cancelled=bucket["cancelled"],
                timeouts=bucket["timeouts"],
                inflight=bucket["inflight"],
                avg_duration_ms=avg_duration_ms,
                p95_duration_ms=bucket["p95_duration_ms"],
            )
        )
    return points


def build_task_control_summary(
    instances: list[Instance],
    *,
    active_sessions_by_instance_id: dict[UUID, AgentSession],
    task_name: str,
    cutoff: datetime,
) -> TaskControlStateSummary:
    return TaskControlStateSummary(
        task_name=task_name,
        instances=[
            build_task_instance_control_state(
                instance,
                active_session=active_sessions_by_instance_id.get(instance.instance_id),
                task_name=task_name,
                cutoff=cutoff,
            )
            for instance in instances
        ],
    )


def build_task_instance_control_state(
    instance: Instance,
    *,
    active_session: AgentSession | None,
    task_name: str,
    cutoff: datetime,
) -> TaskInstanceControlState:
    task_control_state = _extract_task_control_state(instance.app_snapshot_json, task_name)
    supported_commands = _task_control_supported_commands(active_session, task_control_state)
    return TaskInstanceControlState(
        instance_id=instance.instance_id,
        node_name=instance.node_name,
        connectivity=get_instance_connectivity(instance, cutoff=cutoff),
        status=instance.status,
        last_seen_at=instance.last_seen_at,
        supported_commands=supported_commands,
        state_known=task_control_state is not None,
        pause_requested=_coerce_bool(task_control_state, "pause_requested"),
        paused=_coerce_bool(task_control_state, "paused"),
        accepting_new_work=_coerce_bool(task_control_state, "accepting_new_work"),
        runner_count=_coerce_non_negative_int(task_control_state, "runner_count"),
        parked_runner_count=_coerce_non_negative_int(task_control_state, "parked_runner_count"),
        fetching_runner_count=_coerce_non_negative_int(task_control_state, "fetching_runner_count"),
        inflight_task_count=_coerce_non_negative_int(task_control_state, "inflight_task_count"),
    )


def _task_control_supported_commands(
    active_session: AgentSession | None,
    task_control_state: dict[str, object] | None,
) -> list[str]:
    if active_session is None:
        return []
    accepted_capabilities = set(active_session.accepted_capabilities_json)
    capability_commands: list[str] = []
    if "command.pause_task" in accepted_capabilities:
        capability_commands.append("pause_task")
    if "command.resume_task" in accepted_capabilities:
        capability_commands.append("resume_task")
    if "command.restart_task" in accepted_capabilities:
        capability_commands.append("restart_task")
    if "command.discard_dead_letters" in accepted_capabilities:
        capability_commands.append("discard_dead_letters")
    if "command.replay_dead_letters" in accepted_capabilities:
        capability_commands.append("replay_dead_letters")
    if "command.run_task_once" in accepted_capabilities:
        capability_commands.append("run_task_once")

    task_supported_commands = _extract_task_supported_commands(task_control_state)
    if task_supported_commands is None:
        return [
            command_kind
            for command_kind in capability_commands
            if command_kind in {"pause_task", "resume_task", "restart_task"}
        ]
    return [
        command_kind
        for command_kind in capability_commands
        if command_kind in task_supported_commands
    ]


def _extract_task_control_state(
    app_snapshot_json: dict[str, object] | None,
    task_name: str,
) -> dict[str, object] | None:
    if not isinstance(app_snapshot_json, dict):
        return None
    task_control_states = app_snapshot_json.get("task_control_states")
    if not isinstance(task_control_states, list):
        return None
    for value in task_control_states:
        if not isinstance(value, dict):
            continue
        if value.get("task_name") == task_name:
            return dict(value)
    return None


def _extract_task_supported_commands(
    task_control_state: dict[str, object] | None,
) -> set[str] | None:
    if task_control_state is None:
        return None
    raw_supported_commands = task_control_state.get("supported_commands")
    if not isinstance(raw_supported_commands, list):
        return None
    supported_commands: set[str] = set()
    for command_kind in raw_supported_commands:
        if command_kind in {
            "pause_task",
            "resume_task",
            "restart_task",
            "discard_dead_letters",
            "replay_dead_letters",
            "run_task_once",
        }:
            supported_commands.add(command_kind)
    return supported_commands


def _coerce_bool(payload: dict[str, object] | None, key: str) -> bool | None:
    if payload is None:
        return None
    value = payload.get(key)
    if isinstance(value, bool):
        return value
    return None


def _coerce_non_negative_int(payload: dict[str, object] | None, key: str) -> int | None:
    if payload is None:
        return None
    value = payload.get(key)
    if isinstance(value, int) and value >= 0:
        return value
    return None


def build_task_pause_requested_map(
    db: Session,
    *,
    service_id: UUID,
) -> dict[str, bool]:
    """Aggregate per-task ``pause_requested`` across a service's instances.

    For each task_name, returns True when any instance reported
    ``pause_requested=true``; False when at least one instance reported a known
    boolean state and none reported True. Tasks with no instance reporting a
    known state are absent from the map (caller leaves the summary field as
    None). Loads only the ``app_snapshot_json`` column and parses the same
    ``task_control_states`` shape used by the per-instance detail path, so the
    semantics match ``_coerce_bool``.
    """
    snapshot_rows = db.execute(
        select(Instance.app_snapshot_json).where(Instance.service_id == service_id)
    ).all()

    result: dict[str, bool] = {}
    for (app_snapshot_json,) in snapshot_rows:
        if not isinstance(app_snapshot_json, dict):
            continue
        task_control_states = app_snapshot_json.get("task_control_states")
        if not isinstance(task_control_states, list):
            continue
        for value in task_control_states:
            if not isinstance(value, dict):
                continue
            task_name = value.get("task_name")
            if not isinstance(task_name, str):
                continue
            pause_requested = _coerce_bool(value, "pause_requested")
            if pause_requested is None:
                # Unknown state: do not flip a known True/False to unknown.
                result.setdefault(task_name, False)
            elif pause_requested:
                result[task_name] = True
            else:
                result.setdefault(task_name, False)
    return result


def build_task_supported_commands_map(
    db: Session,
    *,
    service_id: UUID,
    as_of: datetime | None = None,
) -> dict[str, list[str]]:
    as_of = as_of or utcnow()
    cutoff = online_cutoff(as_of)
    instances = db.scalars(select(Instance).where(Instance.service_id == service_id)).all()
    online_instances = [
        instance
        for instance in instances
        if get_instance_connectivity(instance, cutoff=cutoff) == "online"
    ]
    active_sessions_by_instance_id = get_active_sessions_by_instance_id(
        db,
        service_id=service_id,
    )

    supported_by_task: dict[str, set[str]] = {}
    for instance in online_instances:
        active_session = active_sessions_by_instance_id.get(instance.instance_id)
        if active_session is None or not isinstance(instance.app_snapshot_json, dict):
            continue
        task_control_states = instance.app_snapshot_json.get("task_control_states")
        if not isinstance(task_control_states, list):
            continue
        for value in task_control_states:
            if not isinstance(value, dict):
                continue
            task_name = value.get("task_name")
            if not isinstance(task_name, str):
                continue
            supported_by_task.setdefault(task_name, set()).update(
                _task_control_supported_commands(active_session, dict(value))
            )

    command_order = [
        "pause_task",
        "resume_task",
        "restart_task",
        "discard_dead_letters",
        "replay_dead_letters",
        "run_task_once",
    ]
    return {
        task_name: [command for command in command_order if command in supported_commands]
        for task_name, supported_commands in supported_by_task.items()
    }


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
        traceback=event.traceback,
        meta=event.meta_json,
        received_at=event.received_at,
        created_at=event.created_at,
        level=derive_event_level(event.kind),
    )


def command_event_timestamp(command: AgentCommand) -> datetime:
    return (
        command.finished_at
        or command.acked_at
        or command.dispatched_at
        or command.created_at
    )


def command_event_level(command: AgentCommand) -> EventLogLevel:
    if command.status in {"failed", "timeout", "cancelled", "expired", "rejected"}:
        return "error"
    if command.status in {"pending", "dispatched", "accepted"}:
        return "warn"
    return "info"


def build_task_runtime_history_item(event: TaskEvent) -> TaskEventHistoryItem:
    return TaskEventHistoryItem(
        id=f"runtime:{event.event_id}",
        source_type="runtime",
        instance_id=event.instance_id,
        task_name=event.task_name,
        kind=event.kind,
        occurred_at=event.occurred_at,
        level=derive_event_level(event.kind),
        message=event.message,
        meta=event.meta_json,
        attempts=event.attempts,
        duration_ms=event.duration_ms,
        failure_kind=event.failure_kind,
        exception_type=event.exception_type,
        traceback=event.traceback,
        created_at=event.created_at,
        updated_at=event.created_at,
    )


def build_task_command_history_item(command: AgentCommand, *, task_name: str) -> TaskEventHistoryItem:
    meta: dict[str, Any] = {
        "status": command.status,
        "args": command.args_json,
        "source_surface": command.source_surface,
    }
    if command.created_by:
        meta["created_by"] = command.created_by
    if command.reason:
        meta["reason"] = command.reason
    if command.result_json is not None:
        meta["result"] = command.result_json

    return TaskEventHistoryItem(
        id=f"command:{command.command_id}",
        source_type="command",
        instance_id=command.instance_id,
        task_name=task_name,
        kind=command.kind,
        occurred_at=command_event_timestamp(command),
        level=command_event_level(command),
        message=command.error_message or command.reason or command.status,
        meta=meta,
        duration_ms=command.duration_ms,
        command_id=command.command_id,
        command_status=command.status,
        ack_status=command.ack_status,
        created_at=command.created_at,
        updated_at=command.updated_at,
    )


def build_task_event_history_items(
    db: Session,
    *,
    service_id: UUID,
    task_name: str,
    lookback_started_at: datetime,
    limit: int,
    offset: int,
) -> tuple[list[TaskEventHistoryItem], int]:
    runtime_events = db.scalars(
        select(TaskEvent).where(
            TaskEvent.service_id == service_id,
            TaskEvent.task_name == task_name,
            TaskEvent.occurred_at >= lookback_started_at,
        )
    ).all()

    command_time = func.coalesce(
        AgentCommand.finished_at,
        AgentCommand.acked_at,
        AgentCommand.dispatched_at,
        AgentCommand.created_at,
    )
    commands = db.scalars(
        select(AgentCommand).where(
            AgentCommand.service_id == service_id,
            AgentCommand.kind.in_(TASK_EVENT_HISTORY_COMMAND_KINDS),
            command_time >= lookback_started_at,
        )
    ).all()

    history_items = [
        build_task_runtime_history_item(event)
        for event in runtime_events
    ]
    for command in commands:
        command_task_name = command.args_json.get("task_name")
        if command_task_name != task_name:
            continue
        history_items.append(build_task_command_history_item(command, task_name=task_name))

    history_items.sort(key=lambda item: (item.occurred_at, item.id), reverse=True)
    total = len(history_items)
    return history_items[offset : offset + limit], total


def build_recent_event_summary(event: TaskEvent, *, service: Service) -> RecentEventSummary:
    return RecentEventSummary(
        **build_task_event_summary(event).model_dump(),
        service_name=service.name,
        environment=service.environment,
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
    online_cutoff: datetime,
    health_cutoff: datetime,
) -> tuple[ServiceSummary, InstanceConnectivityCounts]:
    total, online, never_reported, last_seen_at = db.execute(
        select(
            health_participation_instance_count_expression(health_cutoff),
            online_instance_count_expression(online_cutoff),
            never_reported_instance_count_expression(health_cutoff),
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


def build_command_status_counts(rows: list[tuple[str, int]]) -> AgentCommandStatusCounts:
    counts = {status_value: 0 for status_value in AgentCommandStatusCounts.model_fields}
    for status_value, count in rows:
        if status_value in counts:
            counts[status_value] = count
    in_flight = counts["pending"] + counts["dispatched"] + counts["accepted"]
    total = (
        counts["pending"]
        + counts["dispatched"]
        + counts["accepted"]
        + counts["expired"]
        + counts["rejected"]
        + counts["succeeded"]
        + counts["failed"]
        + counts["timeout"]
        + counts["cancelled"]
    )
    counts["in_flight"] = in_flight
    counts["total"] = total
    return AgentCommandStatusCounts(**counts)


def get_service_command_overview(
    db: Session,
    *,
    service_id: UUID,
) -> AgentCommandOverview:
    expire_stale_commands(db, service_id=service_id)
    status_rows = db.execute(
        select(AgentCommand.status, func.count(AgentCommand.id))
        .where(AgentCommand.service_id == service_id)
        .group_by(AgentCommand.status)
    ).all()
    last_command_at, last_completed_at = db.execute(
        select(
            func.max(AgentCommand.created_at),
            func.max(AgentCommand.finished_at),
        ).where(AgentCommand.service_id == service_id)
    ).one()
    active_session_count = db.scalar(
        select(func.count())
        .select_from(AgentSession)
        .where(
            AgentSession.service_id == service_id,
            AgentSession.status == "active",
        )
    ) or 0
    return AgentCommandOverview(
        statuses=build_command_status_counts(
            [(status_value, count) for status_value, count in status_rows]
        ),
        active_session_count=active_session_count,
        last_command_at=last_command_at,
        last_completed_at=last_completed_at,
    )


def build_task_event_counts(*, counts_by_kind: dict[str, int]) -> TaskEventCounts:
    return TaskEventCounts(
        started=counts_by_kind.get("started", 0),
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
    lookback_minutes: int = 1,
    online_instance_count: int = 0,
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

    # Aggregate pause_requested across this service's instances so the dashboard
    # list can surface a "paused" status without hitting the per-instance detail
    # path. Tasks absent from the map keep summary.pause_requested=None (unknown).
    pause_requested_map = build_task_pause_requested_map(db, service_id=service_id)
    for task_name, pause_requested in pause_requested_map.items():
        summary = summaries.get(task_name)
        if summary is None:
            summary = TaskDashboardSummary(task_name=task_name, event_counts=TaskEventCounts())
            summaries[task_name] = summary
        summary.pause_requested = pause_requested

    supported_commands_map = build_task_supported_commands_map(db, service_id=service_id)
    for task_name, supported_commands in supported_commands_map.items():
        summary = summaries.get(task_name)
        if summary is None:
            summary = TaskDashboardSummary(task_name=task_name, event_counts=TaskEventCounts())
            summaries[task_name] = summary
        summary.supported_commands = supported_commands

    uptime_references = build_task_uptime_reference_map(
        db,
        service_id=service_id,
        task_names=summaries.keys(),
    )
    for task_name, uptime_reference_at in uptime_references.items():
        summaries[task_name].uptime_reference_at = uptime_reference_at

    # Finalise view-facing derived fields on every summary so clients never have
    # to recompute business state.
    for summary in summaries.values():
        finalize_task_summary(
            summary,
            lookback_minutes=lookback_minutes,
            online_instance_count=online_instance_count,
        )

    return summaries


def build_task_uptime_reference_map(
    db: Session,
    *,
    service_id: UUID,
    task_names: Iterable[str],
) -> dict[str, datetime]:
    task_name_list = list(task_names)
    if not task_name_list:
        return {}

    result: dict[str, datetime] = {}
    metric_rows = db.execute(
        select(
            TaskMetricWindow.task_name,
            func.max(TaskMetricWindow.window_ended_at),
        )
        .where(
            TaskMetricWindow.service_id == service_id,
            TaskMetricWindow.task_name.in_(task_name_list),
        )
        .group_by(TaskMetricWindow.task_name)
    ).all()
    for task_name, window_ended_at in metric_rows:
        if window_ended_at is not None:
            result[task_name] = window_ended_at

    event_rows = db.execute(
        select(
            TaskEvent.task_name,
            func.max(TaskEvent.occurred_at),
        )
        .where(
            TaskEvent.service_id == service_id,
            TaskEvent.task_name.in_(task_name_list),
        )
        .group_by(TaskEvent.task_name)
    ).all()
    for task_name, event_at in event_rows:
        if event_at is None:
            continue
        current = result.get(task_name)
        if current is None or event_at > current:
            result[task_name] = event_at
    return result


def finalize_task_summary(
    summary: TaskDashboardSummary,
    *,
    lookback_minutes: int,
    online_instance_count: int,
) -> None:
    """Populate view-facing derived fields on a TaskDashboardSummary in place."""
    # error_count blends the authoritative event-sourced failure counts
    # (event_counts.failed / dead_lettered) with metric-sourced timeouts,
    # which have no corresponding TaskEvent kind. This keeps the failing-task
    # signal consistent with the historical event-based service aggregate.
    error_count = compute_error_count(
        failed=summary.event_counts.failed,
        dead_lettered=summary.event_counts.dead_lettered,
        timeouts=summary.timeouts,
    )
    summary.error_count = error_count
    summary.success_rate = compute_success_rate(
        succeeded=summary.succeeded,
        failed=summary.failed,
        dead_lettered=summary.dead_lettered,
        cancelled=summary.cancelled,
    )
    summary.throughput_per_min = compute_throughput_per_min(
        fetched=summary.fetched,
        lookback_minutes=lookback_minutes,
    )
    summary.retry_attempts = extract_retry_attempts(summary.retry_policy)
    summary.source_label = extract_source_label(
        source_name=summary.source_name,
        source_config=summary.source_config,
    )
    summary.sink_label = extract_sink_label(summary.emit)
    summary.config_yaml = build_task_config_yaml(summary)
    uptime_reference_candidates = [
        candidate
        for candidate in (summary.latest_window_ended_at, summary.last_event_at)
        if candidate is not None
    ]
    if summary.uptime_reference_at is None:
        summary.uptime_reference_at = (
            max(uptime_reference_candidates) if uptime_reference_candidates else None
        )
    summary.view_status = derive_task_view_status(
        online_instance_count=online_instance_count,
        pause_requested=summary.pause_requested,
        error_count=error_count,
        started=summary.started,
        succeeded=summary.succeeded,
    )


def get_service_topology_hashes(
    db: Session,
    *,
    service_id: UUID,
    cutoff: datetime,
) -> list[str]:
    return list(
        db.scalars(
            select(Instance.last_topology_hash)
            .where(
                Instance.service_id == service_id,
                Instance.last_topology_hash.is_not(None),
                Instance.last_seen_at.is_not(None),
                Instance.last_seen_at >= cutoff,
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
