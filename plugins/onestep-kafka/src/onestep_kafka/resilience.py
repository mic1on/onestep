from __future__ import annotations

from onestep.resilience import ConnectorErrorKind, ConnectorOperation, ConnectorOperationError

try:  # pragma: no cover - optional dependency
    import aiokafka.errors as aiokafka_errors
except ImportError:  # pragma: no cover - optional dependency
    aiokafka_errors = None


def classify_kafka_error(exc: BaseException) -> ConnectorErrorKind | None:
    if isinstance(exc, (ConnectionError, OSError, TimeoutError)):
        return ConnectorErrorKind.DISCONNECTED
    message = str(exc).lower()
    if "connection" in message and "closed" in message:
        return ConnectorErrorKind.DISCONNECTED
    if "timed out" in message or "timeout" in message:
        return ConnectorErrorKind.TRANSIENT
    if aiokafka_errors is None:
        return None

    kafka_error = getattr(aiokafka_errors, "KafkaError", None)
    if isinstance(kafka_error, type) and isinstance(exc, kafka_error):
        retriable = getattr(exc, "retriable", None)
        if callable(retriable) and retriable():
            return ConnectorErrorKind.TRANSIENT
        invalid_config = getattr(aiokafka_errors, "InvalidConfigurationError", None)
        if isinstance(invalid_config, type) and isinstance(exc, invalid_config):
            return ConnectorErrorKind.MISCONFIGURED
        return ConnectorErrorKind.PERMANENT
    return None


def as_kafka_connector_operation_error(
    *,
    operation: ConnectorOperation,
    exc: BaseException,
    source_name: str | None = None,
    retry_delay_s: float | None = None,
) -> ConnectorOperationError | None:
    kind = classify_kafka_error(exc)
    if kind is None:
        return None
    return ConnectorOperationError(
        backend="kafka",
        operation=operation,
        kind=kind,
        source_name=source_name,
        retry_delay_s=retry_delay_s,
        cause=exc,
    )
