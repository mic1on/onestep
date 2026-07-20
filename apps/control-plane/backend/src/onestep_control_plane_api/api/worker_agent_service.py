from __future__ import annotations

import hashlib
import io
import json
import secrets
import zipfile
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.common import utcnow
from onestep_control_plane_api.api.schemas import (
    WorkerAgentCommandAckMessage,
    WorkerAgentCommandMessage,
    WorkerAgentCommandPayload,
    WorkerAgentCommandResultMessage,
    WorkerAgentHeartbeatMessage,
    WorkerAgentHelloAckMessage,
    WorkerAgentHelloAckPayload,
    WorkerAgentHelloMessage,
    WorkerAgentRegistrationRequest,
    WorkerAgentRegistrationResponse,
    WorkerAgentSummary,
    WorkerDeploymentCreateRequest,
    WorkerDeploymentEventMessage,
    WorkerDeploymentEventSummary,
    WorkerDeploymentSummary,
    WorkflowPackageSummary,
)
from onestep_control_plane_api.api.security import (
    hash_worker_agent_token,
    validate_worker_agent_registration_token_value,
)
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db.models import (
    WorkerAgent,
    WorkerAgentCommand,
    WorkerAgentSession,
    WorkerDeployment,
    WorkerDeploymentEvent,
    WorkflowPackage,
)

SUPPORTED_WORKER_AGENT_PROTOCOL_VERSION = "1"
DEFAULT_WORKER_AGENT_HEARTBEAT_INTERVAL_S = 30
DEFAULT_WORKFLOW_PACKAGE_ENTRYPOINT = "worker.yaml"
WORKFLOW_PACKAGE_MANIFEST_NAME = "onestep-package.json"
WORKER_AGENT_CAPABILITIES = frozenset(
    {
        "deployment.start",
        "deployment.stop",
        "deployment.restart",
        "agent.sync_state",
    }
)
WORKER_AGENT_COMMAND_CAPABILITY_BY_KIND = {
    "start_deployment": "deployment.start",
    "stop_deployment": "deployment.stop",
    "restart_deployment": "deployment.restart",
    "sync_agent_state": "agent.sync_state",
}
WORKER_AGENT_REDELIVERABLE_COMMAND_STATUSES = frozenset({"pending", "dispatched"})


def _package_storage_dir() -> Path:
    return Path(settings.worker_package_storage_dir).expanduser()


def _new_connection_token() -> str:
    return secrets.token_urlsafe(32)


def _new_message_id() -> str:
    return f"msg_{uuid4().hex}"


def _new_session_id() -> str:
    return f"worker_sess_{uuid4().hex}"


def _accepted_capabilities(requested: list[str]) -> list[str]:
    return sorted(
        {capability for capability in requested if capability in WORKER_AGENT_CAPABILITIES}
    )


def record_worker_deployment_event(
    db: Session,
    *,
    deployment_id: UUID,
    worker_agent_id: UUID,
    event_type: str,
    observed_status: str | None = None,
    message: str = "",
    payload: dict[str, object] | None = None,
) -> WorkerDeploymentEvent:
    event = WorkerDeploymentEvent(
        deployment_id=deployment_id,
        worker_agent_id=worker_agent_id,
        event_type=event_type,
        observed_status=observed_status,
        message=message,
        payload_json=payload or {},
    )
    db.add(event)
    return event


def build_worker_deployment_event_summary(
    event: WorkerDeploymentEvent,
) -> WorkerDeploymentEventSummary:
    return WorkerDeploymentEventSummary(
        deployment_id=event.deployment_id,
        worker_agent_id=event.worker_agent_id,
        event_type=event.event_type,
        observed_status=event.observed_status,
        message=event.message,
        payload=event.payload_json,
        created_at=event.created_at,
    )


