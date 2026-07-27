from __future__ import annotations

from importlib import metadata as importlib_metadata

import pytest
from onestep_elasticsearch import (
    ElasticsearchBulkError,
    ElasticsearchBulkItemError,
    ElasticsearchBulkSink,
    ElasticsearchConnector,
    register,
    register_resources,
)

from onestep import ResourceRegistry, load_app_config


def _entry_points_for_group(group: str):
    entry_points = importlib_metadata.entry_points()
    if hasattr(entry_points, "select"):
        return list(entry_points.select(group=group))
    return list(entry_points.get(group, ()))


def test_package_exports_the_approved_python_surface() -> None:
    assert register is register_resources
    assert ElasticsearchConnector.__name__ == "ElasticsearchConnector"
    assert ElasticsearchBulkSink.__name__ == "ElasticsearchBulkSink"
    assert ElasticsearchBulkError.__name__ == "ElasticsearchBulkError"
    assert ElasticsearchBulkItemError.__name__ == "ElasticsearchBulkItemError"


def test_package_exposes_resource_entry_point() -> None:
    assert any(
        item.name == "elasticsearch" and item.value == "onestep_elasticsearch:register"
        for item in _entry_points_for_group("onestep.resources")
    )


def _config(resources):
    return {
        "apiVersion": "onestep/v1alpha1",
        "kind": "App",
        "app": {"name": "search"},
        "resources": resources,
        "tasks": [],
    }


def test_catalog_matches_strict_surface() -> None:
    registry = ResourceRegistry()
    register(registry)
    catalog = {entry.type: entry for entry in registry.catalog_entries()}

    assert catalog["elasticsearch"].roles == ("connector",)
    assert catalog["elasticsearch_bulk_sink"].roles == ("sink",)
    assert catalog["elasticsearch_bulk_sink"].connector_types == ("elasticsearch",)
    assert catalog["elasticsearch_bulk_sink"].topology_fields == (
        "index",
        "operation",
        "chunk_size",
    )
    connector_fields = {field.name: field for field in catalog["elasticsearch"].fields}
    assert connector_fields["password"].secret is True
    assert connector_fields["api_key"].secret is True
    assert connector_fields["bearer_token"].secret is True
    assert connector_fields["headers"].secret is True
    assert connector_fields["client_key"].secret is True


def test_strict_yaml_builds_connector_and_sink() -> None:
    app = load_app_config(
        _config(
            {
                "search": {
                    "type": "elasticsearch",
                    "hosts": ["https://search:9200"],
                    "distribution": "opensearch",
                    "api_key": "secret",
                },
                "events": {
                    "type": "elasticsearch_bulk_sink",
                    "connector": "search",
                    "index": "events",
                    "operation": "create",
                    "chunk_size": 25,
                },
            }
        ),
        strict=True,
    )

    assert isinstance(app.resources["search"], ElasticsearchConnector)
    assert app.resources["events"].operation == "create"


@pytest.mark.parametrize(
    "connector",
    [
        {"type": "elasticsearch", "hosts": []},
        {"type": "elasticsearch", "hosts": ["ftp://search"]},
        {"type": "elasticsearch", "hosts": ["https://search"], "username": "u"},
        {
            "type": "elasticsearch",
            "hosts": ["https://search"],
            "api_key": "a",
            "bearer_token": "b",
        },
    ],
)
def test_strict_yaml_rejects_invalid_connector(connector) -> None:
    with pytest.raises((TypeError, ValueError)):
        load_app_config(_config({"search": connector}), strict=True)


@pytest.mark.parametrize(
    "sink",
    [
        {
            "type": "elasticsearch_bulk_sink",
            "connector": "search",
            "index": "events",
            "operation": "update",
        },
        {
            "type": "elasticsearch_bulk_sink",
            "connector": "search",
            "index": "events",
            "chunk_size": 0,
        },
        {
            "type": "elasticsearch_bulk_sink",
            "connector": "search",
            "index": "events",
            "max_chunk_bytes": -1,
        },
        {
            "type": "elasticsearch_bulk_sink",
            "connector": "search",
            "index": "events",
            "refresh": "immediate",
        },
        {
            "type": "elasticsearch_bulk_sink",
            "connector": "search",
            "index": "events",
            "refresh": 1,
        },
        {
            "type": "elasticsearch_bulk_sink",
            "connector": "search",
            "index": "events",
            "dynamic_action": "index",
        },
    ],
)
def test_strict_yaml_rejects_invalid_sink_fields(sink) -> None:
    with pytest.raises((TypeError, ValueError)):
        load_app_config(
            _config(
                {
                    "search": {"type": "elasticsearch", "hosts": "https://search:9200"},
                    "events": sink,
                }
            ),
            strict=True,
        )


def test_strict_yaml_rejects_wrong_connector_reference_type() -> None:
    with pytest.raises(TypeError, match="not an ElasticsearchConnector"):
        load_app_config(
            _config(
                {
                    "queue": {"type": "memory", "maxsize": 1},
                    "events": {
                        "type": "elasticsearch_bulk_sink",
                        "connector": "queue",
                        "index": "events",
                    },
                }
            ),
            strict=True,
        )
