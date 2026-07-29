from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from onestep.resilience import ConnectorErrorKind, ConnectorOperation, ConnectorOperationError

try:  # pragma: no cover - optional dependency
    import sqlalchemy as sa
except ImportError:  # pragma: no cover - optional dependency
    sa = None

_REDACTED = "<redacted>"
_MAX_MESSAGE_LENGTH = 500
_SECRET_OPTION_KEYS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "access_token",
        "api_key",
        "apikey",
        "credentials",
        "authorization",
    }
)


@dataclass(frozen=True)
class MySQLErrorCause(Exception):
    message: str

    def __str__(self) -> str:
        return f"mysql error: {self.message}"


def collect_sensitive_tokens(*config_values: object) -> list[str]:
    """Collect secret substrings that may surface in MySQL error messages.

    SQLAlchemy masks the password in its rendered URL, but the underlying DBAPI
    ``orig`` exception can still echo the full DSN (e.g. ``Access denied for
    user 'user'@'host' (using password: YES)`` after echoing the connection
    string). Tokens are derived from connector config and used to scrub error
    causes before they leave the plugin.
    """
    tokens: list[str] = []
    seen: set[str] = set()

    def add(value: object) -> None:
        if value is None:
            return
        text = str(value)
        if text and text not in seen:
            seen.add(text)
            tokens.append(text)

    for value in config_values:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if str(key).lower() in _SECRET_OPTION_KEYS:
                    add(item)
            continue
        add(value)
        try:
            parsed = urlsplit(str(value))
        except ValueError:
            continue
        username = parsed.username
        password = parsed.password
        if username and password:
            add(f"{username}:{password}")
            add(f"{username}:{password}@")
        add(password)
    return tokens


def _redact_message(message: str, tokens: list[str]) -> str:
    redacted = message
    for token in sorted(tokens, key=len, reverse=True):
        if token:
            redacted = redacted.replace(token, _REDACTED)
    return redacted[:_MAX_MESSAGE_LENGTH]


def redacted_mysql_cause(
    exc: BaseException, *, secrets: list[str] | None = None
) -> MySQLErrorCause:
    return MySQLErrorCause(_redact_message(str(exc), secrets or []))


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
    kind = classify_sqlalchemy_error(exc)
    if kind is None:
        return None
    return ConnectorOperationError(
        backend="mysql",
        operation=operation,
        kind=kind,
        source_name=source_name,
        retry_delay_s=retry_delay_s,
        cause=redacted_mysql_cause(exc, secrets=secrets),
    )
