from __future__ import annotations

import asyncio
from importlib import metadata as importlib_metadata
from typing import Any

import pytest
from onestep_sqs import SQSConnector, SQSQueue, register
from onestep_sqs.resilience import (
    SQSErrorCause,
    as_sqs_connector_operation_error,
    classify_sqs_error,
)

from onestep import Envelope
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
        entry_point.name == "sqs"
        and entry_point.value == "onestep_sqs:register"
        for entry_point in entry_points
    )


def test_sqs_plugin_registers_catalog_metadata() -> None:
    registry = ResourceRegistry()
    register(registry)
    catalog = {entry.type: entry for entry in registry.catalog_entries()}
    connector_fields = {field.name: field for field in catalog["sqs"].fields}
    queue_fields = {field.name: field for field in catalog["sqs_queue"].fields}

    assert catalog["sqs"].roles == ("connector",)
    assert connector_fields["options"].secret is True
    assert catalog["sqs_queue"].roles == ("source", "sink")
    assert catalog["sqs_queue"].connector_types == ("sqs",)
    assert queue_fields["url"].required is True
    assert queue_fields["url"].secret is True


def test_yaml_builds_sqs_resources_via_plugin_entry_point() -> None:
    app = load_app_config(
        {
            "apiVersion": "onestep/v1alpha1",
            "kind": "App",
            "app": {
                "name": "sqs-plugin",
            },
            "resources": {
                "sqs": {
                    "type": "sqs",
                    "region_name": "ap-southeast-1",
                    "options": {"endpoint_url": "http://localstack:4566"},
                },
                "jobs": {
                    "type": "sqs_queue",
                    "connector": "sqs",
                    "url": "https://sqs.ap-southeast-1.amazonaws.com/123456789/jobs",
                    "wait_time_s": 0,
                    "batch_size": 5,
                    "on_fail": "release",
                },
            },
            "tasks": [],
        },
        strict=True,
    )

    assert isinstance(app.resources["sqs"], SQSConnector)
    assert app.resources["sqs"].region_name == "ap-southeast-1"
    assert app.resources["sqs"].options == {"endpoint_url": "http://localstack:4566"}
    assert isinstance(app.resources["jobs"], SQSQueue)
    assert app.resources["jobs"].connector is app.resources["sqs"]
    assert app.resources["jobs"].batch_size == 5
    assert app.resources["jobs"].on_fail == "release"


def test_sqs_plugin_normalizes_sqs_errors() -> None:
    timeout = TimeoutError("timeout")

    assert classify_sqs_error(timeout) is ConnectorErrorKind.DISCONNECTED
    normalized = as_sqs_connector_operation_error(
        operation=ConnectorOperation.SEND,
        exc=timeout,
        source_name="jobs",
        retry_delay_s=3.0,
    )

    assert isinstance(normalized, ConnectorOperationError)
    assert normalized.backend == "sqs"
    assert normalized.operation is ConnectorOperation.SEND
    assert normalized.kind is ConnectorErrorKind.DISCONNECTED
    assert normalized.source_name == "jobs"
    assert normalized.retry_delay_s == 3.0
    assert isinstance(normalized.cause, SQSErrorCause)
    assert "timeout" in str(normalized.cause)


def _entry_points_for_group(group: str) -> tuple[Any, ...]:
    entry_points = importlib_metadata.entry_points()
    if hasattr(entry_points, "select"):
        return tuple(entry_points.select(group=group))
    return tuple(entry_points.get(group, ()))


def test_sqs_connector_error_does_not_leak_option_secrets() -> None:
    secret_key = "AKIAIOSFODNN7EXAMPLE"
    secret_token = "IQoJb3JpZ2luX2VjEPn//////////wEaCXVzLXdlc3QtMiJIMEYCIQ"

    class BrokenClient:
        def send_message(self, **kwargs: Any) -> None:
            raise ConnectionError(
                f"Access denied with key {secret_key} and token {secret_token}"
            )

    connector = SQSConnector(
        options={
            "aws_access_key_id": secret_key,
            "aws_session_token": secret_token,
        },
        client=BrokenClient(),
    )
    queue = connector.queue("https://sqs.example.test/123/jobs", wait_time_s=0)

    with pytest.raises(ConnectorOperationError) as captured:
        asyncio.run(queue.send(Envelope(body={"job": 1})))

    cause = str(captured.value.cause)
    assert secret_key not in cause
    assert secret_token not in cause
    assert "<redacted>" in cause
