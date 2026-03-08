from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from onestep.envelope import Envelope
from onestep.state import CursorStore, InMemoryCursorStore
from onestep.state_sqlalchemy import SQLAlchemyCursorStore, SQLAlchemyStateStore

from .base import Delivery, Sink, Source

try:
    import sqlalchemy as sa
    from sqlalchemy import create_engine
except ImportError:  # pragma: no cover - exercised when optional deps are missing
    sa = None
    create_engine = None


class MySQLConnector:
    def __init__(self, dsn: str, **engine_options: Any) -> None:
        if create_engine is None:
            raise RuntimeError("MySQLConnector requires SQLAlchemy. Install onestep[mysql].")
        self.dsn = dsn
        self.engine = create_engine(dsn, future=True, pool_pre_ping=True, **engine_options)
        self._tables: dict[str, Any] = {}

    async def close(self) -> None:
        await asyncio.to_thread(self.engine.dispose)

    def state_store(
        self,
        *,
        table: str = "onestep_state",
        key_column: str = "state_key",
        value_column: str = "state_value",
        updated_at_column: str = "updated_at",
        auto_create: bool = True,
    ) -> SQLAlchemyStateStore:
        return SQLAlchemyStateStore(
            engine=self.engine,
            table=table,
            key_column=key_column,
            value_column=value_column,
            updated_at_column=updated_at_column,
            auto_create=auto_create,
        )

    def cursor_store(
        self,
        *,
        table: str = "onestep_cursor",
        key_column: str = "cursor_key",
        value_column: str = "cursor_value",
        updated_at_column: str = "updated_at",
        auto_create: bool = True,
    ) -> SQLAlchemyCursorStore:
        return SQLAlchemyCursorStore(
            engine=self.engine,
            table=table,
            key_column=key_column,
            value_column=value_column,
            updated_at_column=updated_at_column,
            auto_create=auto_create,
        )

    def table_queue(
        self,
        *,
        table: str,
        key: str,
        where: str,
        claim: Mapping[str, Any],
        ack: Mapping[str, Any],
        nack: Mapping[str, Any] | None = None,
        batch_size: int = 100,
        poll_interval_s: float = 1.0,
    ) -> "TableQueueSource":
        return TableQueueSource(
            connector=self,
            table=table,
            key=key,
            where=where,
            claim=dict(claim),
            ack=dict(ack),
            nack=dict(nack or {}),
            batch_size=batch_size,
            poll_interval_s=poll_interval_s,
        )

    def incremental(
        self,
        *,
        table: str,
        key: str,
        cursor: Sequence[str],
        where: str | None = None,
        batch_size: int = 1000,
        poll_interval_s: float = 1.0,
        state: CursorStore | None = None,
        state_key: str | None = None,
    ) -> "IncrementalTableSource":
        if len(cursor) < 1:
            raise ValueError("cursor must contain at least one column")
        return IncrementalTableSource(
            connector=self,
            table=table,
            key=key,
            cursor=tuple(cursor),
            where=where,
            batch_size=batch_size,
            poll_interval_s=poll_interval_s,
            state=state or InMemoryCursorStore(),
            state_key=state_key or f"{table}:{','.join(cursor)}",
        )

    def table_sink(
        self,
        *,
        table: str,
        mode: str = "insert",
        keys: Sequence[str] = (),
    ) -> "TableSink":
        return TableSink(connector=self, table=table, mode=mode, keys=tuple(keys))

    def _table(self, table_name: str):
        table = self._tables.get(table_name)
        if table is None:
            metadata = sa.MetaData()
            table = sa.Table(table_name, metadata, autoload_with=self.engine)
            self._tables[table_name] = table
        return table


@dataclass
class _TableRowRef:
    table: str
    key: str
    key_value: Any


