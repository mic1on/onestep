from __future__ import annotations

from importlib import metadata as importlib_metadata
from typing import Any

import pytest

from onestep_cf_queues import CFQueue, CFQueuesConnector, register
from onestep_cf_queues.resilience import (
    CFQueuesErrorCause,
    as_cf_connector_operation_error,
    classify_cf_error,
    classify_cf_status,
)

from onestep.config import load_app_config
from onestep.resilience import (
    ConnectorErrorKind,
    ConnectorOperation,
    ConnectorOperationError,
    is_retryable_connector_error,
)
from onestep.resource_registry import ResourceRegistry


def test_package_exposes_onestep_resource_entry_point() -> None:
    entry_points = _entry_points_for_group("onestep.resources")

    assert any(
        entry_point.name == "cf_queues"
        and entry_point.value == "onestep_cf_queues:register"
        for entry_point in entry_points
    )


def test_cf_queues_plugin_registers_catalog_metadata() -> None:
    registry = ResourceRegistry()
    register(registry)
    catalog = {entry.type: entry for entry in registry.catalog_entries()}
    connector_fields = {field.name: field for field in catalog["cf_queues"].fields}
    queue_fields = {field.name: field for field in catalog["cf_queue"].fields}

    assert catalog["cf_queues"].roles == ("connector",)
    assert connector_fields["api_token"].secret is True
    assert connector_fields["api_token"].required is True
    assert catalog["cf_queue"].roles == ("source", "sink")
    assert catalog["cf_queue"].connector_types == ("cf_queues",)
    assert queue_fields["queue_id"].required is True


def test_yaml_builds_cf_queue_resources_via_plugin_entry_point() -> None:
    app = load_app_config(
        {
            "apiVersion": "onestep/v1alpha1",
            "kind": "App",
            "app": {"name": "cf-queues-plugin"},
            "resources": {
                "cf": {
                    "type": "cf_queues",
                    "account_id": "acct-123",
                    "api_token": "token-abc",
                },
                "jobs": {
                    "type": "cf_queue",
                    "connector": "cf",
                    "queue_id": "queue-xyz",
                    "batch_size": 25,
                    "on_fail": "retry",
                },
            },
            "tasks": [],
        },
        strict=True,
    )

    assert isinstance(app.resources["cf"], CFQueuesConnector)
    assert app.resources["cf"].account_id == "acct-123"
    assert app.resources["cf"].api_token == "token-abc"
    assert isinstance(app.resources["jobs"], CFQueue)
    assert app.resources["jobs"].connector is app.resources["cf"]
    assert app.resources["jobs"].queue_id == "queue-xyz"
    assert app.resources["jobs"].batch_size == 25
    assert app.resources["jobs"].on_fail == "retry"


def test_cf_queues_plugin_normalizes_transport_errors() -> None:
    timeout = TimeoutError("timeout")

    assert classify_cf_error(timeout) is ConnectorErrorKind.DISCONNECTED
    normalized = as_cf_connector_operation_error(
        operation=ConnectorOperation.SEND,
        exc=timeout,
        source_name="jobs",
        retry_delay_s=3.0,
    )

    assert isinstance(normalized, ConnectorOperationError)
    assert normalized.backend == "cf_queues"


def _real_cloudflare_exception(kind: str, status: int | None = None) -> Exception:
    httpx = pytest.importorskip("httpx")
    cloudflare = pytest.importorskip("cloudflare")

    request = httpx.Request("POST", "https://api.cloudflare.com")
    if kind == "timeout":
        return cloudflare.APITimeoutError(request=request)
    if kind == "status":
        response = httpx.Response(status, request=request)
        return cloudflare.APIStatusError("api status", response=response, body=None)
    if kind == "api_error":
        return cloudflare.APIError("bare api error", request, body=None)
    raise AssertionError(f"unknown sdk exception kind: {kind}")


def test_cf_api_timeout_is_retryable_for_fetch() -> None:
    """P0-1: pull/fetch timeout must be DISCONNECTED (retryable), not UNCERTAIN."""
    exc = _real_cloudflare_exception("timeout")

    assert classify_cf_error(exc, operation=ConnectorOperation.FETCH) is (
        ConnectorErrorKind.DISCONNECTED
    )
    assert classify_cf_error(exc, operation=ConnectorOperation.OPEN) is (
        ConnectorErrorKind.DISCONNECTED
    )
    assert classify_cf_error(exc, operation=ConnectorOperation.ACK) is (
        ConnectorErrorKind.DISCONNECTED
    )
    assert classify_cf_error(exc, operation=ConnectorOperation.RETRY) is (
        ConnectorErrorKind.DISCONNECTED
    )


