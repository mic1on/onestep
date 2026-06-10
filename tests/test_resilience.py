from __future__ import annotations

from onestep.resilience import (
    ConnectorErrorKind,
    ConnectorOperation,
    ConnectorOperationError,
    connector_retry_delay,
    is_retryable_connector_error,
)


def test_connector_operation_error_keeps_normalized_fields() -> None:
    cause = TimeoutError("timeout")

    result = ConnectorOperationError(
        backend="custom",
        operation=ConnectorOperation.FETCH,
        kind=ConnectorErrorKind.TRANSIENT,
        source_name="source",
        retry_delay_s=2.5,
        cause=cause,
    )

    assert result.backend == "custom"
    assert result.operation is ConnectorOperation.FETCH
    assert result.kind is ConnectorErrorKind.TRANSIENT
    assert result.source_name == "source"
    assert result.retry_delay_s == 2.5
    assert result.cause is cause
    assert str(result) == "custom fetch failed (transient) for source"


def test_retryable_connector_error_kinds_are_backend_neutral() -> None:
    assert is_retryable_connector_error(ConnectorErrorKind.DISCONNECTED)
    assert is_retryable_connector_error(ConnectorErrorKind.TRANSIENT)
    assert is_retryable_connector_error(ConnectorErrorKind.THROTTLED)
    assert not is_retryable_connector_error(ConnectorErrorKind.MISCONFIGURED)
    assert not is_retryable_connector_error(ConnectorErrorKind.PERMANENT)


def test_connector_retry_delay_uses_normalized_error_metadata() -> None:
    throttled = ConnectorOperationError(
        backend="custom",
        operation=ConnectorOperation.SEND,
        kind=ConnectorErrorKind.THROTTLED,
    )
    explicit = ConnectorOperationError(
        backend="custom",
        operation=ConnectorOperation.SEND,
        kind=ConnectorErrorKind.TRANSIENT,
        retry_delay_s=0.25,
    )

    assert connector_retry_delay(throttled, fallback_s=0.1) == 1.0
    assert connector_retry_delay(explicit, fallback_s=5.0) == 0.25
