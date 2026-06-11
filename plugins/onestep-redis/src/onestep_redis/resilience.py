from __future__ import annotations

from onestep.resilience import ConnectorErrorKind, ConnectorOperation, ConnectorOperationError

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


def as_redis_connector_operation_error(
    *,
    operation: ConnectorOperation,
    exc: BaseException,
    source_name: str | None = None,
    retry_delay_s: float | None = None,
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
        cause=exc,
    )
