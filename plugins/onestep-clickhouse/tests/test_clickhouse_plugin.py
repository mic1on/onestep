from __future__ import annotations

from importlib import metadata as importlib_metadata

import pytest

from onestep import ResourceRegistry, load_app_config
from onestep_clickhouse import (
    ClickHouseConnector,
    ClickHousePayloadError,
    ClickHouseTableSink,
    register,
    register_resources,
)


def test_public_surface_and_entry_point() -> None:
    assert register is register_resources
    assert ClickHouseConnector.__name__ == "ClickHouseConnector"
    assert ClickHouseTableSink.__name__ == "ClickHouseTableSink"
    assert ClickHousePayloadError.__name__ == "ClickHousePayloadError"
    entry_points = importlib_metadata.entry_points()
    selected = (
        entry_points.select(group="onestep.resources")
        if hasattr(entry_points, "select")
        else entry_points.get("onestep.resources", ())
    )
    assert any(
        item.name == "clickhouse" and item.value == "onestep_clickhouse:register"
        for item in selected
    )


def _config(resources):
    return {
        "apiVersion": "onestep/v1alpha1",
        "kind": "App",
        "app": {"name": "clickhouse"},
        "resources": resources,
        "tasks": [],
    }


def test_catalog_and_strict_yaml_surface() -> None:
    registry = ResourceRegistry()
    register(registry)
    catalog = {entry.type: entry for entry in registry.catalog_entries()}
    assert catalog["clickhouse"].roles == ("connector",)
    assert catalog["clickhouse_table_sink"].roles == ("sink",)
    assert catalog["clickhouse_table_sink"].topology_fields == (
        "table",
        "columns",
        "batch_size",
    )

    app = load_app_config(
        _config(
            {
                "db": {
                    "type": "clickhouse",
                    "dsn": "https://writer:secret@clickhouse:8443/analytics",
                    "client_options": {"connect_timeout": 10},
                },
                "events": {
                    "type": "clickhouse_table_sink",
                    "connector": "db",
                    "table": "events",
                    "columns": ["id", "kind"],
                    "batch_size": 100,
                    "settings": {
                        "async_insert": 1,
                        "wait_for_async_insert": 1,
                    },
                },
            }
        ),
        strict=True,
    )
    assert isinstance(app.resources["db"], ClickHouseConnector)
    assert app.resources["events"].columns == ("id", "kind")


def test_strict_yaml_rejects_unacknowledged_async_insert() -> None:
    with pytest.raises(ValueError, match="wait_for_async_insert"):
        load_app_config(
            _config(
                {
                    "db": {
                        "type": "clickhouse",
                        "dsn": "http://clickhouse:8123/default",
                    },
                    "sink": {
                        "type": "clickhouse_table_sink",
                        "connector": "db",
                        "table": "events",
                        "settings": {"async_insert": 1},
                    },
                }
            ),
            strict=True,
        )
