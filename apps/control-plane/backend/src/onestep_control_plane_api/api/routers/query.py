from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.common import as_utc, utcnow
from onestep_control_plane_api.api.constants import (
    DEFAULT_LOOKBACK_MINUTES,
    DEFAULT_PAGE_LIMIT,
    DEFAULT_RECENT_EVENT_LIMIT,
    DEFAULT_TASK_ACTIVITY_LIMIT,
    MAX_LOOKBACK_MINUTES,
    MAX_PAGE_LIMIT,
    MAX_RECENT_EVENT_LIMIT,
    MAX_TASK_ACTIVITY_LIMIT,
)
from onestep_control_plane_api.api.query_support import (
    build_instance_status_counts,
    build_instance_summary,
    build_metric_window_summary,
    build_service_stats_subquery,
    build_service_summary,
    build_task_event_summary,
    build_task_summary_map,
    get_service_instance_or_404,
    get_service_or_404,
    get_service_summary_data,
    get_service_topology_hashes,
    online_cutoff,
    service_has_task_data,
    sort_task_summaries,
)
from onestep_control_plane_api.api.schemas import (
    Environment,
    HealthStatus,
    InstanceConnectivity,
    InstanceDetailResponse,
    InstanceListResponse,
    ServiceDashboardResponse,
    ServiceListResponse,
    ServiceSummary,
    TaskDashboardListResponse,
    TaskDashboardSummary,
    TaskDetailResponse,
    TaskEventCounts,
    TaskEventKind,
    TaskEventListResponse,
    TaskMetricWindowListResponse,
)
from onestep_control_plane_api.api.security import require_console_auth
from onestep_control_plane_api.db.models import Instance, Service, TaskEvent, TaskMetricWindow
from onestep_control_plane_api.db.session import get_db_session

router = APIRouter(
    prefix="/api/v1",
    tags=["query"],
    dependencies=[Depends(require_console_auth)],
)


@router.get("/services", response_model=ServiceListResponse)
def list_services(
    environment: Environment | None = Query(default=None),
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db_session),
) -> ServiceListResponse:
    cutoff = online_cutoff(utcnow())
    filters = []
    if environment is not None:
        filters.append(Service.environment == environment)

    count_stmt = select(func.count()).select_from(Service)
    if filters:
        count_stmt = count_stmt.where(*filters)
    total = db.scalar(count_stmt) or 0

    stats = build_service_stats_subquery(cutoff)
    services_stmt = (
        select(
            Service,
            func.coalesce(stats.c.instance_count, 0),
            func.coalesce(stats.c.online_instance_count, 0),
            stats.c.last_seen_at,
        )
        .outerjoin(stats, Service.id == stats.c.service_id)
        .order_by(Service.environment, Service.name)
        .offset(offset)
        .limit(limit)
    )
    if filters:
        services_stmt = services_stmt.where(*filters)

    rows = db.execute(services_stmt).all()
    items = [
        build_service_summary(
            service,
            instance_count=instance_count,
            online_instance_count=online_instance_count,
            last_seen_at=last_seen_at,
        )
        for service, instance_count, online_instance_count, last_seen_at in rows
    ]
    return ServiceListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/services/{service_name}", response_model=ServiceSummary)
def get_service_summary(
    service_name: str,
    environment: Environment = Query(...),
    db: Session = Depends(get_db_session),
) -> ServiceSummary:
    service = get_service_or_404(db, service_name=service_name, environment=environment)
    cutoff = online_cutoff(utcnow())
    service_summary, _ = get_service_summary_data(db, service=service, cutoff=cutoff)
    return service_summary