def list_worker_deployment_events(
    db: Session,
    *,
    deployment_id: UUID,
    limit: int,
    offset: int,
) -> tuple[int, list[WorkerDeploymentEvent]]:
    total = (
        db.scalar(
            select(func.count())
            .select_from(WorkerDeploymentEvent)
            .where(WorkerDeploymentEvent.deployment_id == deployment_id)
        )
        or 0
    )
    items = db.scalars(
        select(WorkerDeploymentEvent)
        .where(WorkerDeploymentEvent.deployment_id == deployment_id)
        .order_by(WorkerDeploymentEvent.created_at.asc(), WorkerDeploymentEvent.id.asc())
        .limit(limit)
        .offset(offset)
    ).all()
    return total, list(items)


def handle_worker_agent_hello(
    db: Session,
    *,
    worker_agent: WorkerAgent,
    message: WorkerAgentHelloMessage,
    connected_at,
) -> WorkerAgentHelloAckMessage:
    if message.payload.protocol_version != SUPPORTED_WORKER_AGENT_PROTOCOL_VERSION:
        raise ValueError(
            f"protocol_version={message.payload.protocol_version} is not supported"
        )
    if message.payload.worker_agent_id != worker_agent.worker_agent_id:
        raise ValueError(
            "worker_agent_id does not match the authenticated connection token"
        )

    accepted_capabilities = _accepted_capabilities(message.payload.capabilities)
    session_id = _new_session_id()
    db.execute(
        update(WorkerAgentSession)
        .where(
            WorkerAgentSession.worker_agent_id == worker_agent.worker_agent_id,
            WorkerAgentSession.status == "active",
        )
        .values(
            status="disconnected",
            disconnected_at=connected_at,
            updated_at=connected_at,
        )
    )
    worker_agent.status = "online"
    worker_agent.max_concurrent_deployments = message.payload.max_concurrent_deployments
    worker_agent.used_slots = message.payload.used_slots
    worker_agent.capabilities_json = accepted_capabilities
    worker_agent.last_seen_at = connected_at
    worker_agent.updated_at = connected_at
    db.add(
        WorkerAgentSession(
            session_id=session_id,
            worker_agent_id=worker_agent.worker_agent_id,
            protocol_version=message.payload.protocol_version,
            status="active",
            capabilities_json=list(message.payload.capabilities),
            accepted_capabilities_json=accepted_capabilities,
            connected_at=connected_at,
            last_hello_at=connected_at,
            last_message_at=connected_at,
        )
    )
    db.commit()

    return WorkerAgentHelloAckMessage(
        type="hello_ack",
        message_id=_new_message_id(),
        sent_at=connected_at,
        payload=WorkerAgentHelloAckPayload(
            session_id=session_id,
            protocol_version=SUPPORTED_WORKER_AGENT_PROTOCOL_VERSION,
            heartbeat_interval_s=DEFAULT_WORKER_AGENT_HEARTBEAT_INTERVAL_S,
            accepted_capabilities=accepted_capabilities,
            server_time=connected_at,
        ),
    )


def mark_worker_agent_session_message(
    db: Session,
    *,
    session_id: str,
    occurred_at,
) -> None:
    db.execute(
        update(WorkerAgentSession)
        .where(WorkerAgentSession.session_id == session_id)
        .values(last_message_at=occurred_at, updated_at=occurred_at)
    )
    db.commit()


def apply_worker_agent_heartbeat(
    db: Session,
    *,
    worker_agent: WorkerAgent,
    session_id: str,
    message: WorkerAgentHeartbeatMessage,
    received_at,
) -> None:
    if message.payload.worker_agent_id != worker_agent.worker_agent_id:
        raise ValueError(
            "worker_agent_id does not match the authenticated connection token"
        )
    worker_agent.status = "online"
    worker_agent.used_slots = message.payload.used_slots
    worker_agent.last_seen_at = received_at
    worker_agent.updated_at = received_at
    db.execute(
        update(WorkerAgentSession)
        .where(WorkerAgentSession.session_id == session_id)
        .values(last_message_at=received_at, updated_at=received_at)
    )
    db.commit()


