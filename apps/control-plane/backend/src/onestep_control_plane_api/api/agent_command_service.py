from __future__ import annotations

import uuid
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.common import utcnow
from onestep_control_plane_api.api.schemas import (
    AgentCommandCreateRequest,
    AgentCommandMessage,
    AgentCommandPayload,
    AgentCommandSummary,
)
from onestep_control_plane_api.db.models import AgentCommand, Instance

NON_TERMINAL_COMMAND_STATUSES = frozenset({"pending", "dispatched", "accepted"})
TERMINAL_COMMAND_STATUSES = frozenset({"rejected", "succeeded", "failed", "timeout", "cancelled"})


def _new_message_id() -> str:
    return f"msg_{uuid.uuid4().hex}"


def _new_command_id() -> str:
    return f"cmd_{uuid.uuid4().hex}"


def get_instance_or_404(db: Session, instance_id: UUID) -> Instance:
    instance = db.scalar(select(Instance).where(Instance.instance_id == instance_id))
    if instance is None:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"instance {instance_id} was not found",
        )
    return instance


def create_command(
    db: Session,
    *,
    instance: Instance,
    request: AgentCommandCreateRequest,
    created_at: datetime | None = None,
) -> AgentCommand:
    created_at = created_at or utcnow()
    command = AgentCommand(
        command_id=_new_command_id(),
        service=instance.service,
        instance_id=instance.instance_id,
        kind=request.kind,
        args_json=request.args,
        timeout_s=request.timeout_s,
        status="pending",
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(command)
    db.commit()
    db.refresh(command)
    return command


def list_commands_for_instance(
    db: Session,
    *,
    instance_id: UUID,
    limit: int,
    offset: int,
) -> tuple[int, list[AgentCommand]]:
    total = db.query(AgentCommand).filter(AgentCommand.instance_id == instance_id).count()
    items = (
        db.query(AgentCommand)
        .filter(AgentCommand.instance_id == instance_id)
        .order_by(AgentCommand.created_at.desc(), AgentCommand.command_id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return total, items


def list_commands_for_service(
    db: Session,
    *,
    service_id: UUID,
    instance_id: UUID | None,
    kind: str | None,
    status: str | None,
    limit: int,
    offset: int,
) -> tuple[int, list[AgentCommand]]:
    filters = [AgentCommand.service_id == service_id]
    if instance_id is not None:
        filters.append(AgentCommand.instance_id == instance_id)
    if kind is not None:
        filters.append(AgentCommand.kind == kind)
    if status is not None:
        filters.append(AgentCommand.status == status)

    total = db.query(AgentCommand).filter(*filters).count()
    items = (
        db.query(AgentCommand)
        .filter(*filters)
        .order_by(AgentCommand.created_at.desc(), AgentCommand.command_id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return total, items


def list_pending_commands_for_instance(
    db: Session,
    *,
    instance_id: UUID,
) -> list[AgentCommand]:
    return (
        db.query(AgentCommand)
        .filter(
            AgentCommand.instance_id == instance_id,
            AgentCommand.status.in_(NON_TERMINAL_COMMAND_STATUSES),
        )
        .order_by(AgentCommand.created_at.asc(), AgentCommand.command_id.asc())
        .all()
    )


def mark_command_dispatched(
    db: Session,
    *,
    command: AgentCommand,
    session_id: str,
    dispatched_at: datetime | None = None,
) -> AgentCommand:
    dispatched_at = dispatched_at or utcnow()
    command.session_id = session_id
    command.dispatched_at = dispatched_at
    if command.status == "pending":
        command.status = "dispatched"
    command.updated_at = dispatched_at
    db.commit()
    db.refresh(command)
    return command


def build_command_message(command: AgentCommand, *, sent_at: datetime | None = None) -> AgentCommandMessage:
    sent_at = sent_at or utcnow()
    return AgentCommandMessage(
        type="command",
        message_id=_new_message_id(),
        sent_at=sent_at,
        payload=AgentCommandPayload(
            command_id=command.command_id,
            kind=command.kind,
            args=command.args_json,
            timeout_s=command.timeout_s,
            created_at=command.created_at,
        ),
    )


def get_command_by_id(
    db: Session,
    *,
    command_id: str,
) -> AgentCommand | None:
    return db.scalar(select(AgentCommand).where(AgentCommand.command_id == command_id))


def build_command_summary(command: AgentCommand) -> AgentCommandSummary:
    return AgentCommandSummary(
        command_id=command.command_id,
        instance_id=command.instance_id,
        node_name=command.instance.node_name if command.instance is not None else None,
        session_id=command.session_id,
        kind=command.kind,
        args=command.args_json,
        timeout_s=command.timeout_s,
        status=command.status,
        ack_status=command.ack_status,
        result=command.result_json,
        error_code=command.error_code,
        error_message=command.error_message,
        created_at=command.created_at,
        dispatched_at=command.dispatched_at,
        acked_at=command.acked_at,
        finished_at=command.finished_at,
        updated_at=command.updated_at,
    )