@pytest.mark.parametrize("operation", list(ConnectorOperation))
def test_cf_api_timeout_operation_aware_classification(operation: ConnectorOperation) -> None:
    exc = _real_cloudflare_exception("timeout")
    kind = classify_cf_error(exc, operation=operation)

    if operation is ConnectorOperation.SEND:
        assert kind is ConnectorErrorKind.UNCERTAIN
    else:
        assert kind is ConnectorErrorKind.DISCONNECTED


@pytest.mark.parametrize(
    ("status", "expected_kind", "retryable"),
    [
        (429, ConnectorErrorKind.THROTTLED, True),
        (503, ConnectorErrorKind.TRANSIENT, True),
        (403, ConnectorErrorKind.MISCONFIGURED, False),
    ],
)
def test_cf_api_status_error_classification(status, expected_kind, retryable) -> None:
    exc = _real_cloudflare_exception("status", status)

    assert classify_cf_error(exc, operation=ConnectorOperation.FETCH) is expected_kind
    normalized = as_cf_connector_operation_error(
        operation=ConnectorOperation.FETCH,
        exc=exc,
        source_name="jobs",
        retry_delay_s=1.0,
    )

    assert isinstance(normalized, ConnectorOperationError)
    assert normalized.kind is expected_kind
    assert is_retryable_connector_error(normalized) is retryable


def test_cf_bare_api_error_is_transient_never_none() -> None:
    """P0-2: unknown SDK exceptions must never escape normalization as None."""
    for kind in ("api_error",):
        exc = _real_cloudflare_exception(kind)

        assert classify_cf_error(exc) is ConnectorErrorKind.TRANSIENT
        assert classify_cf_error(exc, operation=ConnectorOperation.FETCH) is (
            ConnectorErrorKind.TRANSIENT
        )
        normalized = as_cf_connector_operation_error(
            operation=ConnectorOperation.FETCH,
            exc=exc,
            source_name="jobs",
            retry_delay_s=1.0,
        )

        assert isinstance(normalized, ConnectorOperationError)
        assert is_retryable_connector_error(normalized) is True


def test_cf_api_response_validation_error_is_transient_never_none() -> None:
    httpx = pytest.importorskip("httpx")
    cloudflare = pytest.importorskip("cloudflare")
    request = httpx.Request("GET", "https://api.cloudflare.com")
    exc = cloudflare.APIResponseValidationError(
        httpx.Response(200, request=request), body=None
    )

    assert classify_cf_error(exc, operation=ConnectorOperation.FETCH) is (
        ConnectorErrorKind.TRANSIENT
    )
    normalized = as_cf_connector_operation_error(
        operation=ConnectorOperation.FETCH,
        exc=exc,
        source_name="jobs",
        retry_delay_s=1.0,
    )

    assert isinstance(normalized, ConnectorOperationError)
    assert is_retryable_connector_error(normalized) is True


def test_cf_queues_status_classification() -> None:
    from onestep.resilience import ConnectorErrorKind as Kind

    assert classify_cf_status(429) is Kind.THROTTLED
    assert classify_cf_status(503) is Kind.TRANSIENT
    assert classify_cf_status(403) is Kind.MISCONFIGURED
    assert classify_cf_status(400) is Kind.PERMANENT


def test_cf_queues_error_does_not_leak_api_token() -> None:
    secret = "cf-token-super-secret-value"
    exc = ConnectionError(f"connect failed for Bearer {secret}")

    normalized = as_cf_connector_operation_error(
        operation=ConnectorOperation.FETCH,
        exc=exc,
        source_name="jobs",
        secrets=[secret, f"Bearer {secret}"],
    )

    assert normalized is not None
    cause = str(normalized.cause)
    assert secret not in cause
    assert "<redacted>" in cause


def _entry_points_for_group(group: str) -> tuple[Any, ...]:
    entry_points = importlib_metadata.entry_points()
    if hasattr(entry_points, "select"):
        return tuple(entry_points.select(group=group))
    return tuple(entry_points.get(group, ()))