def apply_worker_deployment_event(
    db: Session,
    *,
    worker_agent: WorkerAgent,
    message: WorkerDeploymentEventMessage,
    received_at,
) -> bool:
    deployment = db.scalar(
        select(WorkerDeployment).where(
            WorkerDeployment.deployment_id == message.payload.deployment_id,
            WorkerDeployment.worker_agent_id == worker_agent.worker_agent_id,
        )
    )
    if deployment is None:
        return False

    if message.payload.observed_status is not None:
        deployment.observed_status = message.payload.observed_status
        deployment.updated_at = received_at
    record_worker_deployment_event(
        db,
        deployment_id=deployment.deployment_id,
        worker_agent_id=worker_agent.worker_agent_id,
        event_type=message.payload.event_type,
        observed_status=message.payload.observed_status,
        message=message.payload.message,
        payload={"source": "worker_agent", **message.payload.payload},
    )
    db.commit()
    return True


def get_worker_agent_command_or_404(db: Session, command_id: UUID) -> WorkerAgentCommand:
    command = db.scalar(
        select(WorkerAgentCommand).where(WorkerAgentCommand.command_id == command_id)
    )
    if command is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"worker agent command {command_id} was not found",
        )
    return command


def build_worker_agent_command_message(
    command: WorkerAgentCommand,
) -> WorkerAgentCommandMessage:
    now = utcnow()
    return WorkerAgentCommandMessage(
        type="command",
        message_id=_new_message_id(),
        sent_at=now,
        payload=WorkerAgentCommandPayload(
            command_id=command.command_id,
            kind=command.kind,
            deployment_id=command.deployment_id,
            timeout_s=command.timeout_s,
            args=command.args_json,
            created_at=command.created_at,
        ),
    )


def get_worker_agent_command_capability(kind: str) -> str | None:
    return WORKER_AGENT_COMMAND_CAPABILITY_BY_KIND.get(kind)


def list_redeliverable_worker_agent_commands(
    db: Session,
    *,
    worker_agent_id: UUID,
) -> list[WorkerAgentCommand]:
    return list(
        db.scalars(
            select(WorkerAgentCommand)
            .where(
                WorkerAgentCommand.worker_agent_id == worker_agent_id,
                WorkerAgentCommand.status.in_(WORKER_AGENT_REDELIVERABLE_COMMAND_STATUSES),
            )
            .order_by(WorkerAgentCommand.created_at.asc(), WorkerAgentCommand.command_id.asc())
        ).all()
    )


def reject_worker_agent_command_without_delivery(
    db: Session,
    *,
    command: WorkerAgentCommand,
    error_code: str,
    error_message: str,
) -> WorkerAgentCommand:
    now = utcnow()
    command.status = "rejected"
    command.error_code = error_code
    command.error_message = error_message
    command.finished_at = now
    command.updated_at = now
    _apply_worker_command_failure_to_deployment(
        db,
        command=command,
        error_code=error_code,
        error_message=error_message,
    )
    if command.deployment_id is not None:
        record_worker_deployment_event(
            db,
            deployment_id=command.deployment_id,
            worker_agent_id=command.worker_agent_id,
            event_type="command_rejected",
            observed_status="failed",
            message=error_message,
            payload={"command_id": str(command.command_id), "error_code": error_code},
        )
    db.commit()
    db.refresh(command)
    return command


def _deployment_package_args(
    *,
    deployment: WorkerDeployment,
    package: WorkflowPackage,
    env: dict[str, str] | None = None,
) -> dict[str, object]:
    return {
        "deployment_id": str(deployment.deployment_id),
        "package_id": str(package.package_id),
        "package_checksum": package.checksum_sha256,
        "download_url": f"/api/v1/workflow-packages/{package.package_id}/download",
        "entrypoint": package.entrypoint,
        "params": deployment.params_json,
        "env": env if env is not None else deployment.env_json,
        "credential_refs": deployment.credential_refs_json,
    }


