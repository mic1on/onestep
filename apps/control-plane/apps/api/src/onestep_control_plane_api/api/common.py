from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable
from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.schemas import RuntimeDescriptor, ServiceDescriptor
from onestep_control_plane_api.db.models import Instance, Service, TaskEvent, TaskMetricWindow

T = TypeVar("T")


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)


def dedupe_by_key(items: Iterable[T], key_fn: Callable[[T], Hashable]) -> list[T]:
    deduped: dict[object, T] = {}
    for item in items:
        deduped.setdefault(key_fn(item), item)
    return list(deduped.values())


def get_service(
    db: Session,
    identity: ServiceDescriptor,
) -> Service | None:
    return db.scalar(
        select(Service).where(
            Service.name == identity.name,
            Service.environment == identity.environment,
        )
    )


def ensure_service(
    db: Session,
    identity: ServiceDescriptor,
    *,
    update_existing_version: bool,
) -> Service:
    service = get_service(db, identity)
    if service is None:
        service = Service(
            name=identity.name,
            environment=identity.environment,
            latest_deployment_version=identity.deployment_version,
        )
        db.add(service)
        db.flush()
        return service

    if update_existing_version:
        service.latest_deployment_version = identity.deployment_version
    return service


def get_instance(
    db: Session,
    instance_id: UUID,
) -> Instance | None:
    return db.scalar(select(Instance).where(Instance.instance_id == instance_id))


def create_instance_stub(
    service: Service,
    identity: ServiceDescriptor,
    *,
    runtime: RuntimeDescriptor | None = None,
    status_value: str | None = None,
    last_sync_at: datetime | None = None,
    last_topology_hash: str | None = None,
    app_snapshot_json: dict[str, object] | None = None,
    last_sync_sent_at: datetime | None = None,
    last_sync_sequence: int | None = None,
    last_seen_at: datetime | None = None,
    last_heartbeat_sent_at: datetime | None = None,
    last_heartbeat_sequence: int | None = None,
) -> Instance:
    return Instance(
        service=service,
        instance_id=identity.instance_id,
        node_name=identity.node_name,
        hostname=runtime.hostname if runtime is not None else None,
        pid=runtime.pid if runtime is not None else None,
        deployment_version=identity.deployment_version,
        onestep_version=runtime.onestep_version if runtime is not None else None,
        python_version=runtime.python_version if runtime is not None else None,
        started_at=as_utc(runtime.started_at) if runtime is not None else None,
        last_sync_at=last_sync_at,
        last_topology_hash=last_topology_hash,
        app_snapshot_json=app_snapshot_json,
        last_sync_sent_at=last_sync_sent_at,
        last_sync_sequence=last_sync_sequence,
        last_heartbeat_sent_at=last_heartbeat_sent_at,
        last_heartbeat_sequence=last_heartbeat_sequence,
        last_seen_at=last_seen_at,
        status=status_value or "unknown",
    )


def ensure_instance_identity_matches(
    instance: Instance,
    identity: ServiceDescriptor,
) -> None:
    if (
        instance.service.name == identity.name
        and instance.service.environment == identity.environment
    ):
        return

    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "instance_id is already bound to "
            f"{instance.service.name}/{instance.service.environment} and cannot be reused by "
            f"{identity.name}/{identity.environment}"
        ),
    )


def is_newer_heartbeat(
    instance: Instance,
    *,
    sent_at: datetime,
    sequence: int,
) -> bool:
    return is_newer_ingestion(
        last_sequence=instance.last_heartbeat_sequence,
        last_sent_at=instance.last_heartbeat_sent_at,
        sent_at=sent_at,
        sequence=sequence,
    )


def is_newer_sync(
    instance: Instance,
    *,
    sent_at: datetime,
    sequence: int,
) -> bool:
    return is_newer_ingestion(
        last_sequence=instance.last_sync_sequence,
        last_sent_at=instance.last_sync_sent_at,
        sent_at=sent_at,
        sequence=sequence,
    )


def is_newer_ingestion(
    *,
    last_sequence: int | None,
    last_sent_at: datetime | None,
    sent_at: datetime,
    sequence: int,
) -> bool:
    if last_sequence is None:
        return True
    if sequence > last_sequence:
        return True
    if sequence < last_sequence:
        return False

    if last_sent_at is None:
        return True
    return sent_at > last_sent_at


def apply_heartbeat_snapshot(
    instance: Instance,
    *,
    service: Service,
    identity: ServiceDescriptor,
    runtime: RuntimeDescriptor,
    status_value: str,
    sent_at: datetime,
    sequence: int,
    received_at: datetime,
) -> None:
    instance.service = service
    instance.node_name = identity.node_name
    instance.hostname = runtime.hostname
    instance.pid = runtime.pid
    instance.deployment_version = identity.deployment_version
    instance.onestep_version = runtime.onestep_version
    instance.python_version = runtime.python_version
    instance.started_at = as_utc(runtime.started_at)
    instance.last_heartbeat_sent_at = sent_at
    instance.last_heartbeat_sequence = sequence
    instance.last_seen_at = received_at
    instance.status = status_value


def apply_sync_snapshot(
    instance: Instance,
    *,
    service: Service,
    identity: ServiceDescriptor,
    runtime: RuntimeDescriptor,
    topology_hash: str,
    app_snapshot_json: dict[str, object],
    sent_at: datetime,
    sequence: int,
    received_at: datetime,
) -> None:
    instance.service = service
    instance.node_name = identity.node_name
    instance.hostname = runtime.hostname
    instance.pid = runtime.pid
    instance.deployment_version = identity.deployment_version
    instance.onestep_version = runtime.onestep_version
    instance.python_version = runtime.python_version
    instance.started_at = as_utc(runtime.started_at)
    instance.last_sync_at = received_at
    instance.last_topology_hash = topology_hash
    instance.app_snapshot_json = app_snapshot_json
    instance.last_sync_sent_at = sent_at
    instance.last_sync_sequence = sequence


def existing_metric_keys(
    db: Session,
    instance_id: UUID,
    task_keys: list[tuple[str, str]],
) -> set[tuple[UUID, str, str]]:
    if not task_keys:
        return set()

    task_names = {task_name for task_name, _ in task_keys}
    window_ids = {window_id for _, window_id in task_keys}
    rows = db.execute(
        select(
            TaskMetricWindow.instance_id,
            TaskMetricWindow.task_name,
            TaskMetricWindow.window_id,
        ).where(
            TaskMetricWindow.instance_id == instance_id,
            TaskMetricWindow.task_name.in_(task_names),
            TaskMetricWindow.window_id.in_(window_ids),
        )
    )
    return {(row.instance_id, row.task_name, row.window_id) for row in rows}


def existing_event_ids(db: Session, event_ids: set[str]) -> set[str]:
    if not event_ids:
        return set()
    return set(
        db.scalars(select(TaskEvent.event_id).where(TaskEvent.event_id.in_(event_ids))).all()
    )
