from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.agent_command_service import (
    build_command_message,
    build_command_summary,
    create_command,
    get_instance_or_404,
    list_commands_for_instance,
    mark_command_dispatched,
)
from onestep_control_plane_api.api.agent_connection_registry import agent_connection_registry
from onestep_control_plane_api.api.schemas import (
    AgentCommandCreateRequest,
    AgentCommandListResponse,
    AgentCommandSummary,
)
from onestep_control_plane_api.api.security import require_console_auth
from onestep_control_plane_api.db.session import get_db_session

router = APIRouter(
    prefix="/api/v1/instances",
    tags=["commands"],
    dependencies=[Depends(require_console_auth)],
)


@router.get("/{instance_id}/commands", response_model=AgentCommandListResponse)
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


@router.post("/{instance_id}/commands", response_model=AgentCommandSummary)
async def create_instance_command(
    instance_id: UUID,
    request: AgentCommandCreateRequest,
    db: Session = Depends(get_db_session),
) -> AgentCommandSummary:
    instance = get_instance_or_404(db, instance_id)
    command = create_command(db, instance=instance, request=request)

    live_connection = await agent_connection_registry.get(instance_id)
    if live_connection is not None:
        command = mark_command_dispatched(
            db,
            command=command,
            session_id=live_connection.session_id,
        )
        await live_connection.send_queue.put(build_command_message(command).model_dump(mode="json"))

    return build_command_summary(command)