def create_worker_deployment_command(
    db: Session,
    *,
    deployment: WorkerDeployment,
    kind: str,
    package: WorkflowPackage | None = None,
    env: dict[str, str] | None = None,
    timeout_s: int = 30,
) -> WorkerAgentCommand:
    args_json: dict[str, object] = {"deployment_id": str(deployment.deployment_id)}
    if kind in {"start_deployment", "restart_deployment"}:
        if package is None:
            raise ValueError(f"package is required when kind={kind}")
        args_json = _deployment_package_args(deployment=deployment, package=package, env=env)

    command = WorkerAgentCommand(
        command_id=uuid4(),
        worker_agent_id=deployment.worker_agent_id,
        deployment_id=deployment.deployment_id,
        kind=kind,
        args_json=args_json,
        timeout_s=timeout_s,
        status="pending",
    )
    db.add(command)
    record_worker_deployment_event(
        db,
        deployment_id=deployment.deployment_id,
        worker_agent_id=deployment.worker_agent_id,
        event_type="command_created",
        observed_status=deployment.observed_status,
        message=f"{kind} command created",
        payload={"command_id": str(command.command_id), "kind": kind},
    )
    db.commit()
    db.refresh(command)
    return command


def create_start_deployment_command(
    db: Session,
    *,
    deployment: WorkerDeployment,
    package: WorkflowPackage,
    env: dict[str, str] | None = None,
    timeout_s: int = 30,
) -> WorkerAgentCommand:
    return create_worker_deployment_command(
        db,
        deployment=deployment,
        kind="start_deployment",
        package=package,
        env=env,
        timeout_s=timeout_s,
    )


def create_stop_deployment_command(
    db: Session,
    *,
    deployment: WorkerDeployment,
    timeout_s: int = 30,
) -> WorkerAgentCommand:
    deployment.desired_status = "stopped"
    deployment.observed_status = "stopping"
    deployment.updated_at = utcnow()
    return create_worker_deployment_command(
        db,
        deployment=deployment,
        kind="stop_deployment",
        timeout_s=timeout_s,
    )


def create_restart_deployment_command(
    db: Session,
    *,
    deployment: WorkerDeployment,
    package: WorkflowPackage,
    env: dict[str, str] | None = None,
    timeout_s: int = 30,
) -> WorkerAgentCommand:
    deployment.desired_status = "running"
    deployment.observed_status = "assigned"
    deployment.updated_at = utcnow()
    return create_worker_deployment_command(
        db,
        deployment=deployment,
        kind="restart_deployment",
        package=package,
        env=env,
        timeout_s=timeout_s,
    )


async def dispatch_worker_agent_command(
    db: Session,
    *,
    command: WorkerAgentCommand,
    send_queue,
    session_id: str,
) -> WorkerAgentCommand:
    now = utcnow()
    command.status = "dispatched"
    command.session_id = session_id
    command.dispatched_at = now
    command.updated_at = now
    if command.deployment_id is not None:
        record_worker_deployment_event(
            db,
            deployment_id=command.deployment_id,
            worker_agent_id=command.worker_agent_id,
            event_type="command_dispatched",
            message=f"{command.kind} command dispatched",
            payload={
                "command_id": str(command.command_id),
                "kind": command.kind,
                "session_id": session_id,
            },
        )
    db.commit()
    db.refresh(command)
    await send_queue.put(build_worker_agent_command_message(command).model_dump(mode="json"))
    return command


def handle_worker_agent_command_ack(
    db: Session,
    *,
    worker_agent: WorkerAgent,
    session_id: str,
    message: WorkerAgentCommandAckMessage,
    received_at,
) -> bool:
    command = db.scalar(
        select(WorkerAgentCommand).where(
            WorkerAgentCommand.command_id == message.payload.command_id,
            WorkerAgentCommand.worker_agent_id == worker_agent.worker_agent_id,
        )
    )
    if command is None:
        return False
    if command.finished_at is not None or command.ack_status is not None:
        return True

    command.session_id = session_id
    command.ack_status = message.payload.status
    command.acked_at = received_at
    command.updated_at = received_at
    if message.payload.status == "accepted":
        command.status = "accepted"
        event_type = "command_acknowledged"
        event_status = None
    else:
        command.status = "rejected"
        command.finished_at = received_at
        command.error_code = message.payload.error_code
        command.error_message = message.payload.error_message
        event_type = "command_rejected"
        event_status = "failed"
        _apply_worker_command_failure_to_deployment(
            db,
            command=command,
            error_code=message.payload.error_code,
            error_message=message.payload.error_message,
        )
    if command.deployment_id is not None:
        record_worker_deployment_event(
            db,
            deployment_id=command.deployment_id,
            worker_agent_id=command.worker_agent_id,
            event_type=event_type,
            observed_status=event_status,
            message=f"{command.kind} command {message.payload.status}",
            payload={
                "command_id": str(command.command_id),
                "kind": command.kind,
                "ack_status": message.payload.status,
                "error_code": message.payload.error_code,
            },
        )
    db.commit()
    return True


