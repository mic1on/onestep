from __future__ import annotations

import hashlib
import secrets
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.common import utcnow
from onestep_control_plane_api.api.schemas import (
    WorkerAgentHeartbeatMessage,
    WorkerAgentHelloAckMessage,
    WorkerAgentHelloAckPayload,
    WorkerAgentHelloMessage,
    WorkerAgentRegistrationRequest,
    WorkerAgentRegistrationResponse,
    WorkerAgentSummary,
    WorkerDeploymentCreateRequest,
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
    WorkerAgentSession,
    WorkerDeployment,
    WorkflowPackage,
)

SUPPORTED_WORKER_AGENT_PROTOCOL_VERSION = "1"
DEFAULT_WORKER_AGENT_HEARTBEAT_INTERVAL_S = 30
WORKER_AGENT_CAPABILITIES = frozenset(
    {
        "deployment.start",
        "deployment.stop",
        "deployment.restart",
        "agent.sync_state",
    }
)


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
    entrypoint: str,
    created_by: str,
) -> WorkflowPackage:
    package_id = uuid4()
    checksum = hashlib.sha256(content).hexdigest()
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
        entrypoint=entrypoint,
        metadata_json={},
        created_by=created_by,
    )
    db.add(package)
    db.commit()
    db.refresh(package)
    return package


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
