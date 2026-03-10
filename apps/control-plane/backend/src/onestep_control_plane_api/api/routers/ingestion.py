from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.common import (
    apply_heartbeat_snapshot,
    apply_sync_snapshot,
    as_utc,
    build_insert_statement,
    dedupe_by_key,
    ensure_instance_stub,
    ensure_instance_identity_matches,
    ensure_service,
    is_newer_heartbeat,
    is_newer_sync,
    utcnow,
)
from onestep_control_plane_api.api.ingestion_support import (
    build_metric_window_payload,
    build_task_event_payload,
    sync_task_definitions,
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
from onestep_control_plane_api.api.security import require_ingest_token
from onestep_control_plane_api.db.models import TaskEvent, TaskMetricWindow
from onestep_control_plane_api.db.session import get_db_session

router = APIRouter(
    prefix="/api/v1/agents",
    tags=["ingestion"],
    dependencies=[Depends(require_ingest_token)],
)


@router.post(
    "/sync",
    response_model=SyncAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def ingest_sync(
    request: SyncIngestRequest,
    db: Session = Depends(get_db_session),
) -> SyncAcceptedResponse:
    received_at = utcnow()
    sent_at = as_utc(request.sent_at)
    app_snapshot_json = request.app.model_dump(mode="json")
    service = ensure_service(db, request.service, update_existing_version=False)
    instance = ensure_instance_stub(db, service=service, identity=request.service)
    ensure_instance_identity_matches(instance, request.service)

    apply_sync = is_newer_sync(instance, sent_at=sent_at, sequence=request.sequence)
    topology_changed = instance.last_topology_hash != request.app.topology_hash

    if apply_sync:
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

    if apply_sync and topology_changed:
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


@router.post(
    "/heartbeat",
    response_model=IngestionAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def ingest_heartbeat(
    request: HeartbeatIngestRequest,
    db: Session = Depends(get_db_session),
) -> IngestionAcceptedResponse:
    received_at = utcnow()
    sent_at = as_utc(request.sent_at)
    service = ensure_service(db, request.service, update_existing_version=False)
    instance = ensure_instance_stub(db, service=service, identity=request.service)
    ensure_instance_identity_matches(instance, request.service)

    if is_newer_heartbeat(instance, sent_at=sent_at, sequence=request.sequence):
        service.latest_deployment_version = request.service.deployment_version
        apply_heartbeat_snapshot(
            instance,
            service=service,
            identity=request.service,
            runtime=request.runtime,
            status_value=request.health.status,
            sent_at=sent_at,
            sequence=request.sequence,
            received_at=received_at,
        )

    db.commit()
    return IngestionAcceptedResponse(received_at=received_at)


@router.post(
    "/metrics",
    response_model=MetricsAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def ingest_metrics(
    request: MetricsIngestRequest,
    db: Session = Depends(get_db_session),
) -> MetricsAcceptedResponse:
    received_at = utcnow()
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

    db.commit()
    return MetricsAcceptedResponse(received_at=received_at, ingested_count=inserted_count)


@router.post(
    "/events",
    response_model=EventsAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def ingest_events(
    request: EventsIngestRequest,
    db: Session = Depends(get_db_session),
) -> EventsAcceptedResponse:
    received_at = utcnow()
    service = ensure_service(db, request.service, update_existing_version=False)
    instance = ensure_instance_stub(db, service=service, identity=request.service)
    ensure_instance_identity_matches(instance, request.service)

    events = dedupe_by_key(request.events, lambda event: event.event_id)
    inserted_count = 0
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
            .returning(TaskEvent.id)
        ).all()
        inserted_count = len(inserted_rows)

    db.commit()
    return EventsAcceptedResponse(received_at=received_at, ingested_count=inserted_count)
