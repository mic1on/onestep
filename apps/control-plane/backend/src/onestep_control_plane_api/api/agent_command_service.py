from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.agent_connection_registry import agent_connection_registry
from onestep_control_plane_api.api.common import utcnow
from onestep_control_plane_api.api.schemas import (
    TASK_SCOPED_COMMAND_KINDS,
    AgentCommandCreateRequest,
    AgentCommandKind,
    AgentCommandMessage,
    AgentCommandPayload,
    AgentCommandSourceSurface,
    AgentCommandSummary,
    InstanceConnectivity,
    ServiceCommandFanoutCounts,
    ServiceCommandFanoutRequest,
    ServiceCommandFanoutResponse,
    ServiceCommandFanoutTargetSummary,
)
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db.models import AgentCommand, AgentSession, Instance, Service
from onestep_control_plane_api.db.session import session_scope
from onestep_control_plane_api.ops.observability import record_agent_command_outcome

REDISPATCHABLE_COMMAND_STATUSES = frozenset({"pending", "dispatched"})
RECONCILABLE_COMMAND_STATUSES = frozenset({"pending", "dispatched", "accepted"})
TERMINAL_COMMAND_STATUSES = frozenset(
    {"expired", "rejected", "succeeded", "failed", "timeout", "cancelled"}
)
COMMAND_CAPABILITY_BY_KIND: dict[AgentCommandKind, str] = {
    "ping": "command.ping",
    "restart": "command.restart",
    "drain": "command.drain",
    "pause_task": "command.pause_task",
    "resume_task": "command.resume_task",
    "restart_task": "command.restart_task",
    "discard_dead_letters": "command.discard_dead_letters",
    "replay_dead_letters": "command.replay_dead_letters",
    "run_task_once": "command.run_task_once",
    "sync_now": "command.sync_now",
    "flush_metrics": "command.flush_metrics",
    "flush_events": "command.flush_events",
    "shutdown": "command.shutdown",
}
QUEUEABLE_COMMAND_KINDS = frozenset({"ping", "sync_now", "flush_metrics", "flush_events"})


@dataclass(frozen=True)
class CommandAuditMetadata:
    created_by: str | None
    reason: str | None
    source_surface: AgentCommandSourceSurface


def _new_message_id() -> str:
    return f"msg_{uuid.uuid4().hex}"


def _new_command_id() -> str:
    return f"cmd_{uuid.uuid4().hex}"


def get_command_capability(kind: AgentCommandKind) -> str:
    return COMMAND_CAPABILITY_BY_KIND[kind]


def _extract_task_control_state(
    app_snapshot_json: dict[str, object] | None,
    *,
    task_name: str,
) -> dict[str, object] | None:
    if not isinstance(app_snapshot_json, dict):
        return None
    task_control_states = app_snapshot_json.get("task_control_states")
    if not isinstance(task_control_states, list):
        return None
    for value in task_control_states:
        if not isinstance(value, dict):
            continue
        if value.get("task_name") == task_name:
            return dict(value)
    return None


def _task_support_rejection(
    instance: Instance,
    *,
    kind: AgentCommandKind,
    args: dict[str, object],
) -> tuple[str, str] | None:
    if kind not in TASK_SCOPED_COMMAND_KINDS:
        return None
    task_name = args.get("task_name")
    if not isinstance(task_name, str) or not task_name.strip():
        return "task_name_missing", "task-scoped command is missing args.task_name"

    task_control_state = _extract_task_control_state(
        instance.app_snapshot_json,
        task_name=task_name.strip(),
    )
    if task_control_state is None:
        return (
            "task_state_unknown",
            f"latest task control snapshot for {task_name.strip()} is unavailable",
        )

    raw_supported_commands = task_control_state.get("supported_commands")
    if not isinstance(raw_supported_commands, list):
        if kind in {"pause_task", "resume_task", "restart_task"}:
            return None
        return (
            "task_command_unsupported",
            f"task {task_name.strip()} does not advertise {kind}",
        )

    supported_commands = {
        command_kind for command_kind in raw_supported_commands if isinstance(command_kind, str)
    }
    if kind not in supported_commands:
        return (
            "task_command_unsupported",
            f"task {task_name.strip()} does not advertise {kind}",
        )
    return None


def get_instance_or_404(db: Session, instance_id: UUID) -> Instance:
    instance = db.scalar(select(Instance).where(Instance.instance_id == instance_id))
    if instance is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"instance {instance_id} was not found",
        )
    return instance


def _get_active_session_for_instance(
    db: Session,
    *,
    instance_id: UUID,
) -> AgentSession | None:
    return db.scalar(
        select(AgentSession)
        .where(
            AgentSession.instance_id == instance_id,
            AgentSession.status == "active",
        )
        .order_by(AgentSession.connected_at.desc(), AgentSession.session_id.desc())
        .limit(1)
    )


def _get_latest_session_for_instance(
    db: Session,
    *,
    instance_id: UUID,
) -> AgentSession | None:
    return db.scalar(
        select(AgentSession)
        .where(AgentSession.instance_id == instance_id)
        .order_by(AgentSession.connected_at.desc(), AgentSession.session_id.desc())
        .limit(1)
    )