def _apply_worker_command_failure_to_deployment(
    db: Session,
    *,
    command: WorkerAgentCommand,
    error_code: str | None,
    error_message: str | None,
) -> None:
    if command.deployment_id is None:
        return
    deployment = db.scalar(
        select(WorkerDeployment).where(
            WorkerDeployment.deployment_id == command.deployment_id
        )
    )
    if deployment is None:
        return
    deployment.observed_status = "failed"
    deployment.last_error_code = error_code
    deployment.last_error_message = error_message
    deployment.updated_at = utcnow()


def _apply_worker_command_success_to_deployment(
    db: Session,
    *,
    command: WorkerAgentCommand,
    result: dict[str, object] | None,
    finished_at,
) -> None:
    if command.deployment_id is None:
        return
    deployment = db.scalar(
        select(WorkerDeployment).where(
            WorkerDeployment.deployment_id == command.deployment_id
        )
    )
    if deployment is None:
        return

    if command.kind in {"start_deployment", "restart_deployment"}:
        deployment.observed_status = "running"
        deployment.started_at = finished_at
        if isinstance(result, dict):
            runtime_instance_id = result.get("runtime_instance_id")
            if isinstance(runtime_instance_id, str):
                try:
                    deployment.runtime_instance_id = UUID(runtime_instance_id)
                except ValueError:
                    pass
    elif command.kind == "stop_deployment":
        deployment.observed_status = "stopped"
        deployment.finished_at = finished_at
    deployment.last_error_code = None
    deployment.last_error_message = None
    deployment.updated_at = utcnow()


def _observed_status_for_successful_command(kind: str) -> str | None:
    if kind in {"start_deployment", "restart_deployment"}:
        return "running"
    if kind == "stop_deployment":
        return "stopped"
    return None


def handle_worker_agent_command_result(
    db: Session,
    *,
    worker_agent: WorkerAgent,
    session_id: str,
    message: WorkerAgentCommandResultMessage,
    received_at,
) -> str:
    command = db.scalar(
        select(WorkerAgentCommand).where(
            WorkerAgentCommand.command_id == message.payload.command_id,
            WorkerAgentCommand.worker_agent_id == worker_agent.worker_agent_id,
        )
    )
    if command is None:
        return "unknown"
    if command.finished_at is not None:
        return "duplicate"

    command.session_id = session_id
    command.status = message.payload.status
    command.finished_at = message.payload.finished_at
    command.result_json = message.payload.result
    command.error_code = message.payload.error_code
    command.error_message = message.payload.error_message
    if command.ack_status is None:
        command.ack_status = "accepted"
        command.acked_at = received_at
    command.updated_at = received_at
    if message.payload.status == "succeeded":
        _apply_worker_command_success_to_deployment(
            db,
            command=command,
            result=message.payload.result,
            finished_at=message.payload.finished_at,
        )
        if command.deployment_id is not None:
            record_worker_deployment_event(
                db,
                deployment_id=command.deployment_id,
                worker_agent_id=command.worker_agent_id,
                event_type="command_succeeded",
                observed_status=_observed_status_for_successful_command(command.kind),
                message=f"{command.kind} command succeeded",
                payload={
                    "command_id": str(command.command_id),
                    "kind": command.kind,
                    "result": message.payload.result or {},
                },
            )
    elif message.payload.status in {"failed", "timeout", "cancelled"}:
        _apply_worker_command_failure_to_deployment(
            db,
            command=command,
            error_code=message.payload.error_code or message.payload.status,
            error_message=message.payload.error_message,
        )
        if command.deployment_id is not None:
            record_worker_deployment_event(
                db,
                deployment_id=command.deployment_id,
                worker_agent_id=command.worker_agent_id,
                event_type="command_failed",
                observed_status="failed",
                message=message.payload.error_message or f"{command.kind} command failed",
                payload={
                    "command_id": str(command.command_id),
                    "kind": command.kind,
                    "status": message.payload.status,
                    "error_code": message.payload.error_code,
                },
            )
    db.commit()
    return "ok"


