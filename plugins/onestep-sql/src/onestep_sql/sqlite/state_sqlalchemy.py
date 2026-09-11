"""SQLite backend adapter for the shared SQLAlchemy state/cursor stores.

Mirrors the mysql/postgres adapters: behaviour lives once in
``onestep_sql._shared.state_sqlalchemy``; only the SQLite-specific asyncio
driver mapping and installation hint stay here.
"""

from __future__ import annotations

from onestep_sql._shared.state_sqlalchemy import (
    SQLAlchemyCursorStore as _SharedCursorStore,
)
from onestep_sql._shared.state_sqlalchemy import (
    SQLAlchemyStateStore as _SharedStateStore,
)

__all__ = ["SQLAlchemyCursorStore", "SQLAlchemyStateStore"]


def _resolve_async_driver(drivername: str) -> str | None:
    """Map SQLite URL drivernames onto the asyncio-compatible aiosqlite dialect."""
    if drivername in ("sqlite", "sqlite+pysqlite"):
        return "sqlite+aiosqlite"
    return None


def _async_dsn(dsn: str) -> str:
    """Return a SQLAlchemy URL backed by an asyncio-compatible dialect."""
    from onestep_sql._shared.state_sqlalchemy import async_dsn

    return async_dsn(dsn, resolve_driver=_resolve_async_driver)


class SQLAlchemyStateStore(_SharedStateStore):
    _install_hint = "Install onestep-sql with the 'sqlite' extra."

    @staticmethod
    def _resolve_async_driver(drivername: str) -> str | None:
        return _resolve_async_driver(drivername)


class SQLAlchemyCursorStore(SQLAlchemyStateStore, _SharedCursorStore):
    """SQLite cursor store; behaviour implemented once in ``onestep_sql._shared``."""