def _get_active_sessions_for_instances(
    db: Session,
    *,
    instance_ids: list[UUID],
) -> dict[UUID, AgentSession]:
    if not instance_ids:
        return {}
    sessions = db.scalars(
        select(AgentSession)
        .where(
            AgentSession.instance_id.in_(instance_ids),
            AgentSession.status == "active",
        )
        .order_by(AgentSession.connected_at.desc(), AgentSession.session_id.desc())
    ).all()
    active_sessions: dict[UUID, AgentSession] = {}
    for session in sessions:
        active_sessions.setdefault(session.instance_id, session)
    return active_sessions


def _get_latest_sessions_for_instances(
    db: Session,
    *,
    instance_ids: list[UUID],
) -> dict[UUID, AgentSession]:
    if not instance_ids:
        return {}
    sessions = db.scalars(
        select(AgentSession)
        .where(AgentSession.instance_id.in_(instance_ids))
        .order_by(
            AgentSession.instance_id,
            AgentSession.connected_at.desc(),
            AgentSession.session_id.desc(),
        )
    ).all()
    latest_sessions: dict[UUID, AgentSession] = {}
    for session in sessions:
        latest_sessions.setdefault(session.instance_id, session)
    return latest_sessions


def _get_instance_connectivity(
    instance: Instance,
    *,
    as_of: datetime,
) -> InstanceConnectivity:
    if instance.last_seen_at is None:
        return "never_reported"
    offline_cutoff = as_of - timedelta(seconds=settings.instance_offline_after_s)
    if instance.last_seen_at >= offline_cutoff:
        return "online"
    return "offline"


def command_supports_queueing(kind: AgentCommandKind) -> bool:
    return kind in QUEUEABLE_COMMAND_KINDS


def _mark_command_rejected_without_delivery(
    db: Session,
    *,
    command: AgentCommand,
    error_code: str,
    error_message: str,
    rejected_at: datetime | None = None,
) -> AgentCommand:
    rejected_at = rejected_at or utcnow()
    command.status = "rejected"
    command.finished_at = rejected_at
    command.error_code = error_code
    command.error_message = error_message
    command.updated_at = rejected_at
    db.commit()
    db.refresh(command)
    record_agent_command_outcome(command.status)
    return command


def create_command(
    db: Session,
    *,
    instance: Instance,
    request: AgentCommandCreateRequest,
    audit: CommandAuditMetadata,
    created_at: datetime | None = None,
) -> AgentCommand:
    created_at = created_at or utcnow()
    command = AgentCommand(
        command_id=_new_command_id(),
        service=instance.service,
        instance_id=instance.instance_id,
        created_by=audit.created_by,
        reason=audit.reason,
        source_surface=audit.source_surface,
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


async def create_instance_command(
    db: Session,
    *,
    instance: Instance,
    request: AgentCommandCreateRequest,
    audit: CommandAuditMetadata,
) -> AgentCommand:
    required_capability = get_command_capability(request.kind)
    active_session = _get_active_session_for_instance(db, instance_id=instance.instance_id)

    if active_session is not None:
        if required_capability not in active_session.accepted_capabilities_json:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"instance {instance.instance_id} active session does not advertise "
                    f"capability {required_capability}"
                ),
            )

        task_support_rejection = _task_support_rejection(
            instance,
            kind=request.kind,
            args=request.args,
        )
        if task_support_rejection is not None:
            _, detail = task_support_rejection
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=detail,
            )

        live_connection = await agent_connection_registry.get(instance.instance_id)
        if live_connection is None:
            if request.delivery_mode == "dispatch_now_only":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"instance {instance.instance_id} has no live control connection; "
                        "retry after reconnect or choose queue_until_reconnect"
                    ),
                )

            return create_command(
                db,
                instance=instance,
                request=request,
                audit=audit,
            )

        command = create_command(
            db,
            instance=instance,
            request=request,
            audit=audit,
        )
        command = mark_command_dispatched(
            db,
            command=command,
            session_id=live_connection.session_id,
        )
        await live_connection.send_queue.put(build_command_message(command).model_dump(mode="json"))
        return command

    if request.delivery_mode == "dispatch_now_only":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"instance {instance.instance_id} has no active control session; "
                "reconnect the agent before dispatching commands"
            ),
        )

    latest_session = _get_latest_session_for_instance(db, instance_id=instance.instance_id)
    if latest_session is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"instance {instance.instance_id} has no prior control session; "
                "queue_until_reconnect requires a previously observed compatible session"
            ),
        )

    if required_capability not in latest_session.accepted_capabilities_json:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"instance {instance.instance_id} last known session does not advertise "
                f"capability {required_capability}"
            ),
        )

    return create_command(
        db,
        instance=instance,
        request=request,
        audit=audit,
    )


def _command_deadline(command: AgentCommand) -> datetime:
    return command.created_at + timedelta(seconds=command.timeout_s)