class TableQueueDelivery(Delivery):
    def __init__(self, source: "TableQueueSource", envelope: Envelope, row_ref: _TableRowRef) -> None:
        super().__init__(envelope)
        self._source = source
        self._row_ref = row_ref

    async def ack(self) -> None:
        await self._source.ack_row(self._row_ref)

    async def retry(self, *, delay_s: float | None = None) -> None:
        await self._source.retry_row(self._row_ref, delay_s=delay_s)

    async def fail(self, exc: Exception | None = None) -> None:
        await self._source.fail_row(self._row_ref, exc=exc)


class TableQueueSource(Source):
    def __init__(
        self,
        *,
        connector: MySQLConnector,
        table: str,
        key: str,
        where: str,
        claim: dict[str, Any],
        ack: dict[str, Any],
        nack: dict[str, Any],
        batch_size: int,
        poll_interval_s: float,
    ) -> None:
        super().__init__(f"mysql.table_queue:{table}")
        self.connector = connector
        self.table_name = table
        self.key = key
        self.where = where
        self.claim = claim
        self.ack = ack
        self.nack = nack
        self.batch_size = batch_size
        self.poll_interval_s = poll_interval_s

    async def fetch(self, limit: int) -> list[Delivery]:
        rows = await asyncio.to_thread(self._fetch_sync, max(1, min(limit, self.batch_size)))
        deliveries: list[Delivery] = []
        for row in rows:
            key_value = row[self.key]
            envelope = Envelope(body=row, meta={"table": self.table_name})
            row_ref = _TableRowRef(self.table_name, self.key, key_value)
            deliveries.append(TableQueueDelivery(self, envelope, row_ref))
        return deliveries

    def _fetch_sync(self, limit: int) -> list[dict[str, Any]]:
        table = self.connector._table(self.table_name)
        with self.connector.engine.begin() as conn:
            stmt = sa.select(table).where(sa.text(self.where)).order_by(table.c[self.key]).limit(limit)
            try:
                stmt = stmt.with_for_update(skip_locked=True)
            except TypeError:
                stmt = stmt.with_for_update()
            rows = [dict(row) for row in conn.execute(stmt).mappings().all()]
            if not rows:
                return []
            ids = [row[self.key] for row in rows]
            conn.execute(sa.update(table).where(table.c[self.key].in_(ids)).values(**self.claim))
            refreshed = conn.execute(
                sa.select(table).where(table.c[self.key].in_(ids)).order_by(table.c[self.key])
            )
            return [dict(row) for row in refreshed.mappings().all()]

    async def ack_row(self, row_ref: _TableRowRef) -> None:
        await asyncio.to_thread(self._update_row_sync, row_ref, self.ack)

    async def retry_row(self, row_ref: _TableRowRef, *, delay_s: float | None = None) -> None:
        if delay_s:
            await asyncio.sleep(delay_s)
        await asyncio.to_thread(self._update_row_sync, row_ref, self.nack)

    async def fail_row(self, row_ref: _TableRowRef, exc: Exception | None = None) -> None:
        await asyncio.to_thread(self._update_row_sync, row_ref, self.nack)

    def _update_row_sync(self, row_ref: _TableRowRef, values: Mapping[str, Any]) -> None:
        if not values:
            return
        table = self.connector._table(row_ref.table)
        with self.connector.engine.begin() as conn:
            conn.execute(
                sa.update(table)
                .where(table.c[row_ref.key] == row_ref.key_value)
                .values(**dict(values))
            )


@dataclass
class _CursorToken:
    value: tuple[Any, ...]


class IncrementalDelivery(Delivery):
    def __init__(self, source: "IncrementalTableSource", envelope: Envelope, token: _CursorToken) -> None:
        super().__init__(envelope)
        self._source = source
        self._token = token

    async def ack(self) -> None:
        await self._source.ack_token(self._token)

    async def retry(self, *, delay_s: float | None = None) -> None:
        if delay_s:
            await asyncio.sleep(delay_s)

    async def fail(self, exc: Exception | None = None) -> None:
        return None