@router.get("/services/{service_name}/dashboard", response_model=ServiceDashboardResponse)
def get_service_dashboard(
    service_name: str,
    environment: Environment = Query(...),
    lookback_minutes: int = Query(default=DEFAULT_LOOKBACK_MINUTES, ge=1, le=MAX_LOOKBACK_MINUTES),
    recent_event_limit: int = Query(
        default=DEFAULT_RECENT_EVENT_LIMIT,
        ge=1,
        le=MAX_RECENT_EVENT_LIMIT,
    ),
    db: Session = Depends(get_db_session),
) -> ServiceDashboardResponse:
    service = get_service_or_404(db, service_name=service_name, environment=environment)
    now = utcnow()
    cutoff = online_cutoff(now)
    lookback_started_at = now - timedelta(minutes=lookback_minutes)

    service_summary, instance_connectivity = get_service_summary_data(
        db,
        service=service,
        cutoff=cutoff,
    )
    status_rows = db.execute(
        select(Instance.status, func.count(Instance.id))
        .where(Instance.service_id == service.id)
        .group_by(Instance.status)
    ).all()
    task_summaries = sort_task_summaries(
        list(
            build_task_summary_map(
                db,
                service_id=service.id,
                lookback_started_at=lookback_started_at,
            ).values()
        )
    )
    recent_events = db.scalars(
        select(TaskEvent)
        .where(
            TaskEvent.service_id == service.id,
            TaskEvent.occurred_at >= lookback_started_at,
        )
        .order_by(TaskEvent.occurred_at.desc(), TaskEvent.event_id)
        .limit(recent_event_limit)
    ).all()
    topology_hashes = get_service_topology_hashes(db, service_id=service.id, cutoff=cutoff)

    return ServiceDashboardResponse(
        service=service_summary,
        lookback_minutes=lookback_minutes,
        lookback_started_at=lookback_started_at,
        instance_connectivity=instance_connectivity,
        instance_statuses=build_instance_status_counts(
            [(status_value, count) for status_value, count in status_rows]
        ),
        task_count=len(task_summaries),
        failing_task_count=sum(
            1
            for task_summary in task_summaries
            if task_summary.event_counts.failed > 0 or task_summary.event_counts.dead_lettered > 0
        ),
        topology_hashes=topology_hashes,
        topology_consistent=len(topology_hashes) <= 1,
        recent_events=[build_task_event_summary(event) for event in recent_events],
    )


@router.get("/services/{service_name}/tasks", response_model=TaskDashboardListResponse)
def list_service_tasks(
    service_name: str,
    environment: Environment = Query(...),
    lookback_minutes: int = Query(default=DEFAULT_LOOKBACK_MINUTES, ge=1, le=MAX_LOOKBACK_MINUTES),
    task_name: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db_session),
) -> TaskDashboardListResponse:
    service = get_service_or_404(db, service_name=service_name, environment=environment)
    lookback_started_at = utcnow() - timedelta(minutes=lookback_minutes)
    task_summaries = sort_task_summaries(
        list(
            build_task_summary_map(
                db,
                service_id=service.id,
                lookback_started_at=lookback_started_at,
            ).values()
        )
    )
    if task_name is not None:
        task_summaries = [
            task_summary for task_summary in task_summaries if task_summary.task_name == task_name
        ]

    total = len(task_summaries)
    items = task_summaries[offset : offset + limit]
    return TaskDashboardListResponse(
        lookback_minutes=lookback_minutes,
        lookback_started_at=lookback_started_at,
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/services/{service_name}/tasks/{task_name}", response_model=TaskDetailResponse)
def get_service_task_detail(
    service_name: str,
    task_name: str,
    environment: Environment = Query(...),
    lookback_minutes: int = Query(default=DEFAULT_LOOKBACK_MINUTES, ge=1, le=MAX_LOOKBACK_MINUTES),
    metric_window_limit: int = Query(
        default=DEFAULT_TASK_ACTIVITY_LIMIT,
        ge=1,
        le=MAX_TASK_ACTIVITY_LIMIT,
    ),
    event_limit: int = Query(
        default=DEFAULT_TASK_ACTIVITY_LIMIT,
        ge=1,
        le=MAX_TASK_ACTIVITY_LIMIT,
    ),
    db: Session = Depends(get_db_session),
) -> TaskDetailResponse:
    service = get_service_or_404(db, service_name=service_name, environment=environment)
    if not service_has_task_data(db, service_id=service.id, task_name=task_name):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"task {task_name} was not found for service {service_name}/{environment}",
        )

    now = utcnow()
    cutoff = online_cutoff(now)
    lookback_started_at = now - timedelta(minutes=lookback_minutes)
    service_summary, _ = get_service_summary_data(db, service=service, cutoff=cutoff)

    summary = build_task_summary_map(
        db,
        service_id=service.id,
        lookback_started_at=lookback_started_at,
    ).get(task_name)
    if summary is None:
        summary = TaskDashboardSummary(task_name=task_name, event_counts=TaskEventCounts())

    recent_metric_windows = db.scalars(
        select(TaskMetricWindow)
        .where(
            TaskMetricWindow.service_id == service.id,
            TaskMetricWindow.task_name == task_name,
            TaskMetricWindow.window_ended_at >= lookback_started_at,
        )
        .order_by(TaskMetricWindow.window_ended_at.desc(), TaskMetricWindow.window_id)
        .limit(metric_window_limit)
    ).all()
    recent_events = db.scalars(
        select(TaskEvent)
        .where(
            TaskEvent.service_id == service.id,
            TaskEvent.task_name == task_name,
            TaskEvent.occurred_at >= lookback_started_at,
        )
        .order_by(TaskEvent.occurred_at.desc(), TaskEvent.event_id)
        .limit(event_limit)
    ).all()

    return TaskDetailResponse(
        service=service_summary,
        task_name=task_name,
        lookback_minutes=lookback_minutes,
        lookback_started_at=lookback_started_at,
        summary=summary,
        recent_metric_windows=[
            build_metric_window_summary(metric_window) for metric_window in recent_metric_windows
        ],
        recent_events=[build_task_event_summary(event) for event in recent_events],
    )


