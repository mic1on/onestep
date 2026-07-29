from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from onestep.resilience import ConnectorErrorKind, ConnectorOperation, ConnectorOperationError

try:  # pragma: no cover - optional dependency
    import redis.exceptions as redis_exceptions
except ImportError:  # pragma: no cover - optional dependency
    redis_exceptions = None

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
class RedisErrorCause(Exception):
    message: str

    def __str__(self) -> str:
        return f"redis error: {self.message}"


def collect_sensitive_tokens(*config_values: object) -> list[str]:
    """Collect secret substrings that may surface in Redis error messages.

    Tokens are derived from connector config: raw URI/connection-string values
    (always included as a catch-all), their parsed userinfo, and known secret
    keys inside option mappings. Error causes are scrubbed against these before
    they leave the plugin.
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


def redacted_redis_cause(
    exc: BaseException, *, secrets: list[str] | None = None
) -> RedisErrorCause:
    return RedisErrorCause(_redact_message(str(exc), secrets or []))


def classify_redis_error(exc: BaseException) -> ConnectorErrorKind | None:
    if isinstance(exc, (ConnectionError, OSError, TimeoutError)):
        return ConnectorErrorKind.DISCONNECTED

    if redis_exceptions is None:
        return None

    disconnected_types = []
    for name in ("ConnectionError", "TimeoutError", "ConnectionPoolExhaustedError"):
        error_type = getattr(redis_exceptions, name, None)
        if isinstance(error_type, type):
            disconnected_types.append(error_type)
    if disconnected_types and isinstance(exc, tuple(disconnected_types)):
        return ConnectorErrorKind.DISCONNECTED

    misconfigured_types = []
    for name in ("AuthenticationError", "NoPermissionError"):
        error_type = getattr(redis_exceptions, name, None)
        if isinstance(error_type, type):
            misconfigured_types.append(error_type)
    if misconfigured_types and isinstance(exc, tuple(misconfigured_types)):
        return ConnectorErrorKind.MISCONFIGURED

    response_error = getattr(redis_exceptions, "ResponseError", None)
    if isinstance(response_error, type) and isinstance(exc, response_error):
        msg = str(exc).lower()
        if any(token in msg for token in ("auth", "noauth", "wrongpass", "invalid password")):
            return ConnectorErrorKind.MISCONFIGURED
        if "permission" in msg or "denied" in msg:
            return ConnectorErrorKind.MISCONFIGURED
        if any(token in msg for token in ("busy", "loading", "master down", "can't sync")):
            return ConnectorErrorKind.TRANSIENT
        return ConnectorErrorKind.TRANSIENT

    for name in ("BusyLoadingError", "ReadOnlyError", "MasterDownError"):
        error_type = getattr(redis_exceptions, name, None)
        if isinstance(error_type, type) and isinstance(exc, error_type):
            return ConnectorErrorKind.TRANSIENT

    return None


def as_redis_connector_operation_error(
    *,
    operation: ConnectorOperation,
    exc: BaseException,
    source_name: str | None = None,
    retry_delay_s: float | None = None,
    secrets: list[str] | None = None,
) -> ConnectorOperationError | None:
    kind = classify_redis_error(exc)
    if kind is None:
        return None
    return ConnectorOperationError(
        backend="redis",
        operation=operation,
        kind=kind,
        source_name=source_name,
        retry_delay_s=retry_delay_s,
        cause=redacted_redis_cause(exc, secrets=secrets),
    )