class IncrementalTableSource(Source):
    def __init__(
        self,
        *,
        connector: MySQLConnector,
        table: str,
        key: str,
        cursor: tuple[str, ...],
        where: str | None,
        batch_size: int,
        poll_interval_s: float,
        state: CursorStore,
        state_key: str,
    ) -> None:
        super().__init__(f"mysql.incremental:{table}")
        self.connector = connector
        self.table_name = table
        self.key = key
        self.cursor = cursor
        self.where = where
        self.batch_size = batch_size
        self.poll_interval_s = poll_interval_s
        self.state = state
        self.state_key = state_key
        self._pending: deque[tuple[Any, ...]] = deque()
        self._acked: set[tuple[Any, ...]] = set()
        self._commit_lock = asyncio.Lock()
        self._loaded = False
        self._last_cursor: tuple[Any, ...] | None = None

    async def open(self) -> None:
        if not self._loaded:
            loaded = await self.state.load(self.state_key)
            if loaded is not None:
                self._last_cursor = tuple(loaded)
            self._loaded = True

    async def fetch(self, limit: int) -> list[Delivery]:
        await self.open()
        rows = await asyncio.to_thread(self._fetch_sync, max(1, min(limit, self.batch_size)))
        deliveries: list[Delivery] = []
        for row in rows:
            token = _CursorToken(tuple(row[column] for column in self.cursor))
            self._pending.append(token.value)
            envelope = Envelope(body=row, meta={"table": self.table_name})
            deliveries.append(IncrementalDelivery(self, envelope, token))
        return deliveries

    def _fetch_sync(self, limit: int) -> list[dict[str, Any]]:
        table = self.connector._table(self.table_name)
        stmt = sa.select(table)
        predicates = []
        if self.where:
            predicates.append(sa.text(self.where))
        if self._last_cursor is not None:
            cursor_columns = [table.c[name] for name in self.cursor]
            predicates.append(sa.tuple_(*cursor_columns) > tuple(self._last_cursor))
        if predicates:
            stmt = stmt.where(*predicates)
        order_columns = [table.c[name] for name in self.cursor]
        stmt = stmt.order_by(*order_columns).limit(limit)
        with self.connector.engine.begin() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [dict(row) for row in rows]

    async def ack_token(self, token: _CursorToken) -> None:
        async with self._commit_lock:
            self._acked.add(token.value)
            advanced: tuple[Any, ...] | None = None
            while self._pending and self._pending[0] in self._acked:
                advanced = self._pending.popleft()
                self._acked.remove(advanced)
            if advanced is not None:
                self._last_cursor = advanced
                await self.state.save(self.state_key, list(advanced))


class TableSink(Sink):
    def __init__(self, *, connector: MySQLConnector, table: str, mode: str, keys: tuple[str, ...]) -> None:
        super().__init__(f"mysql.table_sink:{table}")
        if mode not in {"insert", "upsert"}:
            raise ValueError("mode must be either 'insert' or 'upsert'")
        self.connector = connector
        self.table_name = table
        self.mode = mode
        self.keys = keys

    async def send(self, envelope: Envelope) -> None:
        if not isinstance(envelope.body, Mapping):
            raise TypeError("TableSink only accepts mapping payloads")
        await asyncio.to_thread(self._send_sync, dict(envelope.body))

    def _send_sync(self, payload: dict[str, Any]) -> None:
        table = self.connector._table(self.table_name)
        dialect = self.connector.engine.dialect.name
        with self.connector.engine.begin() as conn:
            if self.mode == "insert":
                conn.execute(sa.insert(table).values(**payload))
                return
            if not self.keys:
                raise ValueError("upsert mode requires keys")
            update_payload = {key: value for key, value in payload.items() if key not in self.keys}
            if dialect == "mysql":
                from sqlalchemy.dialects.mysql import insert as mysql_insert

                stmt = mysql_insert(table).values(**payload)
                conn.execute(stmt.on_duplicate_key_update(**update_payload))
                return
            if dialect == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert

                stmt = sqlite_insert(table).values(**payload)
                conn.execute(stmt.on_conflict_do_update(index_elements=list(self.keys), set_=update_payload))
                return
            conn.execute(sa.insert(table).values(**payload))
