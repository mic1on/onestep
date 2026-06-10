from __future__ import annotations

from onestep.resilience import (
    ConnectorErrorKind,
    ConnectorOperation,
    as_connector_operation_error,
    register_connector_error_classifier,
)


def test_connector_error_classifiers_are_registered_by_backend() -> None:
    class CustomError(Exception):
        pass

    register_connector_error_classifier("custom", lambda exc: ConnectorErrorKind.TRANSIENT)

    result = as_connector_operation_error(
        backend="custom",
        operation=ConnectorOperation.FETCH,
        exc=CustomError("temporary"),
        source_name="source",
    )

    assert result is not None
    assert result.kind is ConnectorErrorKind.TRANSIENT
    assert result.backend == "custom"
    assert result.source_name == "source"


def test_unknown_connector_error_backend_returns_none() -> None:
    result = as_connector_operation_error(
        backend="unknown",
        operation=ConnectorOperation.FETCH,
        exc=TimeoutError("timeout"),
    )

    assert result is None
