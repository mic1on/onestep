from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

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
