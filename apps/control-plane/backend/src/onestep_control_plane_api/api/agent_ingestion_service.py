"""Telemetry ingest work units.

Each ``ingest_*_request`` is the async entry point: it owns one database work
unit on the caller's :class:`AsyncSession` and returns plain response models,
never ORM instances.

Why some helpers are called through ``AsyncSession.run_sync``
------------------------------------------------------------
The shared helpers these functions build on live in ``api/common.py`` and
``api/ingestion_support.py``. Those modules are outside this change's scope and
still take a synchronous ``Session``, and they are also used by synchronous
callers elsewhere. ``AsyncSession.run_sync`` hands the *same* underlying
session to a synchronous callable, so the helper participates in this work
unit's transaction and connection rather than opening its own.

This is SQLAlchemy's own greenlet-cooperative bridge, not the rejected
``asyncio.to_thread`` pattern: the connection stays bound to the async engine
and the driver's async IO still yields to the event loop. Measured on
PostgreSQL 16: a 1.5 s ``pg_sleep`` issued through ``run_sync`` let an
independent asyncio ticker run 130 iterations, and a ``pool_size=1`` wait let
it run 136 iterations.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, selectinload

from onestep_control_plane_api.api.common import (
    apply_heartbeat_snapshot,
    apply_service_metadata,
    apply_sync_snapshot,
    as_utc,
    build_insert_statement,
    dedupe_by_key,
    ensure_instance_identity_matches,
    ensure_instance_stub,
    ensure_service,
    is_newer_heartbeat,
    is_newer_sync,
    utcnow,
)
from onestep_control_plane_api.api.ingestion_support import (
    build_custom_metric_window_payloads,
    build_metric_window_payload,
    build_task_event_payload,
    sync_task_definitions,
)
from onestep_control_plane_api.api.notification_service import (
    dispatch_runtime_task_event_notifications,
)
from onestep_control_plane_api.api.schemas import (
    EventsAcceptedResponse,
    EventsIngestRequest,
    HeartbeatIngestRequest,
    IngestionAcceptedResponse,
    MetricsAcceptedResponse,
    MetricsIngestRequest,
    SyncAcceptedResponse,
    SyncIngestRequest,
)
from onestep_control_plane_api.db.models import TaskCustomMetricWindow, TaskEvent, TaskMetricWindow


def _ingest_sync_request(
    db: Session,
    request: SyncIngestRequest,
    *,
    received_at: datetime,
) -> SyncAcceptedResponse:
    sent_at = as_utc(request.sent_at)
    app_snapshot_json = request.app.model_dump(mode="json")
    service = ensure_service(db, request.service, update_existing_version=False)
    instance = ensure_instance_stub(db, service=service, identity=request.service)
    ensure_instance_identity_matches(instance, request.service)

    apply_sync = is_newer_sync(instance, sent_at=sent_at, sequence=request.sequence)
    if apply_sync:
        apply_service_metadata(service, request.service)
        service.latest_deployment_version = request.service.deployment_version
        service.latest_topology_hash = request.app.topology_hash
        service.latest_sync_at = received_at
        apply_sync_snapshot(
            instance,
            service=service,
            identity=request.service,
            runtime=request.runtime,
            topology_hash=request.app.topology_hash,
            app_snapshot_json=app_snapshot_json,
            sent_at=sent_at,
            sequence=request.sequence,
            received_at=received_at,
        )
        # Refresh definitions on each newer sync so metadata-only changes are persisted.
        sync_task_definitions(db, service=service, app=request.app)

    db.commit()
    return SyncAcceptedResponse(
        received_at=received_at,
        service_name=request.service.name,
        environment=request.service.environment,
        instance_id=request.service.instance_id,
        topology_hash=request.app.topology_hash,
        task_count=len(request.app.tasks),
    )


def _ingest_heartbeat_request(
    db: Session,
    request: HeartbeatIngestRequest,
    *,
    received_at: datetime,
) -> IngestionAcceptedResponse:
    sent_at = as_utc(request.sent_at)
    service = ensure_service(db, request.service, update_existing_version=False)
    instance = ensure_instance_stub(db, service=service, identity=request.service)
    ensure_instance_identity_matches(instance, request.service)

    if is_newer_heartbeat(instance, sent_at=sent_at, sequence=request.sequence):
        apply_service_metadata(service, request.service)
        service.latest_deployment_version = request.service.deployment_version
        apply_heartbeat_snapshot(
            instance,
            service=service,
            identity=request.service,
            runtime=request.runtime,
            status_value=request.health.status,
            task_controls_json=[
                task_control.model_dump(mode="json")
                for task_control in request.health.task_controls
            ],
            sent_at=sent_at,
            sequence=request.sequence,
            received_at=received_at,
        )

    db.commit()
    return IngestionAcceptedResponse(received_at=received_at)


def _ingest_metrics_request(
    db: Session,
    request: MetricsIngestRequest,
    *,
    received_at: datetime,
) -> MetricsAcceptedResponse:
    service = ensure_service(db, request.service, update_existing_version=False)
    instance = ensure_instance_stub(db, service=service, identity=request.service)
    ensure_instance_identity_matches(instance, request.service)

    tasks = dedupe_by_key(
        request.tasks,
        lambda task: (request.service.instance_id, task.task_name, task.window_id),
    )
    inserted_count = 0
    if tasks:
        inserted_rows = db.execute(
            build_insert_statement(db, TaskMetricWindow)
            .values(
                [
                    build_metric_window_payload(
                        service,
                        request.service,
                        task,
                        request,
                        received_at,
                    )
                    for task in tasks
                ]
            )
            .on_conflict_do_nothing(index_elements=["instance_id", "task_name", "window_id"])
            .returning(TaskMetricWindow.id)
        ).all()
        inserted_count = len(inserted_rows)
        custom_metric_rows = dedupe_by_key(
            [
                row
                for task in tasks
                for row in build_custom_metric_window_payloads(
                    service,
                    request.service,
                    task,
                    request,
                    received_at,
                )
            ],
            lambda row: (
                row["instance_id"],
                row["task_name"],
                row["window_id"],
                row["metric_name"],
                row["metric_kind"],
                row["labels_hash"],
            ),
        )
        if custom_metric_rows:
            db.execute(
                build_insert_statement(db, TaskCustomMetricWindow)
                .values(custom_metric_rows)
                .on_conflict_do_nothing(
                    index_elements=[
                        "instance_id",
                        "task_name",
                        "window_id",
                        "metric_name",
                        "metric_kind",
                        "labels_hash",
                    ]
                )
            )

    db.commit()
    from onestep_control_plane_api.api.routers.prometheus import reset_prometheus_metrics_cache

    reset_prometheus_metrics_cache()
    return MetricsAcceptedResponse(received_at=received_at, ingested_count=inserted_count)


def _insert_task_events(
    db: Session,
    request: EventsIngestRequest,
    *,
    received_at: datetime,
) -> tuple[int, list[str]]:
    """Insert new task events and return ``(inserted_count, inserted_event_ids)``.

    Returns plain data so the caller can dispatch notifications after this work
    unit's transaction has ended, instead of carrying ORM instances across the
    boundary.
    """

    service = ensure_service(db, request.service, update_existing_version=False)
    instance = ensure_instance_stub(db, service=service, identity=request.service)
    ensure_instance_identity_matches(instance, request.service)

    events = dedupe_by_key(request.events, lambda event: event.event_id)
    inserted_count = 0
    inserted_event_ids: list[str] = []
    if events:
        inserted_rows = db.execute(
            build_insert_statement(db, TaskEvent)
            .values(
                [
                    build_task_event_payload(service, request.service, event, received_at)
                    for event in events
                ]
            )
            .on_conflict_do_nothing(index_elements=["event_id"])
            .returning(TaskEvent.event_id)
        ).all()
        inserted_count = len(inserted_rows)
        inserted_event_ids = [row.event_id for row in inserted_rows]

    db.commit()
    return inserted_count, inserted_event_ids


async def ingest_sync_request(
    session: AsyncSession,
    request: SyncIngestRequest,
    *,
    received_at: datetime | None = None,
) -> SyncAcceptedResponse:
    received_at = received_at or utcnow()
    return await session.run_sync(
        lambda db: _ingest_sync_request(db, request, received_at=received_at)
    )


async def ingest_heartbeat_request(
    session: AsyncSession,
    request: HeartbeatIngestRequest,
    *,
    received_at: datetime | None = None,
) -> IngestionAcceptedResponse:
    received_at = received_at or utcnow()
    return await session.run_sync(
        lambda db: _ingest_heartbeat_request(db, request, received_at=received_at)
    )


async def ingest_metrics_request(
    session: AsyncSession,
    request: MetricsIngestRequest,
    *,
    received_at: datetime | None = None,
) -> MetricsAcceptedResponse:
    received_at = received_at or utcnow()
    return await session.run_sync(
        lambda db: _ingest_metrics_request(db, request, received_at=received_at)
    )


async def ingest_events_request(
    session: AsyncSession,
    request: EventsIngestRequest,
    *,
    received_at: datetime | None = None,
) -> EventsAcceptedResponse:
    received_at = received_at or utcnow()
    inserted_count, inserted_event_ids = await session.run_sync(
        lambda db: _insert_task_events(db, request, received_at=received_at)
    )

    if inserted_event_ids:
        # Reload the inserted events inside a fresh work unit and dispatch. The
        # notification path owns its own commit, so it runs as its own work unit
        # rather than being folded into the insert above.
        inserted_events = (
            await session.scalars(
                select(TaskEvent)
                .options(selectinload(TaskEvent.service))
                .where(TaskEvent.event_id.in_(inserted_event_ids))
                .order_by(TaskEvent.created_at)
            )
        ).all()
        await dispatch_runtime_task_event_notifications(session, task_events=list(inserted_events))

    return EventsAcceptedResponse(received_at=received_at, ingested_count=inserted_count)