@router.get("/services/{service_name}/instances", response_model=InstanceListResponse)
def list_service_instances(
    service_name: str,
    environment: Environment = Query(...),
    connectivity: InstanceConnectivity | None = Query(default=None),
    instance_status: HealthStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db_session),
) -> InstanceListResponse:
    service = get_service_or_404(db, service_name=service_name, environment=environment)
    cutoff = online_cutoff(utcnow())
    filters = [Instance.service_id == service.id]

    if instance_status is not None:
        filters.append(Instance.status == instance_status)

    if connectivity == "online":
        filters.extend([Instance.last_seen_at.is_not(None), Instance.last_seen_at >= cutoff])
    elif connectivity == "offline":
        filters.extend([Instance.last_seen_at.is_not(None), Instance.last_seen_at < cutoff])
    elif connectivity == "never_reported":
        filters.append(Instance.last_seen_at.is_(None))

    total = db.scalar(select(func.count()).select_from(Instance).where(*filters)) or 0
    instances = db.scalars(
        select(Instance)
        .where(*filters)
        .order_by(
            Instance.last_seen_at.is_(None),
            Instance.last_seen_at.desc(),
            Instance.instance_id,
        )
        .offset(offset)
        .limit(limit)
    ).all()

    items = [build_instance_summary(instance, cutoff=cutoff) for instance in instances]
    return InstanceListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/services/{service_name}/instances/{instance_id}",
    response_model=InstanceDetailResponse,
)
def get_service_instance_detail(
    service_name: str,
    instance_id: UUID,
    environment: Environment = Query(...),
    lookback_minutes: int = Query(default=DEFAULT_LOOKBACK_MINUTES, ge=1, le=MAX_LOOKBACK_MINUTES),
    metric_window_limit: int = Query(
        default=DEFAULT_TASK_ACTIVITY_LIMIT,
        ge=1,
        le=MAX_TASK_ACTIVITY_LIMIT,
    ),
    event_limit: int = Query(
        default=DEFAULT_TASK_ACTIVITY_LIMIT,
        ge=1,
        le=MAX_TASK_ACTIVITY_LIMIT,
    ),
    db: Session = Depends(get_db_session),
) -> InstanceDetailResponse:
    service = get_service_or_404(db, service_name=service_name, environment=environment)
    instance = get_service_instance_or_404(
        db,
        service=service,
        service_name=service_name,
        environment=environment,
        instance_id=instance_id,
    )

    now = utcnow()
    cutoff = online_cutoff(now)
    lookback_started_at = now - timedelta(minutes=lookback_minutes)
    service_summary, _ = get_service_summary_data(db, service=service, cutoff=cutoff)

    recent_metric_windows = db.scalars(
        select(TaskMetricWindow)
        .where(
            TaskMetricWindow.service_id == service.id,
            TaskMetricWindow.instance_id == instance_id,
            TaskMetricWindow.window_ended_at >= lookback_started_at,
        )
        .order_by(
            TaskMetricWindow.window_ended_at.desc(),
            TaskMetricWindow.task_name,
            TaskMetricWindow.window_id,
        )
        .limit(metric_window_limit)
    ).all()
    recent_events = db.scalars(
        select(TaskEvent)
        .where(
            TaskEvent.service_id == service.id,
            TaskEvent.instance_id == instance_id,
            TaskEvent.occurred_at >= lookback_started_at,
        )
        .order_by(TaskEvent.occurred_at.desc(), TaskEvent.event_id)
        .limit(event_limit)
    ).all()

    return InstanceDetailResponse(
        service=service_summary,
        lookback_minutes=lookback_minutes,
        lookback_started_at=lookback_started_at,
        instance=build_instance_summary(instance, cutoff=cutoff),
        app_snapshot=instance.app_snapshot_json,
        recent_metric_windows=[
            build_metric_window_summary(metric_window) for metric_window in recent_metric_windows
        ],
        recent_events=[build_task_event_summary(event) for event in recent_events],
    )


