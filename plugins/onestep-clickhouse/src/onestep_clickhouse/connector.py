from __future__ import annotations

from typing import Any

from onestep import Sink


class ClickHousePayloadError(ValueError):
    pass


class ClickHouseConnector:
    def __init__(
        self,
        dsn: str,
        *,
        client_options: dict[str, Any] | None = None,
        client: Any | None = None,
    ) -> None:
        self.dsn = dsn
        self.client_options = dict(client_options or {})
        self._client = client

    def table_sink(self, *, table: str, **options: Any) -> "ClickHouseTableSink":
        return ClickHouseTableSink(connector=self, table=table, **options)


class ClickHouseTableSink(Sink):
    def __init__(
        self, *, connector: ClickHouseConnector, table: str, **options: Any
    ) -> None:
        super().__init__(f"clickhouse.table:{table}")
        self.connector = connector
        self.table = table
        self.options = dict(options)

    async def send(self, envelope) -> None:
        raise NotImplementedError("table insert is introduced by Task 3")
