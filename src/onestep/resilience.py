from __future__ import annotations

from enum import Enum


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
