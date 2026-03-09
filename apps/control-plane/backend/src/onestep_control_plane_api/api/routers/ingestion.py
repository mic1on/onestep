from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.common import (
    apply_heartbeat_snapshot,
    apply_sync_snapshot,
    as_utc,
    create_instance_stub,
    dedupe_by_key,
    ensure_instance_identity_matches,
    ensure_service,
    existing_event_ids,
    existing_metric_keys,
    get_instance,
    is_newer_heartbeat,
    is_newer_sync,
    utcnow,
)
from onestep_control_plane_api.api.ingestion_support import (
    build_metric_window,
    build_task_event,
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
    instance = get_instance(db, request.service.instance_id)
    apply_sync = instance is None
    topology_changed = instance is None

    if instance is None:
        service = ensure_service(db, request.service, update_existing_version=True)
        service.latest_topology_hash = request.app.topology_hash
        service.latest_sync_at = received_at
        instance = create_instance_stub(
            service=service,
            identity=request.service,
            runtime=request.runtime,
            last_sync_at=received_at,
            last_topology_hash=request.app.topology_hash,
            app_snapshot_json=app_snapshot_json,
            last_sync_sent_at=sent_at,
            last_sync_sequence=request.sequence,
        )
        db.add(instance)
        db.flush()
    else:
        ensure_instance_identity_matches(instance, request.service)
        service = instance.service
        if is_newer_sync(instance, sent_at=sent_at, sequence=request.sequence):
            apply_sync = True
            topology_changed = instance.last_topology_hash != request.app.topology_hash
            service = ensure_service(db, request.service, update_existing_version=True)
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
    instance = get_instance(db, request.service.instance_id)

    if instance is None:
        service = ensure_service(db, request.service, update_existing_version=True)
        instance = create_instance_stub(
            service=service,
            identity=request.service,
            runtime=request.runtime,
            status_value=request.health.status,
            last_seen_at=received_at,
            last_heartbeat_sent_at=sent_at,
            last_heartbeat_sequence=request.sequence,
        )
        db.add(instance)
        db.flush()
    else:
        ensure_instance_identity_matches(instance, request.service)
        if is_newer_heartbeat(instance, sent_at=sent_at, sequence=request.sequence):
            service = ensure_service(db, request.service, update_existing_version=True)
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
    instance = get_instance(db, request.service.instance_id)
    if instance is None:
        service = ensure_service(db, request.service, update_existing_version=False)
        instance = create_instance_stub(service=service, identity=request.service)
        db.add(instance)
        db.flush()
    else:
        ensure_instance_identity_matches(instance, request.service)
        service = instance.service

    tasks = dedupe_by_key(
        request.tasks,
        lambda task: (request.service.instance_id, task.task_name, task.window_id),
    )
    present_keys = existing_metric_keys(
        db,
        request.service.instance_id,
        [(task.task_name, task.window_id) for task in tasks],
    )
    inserted_count = 0

    for task in tasks:
        key = (request.service.instance_id, task.task_name, task.window_id)
        if key in present_keys:
            continue
        db.add(build_metric_window(service, request.service, task, request, received_at))
        inserted_count += 1

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
    instance = get_instance(db, request.service.instance_id)
    if instance is None:
        service = ensure_service(db, request.service, update_existing_version=False)
        instance = create_instance_stub(service=service, identity=request.service)
        db.add(instance)
        db.flush()
    else:
        ensure_instance_identity_matches(instance, request.service)
        service = instance.service

    events = dedupe_by_key(request.events, lambda event: event.event_id)
    present_event_ids = existing_event_ids(db, {event.event_id for event in events})
    inserted_count = 0

    for event in events:
        if event.event_id in present_event_ids:
            continue
        db.add(build_task_event(service, request.service, event, received_at))
        inserted_count += 1

    db.commit()
    return EventsAcceptedResponse(received_at=received_at, ingested_count=inserted_count)
