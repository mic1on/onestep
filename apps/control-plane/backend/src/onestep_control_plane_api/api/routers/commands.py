from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.agent_command_service import (
    CommandAuditMetadata,
    build_command_summary,
    create_service_command_fanout,
    get_instance_or_404,
    list_commands_for_instance,
)
from onestep_control_plane_api.api.agent_command_service import (
    create_instance_command as dispatch_instance_command,
)
from onestep_control_plane_api.api.query_support import get_service_or_404
from onestep_control_plane_api.api.schemas import (
    AgentCommandCreateRequest,
    AgentCommandListResponse,
    AgentCommandSummary,
    Environment,
    ServiceCommandFanoutRequest,
    ServiceCommandFanoutResponse,
    TaskCommandFanoutRequest,
)
from onestep_control_plane_api.api.security import (
    require_command_role,
    require_console_auth,
)
from onestep_control_plane_api.api.ui_event_stream import publish_ui_stream_event
from onestep_control_plane_api.db.session import get_db_session

router = APIRouter(
    prefix="/api/v1",
    tags=["commands"],
    dependencies=[Depends(require_console_auth)],
)


@router.get("/instances/{instance_id}/commands", response_model=AgentCommandListResponse)
def list_instance_commands(
    instance_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db_session),
) -> AgentCommandListResponse:
    get_instance_or_404(db, instance_id)
    total, commands = list_commands_for_instance(
        db,
        instance_id=instance_id,
        limit=limit,
        offset=offset,
    )
    return AgentCommandListResponse(
        items=[build_command_summary(command) for command in commands],
        total=total,
        limit=limit,
        offset=offset,
)


@router.post("/instances/{instance_id}/commands", response_model=AgentCommandSummary)
async def create_instance_command(
    instance_id: UUID,
    request: AgentCommandCreateRequest,
    http_request: Request,
    identity=Depends(require_console_auth),
    db: Session = Depends(get_db_session),
) -> AgentCommandSummary:
    require_command_role(request.kind, identity, http_request)
    instance = get_instance_or_404(db, instance_id)
    command = await dispatch_instance_command(
        db,
        instance=instance,
        request=request,
        audit=CommandAuditMetadata(
            created_by=identity.username if identity is not None else None,
            reason=request.reason,
            source_surface="instance_detail",
        ),
    )
    publish_ui_stream_event("commands")
    return build_command_summary(command)


@router.post(
    "/services/{service_name}/commands",
    response_model=ServiceCommandFanoutResponse,
)
async def create_service_command(
    service_name: str,
    request: ServiceCommandFanoutRequest,
    http_request: Request,
    environment: Environment = Query(...),
    identity=Depends(require_console_auth),
    db: Session = Depends(get_db_session),
) -> ServiceCommandFanoutResponse:
    require_command_role(request.kind, identity, http_request)
    service = get_service_or_404(db, service_name=service_name, environment=environment)
    response = await create_service_command_fanout(
        db,
        service=service,
        request=request,
        created_by=identity.username if identity is not None else None,
        source_surface="service_detail_fanout",
    )
    if response.counts.total > 0:
        publish_ui_stream_event("commands")
    return response


@router.post(
    "/services/{service_name}/tasks/{task_name}/commands",
    response_model=ServiceCommandFanoutResponse,
)
async def create_task_command(
    service_name: str,
    task_name: str,
    request: TaskCommandFanoutRequest,
    http_request: Request,
    environment: Environment = Query(...),
    identity=Depends(require_console_auth),
    db: Session = Depends(get_db_session),
) -> ServiceCommandFanoutResponse:
    require_command_role(request.kind, identity, http_request)
    service = get_service_or_404(db, service_name=service_name, environment=environment)
    response = await create_service_command_fanout(
        db,
        service=service,
        request=ServiceCommandFanoutRequest(
            kind=request.kind,
            args={"task_name": task_name, **request.args},
            timeout_s=request.timeout_s,
            reason=request.reason,
            target_mode=request.target_mode,
            target_instance_ids=request.target_instance_ids,
            offline_behavior=request.offline_behavior,
        ),
        created_by=identity.username if identity is not None else None,
        source_surface="task_detail",
    )
    if response.counts.total > 0:
        publish_ui_stream_event("commands")
    return response