def close_worker_agent_session(
    db: Session,
    *,
    worker_agent_id: UUID,
    session_id: str,
    disconnected_at,
) -> None:
    db.execute(
        update(WorkerAgentSession)
        .where(
            WorkerAgentSession.session_id == session_id,
            WorkerAgentSession.status == "active",
        )
        .values(
            status="disconnected",
            disconnected_at=disconnected_at,
            last_message_at=disconnected_at,
            updated_at=disconnected_at,
        )
    )
    active_session_count = db.scalar(
        select(func.count())
        .select_from(WorkerAgentSession)
        .where(
            WorkerAgentSession.worker_agent_id == worker_agent_id,
            WorkerAgentSession.status == "active",
        )
    )
    if not active_session_count:
        worker_agent = get_worker_agent_or_404(db, worker_agent_id)
        worker_agent.status = "offline"
        worker_agent.updated_at = disconnected_at
    db.commit()


def register_worker_agent(
    db: Session,
    request: WorkerAgentRegistrationRequest,
) -> WorkerAgentRegistrationResponse:
    if not settings.worker_agent_registration_tokens:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="worker agent registration is not configured",
        )
    if not validate_worker_agent_registration_token_value(request.registration_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid worker agent registration token",
        )

    connection_token = _new_connection_token()
    worker_agent = WorkerAgent(
        worker_agent_id=uuid4(),
        display_name=request.display_name,
        status="offline",
        execution_mode=request.execution_mode,
        max_concurrent_deployments=request.max_concurrent_deployments,
        used_slots=0,
        labels_json=request.labels,
        capabilities_json=_accepted_capabilities(request.capabilities),
        agent_version=request.agent_version,
        onestep_version=request.onestep_version,
        python_version=request.python_version,
        platform_json=request.platform,
        connection_token_hash=hash_worker_agent_token(connection_token),
    )
    db.add(worker_agent)
    db.commit()
    db.refresh(worker_agent)

    return WorkerAgentRegistrationResponse(
        worker_agent_id=worker_agent.worker_agent_id,
        connection_token=connection_token,
        heartbeat_interval_s=DEFAULT_WORKER_AGENT_HEARTBEAT_INTERVAL_S,
        accepted_capabilities=worker_agent.capabilities_json,
    )


def get_worker_agent_or_404(db: Session, worker_agent_id: UUID) -> WorkerAgent:
    worker_agent = db.scalar(
        select(WorkerAgent).where(WorkerAgent.worker_agent_id == worker_agent_id)
    )
    if worker_agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"worker agent {worker_agent_id} was not found",
        )
    return worker_agent


