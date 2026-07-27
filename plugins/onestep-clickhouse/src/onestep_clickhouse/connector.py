from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from onestep import (
    ConnectorErrorKind,
    ConnectorOperation,
    ConnectorOperationError,
    Envelope,
    Sink,
)


class ClickHousePayloadError(ValueError):
    pass


class ClickHouseConnector:
    def __init__(
        self,
        dsn: str,
        *,
        client_options: Mapping[str, Any] | None = None,
        client: Any | None = None,
    ) -> None:
        if not dsn:
            raise ValueError("dsn must not be empty")
        self.dsn = dsn
        self.client_options = dict(client_options or {})
        self._client = client
        self._owns_client = client is None
        self._closed = False

    async def _get_client(self):
        if self._client is None:
            import clickhouse_connect

            self._client = await clickhouse_connect.get_async_client(
                dsn=self.dsn, **self.client_options
            )
        return self._client

    def table_sink(self, *, table: str, **options: Any) -> "ClickHouseTableSink":
        return ClickHouseTableSink(connector=self, table=table, **options)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_client and self._client is not None:
            result = self._client.close()
            if hasattr(result, "__await__"):
                await result


class ClickHouseTableSink(Sink):
    def __init__(
        self,
        *,
        connector: ClickHouseConnector,
        table: str,
        columns: Sequence[str] | None = None,
        batch_size: int = 1000,
        settings: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(f"clickhouse.table:{table}")
        if not table:
            raise ValueError("table must not be empty")
        if columns is not None and (
            not columns or len(set(columns)) != len(columns)
        ):
            raise ValueError("columns must be a non-empty unique sequence")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.connector = connector
        self.table = table
        self.columns = tuple(columns) if columns is not None else None
        self.batch_size = batch_size
        self.settings = dict(settings or {})
        if self.settings.get("async_insert") in {1, True, "1"} and self.settings.get(
            "wait_for_async_insert"
        ) not in {1, True, "1"}:
            raise ValueError("async_insert requires wait_for_async_insert=1")

    def _documents(self, body: Any) -> list[dict[str, Any]]:
        if isinstance(body, Mapping):
            return [dict(body)]
        if not isinstance(body, Sequence) or isinstance(
            body, (str, bytes, bytearray)
        ):
            raise ClickHousePayloadError(
                "payload must be a mapping or non-empty sequence of mappings"
            )
        if not body:
            raise ClickHousePayloadError("payload sequence must not be empty")
        if any(not isinstance(item, Mapping) for item in body):
            raise ClickHousePayloadError("every payload item must be a mapping")
        return [dict(item) for item in body]

    def _normalize(self, body: Any) -> tuple[tuple[str, ...], list[list[Any]]]:
        documents = self._documents(body)
        columns = self.columns or tuple(documents[0].keys())
        expected = set(columns)
        rows: list[list[Any]] = []
        for index, document in enumerate(documents):
            actual = set(document)
            if actual != expected:
                missing = sorted(expected - actual)
                extra = sorted(actual - expected)
                raise ClickHousePayloadError(
                    f"row {index} column mismatch: missing={missing}, extra={extra}"
                )
            rows.append([document[column] for column in columns])
        return columns, rows

    async def send(self, envelope: Envelope) -> None:
        try:
            columns, rows = self._normalize(envelope.body)
        except ClickHousePayloadError as exc:
            raise ConnectorOperationError(
                backend="clickhouse",
                operation=ConnectorOperation.SEND,
                kind=ConnectorErrorKind.PERMANENT,
                source_name=self.name,
                cause=exc,
            ) from exc
        client = await self.connector._get_client()
        committed = 0
        try:
            for start in range(0, len(rows), self.batch_size):
                chunk = rows[start : start + self.batch_size]
                await client.insert(
                    self.table,
                    chunk,
                    column_names=columns,
                    settings=self.settings,
                )
                committed += 1
        except Exception as exc:
            from .resilience import classify_clickhouse_error

            kind = classify_clickhouse_error(exc)
            if kind is None:
                raise
            if committed:
                kind = ConnectorErrorKind.UNCERTAIN
            raise ConnectorOperationError(
                backend="clickhouse",
                operation=ConnectorOperation.SEND,
                kind=kind,
                source_name=self.name,
                cause=exc,
            ) from exc
