from __future__ import annotations

from importlib import metadata as importlib_metadata
from typing import Any

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
    assert normalized.operation is ConnectorOperation.SEND
    assert normalized.kind is ConnectorErrorKind.DISCONNECTED
    assert normalized.source_name == "jobs"
    assert normalized.retry_delay_s == 3.0
    assert isinstance(normalized.cause, CFQueuesErrorCause)


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
