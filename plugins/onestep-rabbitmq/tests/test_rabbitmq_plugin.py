from __future__ import annotations

from importlib import metadata as importlib_metadata
from typing import Any

from onestep.config import load_app_config
from onestep.resilience import ConnectorErrorKind
from onestep_rabbitmq import RabbitMQConnector, RabbitMQQueue
from onestep_rabbitmq.resilience import classify_rabbitmq_error


def test_package_exposes_onestep_resource_entry_point() -> None:
    entry_points = _entry_points_for_group("onestep.resources")

    assert any(
        entry_point.name == "rabbitmq"
        and entry_point.value == "onestep_rabbitmq:register"
        for entry_point in entry_points
    )


def test_yaml_builds_rabbitmq_resources_via_plugin_entry_point() -> None:
    app = load_app_config(
        {
            "apiVersion": "onestep/v1alpha1",
            "kind": "App",
            "app": {
                "name": "rabbitmq-plugin",
            },
            "resources": {
                "rmq": {
                    "type": "rabbitmq",
                    "url": "amqp://guest:guest@localhost/",
                    "options": {"client_properties": {"connection_name": "yaml-worker"}},
                },
                "jobs": {
                    "type": "rabbitmq_queue",
                    "connector": "rmq",
                    "queue": "incoming_jobs",
                    "exchange": "jobs.events",
                    "routing_key": "jobs.created",
                    "exclusive": True,
                    "prefetch": 50,
                },
            },
            "tasks": [],
        },
        strict=True,
    )

    assert isinstance(app.resources["rmq"], RabbitMQConnector)
    assert app.resources["rmq"].url == "amqp://guest:guest@localhost/"
    assert app.resources["rmq"].options == {"client_properties": {"connection_name": "yaml-worker"}}
    assert isinstance(app.resources["jobs"], RabbitMQQueue)
    assert app.resources["jobs"].connector is app.resources["rmq"]
    assert app.resources["jobs"].name == "incoming_jobs"
    assert app.resources["jobs"].routing_key == "jobs.created"
    assert app.resources["jobs"].exchange_name == "jobs.events"
    assert app.resources["jobs"].exclusive is True
    assert app.resources["jobs"].prefetch == 50


def test_rabbitmq_plugin_registers_error_classifier() -> None:
    assert classify_rabbitmq_error(ConnectionError("connection refused")) is ConnectorErrorKind.DISCONNECTED
    assert classify_rabbitmq_error(RuntimeError("connection closed")) is ConnectorErrorKind.DISCONNECTED


def _entry_points_for_group(group: str) -> tuple[Any, ...]:
    entry_points = importlib_metadata.entry_points()
    if hasattr(entry_points, "select"):
        return tuple(entry_points.select(group=group))
    return tuple(entry_points.get(group, ()))
