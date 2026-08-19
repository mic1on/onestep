from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from onestep.connectors.base import Delivery, Sink, Source
from onestep.envelope import Envelope
from onestep.resilience import ConnectorOperation
from onestep.state import CursorStore, InMemoryCursorStore

from .resilience import as_postgres_connector_operation_error, collect_sensitive_tokens
from .state_sqlalchemy import SQLAlchemyCursorStore, SQLAlchemyStateStore

try:
    import sqlalchemy as sa
    from sqlalchemy import create_engine
except ImportError:  # pragma: no cover - exercised when optional deps are missing
    sa = None
    create_engine = None


class PostgresConnector:
    def __init__(self, dsn: str, **engine_options: Any) -> None:
        if create_engine is None:
            raise RuntimeError("PostgresConnector requires SQLAlchemy. Install onestep-postgres.")
        self.dsn = dsn
        self._sensitive_tokens = collect_sensitive_tokens(dsn, engine_options)
        self.engine = create_engine(dsn, future=True, pool_pre_ping=True, **engine_options)
        self._tables: dict[str, Any] = {}

    def _secret_tokens(self) -> list[str]:
        return self.secret_tokens()

    def secret_tokens(self) -> list[str]:
        """Return a copy of tokens that must be redacted from connector errors."""
        return list(self._sensitive_tokens)

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
    ) -> "PostgresTableQueueSource":
        return PostgresTableQueueSource(
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
    ) -> "PostgresIncrementalSource":
        if len(cursor) < 1:
            raise ValueError("cursor must contain at least one column")
        effective_cursor = tuple(cursor) if key in cursor else (*tuple(cursor), key)
        return PostgresIncrementalSource(
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

    def table_sink(
        self,
        *,
        table: str,
        mode: str = "insert",
        keys: Sequence[str] = (),
        update_columns: Sequence[str | Mapping[str, str]] | None = None,
        update_expr: Mapping[str, str] | None = None,
        serialize_json: str = "auto",
    ) -> "PostgresTableSink":
        return PostgresTableSink(
            connector=self,
            table=table,
            mode=mode,
            keys=tuple(keys),
            update_columns=tuple(update_columns) if update_columns is not None else None,
            update_expr=update_expr,
            serialize_json=serialize_json,
        )

    def execution_backend(
        self,
        *,
        table: str = "onestep_executions",
        attempts_table: str = "onestep_execution_attempts",
        auto_create: bool = True,
        max_payload_bytes: int = 1024 * 1024,
        max_metadata_bytes: int = 64 * 1024,
        max_result_bytes: int = 1024 * 1024,
        reclaim_batch_size: int = 100,
    ) -> "PostgresExecutionBackend":
        from .execution_backend import PostgresExecutionBackend

        return PostgresExecutionBackend.from_connector(
            self,
            table=table,
            attempts_table=attempts_table,
            auto_create=auto_create,
            max_payload_bytes=max_payload_bytes,
            max_metadata_bytes=max_metadata_bytes,
            max_result_bytes=max_result_bytes,
            reclaim_batch_size=reclaim_batch_size,
        )

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


@dataclass
class _TableRowRef:
    table: str
    key: str
    key_value: Any


class PostgresTableQueueDelivery(Delivery):
    def __init__(self, source: "PostgresTableQueueSource", envelope: Envelope, row_ref: _TableRowRef) -> None:
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


class PostgresTableQueueSource(Source):
    fetch_is_cancel_safe = False

    def __init__(
        self,
        *,
        connector: PostgresConnector,
        table: str,
        key: str,
        where: str,
        claim: dict[str, Any],
        ack: dict[str, Any],
        nack: dict[str, Any],
        batch_size: int,
        poll_interval_s: float,
    ) -> None:
        super().__init__(f"postgres.table_queue:{table}")
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
            connector_error = as_postgres_connector_operation_error(
                operation=ConnectorOperation.FETCH,
                exc=exc,
                source_name=self.name,
                retry_delay_s=self.poll_interval_s,
                secrets=self.connector.secret_tokens(),
            )
            if connector_error is None:
                raise
            raise connector_error from None
        deliveries: list[Delivery] = []
        for row in rows:
            key_value = row[self.key]
            envelope = Envelope(body=row, meta={"table": self.table_name})
            row_ref = _TableRowRef(self.table_name, self.key, key_value)
            deliveries.append(PostgresTableQueueDelivery(self, envelope, row_ref))
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


@dataclass
class _CursorToken:
    value: tuple[Any, ...]


class IncrementalDelivery(Delivery):
    def __init__(self, source: "PostgresIncrementalSource", envelope: Envelope, token: _CursorToken) -> None:
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


class PostgresIncrementalSource(Source):
    def __init__(
        self,
        *,
        connector: PostgresConnector,
        table: str,
        key: str,
        cursor: tuple[str, ...],
        where: str | None,
        batch_size: int,
        poll_interval_s: float,
        state: CursorStore,
        state_key: str,
    ) -> None:
        super().__init__(f"postgres.incremental:{table}")
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
            connector_error = as_postgres_connector_operation_error(
                operation=ConnectorOperation.FETCH,
                exc=exc,
                source_name=self.name,
                retry_delay_s=self.poll_interval_s,
                secrets=self.connector.secret_tokens(),
            )
            if connector_error is None:
                raise
            raise connector_error from None
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


logger = logging.getLogger(__name__)


_UPDATE_COLUMN_POLICIES = frozenset({"overwrite", "skip_null", "backfill"})


def _normalize_update_columns(
    update_columns: Sequence[str | Mapping[str, str]] | None,
    *,
    keys: tuple[str, ...],
    update_expr: Mapping[str, str] | None = None,
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
                raise ValueError(
                    f"unknown update_columns entry keys: {', '.join(sorted(unknown_keys))}"
                )
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
    update_expr_keys = set(update_expr) if update_expr else set()
    conflicting = sorted(set(policies) & update_expr_keys)
    if conflicting:
        raise ValueError(f"update_columns policy conflicts with update_expr for: {', '.join(conflicting)}")
    return tuple(names), policies


class PostgresTableSink(Sink):
    def __init__(
        self,
        *,
        connector: PostgresConnector,
        table: str,
        mode: str,
        keys: tuple[str, ...],
        update_columns: Sequence[str | Mapping[str, str]] | None = None,
        update_expr: Mapping[str, str] | None = None,
        serialize_json: str = "auto",
    ) -> None:
        super().__init__(f"postgres.table_sink:{table}")
        if mode not in {"insert", "upsert", "update"}:
            raise ValueError("mode must be one of 'insert', 'upsert' or 'update'")
        if serialize_json not in {"auto", "always", "never"}:
            raise ValueError("serialize_json must be 'auto', 'always' or 'never'")
        update_expr_dict = dict(update_expr or {})
        if update_expr is not None:
            if mode == "insert":
                raise ValueError("update_expr only applies to upsert or update mode")
            if not all(isinstance(key, str) and isinstance(value, str) for key, value in update_expr.items()):
                raise TypeError("update_expr keys and values must be strings")
        update_columns_tuple, column_policies = _normalize_update_columns(
            update_columns, keys=keys, update_expr=update_expr_dict
        )
        if mode in {"upsert", "update"} and update_columns_tuple == () and not update_expr_dict:
            raise ValueError(f"{mode} mode requires update_expr when update_columns is empty")
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
            raise TypeError("PostgresTableSink only accepts mapping payloads")
        try:
            await asyncio.to_thread(self._send_sync, dict(envelope.body))
        except Exception as exc:
            connector_error = as_postgres_connector_operation_error(
                operation=ConnectorOperation.SEND,
                exc=exc,
                source_name=self.name,
                retry_delay_s=1.0,
                secrets=self.connector.secret_tokens(),
            )
            if connector_error is None:
                raise
            raise connector_error from None

    def _send_sync(self, payload: dict[str, Any]) -> None:
        stmt = self._build_statement(payload)
        if stmt is None:
            logger.info(
                "postgres table sink %s skipped write: all update columns are null under "
                "skip_null policy (keys=%s)",
                self.name,
                {key: payload.get(key) for key in self.keys},
            )
            return
        with self.connector.engine.begin() as conn:
            result = conn.execute(stmt)
            if self.mode == "update" and result.rowcount == 0:
                logger.info(
                    "postgres table sink %s update matched no rows (keys=%s)",
                    self.name,
                    {key: payload.get(key) for key in self.keys},
                )

    def _build_statement(self, payload: dict[str, Any]) -> Any | None:
        table = self.connector._table(self.table_name)
        payload = self._coerce_json_values(payload, table)
        if self.mode == "insert":
            return sa.insert(table).values(**payload)
        if not self.keys:
            raise ValueError(f"{self.mode} mode requires keys")
        if self.mode == "update":
            missing_keys = [key for key in self.keys if key not in payload]
            if missing_keys:
                raise ValueError(
                    f"update mode requires keys present in payload: {', '.join(missing_keys)}"
                )
            update_payload, skipped_by_policy = self._update_payload(payload, table)
            if not update_payload:
                if skipped_by_policy:
                    return None
                raise ValueError(f"{self.mode} mode requires at least one update column or update_expr")
            conditions = [table.columns[key] == payload[key] for key in self.keys]
            return sa.update(table).where(sa.and_(*conditions)).values(**update_payload)
        # upsert mode
        dialect = self.connector.engine.dialect.name
        update_payload, skipped_by_policy = self._update_payload(payload, table)
        if not update_payload:
            if skipped_by_policy:
                return None
            raise ValueError(f"{self.mode} mode requires at least one update column or update_expr")
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as postgres_insert

            stmt = postgres_insert(table).values(**payload)
            return stmt.on_conflict_do_update(
                index_elements=list(self.keys), set_=update_payload
            )
        if dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            stmt = sqlite_insert(table).values(**payload)
            return stmt.on_conflict_do_update(
                index_elements=list(self.keys), set_=update_payload
            )
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
        for column in table.columns:
            if column.name not in coerced:
                continue
            value = coerced[column.name]
            if not isinstance(value, (list, dict)):
                continue
            if self.serialize_json == "always":
                coerced[column.name] = json.dumps(value, ensure_ascii=False)
            elif self.serialize_json == "auto":
                col_type = str(column.type)
                type_lower = col_type.lower()
                if "json" not in type_lower and "text" in type_lower or "char" in type_lower:
                    coerced[column.name] = json.dumps(value, ensure_ascii=False)
        return coerced
