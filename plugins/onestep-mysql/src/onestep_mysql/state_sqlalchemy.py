from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

try:
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
except ImportError:  # pragma: no cover - exercised when optional deps are missing
    sa = None
    AsyncEngine = None
    create_async_engine = None


def _async_dsn(dsn: str) -> str:
    """Return a SQLAlchemy URL backed by an asyncio-compatible dialect."""
    if sa is None:
        return dsn
    url = sa.engine.make_url(dsn)
    if url.drivername == "mysql" or url.drivername.startswith("mysql+"):
        return url.set(drivername="mysql+asyncmy").render_as_string(hide_password=False)
    if url.drivername == "sqlite" or url.drivername == "sqlite+pysqlite":
        return url.set(drivername="sqlite+aiosqlite").render_as_string(hide_password=False)
    return dsn


class SQLAlchemyStateStore:
    def __init__(
        self,
        *,
        dsn: str | None = None,
        engine: Any | None = None,
        table: str = "onestep_state",
        key_column: str = "state_key",
        value_column: str = "state_value",
        updated_at_column: str = "updated_at",
        auto_create: bool = True,
        **engine_options: Any,
    ) -> None:
        if create_async_engine is None or sa is None:
            raise RuntimeError("SQLAlchemyStateStore requires SQLAlchemy. Install SQLAlchemy or onestep-mysql.")
        if engine is None and dsn is None:
            raise ValueError("dsn or engine is required")
        if engine is not None and dsn is not None:
            raise ValueError("pass either dsn or engine, not both")
        engine_options.setdefault("pool_pre_ping", True)
        self.engine = engine or create_async_engine(_async_dsn(dsn), **engine_options)
        self._owns_engine = engine is None
        self.table_name = table
        self.key_column_name = key_column
        self.value_column_name = value_column
        self.updated_at_column_name = updated_at_column
        self.auto_create = auto_create
        self._metadata = sa.MetaData()
        self._table = sa.Table(
            table,
            self._metadata,
            sa.Column(key_column, sa.String(255), primary_key=True),
            sa.Column(value_column, sa.Text(), nullable=False),
            sa.Column(updated_at_column, sa.DateTime(timezone=True), nullable=False),
        )
        self._ready = False
        self._ready_lock: asyncio.Lock | None = None

    async def load(self, key: str) -> Any | None:
        await self._ensure_ready()
        key_column = self._table.c[self.key_column_name]
        value_column = self._table.c[self.value_column_name]
        async with self.engine.begin() as conn:
            row = (
                await conn.execute(sa.select(value_column).where(key_column == key))
            ).scalar_one_or_none()
        if row is None:
            return None
        return json.loads(row)

    async def save(self, key: str, value: Any) -> None:
        await self._ensure_ready()
        key_column = self._table.c[self.key_column_name]
        payload = {
            self.key_column_name: key,
            self.value_column_name: json.dumps(value, ensure_ascii=False),
            self.updated_at_column_name: datetime.now(timezone.utc),
        }
        async with self.engine.begin() as conn:
            exists = (
                await conn.execute(sa.select(key_column).where(key_column == key))
            ).scalar_one_or_none()
            if exists is None:
                await conn.execute(sa.insert(self._table).values(**payload))
                return
            await conn.execute(
                sa.update(self._table)
                .where(key_column == key)
                .values(
                    **{
                        self.value_column_name: payload[self.value_column_name],
                        self.updated_at_column_name: payload[self.updated_at_column_name],
                    }
                )
            )

    async def delete(self, key: str) -> None:
        await self._ensure_ready()
        key_column = self._table.c[self.key_column_name]
        async with self.engine.begin() as conn:
            await conn.execute(sa.delete(self._table).where(key_column == key))

    async def close(self) -> None:
        if self._owns_engine:
            await self.engine.dispose()

    async def _ensure_ready(self) -> None:
        if self._ready or not self.auto_create:
            return
        lock = self._ready_lock
        if lock is None:
            lock = asyncio.Lock()
            self._ready_lock = lock
        async with lock:
            if self._ready:
                return
            async with self.engine.begin() as conn:
                await conn.run_sync(
                    lambda sync_conn: self._metadata.create_all(
                        sync_conn, tables=[self._table], checkfirst=True
                    )
                )
            self._ready = True


class SQLAlchemyCursorStore(SQLAlchemyStateStore):
    _DATETIME_TYPE_KEY = "__onestep_cursor_type__"
    _DATETIME_TYPE_VALUE = "datetime"
    _DATETIME_VALUE_KEY = "value"

    async def load(self, key: str) -> Any | None:
        value = await super().load(key)
        if not isinstance(value, list):
            return value
        return [self._decode_cursor_component(component) for component in value]

    async def save(self, key: str, value: Any) -> None:
        if isinstance(value, (list, tuple)):
            value = [self._encode_cursor_component(component) for component in value]
        await super().save(key, value)

    @classmethod
    def _encode_cursor_component(cls, value: Any) -> Any:
        if isinstance(value, datetime):
            return {
                cls._DATETIME_TYPE_KEY: cls._DATETIME_TYPE_VALUE,
                cls._DATETIME_VALUE_KEY: value.isoformat(),
            }
        return value

    @classmethod
    def _decode_cursor_component(cls, value: Any) -> Any:
        if (
            isinstance(value, dict)
            and set(value) == {cls._DATETIME_TYPE_KEY, cls._DATETIME_VALUE_KEY}
            and value[cls._DATETIME_TYPE_KEY] == cls._DATETIME_TYPE_VALUE
            and isinstance(value[cls._DATETIME_VALUE_KEY], str)
        ):
            return datetime.fromisoformat(value[cls._DATETIME_VALUE_KEY])
        return value
