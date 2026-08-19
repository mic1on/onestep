from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from onestep.connectors.base import Delivery, Sink, Source
from onestep.envelope import Envelope
from onestep.resilience import (
    ConnectorErrorKind,
    ConnectorOperation,
    ConnectorOperationError,
)
from onestep.state import CursorStore, InMemoryCursorStore

from .resilience import as_mysql_connector_operation_error, collect_sensitive_tokens
from .state_sqlalchemy import SQLAlchemyCursorStore, SQLAlchemyStateStore, _async_dsn

logger = logging.getLogger(__name__)

try:
    import sqlalchemy as sa
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
except ImportError:  # pragma: no cover - exercised when optional deps are missing
    sa = None
    AsyncEngine = None
    create_async_engine = None
    make_url = None

try:
    from pymysqlreplication import BinLogStreamReader
    from pymysqlreplication.row_event import (
        DeleteRowsEvent,
        UpdateRowsEvent,
        WriteRowsEvent,
    )
except ImportError:  # pragma: no cover - exercised when optional deps are missing
    BinLogStreamReader = None
    DeleteRowsEvent = None
    UpdateRowsEvent = None
    WriteRowsEvent = None


class MySQLConnector:
    def __init__(self, dsn: str, **engine_options: Any) -> None:
        if create_async_engine is None:
            raise RuntimeError("MySQLConnector requires SQLAlchemy. Install onestep-mysql.")
        self.dsn = dsn
        self._sensitive_tokens = collect_sensitive_tokens(dsn, engine_options)
        engine_options.setdefault("pool_pre_ping", True)
        self.engine: AsyncEngine = create_async_engine(_async_dsn(dsn), **engine_options)
        self._tables: dict[str, Any] = {}
        self._table_lock: asyncio.Lock | None = None

    def _secret_tokens(self) -> list[str]:
        """Secret-bearing config tokens used to scrub error messages."""
        return list(self._sensitive_tokens)

    async def close(self) -> None:
        await self.engine.dispose()

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
    ) -> TableQueueSource:
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
    ) -> IncrementalTableSource:
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
    ) -> BinlogSource:
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
        update_columns: Sequence[str | Mapping[str, str]] | None = None,
        update_expr: Mapping[str, str] | None = None,
        serialize_json: str = "auto",
    ) -> TableSink:
        return TableSink(
            connector=self,
            table=table,
            mode=mode,
            keys=tuple(keys),
            update_columns=tuple(update_columns) if update_columns is not None else None,
            update_expr=update_expr,
            serialize_json=serialize_json,
        )

    async def _table(self, table_name: str):
        table = self._tables.get(table_name)
        if table is None:
            lock = self._table_lock
            if lock is None:
                lock = asyncio.Lock()
                self._table_lock = lock
            async with lock:
                table = self._tables.get(table_name)
                if table is None:
                    metadata = sa.MetaData()
                    async with self.engine.connect() as conn:
                        table = await conn.run_sync(
                            lambda sync_conn: sa.Table(
                                table_name, metadata, autoload_with=sync_conn
                            )
                        )
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
    def __init__(self, source: TableQueueSource, envelope: Envelope, row_ref: _TableRowRef) -> None:
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
            rows = await self._fetch(max(1, min(limit, self.batch_size)))
        except Exception as exc:
            connector_error = as_mysql_connector_operation_error(
                operation=ConnectorOperation.FETCH,
                exc=exc,
                source_name=self.name,
                retry_delay_s=self.poll_interval_s,
                secrets=self.connector._secret_tokens(),
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

    async def _fetch(self, limit: int) -> list[dict[str, Any]]:
        table = await self.connector._table(self.table_name)
        async with self.connector.engine.begin() as conn:
            stmt = sa.select(table).where(sa.text(self.where)).order_by(table.c[self.key]).limit(limit)
            try:
                stmt = stmt.with_for_update(skip_locked=True)
            except TypeError:
                stmt = stmt.with_for_update()
            rows = [dict(row) for row in (await conn.execute(stmt)).mappings().all()]
            if not rows:
                return []
            ids = [row[self.key] for row in rows]
            await conn.execute(sa.update(table).where(table.c[self.key].in_(ids)).values(**self.claim))
            refreshed = await conn.execute(
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
        await self._update_row(row_ref, dict(values))

    async def _update_row(self, row_ref: _TableRowRef, values: Mapping[str, Any]) -> None:
        if not values:
            return
        table = await self.connector._table(row_ref.table)
        async with self.connector.engine.begin() as conn:
            await conn.execute(
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
    def __init__(self, source: BinlogSource, envelope: Envelope, token: _BinlogToken) -> None:
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
            self._committed = await self._current_binlog_token()
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
                secrets=self.connector._secret_tokens(),
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

    async def _current_binlog_token(self) -> _BinlogToken:
        async with self.connector.engine.begin() as conn:
            try:
                row = (await conn.exec_driver_sql("SHOW BINARY LOG STATUS")).mappings().first()
            except sa.exc.SQLAlchemyError:
                row = (await conn.exec_driver_sql("SHOW MASTER STATUS")).mappings().first()
        if row is None:
            raise RuntimeError("MySQL binary log is not enabled")
        file = row.get("File")
        pos = row.get("Position")
        if not isinstance(file, str) or not isinstance(pos, int):
            raise RuntimeError(  # noqa: TRY004 - preserve the connector's public error type
                "MySQL did not return a binary log file and position"
            )
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


@dataclass
class _IncrementalRetryRow:
    envelope: Envelope
    ready_at: float
    in_flight: bool = False


class IncrementalDelivery(Delivery):
    def __init__(self, source: IncrementalTableSource, envelope: Envelope, token: _CursorToken) -> None:
        super().__init__(envelope)
        self._source = source
        self._token = token

    async def ack(self) -> None:
        await self._source.ack_token(self._token)

    async def retry(self, *, delay_s: float | None = None) -> None:
        await self._source.retry_token(
            self._token,
            self.envelope,
            delay_s=delay_s,
        )

    async def fail(self, exc: Exception | None = None) -> None:
        await self._source.fail_token(self._token, exc)

    async def release_unstarted(self) -> None:
        await self._source.release_token(self._token, self.envelope)


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
        self._commit_task: asyncio.Task[None] | None = None
        self._commit_error: BaseException | None = None
        self._retry_rows: dict[tuple[Any, ...], _IncrementalRetryRow] = {}
        self._terminal_error: ConnectorOperationError | None = None
        self._fetch_count = 0
        self._retry_count = 0
        self._cursor_save_count = 0
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
        lock = self._runtime_commit_lock()
        async with lock:
            if self._terminal_error is not None:
                raise self._terminal_error
            retry_delivery = self._take_ready_retry_delivery()
            if retry_delivery is not None:
                return [retry_delivery]
            if self._retry_rows:
                return []
        requested_limit = max(1, min(limit, self.batch_size))
        started_at = time.monotonic()
        try:
            rows = await self._fetch(requested_limit)
        except Exception as exc:
            connector_error = as_mysql_connector_operation_error(
                operation=ConnectorOperation.FETCH,
                exc=exc,
                source_name=self.name,
                retry_delay_s=self.poll_interval_s,
                secrets=self.connector._secret_tokens(),
            )
            if connector_error is None:
                raise
            raise connector_error from exc
        deliveries: list[Delivery] = []
        async with lock:
            for row in rows:
                token = _CursorToken(tuple(row[column] for column in self.cursor))
                self._pending.append(token.value)
                self._fetched_cursor = token.value
                envelope = Envelope(body=row, meta={"table": self.table_name})
                deliveries.append(IncrementalDelivery(self, envelope, token))
            self._fetch_count += 1
            fetch_count = self._fetch_count
            pending_cursor_rows = len(self._pending)
        logger.info(
            "mysql incremental fetch",
            extra={
                "event": "mysql_incremental_fetch",
                "fetch_count": fetch_count,
                "requested_limit": requested_limit,
                "row_count": len(rows),
                "duration_s": round(time.monotonic() - started_at, 3),
                "pending_cursor_rows": pending_cursor_rows,
                "fetched_cursor_lag_rows": pending_cursor_rows,
            },
        )
        return deliveries

    def _take_ready_retry_delivery(self) -> Delivery | None:
        now = time.monotonic()
        for token_value in self._pending:
            retry = self._retry_rows.get(token_value)
            if retry is None or retry.in_flight or retry.ready_at > now:
                continue
            retry.in_flight = True
            return IncrementalDelivery(self, retry.envelope, _CursorToken(token_value))
        return None

    async def _fetch(self, limit: int) -> list[dict[str, Any]]:
        table = await self.connector._table(self.table_name)
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
        async with self.connector.engine.begin() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [dict(row) for row in rows]

    async def ack_token(self, token: _CursorToken) -> None:
        lock = self._runtime_commit_lock()
        async with lock:
            self._acked.add(token.value)
            if not self._pending or self._pending[0] not in self._acked:
                return
            task = self._commit_task
            if task is None or task.done():
                self._commit_error = None
                task = asyncio.create_task(self._flush_commits())
                self._commit_task = task
        await asyncio.shield(task)

    async def retry_token(
        self,
        token: _CursorToken,
        envelope: Envelope,
        *,
        delay_s: float | None,
    ) -> None:
        lock = self._runtime_commit_lock()
        async with lock:
            self._retry_count += 1
            retry_count = self._retry_count
            self._retry_rows[token.value] = _IncrementalRetryRow(
                envelope=Envelope(
                    body=envelope.body,
                    meta=dict(envelope.meta),
                    attempts=envelope.attempts + 1,
                ),
                ready_at=time.monotonic() + max(0.0, delay_s or 0.0),
            )
        logger.info(
            "mysql incremental retry",
            extra={
                "event": "mysql_incremental_retry",
                "retry_count": retry_count,
                "attempt": envelope.attempts + 1,
                "delay_s": max(0.0, delay_s or 0.0),
                "pending_cursor_rows": len(self._pending),
            },
        )

    async def release_token(
        self, token: _CursorToken, envelope: Envelope
    ) -> None:
        lock = self._runtime_commit_lock()
        async with lock:
            retry = self._retry_rows.get(token.value)
            if retry is None:
                retry = _IncrementalRetryRow(envelope=envelope, ready_at=time.monotonic())
                self._retry_rows[token.value] = retry
            retry.in_flight = False
            retry.ready_at = time.monotonic()

    async def fail_token(
        self, token: _CursorToken, exc: Exception | None
    ) -> None:
        lock = self._runtime_commit_lock()
        async with lock:
            self._terminal_error = ConnectorOperationError(
                backend="mysql",
                operation=ConnectorOperation.FETCH,
                kind=ConnectorErrorKind.PERMANENT,
                source_name=self.name,
                retry_delay_s=self.poll_interval_s,
                cause=exc,
                message="mysql incremental source is blocked at a failed cursor row",
            )
            retry = self._retry_rows.get(token.value)
            if retry is not None:
                retry.in_flight = False

    async def _flush_commits(self) -> None:
        # Let acknowledgements released by the same destination batch join one
        # durable cursor write.
        await asyncio.sleep(0)
        try:
            while True:
                lock = self._runtime_commit_lock()
                async with lock:
                    prefix: list[tuple[Any, ...]] = []
                    for token_value in self._pending:
                        if token_value not in self._acked:
                            break
                        prefix.append(token_value)
                    if not prefix:
                        return
                    highest = prefix[-1]
                started_at = time.monotonic()
                async with lock:
                    self._cursor_save_count += 1
                    cursor_save_count = self._cursor_save_count
                try:
                    await self.state.save(self.state_key, list(highest))
                except BaseException:
                    logger.info(
                        "mysql incremental cursor commit",
                        extra={
                            "event": "mysql_incremental_cursor_commit",
                            "cursor_save_count": cursor_save_count,
                            "coalesced_ack_count": len(prefix),
                            "duration_s": round(time.monotonic() - started_at, 3),
                            "outcome": "error",
                            "pending_cursor_rows": len(self._pending),
                            "fetched_cursor_lag_rows": len(self._pending),
                        },
                    )
                    raise
                async with lock:
                    for token_value in prefix:
                        current = self._pending.popleft()
                        if current != token_value:
                            raise RuntimeError(
                                "mysql incremental pending cursor order changed during commit"
                            )
                        self._acked.remove(token_value)
                        self._retry_rows.pop(token_value, None)
                    self._committed_cursor = highest
                    if not self._pending:
                        self._fetched_cursor = highest
                    has_more = bool(
                        self._pending and self._pending[0] in self._acked
                    )
                logger.info(
                    "mysql incremental cursor commit",
                    extra={
                        "event": "mysql_incremental_cursor_commit",
                        "cursor_save_count": cursor_save_count,
                        "coalesced_ack_count": len(prefix),
                        "duration_s": round(time.monotonic() - started_at, 3),
                        "outcome": "success",
                        "pending_cursor_rows": len(self._pending),
                        "fetched_cursor_lag_rows": len(self._pending),
                    },
                )
                if not has_more:
                    return
        except BaseException as exc:
            self._commit_error = exc
            raise
        finally:
            lock = self._runtime_commit_lock()
            async with lock:
                if self._commit_task is asyncio.current_task():
                    self._commit_task = None

    async def close(self) -> None:
        task = self._commit_task
        if task is not None:
            await asyncio.shield(task)

    def _runtime_commit_lock(self) -> asyncio.Lock:
        current_loop = asyncio.get_running_loop()
        if self._commit_lock is None or self._commit_loop is not current_loop:
            self._commit_lock = asyncio.Lock()
            self._commit_loop = current_loop
        return self._commit_lock


_UPDATE_COLUMN_POLICIES = frozenset({"overwrite", "skip_null", "backfill"})


def _normalize_update_columns(
    update_columns: Sequence[str | Mapping[str, str]] | None,
    *,
    keys: tuple[str, ...],
) -> tuple[tuple[str, ...] | None, dict[str, str]]:
    if update_columns is None:
        return None, {}
    names: list[str] = []
    policies: dict[str, str] = {}
    for entry in update_columns:
        if isinstance(entry, str):
            if not entry:
                raise ValueError("update_columns entries must be non-empty")
            name, policy = entry, "overwrite"
        elif isinstance(entry, Mapping):
            unknown_keys = set(entry) - {"name", "policy"}
            if unknown_keys:
                raise ValueError(f"unknown update_columns entry keys: {', '.join(sorted(unknown_keys))}")
            name = entry.get("name")
            policy = entry.get("policy", "overwrite")
            if not isinstance(name, str) or not name:
                raise ValueError("update_columns entry requires a non-empty 'name'")
            if policy not in _UPDATE_COLUMN_POLICIES:
                raise ValueError(
                    "update_columns policy must be one of 'overwrite', 'skip_null' or 'backfill', "
                    f"got {policy!r}"
                )
            if name in keys:
                raise ValueError(f"update_columns policy cannot apply to key column {name!r}")
        else:
            raise TypeError("update_columns entries must be strings or mappings")
        if name in policies:
            raise ValueError(f"duplicate update column {name!r}")
        names.append(name)
        policies[name] = policy
    return tuple(names), policies


class TableSink(Sink):
    def __init__(
        self,
        *,
        connector: MySQLConnector,
        table: str,
        mode: str = "insert",
        keys: tuple[str, ...] = (),
        update_columns: Sequence[str | Mapping[str, str]] | None = None,
        update_expr: Mapping[str, str] | None = None,
        serialize_json: str = "auto",
    ) -> None:
        super().__init__(f"mysql.table_sink:{table}")
        if mode not in {"insert", "upsert", "update"}:
            raise ValueError("mode must be one of 'insert', 'upsert' or 'update'")
        if update_columns is not None and mode == "insert":
            raise ValueError("update_columns only applies to upsert or update mode")
        update_columns_tuple, column_policies = _normalize_update_columns(update_columns, keys=keys)
        update_expr_dict = dict(update_expr or {})
        if update_expr is not None:
            if mode == "insert":
                raise ValueError("update_expr only applies to upsert or update mode")
            if not all(isinstance(key, str) and isinstance(value, str) for key, value in update_expr.items()):
                raise TypeError("update_expr keys and values must be strings")
        conflicting = sorted(set(column_policies) & set(update_expr_dict))
        if conflicting:
            raise ValueError(f"update_columns policy conflicts with update_expr for: {', '.join(conflicting)}")
        if mode in {"upsert", "update"} and update_columns_tuple == () and not update_expr_dict:
            raise ValueError(f"{mode} mode requires update_expr when update_columns is empty")
        if serialize_json not in {"auto", "always", "never"}:
            raise ValueError("serialize_json must be 'auto', 'always' or 'never'")
        self.connector = connector
        self.table_name = table
        self.mode = mode
        self.keys = keys
        self.update_columns = update_columns_tuple
        self.column_policies = column_policies
        self.update_expr = update_expr_dict
        self.serialize_json = serialize_json

    async def send(self, envelope: Envelope) -> None:
        if not isinstance(envelope.body, Mapping):
            raise TypeError("TableSink only accepts mapping payloads")
        try:
            await self._send(dict(envelope.body))
        except Exception as exc:
            connector_error = as_mysql_connector_operation_error(
                operation=ConnectorOperation.SEND,
                exc=exc,
                source_name=self.name,
                retry_delay_s=1.0,
                secrets=self.connector._secret_tokens(),
            )
            if connector_error is None:
                raise
            raise connector_error from exc

    async def _send(self, payload: dict[str, Any]) -> None:
        table = await self.connector._table(self.table_name)
        stmt = self._build_statement(payload, table)
        if stmt is None:
            logger.info(
                "mysql table sink %s skipped write: all update columns are null under skip_null policy (keys=%s)",
                self.name,
                {key: payload.get(key) for key in self.keys},
            )
            return
        async with self.connector.engine.begin() as conn:
            result = await conn.execute(stmt)
            if self.mode == "update" and result.rowcount == 0:
                logger.info(
                    "mysql table sink %s update matched no rows or values unchanged (keys=%s)",
                    self.name,
                    {key: payload.get(key) for key in self.keys},
                )

    def _build_statement(self, payload: dict[str, Any], table: sa.Table):
        payload = self._coerce_json_values(payload, table)
        if self.mode == "insert":
            return sa.insert(table).values(**payload)
        if not self.keys:
            raise ValueError(f"{self.mode} mode requires keys")
        update_payload, skipped_by_policy = self._update_payload(payload, table)
        if not update_payload:
            if skipped_by_policy:
                return None
            raise ValueError(f"{self.mode} mode requires at least one update column or update_expr")
        if self.mode == "update":
            missing_keys = [key for key in self.keys if key not in payload]
            if missing_keys:
                raise ValueError(f"update mode requires keys present in payload: {', '.join(missing_keys)}")
            conditions = [table.columns[key] == payload[key] for key in self.keys]
            return sa.update(table).where(sa.and_(*conditions)).values(**update_payload)
        sync_engine = getattr(self.connector.engine, "sync_engine", self.connector.engine)
        dialect = sync_engine.dialect.name
        if dialect == "mysql":
            from sqlalchemy.dialects.mysql import insert as mysql_insert

            stmt = mysql_insert(table).values(**payload)
            return stmt.on_duplicate_key_update(**update_payload)
        if dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            stmt = sqlite_insert(table).values(**payload)
            return stmt.on_conflict_do_update(index_elements=list(self.keys), set_=update_payload)
        return sa.insert(table).values(**payload)

    def _update_payload(self, payload: dict[str, Any], table: sa.Table) -> tuple[dict[str, Any], bool]:
        if self.update_columns is not None:
            candidates = {key: value for key, value in payload.items() if key in self.update_columns}
        else:
            candidates = {key: value for key, value in payload.items() if key not in self.keys}
        update_payload: dict[str, Any] = {}
        skipped = False
        for column, value in candidates.items():
            policy = self.column_policies.get(column, "overwrite")
            if policy == "skip_null" and value is None:
                skipped = True
                continue
            if policy == "backfill":
                update_payload[column] = sa.func.coalesce(table.columns[column], value)
            else:
                update_payload[column] = value
        for column, expr in self.update_expr.items():
            update_payload[column] = sa.literal_column(expr)
        return update_payload, skipped

    def _coerce_json_values(self, payload: dict[str, Any], table: sa.Table) -> dict[str, Any]:
        if self.serialize_json == "never":
            return payload
        coerced = dict(payload)
        for column_name, value in list(payload.items()):
            if not isinstance(value, (list, dict)):
                continue
            column = table.columns.get(column_name)
            if column is None:
                continue
            is_json_column = isinstance(column.type, sa.JSON)
            if self.serialize_json == "always" or not is_json_column:
                coerced[column_name] = json.dumps(value, ensure_ascii=False)
        return coerced
