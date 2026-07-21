from __future__ import annotations

from importlib import metadata as importlib_metadata
from typing import Any

import pytest

from onestep.config import load_app_config
from onestep.resilience import ConnectorErrorKind, ConnectorOperation, ConnectorOperationError
from onestep.resource_registry import ResourceRegistry
from onestep_kafka import KafkaConnector, KafkaTopic, register
from onestep_kafka.resilience import as_kafka_connector_operation_error, classify_kafka_error


def test_package_exposes_onestep_resource_entry_point() -> None:
    entry_points = _entry_points_for_group("onestep.resources")

    assert any(
        entry_point.name == "kafka"
        and entry_point.value == "onestep_kafka:register"
        for entry_point in entry_points
    )


def test_kafka_plugin_registers_catalog_metadata() -> None:
    registry = ResourceRegistry()
    register(registry)
    catalog = {entry.type: entry for entry in registry.catalog_entries()}
    connector_fields = {field.name: field for field in catalog["kafka"].fields}

    assert catalog["kafka"].roles == ("connector",)
    assert connector_fields["bootstrap_servers"].required is True
    assert connector_fields["options"].secret is True
    assert catalog["kafka_topic"].roles == ("source", "sink")
    assert catalog["kafka_topic"].connector_types == ("kafka",)


def test_yaml_builds_kafka_resources_via_plugin_entry_point() -> None:
    app = load_app_config(
        {
            "apiVersion": "onestep/v1alpha1",
            "kind": "App",
            "app": {"name": "kafka-plugin"},
            "resources": {
                "kafka_main": {
                    "type": "kafka",
                    "bootstrap_servers": ["localhost:9092"],
                    "options": {"security_protocol": "PLAINTEXT"},
                },
                "orders": {
                    "type": "kafka_topic",
                    "connector": "kafka_main",
                    "topic": "orders.events",
                    "group_id": "orders-workers",
                    "client_id": "onestep-test",
                    "batch_size": 25,
                    "poll_timeout_ms": 250,
                    "key": "static-key",
                    "headers": {"source": "onestep"},
                    "consumer_options": {"auto_offset_reset": "earliest"},
                    "producer_options": {"compression_type": "gzip"},
                },
            },
            "tasks": [],
        },
        strict=True,
    )

    connector = app.resources["kafka_main"]
    topic = app.resources["orders"]
    assert isinstance(connector, KafkaConnector)
    assert connector.bootstrap_servers == ["localhost:9092"]
    assert connector.options == {"security_protocol": "PLAINTEXT"}
    assert isinstance(topic, KafkaTopic)
    assert topic.connector is connector
    assert topic.topic == "orders.events"
    assert topic.group_id == "orders-workers"
    assert topic.client_id == "onestep-test"
    assert topic.batch_size == 25
    assert topic.poll_timeout_ms == 250
    assert topic.key == "static-key"
    assert topic.headers == {"source": "onestep"}
    assert topic.consumer_options == {"auto_offset_reset": "earliest"}
    assert topic.producer_options == {"compression_type": "gzip"}


def test_kafka_topic_group_id_is_optional_for_sink_only_resource() -> None:
    app = load_app_config(
        {
            "apiVersion": "onestep/v1alpha1",
            "kind": "App",
            "app": {"name": "kafka-sink-only"},
            "resources": {
                "kafka_main": {
                    "type": "kafka",
                    "bootstrap_servers": "localhost:9092",
                },
                "outbox": {
                    "type": "kafka_topic",
                    "connector": "kafka_main",
                    "topic": "orders.outbox",
                },
            },
            "tasks": [],
        },
        strict=True,
    )

    assert isinstance(app.resources["outbox"], KafkaTopic)
    assert app.resources["outbox"].group_id is None


def test_kafka_topic_rejects_invalid_batch_size_in_strict_mode() -> None:
    with pytest.raises(ValueError, match="'resources.orders.batch_size' must be a positive integer"):
        load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "bad-kafka"},
                "resources": {
                    "kafka_main": {
                        "type": "kafka",
                        "bootstrap_servers": "localhost:9092",
                    },
                    "orders": {
                        "type": "kafka_topic",
                        "connector": "kafka_main",
                        "topic": "orders.events",
                        "batch_size": 0,
                    },
                },
                "tasks": [],
            },
            strict=True,
        )


def test_kafka_plugin_normalizes_common_connection_errors() -> None:
    connection_error = ConnectionError("broker unavailable")

    assert classify_kafka_error(connection_error) is ConnectorErrorKind.DISCONNECTED
    normalized = as_kafka_connector_operation_error(
        operation=ConnectorOperation.OPEN,
        exc=connection_error,
        source_name="orders",
        retry_delay_s=1.0,
    )

    assert isinstance(normalized, ConnectorOperationError)
    assert normalized.backend == "kafka"
    assert normalized.operation is ConnectorOperation.OPEN
    assert normalized.kind is ConnectorErrorKind.DISCONNECTED
    assert normalized.source_name == "orders"
    assert normalized.retry_delay_s == 1.0
    assert normalized.cause is connection_error


def _entry_points_for_group(group: str) -> tuple[Any, ...]:
    entry_points = importlib_metadata.entry_points()
    if hasattr(entry_points, "select"):
        return tuple(entry_points.select(group=group))
    return tuple(entry_points.get(group, ()))