def list_worker_agents(
    db: Session,
    *,
    limit: int,
    offset: int,
) -> tuple[int, list[WorkerAgent]]:
    total = db.scalar(select(func.count()).select_from(WorkerAgent)) or 0
    items = db.scalars(
        select(WorkerAgent)
        .order_by(WorkerAgent.created_at.desc(), WorkerAgent.worker_agent_id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return total, list(items)


def build_worker_agent_summary(worker_agent: WorkerAgent) -> WorkerAgentSummary:
    return WorkerAgentSummary(
        worker_agent_id=worker_agent.worker_agent_id,
        display_name=worker_agent.display_name,
        status=worker_agent.status,
        execution_mode=worker_agent.execution_mode,
        max_concurrent_deployments=worker_agent.max_concurrent_deployments,
        used_slots=worker_agent.used_slots,
        labels=worker_agent.labels_json,
        capabilities=worker_agent.capabilities_json,
        agent_version=worker_agent.agent_version,
        onestep_version=worker_agent.onestep_version,
        python_version=worker_agent.python_version,
        platform=worker_agent.platform_json,
        registered_at=worker_agent.registered_at,
        last_seen_at=worker_agent.last_seen_at,
        created_at=worker_agent.created_at,
        updated_at=worker_agent.updated_at,
    )


def create_workflow_package(
    db: Session,
    *,
    workflow_id: UUID,
    version: str,
    filename: str,
    content_type: str,
    content: bytes,
    entrypoint: str | None,
    created_by: str,
) -> WorkflowPackage:
    package_id = uuid4()
    checksum = hashlib.sha256(content).hexdigest()
    package_manifest = _read_workflow_package_manifest(content)
    resolved_entrypoint = _resolve_workflow_package_entrypoint(entrypoint, package_manifest)
    storage_dir = _package_storage_dir()
    storage_dir.mkdir(parents=True, exist_ok=True)
    storage_path = storage_dir / f"{package_id}.zip"
    storage_path.write_bytes(content)

    package = WorkflowPackage(
        package_id=package_id,
        workflow_id=workflow_id,
        version=version,
        filename=filename,
        content_type=content_type,
        checksum_sha256=checksum,
        size_bytes=len(content),
        storage_path=str(storage_path),
        entrypoint=resolved_entrypoint,
        metadata_json=(
            {"onestep_package_manifest": package_manifest}
            if package_manifest is not None
            else {}
        ),
        created_by=created_by,
    )
    db.add(package)
    db.commit()
    db.refresh(package)
    return package


def _read_workflow_package_manifest(content: bytes) -> dict[str, object] | None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if WORKFLOW_PACKAGE_MANIFEST_NAME not in archive.namelist():
                return None
            raw_manifest = archive.read(WORKFLOW_PACKAGE_MANIFEST_NAME)
    except zipfile.BadZipFile:
        return None

    try:
        manifest = json.loads(raw_manifest.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{WORKFLOW_PACKAGE_MANIFEST_NAME} is not valid JSON",
        ) from exc
    if not isinstance(manifest, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{WORKFLOW_PACKAGE_MANIFEST_NAME} must be a JSON object",
        )
    return manifest


def _resolve_workflow_package_entrypoint(
    entrypoint: str | None,
    package_manifest: dict[str, object] | None,
) -> str:
    if entrypoint is not None:
        stripped = entrypoint.strip()
        if stripped:
            return stripped

    if package_manifest is None or "entrypoint" not in package_manifest:
        return DEFAULT_WORKFLOW_PACKAGE_ENTRYPOINT

    manifest_entrypoint = package_manifest["entrypoint"]
    if not isinstance(manifest_entrypoint, str) or not manifest_entrypoint.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{WORKFLOW_PACKAGE_MANIFEST_NAME} entrypoint must be a non-empty string",
        )
    stripped_manifest_entrypoint = manifest_entrypoint.strip()
    if len(stripped_manifest_entrypoint) > 255:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{WORKFLOW_PACKAGE_MANIFEST_NAME} entrypoint must be at most 255 characters",
        )
    return stripped_manifest_entrypoint


def get_workflow_package_or_404(db: Session, package_id: UUID) -> WorkflowPackage:
    package = db.scalar(select(WorkflowPackage).where(WorkflowPackage.package_id == package_id))
    if package is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"workflow package {package_id} was not found",
        )
    return package


def get_workflow_package_path_or_404(package: WorkflowPackage) -> Path:
    path = Path(package.storage_path)
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"workflow package {package.package_id} content was not found",
        )
    return path


def require_worker_agent_package_assignment(
    db: Session,
    *,
    worker_agent: WorkerAgent,
    package: WorkflowPackage,
) -> None:
    deployment_id = db.scalar(
        select(WorkerDeployment.deployment_id)
        .where(
            WorkerDeployment.worker_agent_id == worker_agent.worker_agent_id,
            WorkerDeployment.workflow_package_id == package.package_id,
        )
        .limit(1)
    )
    if deployment_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"workflow package {package.package_id} is not assigned to worker agent "
                f"{worker_agent.worker_agent_id}"
            ),
        )


