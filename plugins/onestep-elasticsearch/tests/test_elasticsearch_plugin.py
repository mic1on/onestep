from __future__ import annotations

from importlib import metadata as importlib_metadata

from onestep_elasticsearch import (
    ElasticsearchBulkError,
    ElasticsearchBulkItemError,
    ElasticsearchBulkSink,
    ElasticsearchConnector,
    register,
    register_resources,
)


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
        item.name == "elasticsearch"
        and item.value == "onestep_elasticsearch:register"
        for item in _entry_points_for_group("onestep.resources")
    )
