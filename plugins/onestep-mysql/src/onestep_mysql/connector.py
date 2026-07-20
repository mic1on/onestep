from __future__ import annotations

import asyncio
import hashlib
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from onestep.envelope import Envelope
from onestep.resilience import ConnectorOperation
from onestep.state import CursorStore, InMemoryCursorStore

from onestep.connectors.base import Delivery, Sink, Source

from .resilience import as_mysql_connector_operation_error
from .state_sqlalchemy import SQLAlchemyCursorStore, SQLAlchemyStateStore

try:
    import sqlalchemy as sa
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
except ImportError:  # pragma: no cover - exercised when optional deps are missing
    sa = None
    create_engine = None
    make_url = None

try:
    from pymysqlreplication import BinLogStreamReader
    from pymysqlreplication.row_event import DeleteRowsEvent, UpdateRowsEvent, WriteRowsEvent
except ImportError:  # pragma: no cover - exercised when optional deps are missing
    BinLogStreamReader = None
    DeleteRowsEvent = None
    UpdateRowsEvent = None
    WriteRowsEvent = None


class MySQLConnector:
    def __init__(self, dsn: str, **engine_options: Any) -> None:
        if create_engine is None:
            raise RuntimeError("MySQLConnector requires SQLAlchemy. Install onestep-mysql.")
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
        effective_cursor = tuple(cursor) if key in cursor else (*tuple(cursor), key)
        return IncrementalTableSource(
            connector=self,
            table=table,
            key=key,
            cursor=effective_cursor,
            where=where,
            batch_size=batch_size,
            poll_interval_s=poll_interval_s,
            state=state or InMemoryCursorStore(),
            state_key=state_key or _default_incremental_state_key(
                table=table,
                cursor=effective_cursor,
                key=key,
                where=where,
            ),
        )

    def binlog(
        self,
        *,
        server_id: int,
        schemas: Sequence[str] = (),
        tables: Sequence[str] = (),
        events: Sequence[str] = ("insert", "update", "delete"),
        batch_size: int = 100,
        poll_interval_s: float = 1.0,
        state: CursorStore | None = None,
        state_key: str | None = None,
        blocking: bool = False,
    ) -> "BinlogSource":
        return BinlogSource(
            connector=self,
            server_id=server_id,
            schemas=tuple(schemas),
            tables=tuple(tables),
            events=tuple(events),
            batch_size=batch_size,
            poll_interval_s=poll_interval_s,
            state=state or InMemoryCursorStore(),
            state_key=state_key or _default_binlog_state_key(
                schemas=schemas,
                tables=tables,
                events=events,
            ),
            blocking=blocking,
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


def _default_incremental_state_key(
    *,
    table: str,
    cursor: Sequence[str],
    key: str,
    where: str | None,
) -> str:
    normalized_where = " ".join((where or "").split())
    if normalized_where:
        where_fragment = normalized_where
        if len(where_fragment) > 64:
            where_fragment = f"sha1:{hashlib.sha1(where_fragment.encode('utf-8')).hexdigest()}"
    else:
        where_fragment = "-"
    return f"{table}:{','.join(cursor)}:key={key}:where={where_fragment}"


def _default_binlog_state_key(
    *,
    schemas: Sequence[str],
    tables: Sequence[str],
    events: Sequence[str],
) -> str:
    schema_fragment = ",".join(schemas) if schemas else "*"
    table_fragment = ",".join(tables) if tables else "*"
    event_fragment = ",".join(events) if events else "*"
    return f"binlog:schemas={schema_fragment}:tables={table_fragment}:events={event_fragment}"


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

    async def update_current_row(self, values: Mapping[str, Any]) -> None:
        payload = dict(values)
        await self._source.update_row(self._row_ref, payload)
        if isinstance(self.envelope.body, dict):
            self.envelope.body.update(payload)

    async def ack(self) -> None:
        await self._source.ack_row(self._row_ref)

    async def retry(self, *, delay_s: float | None = None) -> None:
        await self._source.retry_row(self._row_ref, delay_s=delay_s)

    async def fail(self, exc: Exception | None = None) -> None:
        await self._source.fail_row(self._row_ref, exc=exc)

    async def release_unstarted(self) -> None:
        await self._source.release_row(self._row_ref)


class TableQueueSource(Source):
    fetch_is_cancel_safe = False

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
        try:
            rows = await asyncio.to_thread(self._fetch_sync, max(1, min(limit, self.batch_size)))
        except Exception as exc:
            connector_error = as_mysql_connector_operation_error(
                operation=ConnectorOperation.FETCH,
                exc=exc,
                source_name=self.name,
                retry_delay_s=self.poll_interval_s,
            )
            if connector_error is None:
                raise
            raise connector_error from exc
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
        await self.update_row(row_ref, self.ack)

    async def retry_row(self, row_ref: _TableRowRef, *, delay_s: float | None = None) -> None:
        if delay_s:
            await asyncio.sleep(delay_s)
        await self.update_row(row_ref, self.nack)

    async def fail_row(self, row_ref: _TableRowRef, exc: Exception | None = None) -> None:
        await self.update_row(row_ref, self.nack)

    async def release_row(self, row_ref: _TableRowRef) -> None:
        await self.update_row(row_ref, self.nack)

    async def update_row(self, row_ref: _TableRowRef, values: Mapping[str, Any]) -> None:
        await asyncio.to_thread(self._update_row_sync, row_ref, dict(values))

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


@dataclass(frozen=True)
class _BinlogToken:
    file: str
    pos: int
    row: int = 0
    rows: int = 1


class BinlogDelivery(Delivery):
    def __init__(self, source: "BinlogSource", envelope: Envelope, token: _BinlogToken) -> None:
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


class BinlogSource(Source):
    def __init__(
        self,
        *,
        connector: MySQLConnector,
        server_id: int,
        schemas: tuple[str, ...],
        tables: tuple[str, ...],
        events: tuple[str, ...],
        batch_size: int,
        poll_interval_s: float,
        state: CursorStore,
        state_key: str,
        blocking: bool,
    ) -> None:
        if server_id <= 0:
            raise ValueError("server_id must be a positive integer")
        normalized_events = tuple(_normalize_binlog_event_name(event) for event in events)
        if not normalized_events:
            raise ValueError("events must contain at least one event")
        super().__init__("mysql.binlog")
        self.connector = connector
        self.server_id = server_id
        self.schemas = schemas
        self.tables = tables
        self.events = normalized_events
        self.batch_size = batch_size
        self.poll_interval_s = poll_interval_s
        self.state = state
        self.state_key = state_key
        self.blocking = blocking
        self._stream: Any | None = None
        self._loaded = False
        self._committed: _BinlogToken | None = None
        self._buffered: deque[tuple[dict[str, Any], _BinlogToken]] = deque()
        self._pending: deque[_BinlogToken] = deque()
        self._acked: set[_BinlogToken] = set()
        self._commit_lock: asyncio.Lock | None = None
        self._commit_loop: asyncio.AbstractEventLoop | None = None

    async def open(self) -> None:
        if self._loaded:
            return
        loaded = await self.state.load(self.state_key)
        if isinstance(loaded, Mapping):
            file = loaded.get("file")
            pos = loaded.get("pos")
            if isinstance(file, str) and isinstance(pos, int):
                self._committed = _BinlogToken(file=file, pos=pos)
        if self._committed is None:
            self._committed = await asyncio.to_thread(self._current_binlog_token_sync)
            await self.state.save(self.state_key, {"file": self._committed.file, "pos": self._committed.pos})
        self._loaded = True

    async def fetch(self, limit: int) -> list[Delivery]:
        await self.open()
        try:
            rows = await asyncio.to_thread(self._fetch_sync, max(1, min(limit, self.batch_size)))
        except Exception as exc:
            connector_error = as_mysql_connector_operation_error(
                operation=ConnectorOperation.FETCH,
                exc=exc,
                source_name=self.name,
                retry_delay_s=self.poll_interval_s,
            )
            if connector_error is None:
                raise
            raise connector_error from exc

        deliveries: list[Delivery] = []
        for payload, token in rows:
            self._pending.append(token)
            envelope = Envelope(body=payload, meta={"source": "mysql_binlog", "binlog": dict(payload["binlog"])})
            deliveries.append(BinlogDelivery(self, envelope, token))
        return deliveries

    async def close(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is not None:
            await asyncio.to_thread(stream.close)

    async def ack_token(self, token: _BinlogToken) -> None:
        lock = self._runtime_commit_lock()
        async with lock:
            self._acked.add(token)
            advanced: _BinlogToken | None = None
            while self._pending and self._pending[0] in self._acked:
                advanced = self._pending.popleft()
                self._acked.remove(advanced)
            if advanced is not None and advanced.row + 1 >= advanced.rows:
                self._committed = advanced
                await self.state.save(self.state_key, {"file": advanced.file, "pos": advanced.pos})

    def _fetch_sync(self, limit: int) -> list[tuple[dict[str, Any], _BinlogToken]]:
        stream = self._ensure_stream()
        rows: list[tuple[dict[str, Any], _BinlogToken]] = []
        while self._buffered and len(rows) < limit:
            rows.append(self._buffered.popleft())
        while len(rows) < limit:
            event = stream.fetchone()
            if event is None:
                break
            event_name = _event_name(event)
            if event_name is None or event_name not in self.events:
                continue
            file = stream.log_file
            token_pos = getattr(stream, "log_pos", None)
            if not isinstance(file, str) or not isinstance(token_pos, int):
                continue
            event_rows = list(getattr(event, "rows", []))
            total_rows = len(event_rows)
            for row_index, row in enumerate(event_rows):
                token = _BinlogToken(file=file, pos=token_pos, row=row_index, rows=total_rows)
                item = (_binlog_payload(event, row, event_name, file=file, pos=token_pos), token)
                if len(rows) < limit:
                    rows.append(item)
                else:
                    self._buffered.append(item)
        return rows

    def _ensure_stream(self) -> Any:
        if self._stream is not None:
            return self._stream
        if BinLogStreamReader is None or WriteRowsEvent is None or UpdateRowsEvent is None or DeleteRowsEvent is None:
            raise RuntimeError("BinlogSource requires mysql-replication. Install onestep-mysql with mysql-replication.")
        self._stream = BinLogStreamReader(
            connection_settings=_binlog_connection_settings(self.connector.dsn),
            server_id=self.server_id,
            resume_stream=self._committed is not None,
            blocking=self.blocking,
            only_events=[WriteRowsEvent, UpdateRowsEvent, DeleteRowsEvent],
            log_file=self._committed.file if self._committed is not None else None,
            log_pos=self._committed.pos if self._committed is not None else None,
            only_schemas=list(self.schemas) if self.schemas else None,
            only_tables=list(self.tables) if self.tables else None,
            use_column_name_cache=True,
        )
        return self._stream

    def _current_binlog_token_sync(self) -> _BinlogToken:
        with self.connector.engine.begin() as conn:
            try:
                row = conn.exec_driver_sql("SHOW BINARY LOG STATUS").mappings().first()
            except Exception:
                row = conn.exec_driver_sql("SHOW MASTER STATUS").mappings().first()
        if row is None:
            raise RuntimeError("MySQL binary log is not enabled")
        file = row.get("File")
        pos = row.get("Position")
        if not isinstance(file, str) or not isinstance(pos, int):
            raise RuntimeError("MySQL did not return a binary log file and position")
        return _BinlogToken(file=file, pos=pos)

    def _runtime_commit_lock(self) -> asyncio.Lock:
        current_loop = asyncio.get_running_loop()
        if self._commit_lock is None or self._commit_loop is not current_loop:
            self._commit_lock = asyncio.Lock()
            self._commit_loop = current_loop
        return self._commit_lock


def _normalize_binlog_event_name(event: str) -> str:
    normalized = event.strip().lower()
    aliases = {
        "write": "insert",
        "create": "insert",
        "insert": "insert",
        "update": "update",
        "delete": "delete",
        "remove": "delete",
    }
    if normalized not in aliases:
        raise ValueError("events must contain only insert, update, or delete")
    return aliases[normalized]


def _event_name(event: Any) -> str | None:
    if WriteRowsEvent is not None and isinstance(event, WriteRowsEvent):
        return "insert"
    if UpdateRowsEvent is not None and isinstance(event, UpdateRowsEvent):
        return "update"
    if DeleteRowsEvent is not None and isinstance(event, DeleteRowsEvent):
        return "delete"
    return None


def _binlog_payload(event: Any, row: Mapping[str, Any], event_name: str, *, file: str, pos: int) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": getattr(event, "schema", None),
        "table": getattr(event, "table", None),
        "event": event_name,
        "binlog": {
            "file": file,
            "pos": pos,
            "timestamp": getattr(event, "timestamp", None),
        },
    }
    if event_name == "update":
        payload["before_values"] = dict(row.get("before_values") or {})
        payload["values"] = dict(row.get("after_values") or {})
        return payload
    payload["values"] = dict(row.get("values") or {})
    return payload


def _binlog_connection_settings(dsn: str) -> dict[str, Any]:
    if make_url is None:
        raise RuntimeError("BinlogSource requires SQLAlchemy. Install onestep-mysql.")
    url = make_url(dsn)
    settings: dict[str, Any] = {
        "host": url.host or "localhost",
        "port": url.port or 3306,
        "user": url.username or "",
        "passwd": url.password or "",
    }
    if url.database:
        settings["db"] = url.database
    for key, value in dict(url.query).items():
        if key in {"charset", "connect_timeout", "read_timeout", "write_timeout", "ssl_ca", "ssl_cert", "ssl_key"}:
            settings[key] = int(value) if key.endswith("_timeout") else value
    return settings


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
        self.configured_cursor = cursor
        self.cursor = cursor if key in cursor else (*cursor, key)
        self.where = where
        self.batch_size = batch_size
        self.poll_interval_s = poll_interval_s
        self.state = state
        self.state_key = state_key
        self._pending: deque[tuple[Any, ...]] = deque()
        self._acked: set[tuple[Any, ...]] = set()
        self._commit_lock: asyncio.Lock | None = None
        self._commit_loop: asyncio.AbstractEventLoop | None = None
        self._loaded = False
        self._committed_cursor: tuple[Any, ...] | None = None
        self._fetched_cursor: tuple[Any, ...] | None = None

    async def open(self) -> None:
        if not self._loaded:
            loaded = await self.state.load(self.state_key)
            if loaded is not None and len(loaded) == len(self.cursor):
                self._committed_cursor = tuple(loaded)
                self._fetched_cursor = self._committed_cursor
            self._loaded = True

    async def fetch(self, limit: int) -> list[Delivery]:
        await self.open()
        try:
            rows = await asyncio.to_thread(self._fetch_sync, max(1, min(limit, self.batch_size)))
        except Exception as exc:
            connector_error = as_mysql_connector_operation_error(
                operation=ConnectorOperation.FETCH,
                exc=exc,
                source_name=self.name,
                retry_delay_s=self.poll_interval_s,
            )
            if connector_error is None:
                raise
            raise connector_error from exc
        deliveries: list[Delivery] = []
        for row in rows:
            token = _CursorToken(tuple(row[column] for column in self.cursor))
            self._pending.append(token.value)
            self._fetched_cursor = token.value
            envelope = Envelope(body=row, meta={"table": self.table_name})
            deliveries.append(IncrementalDelivery(self, envelope, token))
        return deliveries

    def _fetch_sync(self, limit: int) -> list[dict[str, Any]]:
        table = self.connector._table(self.table_name)
        stmt = sa.select(table)
        predicates = []
        if self.where:
            predicates.append(sa.text(self.where))
        read_cursor = self._fetched_cursor or self._committed_cursor
        if read_cursor is not None:
            cursor_columns = [table.c[name] for name in self.cursor]
            predicates.append(sa.tuple_(*cursor_columns) > tuple(read_cursor))
        if predicates:
            stmt = stmt.where(*predicates)
        order_columns = [table.c[name] for name in self.cursor]
        stmt = stmt.order_by(*order_columns).limit(limit)
        with self.connector.engine.begin() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [dict(row) for row in rows]

    async def ack_token(self, token: _CursorToken) -> None:
        lock = self._runtime_commit_lock()
        async with lock:
            self._acked.add(token.value)
            advanced: tuple[Any, ...] | None = None
            while self._pending and self._pending[0] in self._acked:
                advanced = self._pending.popleft()
                self._acked.remove(advanced)
            if advanced is not None:
                self._committed_cursor = advanced
                if not self._pending:
                    self._fetched_cursor = advanced
                await self.state.save(self.state_key, list(advanced))

    def _runtime_commit_lock(self) -> asyncio.Lock:
        current_loop = asyncio.get_running_loop()
        if self._commit_lock is None or self._commit_loop is not current_loop:
            self._commit_lock = asyncio.Lock()
            self._commit_loop = current_loop
        return self._commit_lock


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
        try:
            await asyncio.to_thread(self._send_sync, dict(envelope.body))
        except Exception as exc:
            connector_error = as_mysql_connector_operation_error(
                operation=ConnectorOperation.SEND,
                exc=exc,
                source_name=self.name,
                retry_delay_s=1.0,
            )
            if connector_error is None:
                raise
            raise connector_error from exc

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
