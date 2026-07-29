from __future__ import annotations

import pytest

from onestep import (
    ConnectorErrorKind,
    ConnectorOperationError,
    Delivery,
    Envelope,
    OneStepApp,
    Source,
)
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


def test_empty_mapping_cannot_safely_infer_columns() -> None:
    sink = ClickHouseConnector("http://clickhouse:8123/default").table_sink(
        table="events"
    )
    with pytest.raises(ClickHousePayloadError, match="infer columns"):
        sink._normalize({})


@pytest.mark.parametrize(
    "options",
    [
        {"table": ""},
        {"table": "events", "columns": "id"},
        {"table": "events", "columns": ("id", "")},
        {"table": "events", "batch_size": 1.5},
        {"table": "events", "batch_size": True},
        {"table": "events", "settings": "not-a-mapping"},
        {"table": "events", "settings": {"async_insert": 1}},
    ],
)
def test_invalid_sink_configuration_is_rejected(options) -> None:
    connector = ClickHouseConnector("http://clickhouse:8123/default")
    with pytest.raises((TypeError, ValueError)):
        connector.table_sink(**options)


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
async def test_invalid_send_is_permanent_before_first_network_call() -> None:
    client = FakeAsyncClient()
    sink = ClickHouseConnector(
        "http://clickhouse:8123/default", client=client
    ).table_sink(table="events", columns=("id",))
    with pytest.raises(ConnectorOperationError) as captured:
        await sink.send(Envelope(body=[{"other": "secret-row-value"}]))
    assert captured.value.kind is ConnectorErrorKind.PERMANENT
    assert client.calls == []
    assert "secret-row-value" not in str(captured.value.cause)


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


@pytest.mark.asyncio
async def test_concurrent_first_sends_share_one_lazy_client(monkeypatch) -> None:
    import asyncio
    import clickhouse_connect

    client = FakeAsyncClient()
    entered = asyncio.Event()
    release = asyncio.Event()
    factory_calls = 0

    async def build_client(**options):
        nonlocal factory_calls
        factory_calls += 1
        entered.set()
        await release.wait()
        return client

    monkeypatch.setattr(clickhouse_connect, "get_async_client", build_client)
    connector = ClickHouseConnector("http://clickhouse:8123/default")
    sink = connector.table_sink(table="events", columns=("id",))
    first = asyncio.create_task(sink.send(Envelope(body={"id": 1})))
    await entered.wait()
    second = asyncio.create_task(sink.send(Envelope(body={"id": 2})))
    await asyncio.sleep(0)
    assert factory_calls == 1
    release.set()
    await asyncio.gather(first, second)
    assert factory_calls == 1


@pytest.mark.asyncio
async def test_client_construction_error_uses_connector_error_contract(monkeypatch) -> None:
    import clickhouse_connect

    async def fail_client(**options):
        raise ConnectionError("cannot connect")

    monkeypatch.setattr(clickhouse_connect, "get_async_client", fail_client)
    sink = ClickHouseConnector("http://clickhouse:8123/default").table_sink(
        table="events", columns=("id",)
    )
    with pytest.raises(ConnectorOperationError) as captured:
        await sink.send(Envelope(body={"id": 1}))
    assert captured.value.kind is ConnectorErrorKind.DISCONNECTED


class _AckRecordingDelivery(Delivery):
    def __init__(self, envelope: Envelope) -> None:
        super().__init__(envelope)
        self.acked = False

    async def ack(self) -> None:
        self.acked = True

    async def retry(self, *, delay_s: float | None = None) -> None:
        raise AssertionError("runtime ordering test must not retry")

    async def fail(self, exc: Exception | None = None) -> None:
        raise AssertionError(f"runtime ordering test failed: {exc}")


class _OneShotSource(Source):
    poll_interval_s = 0.01

    def __init__(self, delivery: _AckRecordingDelivery) -> None:
        super().__init__("one-shot")
        self.delivery = delivery
        self.sent = False

    async def fetch(self, limit: int) -> list[Delivery]:
        if self.sent:
            return []
        self.sent = True
        return [self.delivery]


@pytest.mark.asyncio
async def test_runtime_ack_follows_clickhouse_insert_acknowledgement() -> None:
    import asyncio

    entered = asyncio.Event()
    release = asyncio.Event()

    class BlockingClient(FakeAsyncClient):
        async def insert(self, table, rows, *, column_names, settings):
            entered.set()
            await release.wait()
            return await super().insert(
                table, rows, column_names=column_names, settings=settings
            )

    sink = ClickHouseConnector(
        "http://clickhouse:8123/default", client=BlockingClient()
    ).table_sink(table="events", columns=("id",))
    delivery = _AckRecordingDelivery(Envelope(body={"id": 1}))
    source = _OneShotSource(delivery)
    app = OneStepApp("clickhouse-runtime-order", shutdown_timeout_s=1.0)

    @app.task(source=source, emit=sink, concurrency=1)
    async def forward(ctx, item):
        ctx.app.request_shutdown()
        return item

    serving = asyncio.create_task(app.serve())
    await entered.wait()
    assert delivery.acked is False
    release.set()
    await asyncio.wait_for(serving, timeout=2.0)
    assert delivery.acked is True


@pytest.mark.asyncio
async def test_send_failure_does_not_leak_dsn_credentials(monkeypatch) -> None:
    """The DSN password and client_options passwords must be scrubbed from errors."""
    import clickhouse_connect

    secret_dsn = "http://writer:supersecret@clickhouse:8123/default"

    async def fail_client(**options):
        raise ConnectionError(
            f"cannot connect to {secret_dsn} with password 'supersecret'"
        )

    monkeypatch.setattr(clickhouse_connect, "get_async_client", fail_client)
    sink = ClickHouseConnector(
        secret_dsn,
        client_options={"password": "supersecret"},
    ).table_sink(table="events", columns=("id",))

    with pytest.raises(ConnectorOperationError) as captured:
        await sink.send(Envelope(body={"id": 1}))

    err_str = str(captured.value.cause)
    assert "supersecret" not in err_str
    assert "writer:supersecret" not in err_str
    assert "<redacted>" in err_str
    # Non-sensitive info should be preserved
    assert "default" in err_str
