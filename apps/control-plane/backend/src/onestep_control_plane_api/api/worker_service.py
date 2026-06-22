from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.connector_service import (
    build_runtime_connector_payload,
    get_cipher,
    get_connector_or_404,
)
from onestep_control_plane_api.api.schemas import (
    WorkerCreateRequest,
    WorkerDeployRequest,
    WorkerUpdateRequest,
)
from onestep_control_plane_api.api.worker_agent_connection_registry import (
    worker_agent_connection_registry,
)
from onestep_control_plane_api.api.worker_agent_service import (
    build_worker_deployment_summary,
    create_start_deployment_command,
    create_worker_deployment,
    create_workflow_package,
    dispatch_worker_agent_command,
    get_workflow_package_or_404,
    get_workflow_package_path_or_404,
)
from onestep_control_plane_api.api.worker_compiler import (
    REPORTING_TOKEN_ENV,
    compile_worker_yaml,
    merge_package,
)
from onestep_control_plane_api.db.models import Worker

DEFAULT_REPORTING_CONFIG: dict[str, object] = {"mode": "platform", "endpoint_url": None}


@dataclass(frozen=True)
class CompiledWorkerPackage:
    content: bytes
    filename: str


def _normalize_reporting_config(config: dict[str, object] | None) -> dict[str, object]:
    raw = dict(config or {})
    mode = raw.get("mode") or "platform"
    if mode not in {"platform", "custom"}:
        raise HTTPException(status_code=422, detail="reporting_config.mode is invalid")
    endpoint_url = raw.get("endpoint_url")
    endpoint = str(endpoint_url).strip() if endpoint_url is not None else ""
    return {
        "mode": mode,
        "endpoint_url": endpoint or None,
    }


def _reporting_config_from_model(value) -> dict[str, object]:
    if value is None:
        return dict(DEFAULT_REPORTING_CONFIG)
    return _normalize_reporting_config(value.model_dump())


def _reporting_token_from_secret(value) -> str | None:
    if value is None or value.token is None:
        return None
    token = value.token.strip()
    return token or None


def _encrypt_reporting_token(token: str) -> str:
    return get_cipher().encrypt({"token": token})


def _decrypt_reporting_token(encrypted: str | None) -> str | None:
    if not encrypted:
        return None
    value = get_cipher().decrypt(encrypted).get("token")
    if value is None:
        return None
    token = str(value).strip()
    return token or None


def _validate_reporting_state(
    *,
    enabled: bool,
    config: dict[str, object],
    encrypted_secret: str | None,
) -> None:
    if config["mode"] != "custom":
        return
    if enabled and not config.get("endpoint_url"):
        raise HTTPException(status_code=422, detail="custom reporting endpoint_url is required")
    if enabled and not encrypted_secret:
        raise HTTPException(status_code=422, detail="custom reporting token is required")


def _prepare_reporting_for_create(
    request: WorkerCreateRequest,
) -> tuple[bool, dict[str, object], str | None]:
    enabled = request.reporting_enabled
    config = _reporting_config_from_model(request.reporting_config)
    encrypted_secret: str | None = None
    if config["mode"] == "custom":
        token = _reporting_token_from_secret(request.reporting_secret)
        encrypted_secret = _encrypt_reporting_token(token) if token else None
    else:
        config = dict(DEFAULT_REPORTING_CONFIG)
    _validate_reporting_state(enabled=enabled, config=config, encrypted_secret=encrypted_secret)
    return enabled, config, encrypted_secret


def _prepare_reporting_for_update(
    worker: Worker,
    request: WorkerUpdateRequest,
) -> tuple[bool, dict[str, object], str | None]:
    fields = request.model_fields_set
    enabled = worker.reporting_enabled is not False
    if "reporting_enabled" in fields and request.reporting_enabled is not None:
        enabled = request.reporting_enabled

    config = _normalize_reporting_config(worker.reporting_config_json)
    if "reporting_config" in fields and request.reporting_config is not None:
        config = _reporting_config_from_model(request.reporting_config)

    encrypted_secret = worker.reporting_secret_encrypted
    if config["mode"] == "platform":
        config = dict(DEFAULT_REPORTING_CONFIG)
        encrypted_secret = None
    else:
        token = None
        if "reporting_secret" in fields:
            token = _reporting_token_from_secret(request.reporting_secret)
        if token:
            encrypted_secret = _encrypt_reporting_token(token)
    _validate_reporting_state(enabled=enabled, config=config, encrypted_secret=encrypted_secret)
    return enabled, config, encrypted_secret


