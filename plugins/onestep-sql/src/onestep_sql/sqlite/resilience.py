"""SQLite resilience helpers.

The secret-redaction scaffolding is shared in ``onestep_sql._shared.resilience``.
Only the genuinely SQLite-specific part stays here: the SQLAlchemy error
classification table keyed to SQLite error messages (mostly "database is
locked" and integrity/syntax errors).
"""

from __future__ import annotations

from dataclasses import dataclass

from onestep.resilience import (
    ConnectorErrorKind,
    ConnectorOperation,
    ConnectorOperationError,
)
from onestep_sql._shared.resilience import (
    SQLErrorCause,
    as_connector_operation_error,
    collect_sensitive_tokens,
    redact_message,
)

__all__ = [
    "SQLiteErrorCause",
    "as_sqlite_connector_operation_error",
    "classify_sqlalchemy_error",
    "collect_sensitive_tokens",
    "redacted_sqlite_cause",
]

try:  # pragma: no cover - optional dependency
    import sqlalchemy as sa
except ImportError:  # pragma: no cover - optional dependency
    sa = None


@dataclass(frozen=True)
class SQLiteErrorCause(SQLErrorCause):
    backend = "sqlite"


def redacted_sqlite_cause(
    exc: BaseException, *, secrets: list[str] | None = None
) -> SQLiteErrorCause:
    return SQLiteErrorCause(redact_message(str(exc), secrets or []))


def classify_sqlalchemy_error(exc: BaseException) -> ConnectorErrorKind | None:
    if sa is None:
        return None
    sql_exc = sa.exc
    if isinstance(exc, getattr(sql_exc, "TimeoutError", ())):
        return ConnectorErrorKind.TRANSIENT
    if isinstance(exc, getattr(sql_exc, "InterfaceError", ())):
        return ConnectorErrorKind.DISCONNECTED
    if isinstance(exc, getattr(sql_exc, "ProgrammingError", ())):
        return ConnectorErrorKind.PERMANENT
    if isinstance(exc, getattr(sql_exc, "IntegrityError", ())):
        return ConnectorErrorKind.PERMANENT
    if isinstance(exc, getattr(sql_exc, "DBAPIError", ())):
        if getattr(exc, "connection_invalidated", False):
            return ConnectorErrorKind.DISCONNECTED
        message = " ".join(
            str(part).lower()
            for part in (getattr(exc, "orig", None), exc)
            if part is not None
        )
        if "database is locked" in message:
            return ConnectorErrorKind.TRANSIENT
        if any(token in message for token in ("no such table", "no such column", "syntax error")):
            return ConnectorErrorKind.PERMANENT
        if isinstance(exc, getattr(sql_exc, "OperationalError", ())):
            return ConnectorErrorKind.TRANSIENT
    return None


def as_sqlite_connector_operation_error(
    *,
    operation: ConnectorOperation,
    exc: BaseException,
    source_name: str | None = None,
    retry_delay_s: float | None = None,
    secrets: list[str] | None = None,
) -> ConnectorOperationError | None:
    return as_connector_operation_error(
        backend="sqlite",
        operation=operation,
        exc=exc,
        classify=classify_sqlalchemy_error,
        redacted_cause=redacted_sqlite_cause,
        source_name=source_name,
        retry_delay_s=retry_delay_s,
        secrets=secrets,
    )
