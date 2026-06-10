from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Any

try:  # pragma: no cover - optional dependency
    import aio_pika.exceptions as aio_pika_exceptions
except ImportError:  # pragma: no cover - optional dependency
    aio_pika_exceptions = None

try:  # pragma: no cover - optional dependency
    import aiormq.exceptions as aiormq_exceptions
except ImportError:  # pragma: no cover - optional dependency
    aiormq_exceptions = None

try:  # pragma: no cover - optional dependency
    import redis.exceptions as redis_exceptions
except ImportError:  # pragma: no cover - optional dependency
    redis_exceptions = None

class ConnectorErrorKind(str, Enum):
    DISCONNECTED = "disconnected"
    TRANSIENT = "transient"
    THROTTLED = "throttled"
    MISCONFIGURED = "misconfigured"
    PERMANENT = "permanent"
    UNCERTAIN = "uncertain"


class ConnectorOperation(str, Enum):
    OPEN = "open"
    FETCH = "fetch"
    SEND = "send"
    ACK = "ack"
    RETRY = "retry"
    FAIL = "fail"
    CLOSE = "close"


ConnectorErrorClassifier = Callable[[BaseException], ConnectorErrorKind | None]
_CONNECTOR_ERROR_CLASSIFIERS: dict[str, ConnectorErrorClassifier] = {}


class ConnectorOperationError(Exception):
    def __init__(
        self,
        *,
        backend: str,
        operation: ConnectorOperation,
        kind: ConnectorErrorKind,
        source_name: str | None = None,
        retry_delay_s: float | None = None,
        cause: BaseException | None = None,
        message: str | None = None,
    ) -> None:
        self.backend = backend
        self.operation = operation
        self.kind = kind
        self.source_name = source_name
        self.retry_delay_s = retry_delay_s
        self.cause = cause
        super().__init__(message or self._build_message())

    def _build_message(self) -> str:
        target = f" for {self.source_name}" if self.source_name else ""
        return f"{self.backend} {self.operation.value} failed ({self.kind.value}){target}"


def is_retryable_connector_error(exc: ConnectorOperationError | ConnectorErrorKind) -> bool:
    kind = exc.kind if isinstance(exc, ConnectorOperationError) else exc
    return kind in {
        ConnectorErrorKind.DISCONNECTED,
        ConnectorErrorKind.TRANSIENT,
        ConnectorErrorKind.THROTTLED,
    }


def connector_retry_delay(exc: ConnectorOperationError, *, fallback_s: float) -> float:
    if exc.retry_delay_s is not None:
        return max(0.0, exc.retry_delay_s)
    if exc.kind is ConnectorErrorKind.THROTTLED:
        return max(fallback_s, 1.0)
    return max(fallback_s, 0.0)


def register_connector_error_classifier(backend: str, classifier: ConnectorErrorClassifier) -> None:
    normalized = backend.strip().lower()
    if not normalized:
        raise ValueError("backend must be a non-empty string")
    _CONNECTOR_ERROR_CLASSIFIERS[normalized] = classifier


def classify_rabbitmq_error(exc: BaseException) -> ConnectorErrorKind | None:
    if isinstance(exc, (ConnectionError, OSError)):
        return ConnectorErrorKind.DISCONNECTED
    if isinstance(exc, RuntimeError):
        message = str(exc).lower()
        if "closed" in message or "no active transport" in message:
            return ConnectorErrorKind.DISCONNECTED

    if aio_pika_exceptions is not None:
        result = _classify_amqp_error(exc, aio_pika_exceptions)
        if result is not None:
            return result
    if aiormq_exceptions is not None:
        result = _classify_amqp_error(exc, aiormq_exceptions)
        if result is not None:
            return result
    return None