def expire_stale_commands(
    db: Session,
    *,
    instance_id: UUID | None = None,
    service_id: UUID | None = None,
    as_of: datetime | None = None,
    commit: bool = True,
) -> int:
    as_of = as_of or utcnow()
    filters = [AgentCommand.status.in_(RECONCILABLE_COMMAND_STATUSES)]
    if instance_id is not None:
        filters.append(AgentCommand.instance_id == instance_id)
    if service_id is not None:
        filters.append(AgentCommand.service_id == service_id)

    stale_count = 0
    terminal_statuses: list[str] = []
    for command in db.query(AgentCommand).filter(*filters).all():
        deadline = _command_deadline(command)
        if deadline > as_of:
            continue
        if command.status == "accepted" or command.ack_status == "accepted":
            command.status = "timeout"
            command.error_code = "command_timeout_elapsed"
            command.error_message = (
                f"command did not report a terminal result within timeout_s={command.timeout_s}"
            )
            if command.acked_at is not None:
                command.duration_ms = max(
                    int((deadline - command.acked_at).total_seconds() * 1000),
                    0,
                )
        else:
            command.status = "expired"
            command.error_code = "command_delivery_expired"
            command.error_message = (
                f"command was not acknowledged before timeout_s={command.timeout_s} elapsed"
            )
        command.finished_at = deadline
        command.updated_at = as_of
        stale_count += 1
        terminal_statuses.append(command.status)

    # Commit only when the caller owns the transaction. The synchronous read
    # paths rely on this default to persist the expiry writes. A caller that
    # runs inside a larger work unit (the async pending-command listing on the
    # hello path) passes ``commit=False``: an inner commit there would pin the
    # newly inserted AgentSession row before the work unit finishes, so a later
    # failure could no longer roll the session back and an ``active`` session
    # would leak past every cleanup path.
    if commit and stale_count:
        db.commit()
    # Counted after the commit, never before: a counter that moves on a rolled-back
    # expiry would report failures that never happened. With ``commit=False`` the
    # caller owns the transaction and is contracted to commit it.
    for terminal_status in terminal_statuses:
        record_agent_command_outcome(terminal_status)
    return stale_count


def list_commands_for_instance(
    db: Session,
    *,
    instance_id: UUID,
    limit: int,
    offset: int,
) -> tuple[int, list[AgentCommand]]:
    expire_stale_commands(db, instance_id=instance_id)
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
    expire_stale_commands(db, service_id=service_id)
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


