from __future__ import annotations

from onestep.resilience import ConnectorErrorKind, register_connector_error_classifier

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


def register_error_classifiers() -> None:
    register_connector_error_classifier("mysql", classify_sqlalchemy_error)
