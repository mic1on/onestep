from __future__ import annotations

import pytest

from onestep_clickhouse import ClickHouseConnector, ClickHousePayloadError


def test_configured_columns_produce_ordered_rows() -> None:
    sink = ClickHouseConnector("http://clickhouse:8123/default").table_sink(
        table="events", columns=("id", "kind")
    )
    columns, rows = sink._normalize(
        [{"kind": "a", "id": 1}, {"id": 2, "kind": "b"}]
    )
    assert columns == ("id", "kind")
    assert rows == [[1, "a"], [2, "b"]]


def test_first_mapping_infers_deterministic_column_order() -> None:
    sink = ClickHouseConnector("http://clickhouse:8123/default").table_sink(
        table="events"
    )
    columns, rows = sink._normalize(
        [{"id": 1, "kind": "a"}, {"kind": "b", "id": 2}]
    )
    assert columns == ("id", "kind")
    assert rows == [[1, "a"], [2, "b"]]


@pytest.mark.parametrize(
    "body",
    [
        [],
        "text",
        [1],
        [{"id": 1}, {"id": 2, "extra": True}],
        [{"id": 1}, {"other": 2}],
    ],
)
def test_invalid_payloads_fail_before_insert(body) -> None:
    sink = ClickHouseConnector("http://clickhouse:8123/default").table_sink(
        table="events", columns=("id",)
    )
    with pytest.raises(ClickHousePayloadError):
        sink._normalize(body)