def list_redeliverable_commands_for_instance(
    db: Session,
    *,
    instance_id: UUID,
) -> list[AgentCommand]:
    expire_stale_commands(db, instance_id=instance_id)
    return (
        db.query(AgentCommand)
        .filter(
            AgentCommand.instance_id == instance_id,
            AgentCommand.status.in_(REDISPATCHABLE_COMMAND_STATUSES),
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


@dataclass(frozen=True)
class PendingCommandDelivery:
    """Plain data for one redeliverable command, safe outside a transaction.

    The WS path must not carry ORM instances across a transaction boundary:
    reading an attribute after the session closes would trigger implicit IO or
    raise. Every field the outbound ``command`` message and the capability check
    need is captured here instead.
    """

    command_id: str
    kind: AgentCommandKind
    args_json: dict[str, object]
    timeout_s: float
    created_at: datetime


def _to_pending_delivery(command: AgentCommand) -> PendingCommandDelivery:
    return PendingCommandDelivery(
        command_id=command.command_id,
        kind=command.kind,
        args_json=dict(command.args_json or {}),
        timeout_s=command.timeout_s,
        created_at=command.created_at,
    )


async def list_redeliverable_commands_for_instance_async(
    session: AsyncSession,
    *,
    instance_id: UUID,
) -> list[PendingCommandDelivery]:
    """List redeliverable commands as plain data on an async session.

    Stale-command expiry and the listing run in one work unit so the two agree
    on which commands are still live. The caller's work unit owns the commit;
    the inner expiry pass must not commit, or it would pin earlier writes of
    this work unit (the hello's new AgentSession row) before the unit finished.
    """

    await session.run_sync(
        lambda db: expire_stale_commands(db, instance_id=instance_id, commit=False)
    )
    # The session runs with ``autoflush=False``, so the expiry writes made
    # inside ``run_sync`` have to be flushed explicitly for the SELECT below
    # to see them. A flush stays inside the caller's transaction — the unit
    # still commits or rolls back as a whole.
    await session.flush()
    rows = await session.scalars(
        select(AgentCommand)
        .where(
            AgentCommand.instance_id == instance_id,
            AgentCommand.status.in_(REDISPATCHABLE_COMMAND_STATUSES),
        )
        .order_by(AgentCommand.created_at.asc(), AgentCommand.command_id.asc())
    )
    return [_to_pending_delivery(command) for command in rows.all()]


async def mark_command_dispatched_async(
    session: AsyncSession,
    *,
    command_id: str,
    session_id: str,
    dispatched_at: datetime | None = None,
) -> PendingCommandDelivery:
    """Mark a command dispatched on an async session and return its plain data."""

    command = await session.scalar(
        select(AgentCommand).where(AgentCommand.command_id == command_id)
    )
    if command is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"command {command_id} was not found",
        )
    dispatched_at = dispatched_at or utcnow()
    command.session_id = session_id
    command.dispatched_at = dispatched_at
    if command.status == "pending":
        command.status = "dispatched"
    command.updated_at = dispatched_at
    await session.commit()
    return _to_pending_delivery(command)


async def reject_redelivery_for_unsupported_capability_async(
    session: AsyncSession,
    *,
    command_id: str,
    capability: str,
) -> None:
    """Abandon a queued command whose reconnect lacks ``capability``."""

    command = await session.scalar(
        select(AgentCommand).where(AgentCommand.command_id == command_id)
    )
    if command is None:
        return
    rejected_at = utcnow()
    command.status = "rejected"
    command.finished_at = rejected_at
    command.error_code = "command_capability_missing_on_reconnect"
    command.error_message = (
        f"the reconnecting session does not advertise {capability}; queued delivery was abandoned"
    )
    command.updated_at = rejected_at
    await session.commit()
    record_agent_command_outcome(command.status)


def build_command_message(
    command: AgentCommand,
    *,
    sent_at: datetime | None = None,
) -> AgentCommandMessage:
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


def build_command_message_from_delivery(
    delivery: PendingCommandDelivery,
    *,
    sent_at: datetime | None = None,
) -> AgentCommandMessage:
    """Build the outbound ``command`` frame from plain data.

    Same output as :func:`build_command_message`, but it never touches ORM
    state, so it can run after the work unit's transaction has closed.
    """

    sent_at = sent_at or utcnow()
    return AgentCommandMessage(
        type="command",
        message_id=_new_message_id(),
        sent_at=sent_at,
        payload=AgentCommandPayload(
            command_id=delivery.command_id,
            kind=delivery.kind,
            args=delivery.args_json,
            timeout_s=delivery.timeout_s,
            created_at=delivery.created_at,
        ),
    )


def get_command_by_id(
    db: Session,
    *,
    command_id: str,
) -> AgentCommand | None:
    return db.scalar(select(AgentCommand).where(AgentCommand.command_id == command_id))


async def _resolve_command_for_session(
    session: AsyncSession,
    *,
    instance_id: UUID,
    command_id: str,
) -> AgentCommand | None:
    command = await session.scalar(
        select(AgentCommand).where(AgentCommand.command_id == command_id)
    )
    if command is None or command.instance_id != instance_id:
        return None
    return command


async def apply_command_ack(
    session: AsyncSession,
    *,
    instance_id: UUID,
    session_id: str,
    command_id: str,
    ack_status: str,
    acked_at: datetime,
    received_at: datetime,
    error_code: str | None,
    error_message: str | None,
) -> str:
    """Apply one ``command_ack`` on an async session.

    Returns ``"unknown"`` when the command does not belong to this instance,
    ``"ok"`` when the ack was applied, and ``"unchanged"`` when the command was
    already finished or already acknowledged. The last case is deliberately
    distinct: a late ack must not clobber a terminal result, and it must not
    publish a UI event either, because nothing actually changed.

    This function commits: the ack is one complete work unit.
    """

    command = await _resolve_command_for_session(
        session, instance_id=instance_id, command_id=command_id
    )
    if command is None:
        return "unknown"
    if command.finished_at is not None or command.ack_status is not None:
        return "unchanged"

    command.session_id = session_id
    command.ack_status = ack_status
    command.acked_at = acked_at
    command.updated_at = received_at
    if ack_status == "accepted":
        command.status = "accepted"
    else:
        command.status = "rejected"
        command.finished_at = acked_at
        command.error_code = error_code
        command.error_message = error_message
    await session.commit()
    if ack_status != "accepted":
        # A rejected ack is terminal; an accepted ack is in-flight and is counted
        # only when its result arrives.
        record_agent_command_outcome(command.status)
    return "ok"


async def apply_command_result(
    session: AsyncSession,
    *,
    instance_id: UUID,
    session_id: str,
    command_id: str,
    status_value: str,
    finished_at: datetime,
    result_json: dict[str, object] | None,
    duration_ms: int | None,
    received_at: datetime,
    error_code: str | None,
    error_message: str | None,
) -> str:
    """Apply one ``command_result`` on an async session.

    Returns ``"unknown"`` for a command outside this instance and
    ``"duplicate"`` when a terminal result is already recorded — the duplicate
    dedupe the issue requires to survive the conversion.

    This function commits: the result is one complete work unit.
    """

    command = await _resolve_command_for_session(
        session, instance_id=instance_id, command_id=command_id
    )
    if command is None:
        return "unknown"
    if command.finished_at is not None:
        return "duplicate"

    command.session_id = session_id
    command.status = status_value
    command.finished_at = finished_at
    command.result_json = result_json
    command.duration_ms = duration_ms
    command.error_code = error_code
    command.error_message = error_message
    if command.ack_status is None:
        command.ack_status = "accepted"
        command.acked_at = received_at
    command.updated_at = received_at
    await session.commit()
    # The duplicate check above guarantees this is the first terminal result for
    # this command, so one command is counted exactly once.
    record_agent_command_outcome(command.status)
    return "ok"


def build_command_summary(command: AgentCommand) -> AgentCommandSummary:
    return AgentCommandSummary(
        command_id=command.command_id,
        instance_id=command.instance_id,
        node_name=command.instance.node_name if command.instance is not None else None,
        session_id=command.session_id,
        created_by=command.created_by,
        reason=command.reason,
        source_surface=command.source_surface,
        kind=command.kind,
        args=command.args_json,
        timeout_s=command.timeout_s,
        status=command.status,
        ack_status=command.ack_status,
        result=command.result_json,
        duration_ms=command.duration_ms,
        error_code=command.error_code,
        error_message=command.error_message,
        created_at=command.created_at,
        dispatched_at=command.dispatched_at,
        acked_at=command.acked_at,
        finished_at=command.finished_at,
        updated_at=command.updated_at,
    )


@dataclass(frozen=True)
class PendingCommandDispatch:
    """Plain command data used after its creation work unit has closed."""

    command_id: str
    instance_id: UUID
    kind: AgentCommandKind
    args_json: dict[str, object]
    timeout_s: int
    created_at: datetime
    node_name: str | None
    session_id: str | None
    created_by: str | None
    reason: str | None
    source_surface: AgentCommandSourceSurface
    status: str
    dispatched_at: datetime | None
    updated_at: datetime


def _pending_command_dispatch(
    command: AgentCommand, *, node_name: str | None
) -> PendingCommandDispatch:
    return PendingCommandDispatch(
        command_id=command.command_id,
        instance_id=command.instance_id,
        kind=command.kind,
        args_json=dict(command.args_json or {}),
        timeout_s=command.timeout_s,
        created_at=command.created_at,
        node_name=node_name,
        session_id=command.session_id,
        created_by=command.created_by,
        reason=command.reason,
        source_surface=command.source_surface,
        status=command.status,
        dispatched_at=command.dispatched_at,
        updated_at=command.updated_at,
    )


def build_command_summary_from_dispatch(
    command: PendingCommandDispatch,
) -> AgentCommandSummary:
    return AgentCommandSummary(
        command_id=command.command_id,
        instance_id=command.instance_id,
        node_name=command.node_name,
        session_id=command.session_id,
        created_by=command.created_by,
        reason=command.reason,
        source_surface=command.source_surface,
        kind=command.kind,
        args=command.args_json,
        timeout_s=command.timeout_s,
        status=command.status,
        ack_status=None,
        result=None,
        duration_ms=None,
        error_code=None,
        error_message=None,
        created_at=command.created_at,
        dispatched_at=command.dispatched_at,
        acked_at=None,
        finished_at=None,
        updated_at=command.updated_at,
    )


def build_command_message_from_dispatch(
    command: PendingCommandDispatch,
) -> AgentCommandMessage:
    return AgentCommandMessage(
        type="command",
        message_id=_new_message_id(),
        sent_at=utcnow(),
        payload=AgentCommandPayload(
            command_id=command.command_id,
            kind=command.kind,
            args=command.args_json,
            timeout_s=command.timeout_s,
            created_at=command.created_at,
        ),
    )


def reject_redelivery_for_unsupported_capability(
    db: Session,
    *,
    command: AgentCommand,
    capability: str,
) -> AgentCommand:
    return _mark_command_rejected_without_delivery(
        db,
        command=command,
        error_code="command_capability_missing_on_reconnect",
        error_message=(
            "the reconnecting session does not advertise "
            f"{capability}; queued delivery was abandoned"
        ),
    )


def _build_service_command_target_summary(
    *,
    instance: Instance,
    connectivity: InstanceConnectivity,
    outcome: str,
    session_id: str | None = None,
    command_id: str | None = None,
    reason_code: str | None = None,
    reason_message: str | None = None,
) -> ServiceCommandFanoutTargetSummary:
    return ServiceCommandFanoutTargetSummary(
        instance_id=instance.instance_id,
        node_name=instance.node_name,
        connectivity=connectivity,
        session_id=session_id,
        command_id=command_id,
        outcome=outcome,
        reason_code=reason_code,
        reason_message=reason_message,
    )


def _list_fanout_target_instances(
    db: Session,
    *,
    service: Service,
    request: ServiceCommandFanoutRequest,
    as_of: datetime,
) -> list[Instance]:
    instances = db.scalars(
        select(Instance)
        .where(Instance.service_id == service.id)
        .order_by(
            Instance.last_seen_at.is_(None),
            Instance.last_seen_at.desc(),
            Instance.instance_id,
        )
    ).all()

    if request.target_mode == "selected_instances":
        selected_instances = {
            instance.instance_id: instance
            for instance in instances
            if instance.instance_id in set(request.target_instance_ids)
        }
        missing_instance_ids = [
            instance_id
            for instance_id in request.target_instance_ids
            if instance_id not in selected_instances
        ]
        if missing_instance_ids:
            missing_list = ", ".join(str(instance_id) for instance_id in missing_instance_ids)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"instances [{missing_list}] were not found for service "
                    f"{service.name}/{service.environment}"
                ),
            )
        return [selected_instances[instance_id] for instance_id in request.target_instance_ids]

    return [
        instance
        for instance in instances
        if _get_instance_connectivity(instance, as_of=as_of) == "online"
    ]