@router.get(
    "/services/{service_name}/metric-windows",
    response_model=TaskMetricWindowListResponse,
)
def list_service_metric_windows(
    service_name: str,
    environment: Environment = Query(...),
    task_name: str | None = Query(default=None),
    instance_id: UUID | None = Query(default=None),
    ended_after: datetime | None = Query(default=None),
    ended_before: datetime | None = Query(default=None),
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db_session),
) -> TaskMetricWindowListResponse:
    service = get_service_or_404(db, service_name=service_name, environment=environment)
    filters = [TaskMetricWindow.service_id == service.id]

    if task_name is not None:
        filters.append(TaskMetricWindow.task_name == task_name)
    if instance_id is not None:
        filters.append(TaskMetricWindow.instance_id == instance_id)
    if ended_after is not None:
        filters.append(TaskMetricWindow.window_ended_at >= as_utc(ended_after))
    if ended_before is not None:
        filters.append(TaskMetricWindow.window_ended_at <= as_utc(ended_before))

    total = db.scalar(select(func.count()).select_from(TaskMetricWindow).where(*filters)) or 0
    metric_windows = db.scalars(
        select(TaskMetricWindow)
        .where(*filters)
        .order_by(
            TaskMetricWindow.window_ended_at.desc(),
            TaskMetricWindow.task_name,
            TaskMetricWindow.window_id,
        )
        .offset(offset)
        .limit(limit)
    ).all()

    items = [build_metric_window_summary(metric_window) for metric_window in metric_windows]
    return TaskMetricWindowListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/services/{service_name}/events", response_model=TaskEventListResponse)
def list_service_events(
    service_name: str,
    environment: Environment = Query(...),
    task_name: str | None = Query(default=None),
    kind: TaskEventKind | None = Query(default=None),
    instance_id: UUID | None = Query(default=None),
    occurred_after: datetime | None = Query(default=None),
    occurred_before: datetime | None = Query(default=None),
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db_session),
) -> TaskEventListResponse:
    service = get_service_or_404(db, service_name=service_name, environment=environment)
    filters = [TaskEvent.service_id == service.id]

    if task_name is not None:
        filters.append(TaskEvent.task_name == task_name)
    if kind is not None:
        filters.append(TaskEvent.kind == kind)
    if instance_id is not None:
        filters.append(TaskEvent.instance_id == instance_id)
    if occurred_after is not None:
        filters.append(TaskEvent.occurred_at >= as_utc(occurred_after))
    if occurred_before is not None:
        filters.append(TaskEvent.occurred_at <= as_utc(occurred_before))

    total = db.scalar(select(func.count()).select_from(TaskEvent).where(*filters)) or 0
    events = db.scalars(
        select(TaskEvent)
        .where(*filters)
        .order_by(TaskEvent.occurred_at.desc(), TaskEvent.event_id)
        .offset(offset)
        .limit(limit)
    ).all()

    items = [build_task_event_summary(event) for event in events]
    return TaskEventListResponse(items=items, total=total, limit=limit, offset=offset)
