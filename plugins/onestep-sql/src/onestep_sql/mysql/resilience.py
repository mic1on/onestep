"""MySQL resilience helpers.

The secret-redaction scaffolding (token collection, message redaction, the
error-cause base class and the ``ConnectorOperationError`` factory) is shared
with PostgreSQL in ``onestep_sql._shared.resilience`` (issue #133, Phase 2).
Only the genuinely MySQL-specific part stays here: the SQLAlchemy error
classification table keyed to MySQL server error messages.
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
    collect_sensitive_tokens,
    redact_message,
)
from onestep_sql._shared.resilience import (
    as_connector_operation_error as _shared_connector_operation_error,
)

__all__ = [
    "MySQLErrorCause",
    "as_mysql_connector_operation_error",
    "classify_sqlalchemy_error",
    "collect_sensitive_tokens",
    "redacted_mysql_cause",
]

try:  # pragma: no cover - optional dependency
    import sqlalchemy as sa
except ImportError:  # pragma: no cover - optional dependency
    sa = None


@dataclass(frozen=True)
class MySQLErrorCause(SQLErrorCause):
    backend = "mysql"


def redacted_mysql_cause(
    exc: BaseException, *, secrets: list[str] | None = None
) -> MySQLErrorCause:
    return MySQLErrorCause(redact_message(str(exc), secrets or []))


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
    if isinstance(exc, getattr(sql_exc, "DBAPIError", ())):
        if getattr(exc, "connection_invalidated", False):
            return ConnectorErrorKind.DISCONNECTED
        message = " ".join(
            str(part).lower()
            for part in (
                getattr(exc, "orig", None),
                exc,
            )
            if part is not None
        )
        if any(token in message for token in ("server has gone away", "lost connection", "connection refused")):
            return ConnectorErrorKind.DISCONNECTED
        if any(token in message for token in ("lock wait timeout", "deadlock found")):
            return ConnectorErrorKind.TRANSIENT
        if any(token in message for token in ("access denied", "unknown database", "authentication")):
            return ConnectorErrorKind.MISCONFIGURED
        if any(token in message for token in ("no such table", "unknown table", "unknown column", "syntax error")):
            return ConnectorErrorKind.PERMANENT
        if isinstance(exc, getattr(sql_exc, "OperationalError", ())):
            return ConnectorErrorKind.TRANSIENT
    return None


def as_mysql_connector_operation_error(
    *,
    operation: ConnectorOperation,
    exc: BaseException,
    source_name: str | None = None,
    retry_delay_s: float | None = None,
    secrets: list[str] | None = None,
) -> ConnectorOperationError | None:
    return _shared_connector_operation_error(
        backend="mysql",
        operation=operation,
        exc=exc,
        classify=classify_sqlalchemy_error,
        redacted_cause=redacted_mysql_cause,
        source_name=source_name,
        retry_delay_s=retry_delay_s,
        secrets=secrets,
    )