async def create_service_command_fanout(
    db: Session,
    *,
    service: Service,
    request: ServiceCommandFanoutRequest,
    created_by: str | None,
    source_surface: AgentCommandSourceSurface,
) -> ServiceCommandFanoutResponse:
    as_of = utcnow()
    instances = _list_fanout_target_instances(db, service=service, request=request, as_of=as_of)
    instance_ids = [instance.instance_id for instance in instances]
    active_sessions_by_instance_id = _get_active_sessions_for_instances(
        db,
        instance_ids=instance_ids,
    )
    latest_sessions_by_instance_id = _get_latest_sessions_for_instances(
        db,
        instance_ids=instance_ids,
    )
    groups = {
        "dispatched": [],
        "queued": [],
        "skipped": [],
        "rejected": [],
    }
    command_request = AgentCommandCreateRequest(
        kind=request.kind,
        args=request.args,
        timeout_s=request.timeout_s,
        reason=request.reason,
    )
    audit = CommandAuditMetadata(
        created_by=created_by,
        reason=request.reason,
        source_surface=source_surface,
    )
    required_capability = get_command_capability(request.kind)

    for instance in instances:
        connectivity = _get_instance_connectivity(instance, as_of=as_of)
        active_session = active_sessions_by_instance_id.get(instance.instance_id)
        latest_session = latest_sessions_by_instance_id.get(instance.instance_id)
        session_id = active_session.session_id if active_session is not None else None

        if active_session is not None:
            if required_capability not in active_session.accepted_capabilities_json:
                groups["rejected"].append(
                    _build_service_command_target_summary(
                        instance=instance,
                        connectivity=connectivity,
                        outcome="rejected",
                        session_id=active_session.session_id,
                        reason_code="missing_capability",
                        reason_message=(f"active session does not advertise {required_capability}"),
                    )
                )
                continue

            task_support_rejection = _task_support_rejection(
                instance,
                kind=request.kind,
                args=request.args,
            )
            if task_support_rejection is not None:
                reason_code, reason_message = task_support_rejection
                groups["rejected"].append(
                    _build_service_command_target_summary(
                        instance=instance,
                        connectivity=connectivity,
                        outcome="rejected",
                        session_id=active_session.session_id,
                        reason_code=reason_code,
                        reason_message=reason_message,
                    )
                )
                continue

            live_connection = await agent_connection_registry.get(instance.instance_id)
            if live_connection is None:
                if request.offline_behavior == "queue":
                    command = create_command(
                        db,
                        instance=instance,
                        request=command_request,
                        audit=audit,
                    )
                    groups["queued"].append(
                        _build_service_command_target_summary(
                            instance=instance,
                            connectivity=connectivity,
                            outcome="queued",
                            session_id=active_session.session_id,
                            command_id=command.command_id,
                            reason_code="no_live_connection",
                            reason_message=(
                                "active session was recorded but no live connection is available"
                            ),
                        )
                    )
                else:
                    groups["skipped"].append(
                        _build_service_command_target_summary(
                            instance=instance,
                            connectivity=connectivity,
                            outcome="skipped",
                            session_id=active_session.session_id,
                            reason_code="no_live_connection",
                            reason_message=(
                                "active session was recorded but no live connection is available"
                            ),
                        )
                    )
                continue

            command = create_command(
                db,
                instance=instance,
                request=command_request,
                audit=audit,
            )
            command = mark_command_dispatched(
                db,
                command=command,
                session_id=live_connection.session_id,
            )
            await live_connection.send_queue.put(
                build_command_message(command).model_dump(mode="json")
            )
            groups["dispatched"].append(
                _build_service_command_target_summary(
                    instance=instance,
                    connectivity=connectivity,
                    outcome="dispatched",
                    session_id=live_connection.session_id,
                    command_id=command.command_id,
                )
            )
            continue

        if request.offline_behavior == "queue":
            if latest_session is None:
                groups["rejected"].append(
                    _build_service_command_target_summary(
                        instance=instance,
                        connectivity=connectivity,
                        outcome="rejected",
                        reason_code="no_known_session",
                        reason_message="instance has not established any prior control session",
                    )
                )
                continue

            session_id = latest_session.session_id
            if required_capability not in latest_session.accepted_capabilities_json:
                groups["rejected"].append(
                    _build_service_command_target_summary(
                        instance=instance,
                        connectivity=connectivity,
                        outcome="rejected",
                        session_id=session_id,
                        reason_code="missing_capability",
                        reason_message=(
                            f"last known session does not advertise {required_capability}"
                        ),
                    )
                )
                continue

            command = create_command(
                db,
                instance=instance,
                request=command_request,
                audit=audit,
            )
            groups["queued"].append(
                _build_service_command_target_summary(
                    instance=instance,
                    connectivity=connectivity,
                    outcome="queued",
                    session_id=session_id,
                    command_id=command.command_id,
                    reason_code="no_active_session",
                    reason_message="instance is offline; command will wait for the next reconnect",
                )
            )
            continue

        groups["skipped"].append(
            _build_service_command_target_summary(
                instance=instance,
                connectivity=connectivity,
                outcome="skipped",
                reason_code="no_active_session",
                reason_message="instance has no active control session",
            )
        )

    counts = ServiceCommandFanoutCounts(
        dispatched=len(groups["dispatched"]),
        queued=len(groups["queued"]),
        skipped=len(groups["skipped"]),
        rejected=len(groups["rejected"]),
        total=len(instances),
    )
    no_online_for_all_online = request.target_mode == "all_online" and not instances
    return ServiceCommandFanoutResponse(
        kind=request.kind,
        target_mode=request.target_mode,
        offline_behavior=request.offline_behavior,
        noop_reason_code="no_online_instances" if no_online_for_all_online else None,
        noop_reason_message=(
            "service has no online instances eligible for all_online command fanout"
            if request.target_mode == "all_online" and not instances
            else None
        ),
        counts=counts,
        dispatched=groups["dispatched"],
        queued=groups["queued"],
        skipped=groups["skipped"],
        rejected=groups["rejected"],
    )


