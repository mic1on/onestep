"""Agent session bookkeeping work units.

The asynchronous helpers here each run one short database work unit on the
caller's :class:`AsyncSession` and return plain data. The synchronous
``disconnect_active_sessions`` is unchanged and keeps serving the startup path
in ``main.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.common import (
    apply_service_metadata,
    ensure_instance_stub,
    ensure_service,
)
from onestep_control_plane_api.api.schemas import AgentHelloMessage
from onestep_control_plane_api.db.models import AgentSession


def utcnow() -> datetime:
    return datetime.now(UTC)


def disconnect_active_sessions(
    db: Session,
    *,
    disconnected_at: datetime | None = None,
) -> int:
    marker = disconnected_at or utcnow()
    result = db.execute(
        update(AgentSession)
        .where(AgentSession.status == "active")
        .values(
            status="disconnected",
            disconnected_at=marker,
            updated_at=marker,
        )
    )
    db.commit()
    return int(result.rowcount or 0)


@dataclass(frozen=True)
class OpenedAgentSession:
    """Plain data returned by :func:`open_agent_session`."""

    session_id: str
    accepted_capabilities: list[str]


def _open_agent_session(
    db: Session,
    *,
    message: AgentHelloMessage,
    session_id: str,
    accepted_capabilities: list[str],
    connected_at: datetime,
) -> OpenedAgentSession:
    service = ensure_service(db, message.payload.service, update_existing_version=True)
    apply_service_metadata(service, message.payload.service)
    instance = ensure_instance_stub(db, service=service, identity=message.payload.service)
    instance.service = service
    instance.node_name = message.payload.service.node_name
    instance.hostname = message.payload.runtime.hostname
    instance.pid = message.payload.runtime.pid
    instance.deployment_version = message.payload.service.deployment_version
    instance.onestep_version = message.payload.runtime.onestep_version
    instance.python_version = message.payload.runtime.python_version
    instance.started_at = message.payload.runtime.started_at
    instance.last_seen_at = connected_at

    # A new hello supersedes any still-active session for this instance, so the
    # registry and the database agree on which session owns the connection.
    db.execute(
        update(AgentSession)
        .where(
            AgentSession.instance_id == message.payload.service.instance_id,
            AgentSession.status == "active",
        )
        .values(
            status="superseded",
            superseded_at=connected_at,
            disconnected_at=connected_at,
            updated_at=connected_at,
        )
    )
    db.add(
        AgentSession(
            session_id=session_id,
            service=service,
            instance_id=message.payload.service.instance_id,
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
    return OpenedAgentSession(
        session_id=session_id,
        accepted_capabilities=list(accepted_capabilities),
    )


async def open_agent_session(
    session: AsyncSession,
    *,
    message: AgentHelloMessage,
    session_id: str,
    accepted_capabilities: list[str],
    connected_at: datetime,
) -> OpenedAgentSession:
    """Register a hello as one work unit and return plain data.

    The shared helpers it builds on (``ensure_service``, ``ensure_instance_stub``,
    ``apply_service_metadata``) still take a synchronous ``Session`` and are
    used by synchronous callers, so they are invoked through
    ``AsyncSession.run_sync``: the same underlying session, so they participate
    in this work unit rather than opening their own.
    """

    return await session.run_sync(
        lambda db: _open_agent_session(
            db,
            message=message,
            session_id=session_id,
            accepted_capabilities=accepted_capabilities,
            connected_at=connected_at,
        )
    )


def _mark_session_message(db: Session, session_id: str, occurred_at: datetime) -> None:
    db.execute(
        update(AgentSession)
        .where(AgentSession.session_id == session_id)
        .values(last_message_at=occurred_at, updated_at=occurred_at)
    )
    db.commit()


async def mark_session_message(
    session: AsyncSession,
    session_id: str,
    occurred_at: datetime,
) -> None:
    """Record that a session delivered a message, as its own short work unit."""

    await session.run_sync(lambda db: _mark_session_message(db, session_id, occurred_at))


def _close_session(db: Session, session_id: str, *, disconnected_at: datetime) -> None:
    db.execute(
        update(AgentSession)
        .where(AgentSession.session_id == session_id, AgentSession.status == "active")
        .values(
            status="disconnected",
            disconnected_at=disconnected_at,
            last_message_at=disconnected_at,
            updated_at=disconnected_at,
        )
    )
    db.commit()


async def close_agent_session(
    session: AsyncSession,
    session_id: str,
    *,
    disconnected_at: datetime,
) -> None:
    """Mark a session disconnected during disconnect cleanup."""

    await session.run_sync(
        lambda db: _close_session(db, session_id, disconnected_at=disconnected_at)
    )


def session_is_active(db: Session, session_id: str) -> bool:
    """Whether ``session_id`` is still active; used to assert cleanup in tests."""

    return (
        db.query(AgentSession)
        .filter(AgentSession.session_id == session_id, AgentSession.status == "active")
        .count()
        > 0
    )


__all__ = [
    "OpenedAgentSession",
    "close_agent_session",
    "disconnect_active_sessions",
    "mark_session_message",
    "open_agent_session",
    "session_is_active",
    "utcnow",
]
