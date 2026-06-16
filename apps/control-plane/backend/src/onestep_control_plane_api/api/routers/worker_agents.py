from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.schemas import (
    WorkerAgentListResponse,
    WorkerAgentRegistrationRequest,
    WorkerAgentRegistrationResponse,
    WorkerAgentSummary,
    WorkerDeploymentCreateRequest,
    WorkerDeploymentListResponse,
    WorkerDeploymentSummary,
    WorkflowPackageSummary,
)
from onestep_control_plane_api.api.security import (
    require_console_auth,
    require_worker_agent_connection,
)
from onestep_control_plane_api.api.worker_agent_connection_registry import (
    worker_agent_connection_registry,
)
from onestep_control_plane_api.api.worker_agent_service import (
    build_worker_agent_summary,
    build_worker_deployment_summary,
    build_workflow_package_summary,
    create_restart_deployment_command,
    create_start_deployment_command,
    create_stop_deployment_command,
    create_worker_deployment,
    create_workflow_package,
    dispatch_worker_agent_command,
    get_worker_agent_or_404,
    get_worker_deployment_or_404,
    get_workflow_package_or_404,
    get_workflow_package_path_or_404,
    list_worker_agents,
    list_worker_deployments,
    register_worker_agent,
    require_worker_agent_package_assignment,
)
from onestep_control_plane_api.db.models import WorkerAgent, WorkerAgentCommand, WorkerDeployment
from onestep_control_plane_api.db.session import get_db_session

router = APIRouter(prefix="/api/v1", tags=["worker-agents"])


@router.post(
    "/worker-agents/register",
    response_model=WorkerAgentRegistrationResponse,
)
def register_worker_agent_endpoint(
    request: WorkerAgentRegistrationRequest,
    db: Session = Depends(get_db_session),
) -> WorkerAgentRegistrationResponse:
    return register_worker_agent(db, request)


@router.get(
    "/worker-agents",
    response_model=WorkerAgentListResponse,
    dependencies=[Depends(require_console_auth)],
)
def list_worker_agents_endpoint(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db_session),
) -> WorkerAgentListResponse:
    total, items = list_worker_agents(db, limit=limit, offset=offset)
    return WorkerAgentListResponse(
        items=[build_worker_agent_summary(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/worker-agents/{worker_agent_id}",
    response_model=WorkerAgentSummary,
    dependencies=[Depends(require_console_auth)],
)
def get_worker_agent_endpoint(
    worker_agent_id: UUID,
    db: Session = Depends(get_db_session),
) -> WorkerAgentSummary:
    return build_worker_agent_summary(get_worker_agent_or_404(db, worker_agent_id))


@router.post(
    "/workflow-packages",
    response_model=WorkflowPackageSummary,
)
def create_workflow_package_endpoint(
    content: Annotated[bytes, Body(media_type="application/zip")],
    workflow_id: UUID = Query(...),
    version: str = Query(..., min_length=1, max_length=128),
    filename: str = Query("workflow.zip", min_length=1, max_length=255),
    entrypoint: str = Query("worker.yaml", min_length=1, max_length=255),
    content_type: Annotated[str | None, Header(alias="content-type")] = None,
    identity=Depends(require_console_auth),
    db: Session = Depends(get_db_session),
) -> WorkflowPackageSummary:
    package = create_workflow_package(
        db,
        workflow_id=workflow_id,
        version=version,
        filename=filename,
        content_type=content_type or "application/zip",
        content=content,
        entrypoint=entrypoint,
        created_by=identity.username if identity is not None else "system",
    )
    return build_workflow_package_summary(package)


@router.get("/workflow-packages/{package_id}/download")
def download_workflow_package_endpoint(
    package_id: UUID,
    worker_agent: WorkerAgent = Depends(require_worker_agent_connection),
    db: Session = Depends(get_db_session),
) -> FileResponse:
    package = get_workflow_package_or_404(db, package_id)
    require_worker_agent_package_assignment(db, worker_agent=worker_agent, package=package)
    path = get_workflow_package_path_or_404(package)
    return FileResponse(
        path,
        media_type=package.content_type,
        filename=package.filename,
        headers={"x-onestep-package-sha256": package.checksum_sha256},
    )


@router.post(
    "/worker-deployments",
    response_model=WorkerDeploymentSummary,
)
async def create_worker_deployment_endpoint(
    request: WorkerDeploymentCreateRequest,
    identity=Depends(require_console_auth),
    db: Session = Depends(get_db_session),
) -> WorkerDeploymentSummary:
    deployment = create_worker_deployment(
        db,
        request,
        created_by=identity.username if identity is not None else "system",
    )
    package = get_workflow_package_or_404(db, deployment.workflow_package_id)
    if deployment.desired_status == "running":
        command = create_start_deployment_command(db, deployment=deployment, package=package)
        await _dispatch_if_connected(db, deployment=deployment, command=command)
    return build_worker_deployment_summary(deployment)


@router.post(
    "/worker-deployments/{deployment_id}/stop",
    response_model=WorkerDeploymentSummary,
)
async def stop_worker_deployment_endpoint(
    deployment_id: UUID,
    _: object = Depends(require_console_auth),
    db: Session = Depends(get_db_session),
) -> WorkerDeploymentSummary:
    deployment = get_worker_deployment_or_404(db, deployment_id)
    command = create_stop_deployment_command(db, deployment=deployment)
    await _dispatch_if_connected(db, deployment=deployment, command=command)
    db.refresh(deployment)
    return build_worker_deployment_summary(deployment)


@router.post(
    "/worker-deployments/{deployment_id}/restart",
    response_model=WorkerDeploymentSummary,
)
async def restart_worker_deployment_endpoint(
    deployment_id: UUID,
    _: object = Depends(require_console_auth),
    db: Session = Depends(get_db_session),
) -> WorkerDeploymentSummary:
    deployment = get_worker_deployment_or_404(db, deployment_id)
    package = get_workflow_package_or_404(db, deployment.workflow_package_id)
    command = create_restart_deployment_command(db, deployment=deployment, package=package)
    await _dispatch_if_connected(db, deployment=deployment, command=command)
    db.refresh(deployment)
    return build_worker_deployment_summary(deployment)


@router.get(
    "/worker-deployments",
    response_model=WorkerDeploymentListResponse,
    dependencies=[Depends(require_console_auth)],
)
def list_worker_deployments_endpoint(
    worker_agent_id: UUID | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db_session),
) -> WorkerDeploymentListResponse:
    total, items = list_worker_deployments(
        db,
        worker_agent_id=worker_agent_id,
        limit=limit,
        offset=offset,
    )
    return WorkerDeploymentListResponse(
        items=[build_worker_deployment_summary(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/worker-deployments/{deployment_id}",
    response_model=WorkerDeploymentSummary,
    dependencies=[Depends(require_console_auth)],
)
def get_worker_deployment_endpoint(
    deployment_id: UUID,
    db: Session = Depends(get_db_session),
) -> WorkerDeploymentSummary:
    return build_worker_deployment_summary(get_worker_deployment_or_404(db, deployment_id))


async def _dispatch_if_connected(
    db: Session,
    *,
    deployment: WorkerDeployment,
    command: WorkerAgentCommand,
) -> None:
    live_connection = await worker_agent_connection_registry.get(deployment.worker_agent_id)
    if live_connection is None:
        return
    await dispatch_worker_agent_command(
        db,
        command=command,
        send_queue=live_connection.send_queue,
        session_id=live_connection.session_id,
    )
