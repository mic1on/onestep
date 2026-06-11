from __future__ import annotations

from onestep.resilience import ConnectorErrorKind, ConnectorOperation, ConnectorOperationError

try:  # pragma: no cover - optional dependency
    import sqlalchemy as sa
except ImportError:  # pragma: no cover - optional dependency
    sa = None


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
) -> ConnectorOperationError | None:
    kind = classify_sqlalchemy_error(exc)
    if kind is None:
        return None
    return ConnectorOperationError(
        backend="mysql",
        operation=operation,
        kind=kind,
        source_name=source_name,
        retry_delay_s=retry_delay_s,
        cause=exc,
    )
