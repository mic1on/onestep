from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
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


def ingest_sync_request(
    db: Session,
    request: SyncIngestRequest,
    *,
    received_at: datetime | None = None,
) -> SyncAcceptedResponse:
    received_at = received_at or utcnow()
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


def ingest_heartbeat_request(
    db: Session,
    request: HeartbeatIngestRequest,
    *,
    received_at: datetime | None = None,
) -> IngestionAcceptedResponse:
    received_at = received_at or utcnow()
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


def ingest_metrics_request(
    db: Session,
    request: MetricsIngestRequest,
    *,
    received_at: datetime | None = None,
) -> MetricsAcceptedResponse:
    received_at = received_at or utcnow()
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
    return MetricsAcceptedResponse(received_at=received_at, ingested_count=inserted_count)


def ingest_events_request(
    db: Session,
    request: EventsIngestRequest,
    *,
    received_at: datetime | None = None,
) -> EventsAcceptedResponse:
    received_at = received_at or utcnow()
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
    if inserted_event_ids:
        inserted_events = db.scalars(
            select(TaskEvent)
            .options(selectinload(TaskEvent.service))
            .where(TaskEvent.event_id.in_(inserted_event_ids))
            .order_by(TaskEvent.created_at)
        ).all()
        dispatch_runtime_task_event_notifications(db, task_events=inserted_events)
    return EventsAcceptedResponse(received_at=received_at, ingested_count=inserted_count)
