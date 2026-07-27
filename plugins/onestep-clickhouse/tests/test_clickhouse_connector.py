from __future__ import annotations

import pytest

from onestep import ConnectorErrorKind, ConnectorOperationError, Envelope
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


class FakeAsyncClient:
    def __init__(self, *, fail_call: int | None = None) -> None:
        self.calls: list[dict] = []
        self.fail_call = fail_call
        self.closed = False

    async def insert(self, table, rows, *, column_names, settings):
        self.calls.append(
            {
                "table": table,
                "rows": rows,
                "column_names": column_names,
                "settings": settings,
            }
        )
        if self.fail_call == len(self.calls):
            raise TimeoutError("response timed out after submission")
        return object()

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_send_awaits_each_chunk_in_order() -> None:
    client = FakeAsyncClient()
    connector = ClickHouseConnector(
        "http://clickhouse:8123/default", client=client
    )
    sink = connector.table_sink(table="events", columns=("id",), batch_size=2)
    await sink.send(Envelope(body=[{"id": 1}, {"id": 2}, {"id": 3}]))
    assert [call["rows"] for call in client.calls] == [[[1], [2]], [[3]]]


@pytest.mark.asyncio
async def test_later_chunk_timeout_is_uncertain_and_stops() -> None:
    client = FakeAsyncClient(fail_call=2)
    sink = ClickHouseConnector(
        "http://clickhouse:8123/default", client=client
    ).table_sink(table="events", columns=("id",), batch_size=1)
    with pytest.raises(ConnectorOperationError) as captured:
        await sink.send(Envelope(body=[{"id": 1}, {"id": 2}, {"id": 3}]))
    assert captured.value.kind is ConnectorErrorKind.UNCERTAIN
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_injected_client_is_not_closed() -> None:
    client = FakeAsyncClient()
    connector = ClickHouseConnector(
        "http://clickhouse:8123/default", client=client
    )
    await connector.close()
    await connector.close()
    assert client.closed is False


@pytest.mark.asyncio
async def test_owned_client_is_lazy_and_closes_once(monkeypatch) -> None:
    import clickhouse_connect

    client = FakeAsyncClient()
    close_calls = 0

    async def close_once():
        nonlocal close_calls
        close_calls += 1
        client.closed = True

    client.close = close_once
    factory_calls = []

    async def build_client(**options):
        factory_calls.append(options)
        return client

    monkeypatch.setattr(clickhouse_connect, "get_async_client", build_client)
    connector = ClickHouseConnector("http://clickhouse:8123/default")
    assert factory_calls == []
    await connector.table_sink(table="events", columns=("id",)).send(
        Envelope(body={"id": 1})
    )
    assert factory_calls == [{"dsn": "http://clickhouse:8123/default"}]
    await connector.close()
    await connector.close()
    assert client.closed is True
    assert close_calls == 1
