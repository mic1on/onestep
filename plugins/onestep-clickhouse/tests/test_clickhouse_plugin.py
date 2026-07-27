from __future__ import annotations

from importlib import metadata as importlib_metadata

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
