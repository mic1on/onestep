from __future__ import annotations

from onestep.resilience import ConnectorErrorKind, register_connector_error_classifier

try:  # pragma: no cover - optional dependency
    import redis.exceptions as redis_exceptions
except ImportError:  # pragma: no cover - optional dependency
    redis_exceptions = None


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


def register_error_classifiers() -> None:
    register_connector_error_classifier("redis", classify_redis_error)
