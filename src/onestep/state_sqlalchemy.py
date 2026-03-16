from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timezone
from typing import Any

try:
    import sqlalchemy as sa
    from sqlalchemy import create_engine
except ImportError:  # pragma: no cover - exercised when optional deps are missing
    sa = None
    create_engine = None


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
        if create_engine is None or sa is None:
            raise RuntimeError("SQLAlchemyStateStore requires SQLAlchemy. Install onestep[mysql] or onestep[test].")
        if engine is None and dsn is None:
            raise ValueError("dsn or engine is required")
        if engine is not None and dsn is not None:
            raise ValueError("pass either dsn or engine, not both")
        self.engine = engine or create_engine(dsn, future=True, pool_pre_ping=True, **engine_options)
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
        self._ready_lock = threading.Lock()

    async def load(self, key: str) -> Any | None:
        return await asyncio.to_thread(self._load_sync, key)

    async def save(self, key: str, value: Any) -> None:
        await asyncio.to_thread(self._save_sync, key, value)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._delete_sync, key)

    async def close(self) -> None:
        if self._owns_engine:
            await asyncio.to_thread(self.engine.dispose)

    def _load_sync(self, key: str) -> Any | None:
        self._ensure_ready_sync()
        key_column = self._table.c[self.key_column_name]
        value_column = self._table.c[self.value_column_name]
        with self.engine.begin() as conn:
            row = conn.execute(sa.select(value_column).where(key_column == key)).scalar_one_or_none()
        if row is None:
            return None
        return json.loads(row)

    def _save_sync(self, key: str, value: Any) -> None:
        self._ensure_ready_sync()
        key_column = self._table.c[self.key_column_name]
        payload = {
            self.key_column_name: key,
            self.value_column_name: json.dumps(value, ensure_ascii=False),
            self.updated_at_column_name: datetime.now(timezone.utc),
        }
        with self.engine.begin() as conn:
            exists = conn.execute(sa.select(key_column).where(key_column == key)).scalar_one_or_none()
            if exists is None:
                conn.execute(sa.insert(self._table).values(**payload))
                return
            conn.execute(
                sa.update(self._table)
                .where(key_column == key)
                .values(
                    **{
                        self.value_column_name: payload[self.value_column_name],
                        self.updated_at_column_name: payload[self.updated_at_column_name],
                    }
                )
            )

    def _delete_sync(self, key: str) -> None:
        self._ensure_ready_sync()
        key_column = self._table.c[self.key_column_name]
        with self.engine.begin() as conn:
            conn.execute(sa.delete(self._table).where(key_column == key))

    def _ensure_ready_sync(self) -> None:
        if self._ready or not self.auto_create:
            return
        with self._ready_lock:
            if self._ready:
                return
            self._metadata.create_all(self.engine, tables=[self._table], checkfirst=True)
            self._ready = True


class SQLAlchemyCursorStore(SQLAlchemyStateStore):
    pass
