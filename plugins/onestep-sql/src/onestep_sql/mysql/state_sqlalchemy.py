"""MySQL backend adapter for the shared SQLAlchemy state/cursor stores.

The store implementation (serialization, auto-create, datetime-tagged cursor
encoding) lives once in ``onestep_sql._shared.state_sqlalchemy`` (issue #133,
Phase 2). Only the genuinely MySQL-specific parts stay here: the asyncio
driver mapping (``mysql+asyncmy``) and the installation hint, per design §3.1
("driver mapping belongs to the backend adapter").
"""

from __future__ import annotations

from onestep_sql._shared.state_sqlalchemy import (
    SQLAlchemyCursorStore as _SharedCursorStore,
)
from onestep_sql._shared.state_sqlalchemy import (
    SQLAlchemyStateStore as _SharedStateStore,
)
from onestep_sql._shared.state_sqlalchemy import async_dsn, sqlite_async_driver

__all__ = ["SQLAlchemyCursorStore", "SQLAlchemyStateStore"]


def _resolve_async_driver(drivername: str) -> str | None:
    """Map MySQL URL drivernames onto the asyncio-compatible asyncmy dialect."""
    if drivername == "mysql" or drivername.startswith("mysql+"):
        return "mysql+asyncmy"
    return sqlite_async_driver(drivername)


def _async_dsn(dsn: str) -> str:
    """Return a SQLAlchemy URL backed by an asyncio-compatible dialect."""
    return async_dsn(dsn, resolve_driver=_resolve_async_driver)


class SQLAlchemyStateStore(_SharedStateStore):
    _install_hint = "Install onestep-mysql."

    @staticmethod
    def _resolve_async_driver(drivername: str) -> str | None:
        return _resolve_async_driver(drivername)


class SQLAlchemyCursorStore(SQLAlchemyStateStore, _SharedCursorStore):
    """MySQL cursor store; behaviour implemented once in ``onestep_sql._shared``."""
