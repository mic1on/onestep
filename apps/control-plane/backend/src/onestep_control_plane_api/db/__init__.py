"""Database package: models plus the synchronous and native-async session APIs.

The synchronous names (``SessionLocal``, ``engine``, ``get_db_session``,
``init_db``) are imported eagerly. The async singletons (``async_engine``,
``AsyncSessionLocal``) are forwarded lazily through module ``__getattr__`` so
that importing this package never requires an async driver and never builds an
async engine as a side effect.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = [
    "Base",
    "SessionLocal",
    "Service",
    "Instance",
    "NotificationChannel",
    "NotificationDelivery",
    "NotificationInstanceState",
    "TaskCustomMetricWindow",
    "TaskDefinition",
    "TaskEvent",
    "TaskMetricWindow",
    "async_engine",
    "AsyncSessionLocal",
    "engine",
    "get_async_session",
    "get_db_session",
    "init_db",
    "session_scope",
]

from onestep_control_plane_api.db.base import Base
from onestep_control_plane_api.db.models import (
    Instance,
    NotificationChannel,
    NotificationDelivery,
    NotificationInstanceState,
    Service,
    TaskCustomMetricWindow,
    TaskDefinition,
    TaskEvent,
    TaskMetricWindow,
)
from onestep_control_plane_api.db.session import (
    SessionLocal,
    engine,
    get_async_session,
    get_db_session,
    init_db,
    session_scope,
)

_LAZY_SESSION_NAMES = ("async_engine", "AsyncSessionLocal")

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

    async_engine: AsyncEngine
    AsyncSessionLocal: async_sessionmaker[AsyncSession]


def __getattr__(name: str) -> object:
    if name in _LAZY_SESSION_NAMES:
        from onestep_control_plane_api.db import session

        return getattr(session, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_SESSION_NAMES})