def _deployment_runtime_env(worker: Worker, env: dict[str, str]) -> dict[str, str]:
    runtime_env = dict(env)
    if worker.reporting_enabled is False:
        return runtime_env
    config = _normalize_reporting_config(worker.reporting_config_json)
    if config["mode"] != "custom":
        return runtime_env
    token = _decrypt_reporting_token(worker.reporting_secret_encrypted)
    if not token:
        raise HTTPException(status_code=422, detail="custom reporting token is required")
    runtime_env[REPORTING_TOKEN_ENV] = token
    return runtime_env


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
        "reporting_enabled": worker.reporting_enabled is not False,
        "reporting_config": _normalize_reporting_config(worker.reporting_config_json),
        "reporting_token_configured": bool(worker.reporting_secret_encrypted),
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
    reporting_enabled, reporting_config, reporting_secret_encrypted = (
        _prepare_reporting_for_create(request)
    )
    worker = Worker(
        id=uuid4(),
        name=request.name,
        description=request.description,
        handler_package_id=UUID(request.handler_package_id) if request.handler_package_id else None,
        handler_ref=request.handler_ref,
        source_config=request.source_config.model_dump(),
        sink_configs=[s.model_dump() for s in request.sink_configs],
        env_json=request.env,
        reporting_enabled=reporting_enabled,
        reporting_config_json=reporting_config,
        reporting_secret_encrypted=reporting_secret_encrypted,
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
    if {"reporting_enabled", "reporting_config", "reporting_secret"} & request.model_fields_set:
        (
            worker.reporting_enabled,
            worker.reporting_config_json,
            worker.reporting_secret_encrypted,
        ) = _prepare_reporting_for_update(worker, request)
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


def compile_worker_package(db: Session, worker: Worker) -> CompiledWorkerPackage:
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
        "reporting_enabled": worker.reporting_enabled,
        "reporting_config": _normalize_reporting_config(worker.reporting_config_json),
    }
    yaml_str = compile_worker_yaml(worker_dict, connectors)
    return CompiledWorkerPackage(
        content=merge_package(handler_bytes, yaml_str),
        filename=f"{worker.name}.zip",
    )


def compile_worker_package_for_download(db: Session, worker_id: UUID) -> CompiledWorkerPackage:
    return compile_worker_package(db, get_worker_or_404(db, worker_id))


async def deploy_worker(
    db: Session,
    worker_id: UUID,
    request: WorkerDeployRequest,
) -> dict[str, object]:
    worker = get_worker_or_404(db, worker_id)
    compiled_package = compile_worker_package(db, worker)

    merged_pkg = create_workflow_package(
        db,
        workflow_id=uuid4(),
        version=worker.updated_at.isoformat() if worker.updated_at else "deploy",
        filename=compiled_package.filename,
        content_type="application/zip",
        content=compiled_package.content,
        entrypoint="worker.yaml",
        created_by="worker-builder",
    )
    from onestep_control_plane_api.api.schemas import WorkerDeploymentCreateRequest

    deployment_env = request.env if request.env is not None else worker.env_json
    deployment = create_worker_deployment(
        db,
        WorkerDeploymentCreateRequest(
            workflow_package_id=str(merged_pkg.package_id),
            worker_agent_id=request.worker_agent_id,
            desired_status=request.desired_status,
            env=deployment_env,
        ),
        created_by="worker-builder",
    )
    if deployment.desired_status == "running":
        command = create_start_deployment_command(
            db,
            deployment=deployment,
            package=merged_pkg,
            env=_deployment_runtime_env(worker, deployment_env),
        )
        live_connection = await worker_agent_connection_registry.get(deployment.worker_agent_id)
        if live_connection is not None:
            await dispatch_worker_agent_command(
                db,
                command=command,
                send_queue=live_connection.send_queue,
                session_id=live_connection.session_id,
            )
    return build_worker_deployment_summary(deployment).model_dump()