async def create_instance_command_native(
    *, instance_id: UUID, request: AgentCommandCreateRequest, audit: CommandAuditMetadata
) -> AgentCommandSummary:
    """Persist a command in an async work unit and enqueue only after commit."""
    required = get_command_capability(request.kind)
    async with session_scope() as session:
        instance = await session.scalar(select(Instance).where(Instance.instance_id == instance_id))
        if instance is None:
            raise HTTPException(status_code=404, detail=f"instance {instance_id} was not found")
        rows = list(
            (
                await session.scalars(
                    select(AgentSession)
                    .where(AgentSession.instance_id == instance_id)
                    .order_by(AgentSession.connected_at.desc(), AgentSession.session_id.desc())
                )
            ).all()
        )
        active = next((row for row in rows if row.status == "active"), None)
        latest = rows[0] if rows else None
        session_row = active or latest
        if (
            active is None
            and request.delivery_mode == "queue_until_reconnect"
            and session_row is None
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"instance {instance_id} has no prior control session; "
                    "queue_until_reconnect requires a previously observed compatible session"
                ),
            )
        if (
            session_row is not None
            and (active is not None or request.delivery_mode == "queue_until_reconnect")
            and required not in session_row.accepted_capabilities_json
        ):
            qualifier = "active" if active is not None else "last known"
            raise HTTPException(
                status_code=409,
                detail=(
                    f"instance {instance_id} {qualifier} session does not advertise "
                    f"capability {required}"
                ),
            )
        rejection = (
            _task_support_rejection(instance, kind=request.kind, args=request.args)
            if active is not None
            else None
        )
        if rejection is not None:
            raise HTTPException(status_code=409, detail=rejection[1])
        node_name, service_id = instance.node_name, instance.service_id
        has_active = active is not None

    live = await agent_connection_registry.get(instance_id) if has_active else None
    if not has_active and request.delivery_mode == "dispatch_now_only":
        raise HTTPException(
            status_code=409,
            detail=(
                f"instance {instance_id} has no active control session; reconnect the agent "
                "before dispatching commands"
            ),
        )
    if has_active and live is None and request.delivery_mode == "dispatch_now_only":
        raise HTTPException(
            status_code=409,
            detail=(
                f"instance {instance_id} has no live control connection; retry after reconnect "
                "or choose queue_until_reconnect"
            ),
        )

    now = utcnow()
    async with session_scope() as session:
        command = AgentCommand(
            command_id=_new_command_id(),
            service_id=service_id,
            instance_id=instance_id,
            created_by=audit.created_by,
            reason=audit.reason,
            source_surface=audit.source_surface,
            kind=request.kind,
            args_json=request.args,
            timeout_s=request.timeout_s,
            status="dispatched" if live is not None else "pending",
            # Queued commands deliberately have no session_id. The known
            # session is surfaced only in the target summary, matching the
            # legacy command creation path.
            session_id=live.session_id if live is not None else None,
            dispatched_at=now if live is not None else None,
            created_at=now,
            updated_at=now,
        )
        session.add(command)
        pending = _pending_command_dispatch(command, node_name=node_name)
    if live is not None:
        await live.send_queue.put(
            build_command_message_from_dispatch(pending).model_dump(mode="json")
        )
    return build_command_summary_from_dispatch(pending)


