from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.connector_service import (
    build_runtime_connector_payload,
    get_connector_or_404,
)
from onestep_control_plane_api.api.schemas import (
    WorkerCreateRequest,
    WorkerDeployRequest,
    WorkerUpdateRequest,
)
from onestep_control_plane_api.api.worker_agent_service import (
    build_worker_deployment_summary,
    create_worker_deployment,
    create_workflow_package,
    get_workflow_package_or_404,
    get_workflow_package_path_or_404,
)
from onestep_control_plane_api.api.worker_compiler import compile_worker_yaml, merge_package
from onestep_control_plane_api.db.models import Worker


def _serialize(worker: Worker) -> dict[str, object]:
    return {
        "id": str(worker.id),
        "name": worker.name,
        "description": worker.description,
        "handler_package_id": str(worker.handler_package_id) if worker.handler_package_id else None,
        "handler_ref": worker.handler_ref,
        "source_config": worker.source_config,
        "sink_configs": worker.sink_configs,
        "env": worker.env_json or {},
        "status": worker.status,
        "created_at": worker.created_at.isoformat() if worker.created_at else None,
        "updated_at": worker.updated_at.isoformat() if worker.updated_at else None,
    }


def list_workers(db: Session) -> list[dict[str, object]]:
    rows = db.execute(select(Worker).order_by(Worker.updated_at.desc())).scalars().all()
    return [_serialize(w) for w in rows]


def get_worker_or_404(db: Session, worker_id: UUID) -> Worker:
    worker = db.get(Worker, worker_id)
    if worker is None:
        raise HTTPException(status_code=404, detail="worker not found")
    return worker


def create_worker(db: Session, request: WorkerCreateRequest) -> dict[str, object]:
    existing = db.scalar(select(Worker).where(Worker.name == request.name))
    if existing is not None:
        raise HTTPException(status_code=409, detail="worker name already exists")
    worker = Worker(
        id=uuid4(),
        name=request.name,
        description=request.description,
        handler_package_id=UUID(request.handler_package_id) if request.handler_package_id else None,
        handler_ref=request.handler_ref,
        source_config=request.source_config.model_dump(),
        sink_configs=[s.model_dump() for s in request.sink_configs],
        env_json=request.env,
        status="draft",
    )
    db.add(worker)
    db.commit()
    db.refresh(worker)
    return _serialize(worker)


def update_worker(db: Session, worker_id: UUID, request: WorkerUpdateRequest) -> dict[str, object]:
    worker = get_worker_or_404(db, worker_id)
    if request.name is not None:
        dup = db.scalar(select(Worker).where(Worker.name == request.name, Worker.id != worker_id))
        if dup is not None:
            raise HTTPException(status_code=409, detail="worker name already exists")
        worker.name = request.name
    if request.description is not None:
        worker.description = request.description
    if request.handler_package_id is not None:
        worker.handler_package_id = (
            UUID(request.handler_package_id) if request.handler_package_id else None
        )
    if request.handler_ref is not None:
        worker.handler_ref = request.handler_ref
    if request.source_config is not None:
        worker.source_config = request.source_config.model_dump()
    if request.sink_configs is not None:
        worker.sink_configs = [s.model_dump() for s in request.sink_configs]
    if request.env is not None:
        worker.env_json = request.env
    if request.status is not None:
        worker.status = request.status
    db.commit()
    db.refresh(worker)
    return _serialize(worker)


def delete_worker(db: Session, worker_id: UUID) -> None:
    worker = get_worker_or_404(db, worker_id)
    db.delete(worker)
    db.commit()


def _resolve_connectors(db: Session, worker: Worker) -> dict[str, dict[str, object]]:
    """Collect and decrypt all connectors referenced by source + sinks."""
    connector_ids: set[str] = set()
    src = worker.source_config
    if isinstance(src, dict) and src.get("connector_id"):
        connector_ids.add(src["connector_id"])
    for sink in worker.sink_configs:
        if isinstance(sink, dict) and sink.get("connector_id"):
            connector_ids.add(sink["connector_id"])

    resolved: dict[str, dict[str, object]] = {}
    for cid in connector_ids:
        connector = get_connector_or_404(db, UUID(cid))
        resolved[cid] = build_runtime_connector_payload(connector)
    return resolved


def deploy_worker(db: Session, worker_id: UUID, request: WorkerDeployRequest) -> dict[str, object]:
    worker = get_worker_or_404(db, worker_id)
    if worker.handler_package_id is None:
        raise HTTPException(status_code=422, detail="worker has no handler package")

    handler_pkg = get_workflow_package_or_404(db, worker.handler_package_id)
    handler_path = get_workflow_package_path_or_404(handler_pkg)
    handler_bytes = handler_path.read_bytes()

    connectors = _resolve_connectors(db, worker)
    worker_dict = {
        "name": worker.name,
        "handler_ref": worker.handler_ref,
        "source": worker.source_config,
        "sinks": worker.sink_configs,
    }
    yaml_str = compile_worker_yaml(worker_dict, connectors)
    merged_bytes = merge_package(handler_bytes, yaml_str)

    merged_pkg = create_workflow_package(
        db,
        workflow_id=uuid4(),
        version=worker.updated_at.isoformat() if worker.updated_at else "deploy",
        filename=f"{worker.name}.zip",
        content_type="application/zip",
        content=merged_bytes,
        entrypoint="worker.yaml",
        created_by="worker-builder",
    )
    from onestep_control_plane_api.api.schemas import WorkerDeploymentCreateRequest

    deployment = create_worker_deployment(
        db,
        WorkerDeploymentCreateRequest(
            workflow_package_id=str(merged_pkg.package_id),
            worker_agent_id=request.worker_agent_id,
            desired_status=request.desired_status,
            env=request.env if request.env is not None else worker.env_json,
        ),
        created_by="worker-builder",
    )
    return build_worker_deployment_summary(deployment).model_dump()
