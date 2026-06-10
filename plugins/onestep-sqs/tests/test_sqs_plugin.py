from __future__ import annotations

from importlib import metadata as importlib_metadata
from typing import Any

from onestep.config import load_app_config
from onestep.resilience import ConnectorErrorKind
from onestep_sqs import SQSConnector, SQSQueue
from onestep_sqs.resilience import classify_sqs_error


def test_package_exposes_onestep_resource_entry_point() -> None:
    entry_points = _entry_points_for_group("onestep.resources")

    assert any(
        entry_point.name == "sqs"
        and entry_point.value == "onestep_sqs:register"
        for entry_point in entry_points
    )


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


def test_sqs_plugin_registers_sqs_error_classifier() -> None:
    assert classify_sqs_error(TimeoutError("timeout")) is ConnectorErrorKind.DISCONNECTED


def _entry_points_for_group(group: str) -> tuple[Any, ...]:
    entry_points = importlib_metadata.entry_points()
    if hasattr(entry_points, "select"):
        return tuple(entry_points.select(group=group))
    return tuple(entry_points.get(group, ()))
