from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.common import as_utc, build_insert_statement
from onestep_control_plane_api.api.schemas import (
    AppTopologyDescriptor,
    MetricsIngestRequest,
    ServiceDescriptor,
    TaskEventRecord,
    TaskMetricWindowIngest,
    TaskTopologyIngest,
)
from onestep_control_plane_api.db.models import Service, TaskDefinition


def build_metric_window_payload(
    service: Service,
    identity: ServiceDescriptor,
    task: TaskMetricWindowIngest,
    request: MetricsIngestRequest,
    received_at,
) -> dict[str, object]:
    return {
        "service_id": service.id,
        "instance_id": identity.instance_id,
        "task_name": task.task_name,
        "window_id": task.window_id,
        "window_started_at": as_utc(request.window.started_at),
        "window_ended_at": as_utc(request.window.ended_at),
        "fetched": task.fetched,
        "started": task.started,
        "succeeded": task.succeeded,
        "retried": task.retried,
        "failed": task.failed,
        "dead_lettered": task.dead_lettered,
        "cancelled": task.cancelled,
        "timeouts": task.timeouts,
        "inflight": task.inflight,
        "avg_duration_ms": task.avg_duration_ms,
        "p95_duration_ms": task.p95_duration_ms,
        "received_at": received_at,
    }


def build_task_event_payload(
    service: Service,
    identity: ServiceDescriptor,
    event: TaskEventRecord,
    received_at,
) -> dict[str, object]:
    return {
        "event_id": event.event_id,
        "service_id": service.id,
        "instance_id": identity.instance_id,
        "task_name": event.task_name,
        "kind": event.kind,
        "occurred_at": as_utc(event.occurred_at),
        "attempts": event.attempts,
        "duration_ms": event.duration_ms,
        "failure_kind": event.failure.kind if event.failure is not None else None,
        "exception_type": event.failure.exception_type if event.failure is not None else None,
        "message": event.failure.message if event.failure is not None else None,
        "meta_json": event.meta,
        "received_at": received_at,
    }


def build_task_definition_payload(
    task: TaskTopologyIngest,
    *,
    topology_hash: str,
) -> dict[str, object]:
    return {
        "description": task.description,
        "source_name": task.source.name if task.source is not None else None,
        "source_kind": task.source.kind if task.source is not None else None,
        "source_config_json": task.source.config if task.source is not None else None,
        "emit_json": [connector.model_dump(mode="json") for connector in task.emit],
        "concurrency": task.concurrency,
        "timeout_s": task.timeout_s,
        "retry_policy": task.retry.model_dump(mode="json") if task.retry is not None else None,
        "topology_hash": topology_hash,
    }


def sync_task_definitions(
    db: Session,
    *,
    service: Service,
    app: AppTopologyDescriptor,
) -> None:
    incoming_task_names = {task.name for task in app.tasks}

    for task in app.tasks:
        payload = build_task_definition_payload(task, topology_hash=app.topology_hash)
        db.execute(
            build_insert_statement(db, TaskDefinition)
            .values(
                service_id=service.id,
                task_name=task.name,
                **payload,
            )
            .on_conflict_do_update(
                index_elements=["service_id", "task_name"],
                set_=payload,
            )
        )

    delete_stmt = delete(TaskDefinition).where(TaskDefinition.service_id == service.id)
    if incoming_task_names:
        delete_stmt = delete_stmt.where(TaskDefinition.task_name.not_in(incoming_task_names))
    db.execute(delete_stmt)