def build_workflow_package_summary(package: WorkflowPackage) -> WorkflowPackageSummary:
    return WorkflowPackageSummary(
        package_id=package.package_id,
        workflow_id=package.workflow_id,
        version=package.version,
        filename=package.filename,
        content_type=package.content_type,
        checksum_sha256=package.checksum_sha256,
        size_bytes=package.size_bytes,
        entrypoint=package.entrypoint,
        metadata=package.metadata_json,
        created_by=package.created_by,
        created_at=package.created_at,
    )


def create_worker_deployment(
    db: Session,
    request: WorkerDeploymentCreateRequest,
    *,
    created_by: str,
) -> WorkerDeployment:
    worker_agent = get_worker_agent_or_404(db, request.worker_agent_id)
    package = get_workflow_package_or_404(db, request.workflow_package_id)
    now = utcnow()
    deployment = WorkerDeployment(
        deployment_id=uuid4(),
        workflow_package_id=package.package_id,
        worker_agent_id=worker_agent.worker_agent_id,
        desired_status=request.desired_status,
        observed_status="assigned",
        execution_mode=worker_agent.execution_mode,
        params_json=request.params,
        env_json=request.env,
        credential_refs_json=request.credential_refs,
        package_checksum=package.checksum_sha256,
        assigned_at=now,
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    db.add(deployment)
    record_worker_deployment_event(
        db,
        deployment_id=deployment.deployment_id,
        worker_agent_id=deployment.worker_agent_id,
        event_type="deployment_created",
        observed_status=deployment.observed_status,
        message="deployment created",
        payload={
            "workflow_package_id": str(package.package_id),
            "desired_status": deployment.desired_status,
        },
    )
    db.commit()
    db.refresh(deployment)
    return deployment


def get_worker_deployment_or_404(db: Session, deployment_id: UUID) -> WorkerDeployment:
    deployment = db.scalar(
        select(WorkerDeployment).where(WorkerDeployment.deployment_id == deployment_id)
    )
    if deployment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"worker deployment {deployment_id} was not found",
        )
    return deployment


def list_worker_deployments(
    db: Session,
    *,
    worker_agent_id: UUID | None,
    limit: int,
    offset: int,
) -> tuple[int, list[WorkerDeployment]]:
    stmt = select(WorkerDeployment)
    count_stmt = select(func.count()).select_from(WorkerDeployment)
    if worker_agent_id is not None:
        stmt = stmt.where(WorkerDeployment.worker_agent_id == worker_agent_id)
        count_stmt = count_stmt.where(WorkerDeployment.worker_agent_id == worker_agent_id)
    total = db.scalar(count_stmt) or 0
    items = db.scalars(
        stmt.order_by(WorkerDeployment.created_at.desc(), WorkerDeployment.deployment_id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return total, list(items)


def build_worker_deployment_summary(deployment: WorkerDeployment) -> WorkerDeploymentSummary:
    return WorkerDeploymentSummary(
        deployment_id=deployment.deployment_id,
        workflow_package_id=deployment.workflow_package_id,
        worker_agent_id=deployment.worker_agent_id,
        desired_status=deployment.desired_status,
        observed_status=deployment.observed_status,
        runtime_instance_id=deployment.runtime_instance_id,
        execution_mode=deployment.execution_mode,
        params=deployment.params_json,
        env=deployment.env_json,
        credential_refs=deployment.credential_refs_json,
        package_checksum=deployment.package_checksum,
        last_error_code=deployment.last_error_code,
        last_error_message=deployment.last_error_message,
        assigned_at=deployment.assigned_at,
        started_at=deployment.started_at,
        finished_at=deployment.finished_at,
        created_by=deployment.created_by,
        created_at=deployment.created_at,
        updated_at=deployment.updated_at,
    )