async def create_service_command_fanout_native(
    *,
    service_name: str,
    environment: str,
    request: ServiceCommandFanoutRequest,
    created_by: str | None,
    source_surface: AgentCommandSourceSurface,
) -> ServiceCommandFanoutResponse:
    """Resolve targets, persist commands, then enqueue after the transaction."""
    as_of = utcnow()
    required = get_command_capability(request.kind)
    async with session_scope() as session:
        service = await session.scalar(
            select(Service).where(Service.name == service_name, Service.environment == environment)
        )
        if service is None:
            raise HTTPException(
                status_code=404, detail=f"service {service_name}/{environment} was not found"
            )
        instances = list(
            (
                await session.scalars(
                    select(Instance)
                    .where(Instance.service_id == service.id)
                    .order_by(
                        Instance.last_seen_at.is_(None),
                        Instance.last_seen_at.desc(),
                        Instance.instance_id,
                    )
                )
            ).all()
        )
        if request.target_mode == "selected_instances":
            by_id = {item.instance_id: item for item in instances}
            missing = [item for item in request.target_instance_ids if item not in by_id]
            if missing:
                missing_list = ", ".join(str(item) for item in missing)
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"instances [{missing_list}] were not found for service "
                        f"{service_name}/{environment}"
                    ),
                )
            instances = [by_id[item] for item in request.target_instance_ids]
        else:
            instances = [
                item
                for item in instances
                if _get_instance_connectivity(item, as_of=as_of) == "online"
            ]
        ids = [item.instance_id for item in instances]
        session_rows = (
            list(
                (
                    await session.scalars(
                        select(AgentSession)
                        .where(AgentSession.instance_id.in_(ids))
                        .order_by(AgentSession.connected_at.desc(), AgentSession.session_id.desc())
                    )
                ).all()
            )
            if ids
            else []
        )
        active_by_id: dict[UUID, AgentSession] = {}
        latest_by_id: dict[UUID, AgentSession] = {}
        for session_row in session_rows:
            latest_by_id.setdefault(session_row.instance_id, session_row)
            if session_row.status == "active":
                active_by_id.setdefault(session_row.instance_id, session_row)
        candidates = []
        for item in instances:
            active = active_by_id.get(item.instance_id)
            latest = latest_by_id.get(item.instance_id)
            candidates.append(
                {
                    "instance_id": item.instance_id,
                    "service_id": item.service_id,
                    "node_name": item.node_name,
                    "connectivity": _get_instance_connectivity(item, as_of=as_of),
                    "task_rejection": _task_support_rejection(
                        item, kind=request.kind, args=request.args
                    )
                    if active
                    else None,
                    "active_session_id": active.session_id if active else None,
                    "active_capabilities": list(active.accepted_capabilities_json)
                    if active
                    else [],
                    "latest_session_id": latest.session_id if latest else None,
                    "latest_capabilities": list(latest.accepted_capabilities_json)
                    if latest
                    else [],
                }
            )

    groups: dict[str, list[ServiceCommandFanoutTargetSummary]] = {
        "dispatched": [],
        "queued": [],
        "skipped": [],
        "rejected": [],
    }
    pending_commands: list[tuple[dict[str, object], PendingCommandDispatch, object | None]] = []
    for candidate in candidates:
        instance_id = candidate["instance_id"]
        active = candidate["active_session_id"] is not None
        known_session_id = candidate["active_session_id"] or candidate["latest_session_id"]
        outcome = reason_code = reason_message = None
        if active and required not in candidate["active_capabilities"]:
            outcome, reason_code = "rejected", "missing_capability"
            reason_message = f"active session does not advertise {required}"
        elif active and candidate["task_rejection"] is not None:
            outcome = "rejected"
            reason_code, reason_message = candidate["task_rejection"]
        elif not active and request.offline_behavior != "queue":
            outcome, reason_code = "skipped", "no_active_session"
            reason_message = "instance has no active control session"
        elif known_session_id is None:
            outcome, reason_code = "rejected", "no_known_session"
            reason_message = "instance has not established any prior control session"
        elif not active and required not in candidate["latest_capabilities"]:
            outcome, reason_code = "rejected", "missing_capability"
            reason_message = f"last known session does not advertise {required}"
        if outcome is not None:
            groups[outcome].append(
                ServiceCommandFanoutTargetSummary(
                    instance_id=instance_id,
                    node_name=candidate["node_name"],
                    connectivity=candidate["connectivity"],
                    session_id=known_session_id,
                    outcome=outcome,
                    reason_code=reason_code,
                    reason_message=reason_message,
                )
            )
            continue
        live = await agent_connection_registry.get(instance_id) if active else None
        if active and live is None and request.offline_behavior != "queue":
            groups["skipped"].append(
                ServiceCommandFanoutTargetSummary(
                    instance_id=instance_id,
                    node_name=candidate["node_name"],
                    connectivity=candidate["connectivity"],
                    session_id=known_session_id,
                    outcome="skipped",
                    reason_code="no_live_connection",
                    reason_message=(
                        "active session was recorded but no live connection is available"
                    ),
                )
            )
            continue
        outcome = "dispatched" if live is not None else "queued"
        now, command_id = utcnow(), _new_command_id()
        delivery = PendingCommandDispatch(
            command_id=command_id,
            instance_id=instance_id,
            kind=request.kind,
            args_json=dict(request.args),
            timeout_s=request.timeout_s,
            created_at=now,
            node_name=candidate["node_name"],
            session_id=(
                live.session_id if live is not None else known_session_id if active else None
            ),
            created_by=created_by,
            reason=request.reason,
            source_surface=source_surface,
            status="dispatched" if live else "pending",
            dispatched_at=now if live else None,
            updated_at=now,
        )
        pending_commands.append((candidate, delivery, live))
        groups[outcome].append(
            ServiceCommandFanoutTargetSummary(
                instance_id=instance_id,
                node_name=candidate["node_name"],
                connectivity=candidate["connectivity"],
                session_id=live.session_id if live is not None else known_session_id,
                command_id=command_id,
                outcome=outcome,
                reason_code=(
                    "no_live_connection"
                    if active and live is None
                    else "no_active_session"
                    if not active
                    else None
                ),
                reason_message=(
                    "active session was recorded but no live connection is available"
                    if active and live is None
                    else "instance is offline; command will wait for the next reconnect"
                    if not active
                    else None
                ),
            )
        )

    async with session_scope() as session:
        for candidate, delivery, _live in pending_commands:
            session.add(
                AgentCommand(
                    command_id=delivery.command_id,
                    service_id=candidate["service_id"],
                    instance_id=delivery.instance_id,
                    created_by=delivery.created_by,
                    reason=delivery.reason,
                    source_surface=delivery.source_surface,
                    kind=delivery.kind,
                    args_json=delivery.args_json,
                    timeout_s=delivery.timeout_s,
                    status=delivery.status,
                    # Keep queued durable commands detached from an old session.
                    # The target summary still carries the last known session ID.
                    session_id=delivery.session_id if delivery.status == "dispatched" else None,
                    dispatched_at=delivery.dispatched_at,
                    created_at=delivery.created_at,
                    updated_at=delivery.updated_at,
                )
            )
    for _candidate, delivery, live in pending_commands:
        if live is not None:
            await live.send_queue.put(
                build_command_message_from_dispatch(delivery).model_dump(mode="json")
            )

    counts = ServiceCommandFanoutCounts(
        dispatched=len(groups["dispatched"]),
        queued=len(groups["queued"]),
        skipped=len(groups["skipped"]),
        rejected=len(groups["rejected"]),
        total=len(candidates),
    )
    no_online = request.target_mode == "all_online" and not candidates
    return ServiceCommandFanoutResponse(
        kind=request.kind,
        target_mode=request.target_mode,
        offline_behavior=request.offline_behavior,
        noop_reason_code="no_online_instances" if no_online else None,
        noop_reason_message="service has no online instances eligible for all_online command fanout"
        if no_online
        else None,
        counts=counts,
        dispatched=groups["dispatched"],
        queued=groups["queued"],
        skipped=groups["skipped"],
        rejected=groups["rejected"],
    )
