from __future__ import annotations

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
    import botocore.exceptions as botocore_exceptions
except ImportError:  # pragma: no cover - optional dependency
    botocore_exceptions = None

try:  # pragma: no cover - optional dependency
    import sqlalchemy as sa
except ImportError:  # pragma: no cover - optional dependency
    sa = None


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


def classify_sqs_error(exc: BaseException) -> ConnectorErrorKind | None:
    if isinstance(exc, (ConnectionError, OSError, TimeoutError)):
        return ConnectorErrorKind.DISCONNECTED
    if botocore_exceptions is None:
        return None

    transient_types = []
    for name in ("EndpointConnectionError", "ConnectionClosedError", "ReadTimeoutError", "ConnectTimeoutError"):
        error_type = getattr(botocore_exceptions, name, None)
        if isinstance(error_type, type):
            transient_types.append(error_type)
    if transient_types and isinstance(exc, tuple(transient_types)):
        return ConnectorErrorKind.DISCONNECTED

    client_error = getattr(botocore_exceptions, "ClientError", None)
    if isinstance(client_error, type) and isinstance(exc, client_error):
        code = str(exc.response.get("Error", {}).get("Code", "")).lower()
        if code in {
            "throttling",
            "throttlingexception",
            "requestthrottled",
            "toomanyrequestsexception",
            "slowdown",
        }:
            return ConnectorErrorKind.THROTTLED
        if code in {"requesttimeout", "internalerror", "serviceunavailable"}:
            return ConnectorErrorKind.TRANSIENT
        if code in {
            "accessdenied",
            "accessdeniedexception",
            "invalidclienttokenid",
            "signaturedoesnotmatch",
            "aws.simplequeueservice.nonexistentqueue",
        }:
            return ConnectorErrorKind.MISCONFIGURED
        return ConnectorErrorKind.PERMANENT
    return None


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


def as_connector_operation_error(
    *,
    backend: str,
    operation: ConnectorOperation,
    exc: BaseException,
    source_name: str | None = None,
    retry_delay_s: float | None = None,
) -> ConnectorOperationError | None:
    classifier = {
        "rabbitmq": classify_rabbitmq_error,
        "sqs": classify_sqs_error,
        "mysql": classify_sqlalchemy_error,
    }.get(backend)
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