def classify_redis_error(exc: BaseException) -> ConnectorErrorKind | None:
    """Classify Redis errors for proper error handling and retry logic."""
    # Standard Python errors
    if isinstance(exc, (ConnectionError, OSError, TimeoutError)):
        return ConnectorErrorKind.DISCONNECTED
    
    if redis_exceptions is None:
        return None
    
    # Connection-related errors
    disconnected_types = []
    for name in ("ConnectionError", "TimeoutError", "ConnectionPoolExhaustedError"):
        error_type = getattr(redis_exceptions, name, None)
        if isinstance(error_type, type):
            disconnected_types.append(error_type)
    if disconnected_types and isinstance(exc, tuple(disconnected_types)):
        return ConnectorErrorKind.DISCONNECTED
    
    # Authentication/permission errors
    misconfigured_types = []
    for name in ("AuthenticationError", "NoPermissionError"):
        error_type = getattr(redis_exceptions, name, None)
        if isinstance(error_type, type):
            misconfigured_types.append(error_type)
    if misconfigured_types and isinstance(exc, tuple(misconfigured_types)):
        return ConnectorErrorKind.MISCONFIGURED
    
    # Redis-specific errors
    response_error = getattr(redis_exceptions, "ResponseError", None)
    if isinstance(response_error, type) and isinstance(exc, response_error):
        msg = str(exc).lower()
        # Authentication failures
        if any(token in msg for token in ("auth", "noauth", "wrongpass", "invalid password")):
            return ConnectorErrorKind.MISCONFIGURED
        # Permission denied
        if "permission" in msg or "denied" in msg:
            return ConnectorErrorKind.MISCONFIGURED
        # Transient errors
        if any(token in msg for token in ("busy", "loading", "master down", "can't sync")):
            return ConnectorErrorKind.TRANSIENT
        # Unknown response error - treat as transient for safety
        return ConnectorErrorKind.TRANSIENT
    
    # Busy loading or other transient states
    busy_error = getattr(redis_exceptions, "BusyLoadingError", None)
    if isinstance(busy_error, type) and isinstance(exc, busy_error):
        return ConnectorErrorKind.TRANSIENT
    
    # Read-only error (during failover)
    read_only_error = getattr(redis_exceptions, "ReadOnlyError", None)
    if isinstance(read_only_error, type) and isinstance(exc, read_only_error):
        return ConnectorErrorKind.TRANSIENT
    
    # Master down error
    master_down_error = getattr(redis_exceptions, "MasterDownError", None)
    if isinstance(master_down_error, type) and isinstance(exc, master_down_error):
        return ConnectorErrorKind.TRANSIENT
    
    return None


def as_connector_operation_error(
    *,
    backend: str,
    operation: ConnectorOperation,
    exc: BaseException,
    source_name: str | None = None,
    retry_delay_s: float | None = None,
) -> ConnectorOperationError | None:
    classifier = _CONNECTOR_ERROR_CLASSIFIERS.get(backend.strip().lower())
    if classifier is None:
        return None
    kind = classifier(exc)
    if kind is None:
        return None
    return ConnectorOperationError(
        backend=backend,
        operation=operation,
        kind=kind,
        source_name=source_name,
        retry_delay_s=retry_delay_s,
        cause=exc,
    )


def _classify_amqp_error(exc: BaseException, module: Any) -> ConnectorErrorKind | None:
    disconnected_names = {
        "AMQPConnectionError",
        "AMQPChannelError",
        "ConnectionClosed",
        "ChannelClosed",
        "ChannelInvalidStateError",
        "DeliveryError",
    }
    misconfigured_names = {
        "AuthenticationError",
        "ProbableAuthenticationError",
        "IncompatibleProtocolError",
    }
    permanent_names = {
        "ChannelNotFoundEntity",
        "ChannelPreconditionFailed",
    }
    for name in disconnected_names:
        error_type = getattr(module, name, None)
        if isinstance(error_type, type) and isinstance(exc, error_type):
            return ConnectorErrorKind.DISCONNECTED
    for name in misconfigured_names:
        error_type = getattr(module, name, None)
        if isinstance(error_type, type) and isinstance(exc, error_type):
            return ConnectorErrorKind.MISCONFIGURED
    for name in permanent_names:
        error_type = getattr(module, name, None)
        if isinstance(error_type, type) and isinstance(exc, error_type):
            return ConnectorErrorKind.PERMANENT
    return None


register_connector_error_classifier("rabbitmq", classify_rabbitmq_error)
register_connector_error_classifier("redis", classify_redis_error)
