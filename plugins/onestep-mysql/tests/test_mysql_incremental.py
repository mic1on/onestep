import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import sqlalchemy as sa
import pytest
from onestep.resilience import ConnectorOperationError
from onestep.state import InMemoryCursorStore
from onestep_mysql import MySQLConnector


def test_mysql_incremental_cursor_advances_in_order(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'incremental.db'}"
    engine = sa.create_engine(db_url, future=True)
    metadata = sa.MetaData()
    users = sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("updated_at", sa.Integer, nullable=False),
        sa.Column("deleted", sa.Integer, nullable=False, default=0),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            sa.insert(users),
            [
                {"id": 1, "name": "A", "updated_at": 10, "deleted": 0},
                {"id": 2, "name": "B", "updated_at": 10, "deleted": 0},
            ],
        )
    engine.dispose()

    async def scenario() -> None:
        db = MySQLConnector(db_url)
        state = db.cursor_store(table="onestep_cursor")
        source = db.incremental(
            table="users",
            key="id",
            cursor=("updated_at", "id"),
            where="deleted = 0",
            batch_size=10,
            poll_interval_s=0.01,
            state=state,
            state_key="users-sync",
        )

        batch = await source.fetch(10)
        assert [item.payload["id"] for item in batch] == [1, 2]

        await batch[1].ack()
        assert await state.load("users-sync") is None

        await batch[0].ack()
        assert await state.load("users-sync") == [10, 2]
        await db.close()

        restarted_db = MySQLConnector(db_url)
        restarted_state = restarted_db.cursor_store(table="onestep_cursor")
        restarted_source = restarted_db.incremental(
            table="users",
            key="id",
            cursor=("updated_at", "id"),
            where="deleted = 0",
            batch_size=10,
            poll_interval_s=0.01,
            state=restarted_state,
            state_key="users-sync",
        )

        empty_batch = await restarted_source.fetch(10)
        assert empty_batch == []

        verify_engine = sa.create_engine(db_url, future=True)
        with verify_engine.begin() as conn:
            conn.execute(sa.insert(users), [{"id": 3, "name": "C", "updated_at": 11, "deleted": 0}])
        verify_engine.dispose()

        next_batch = await restarted_source.fetch(10)
        assert [item.payload["id"] for item in next_batch] == [3]
        await next_batch[0].ack()
        assert await restarted_state.load("users-sync") == [11, 3]
        await restarted_db.close()

    asyncio.run(scenario())


def test_mysql_incremental_restarts_from_datetime_cursor(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'incremental-datetime.db'}"
    engine = sa.create_engine(db_url, future=True)
    metadata = sa.MetaData()
    rows = sa.Table(
        "rows",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    first_created_at = datetime(2026, 8, 17, 0, 53, 55, 640000)  # noqa: DTZ001
    second_created_at = first_created_at + timedelta(microseconds=1)
    third_created_at = second_created_at + timedelta(microseconds=1)
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            sa.insert(rows),
            [
                {"id": 1, "created_at": first_created_at},
                {"id": 2, "created_at": second_created_at},
            ],
        )
    engine.dispose()

    async def scenario() -> None:
        db = MySQLConnector(db_url)
        state = db.cursor_store(table="onestep_cursor")
        source = db.incremental(
            table="rows",
            key="id",
            cursor=("created_at", "id"),
            batch_size=10,
            poll_interval_s=0.01,
            state=state,
            state_key="follow-records",
        )

        batch = await source.fetch(10)
        assert [item.payload["id"] for item in batch] == [1, 2]
        await asyncio.gather(*(item.ack() for item in batch))
        assert await state.load("follow-records") == [second_created_at, 2]
        await db.close()

        restarted_db = MySQLConnector(db_url)
        restarted_state = restarted_db.cursor_store(table="onestep_cursor")
        restarted_source = restarted_db.incremental(
            table="rows",
            key="id",
            cursor=("created_at", "id"),
            batch_size=10,
            poll_interval_s=0.01,
            state=restarted_state,
            state_key="follow-records",
        )

        assert await restarted_source.fetch(10) == []

        verify_engine = sa.create_engine(db_url, future=True)
        with verify_engine.begin() as conn:
            conn.execute(sa.insert(rows), {"id": 3, "created_at": third_created_at})
        verify_engine.dispose()

        next_batch = await restarted_source.fetch(10)
        assert [item.payload["id"] for item in next_batch] == [3]
        await next_batch[0].ack()
        await restarted_db.close()

    asyncio.run(scenario())


def test_mysql_incremental_does_not_refetch_pending_gap_with_out_of_order_ack(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'incremental_gap.db'}"
    engine = sa.create_engine(db_url, future=True)
    metadata = sa.MetaData()
    users = sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("updated_at", sa.Integer, nullable=False),
        sa.Column("deleted", sa.Integer, nullable=False, default=0),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            sa.insert(users),
            [
                {"id": 1, "name": "A", "updated_at": 10, "deleted": 0},
                {"id": 2, "name": "B", "updated_at": 10, "deleted": 0},
            ],
        )
    engine.dispose()

    async def scenario() -> None:
        db = MySQLConnector(db_url)
        source = db.incremental(
            table="users",
            key="id",
            cursor=("updated_at", "id"),
            where="deleted = 0",
            batch_size=10,
            poll_interval_s=0.01,
        )

        batch = await source.fetch(10)
        assert [item.payload["id"] for item in batch] == [1, 2]

        await batch[1].ack()
        next_batch = await source.fetch(1)
        assert next_batch == []

        await batch[0].ack()
        await db.close()

    asyncio.run(scenario())


def test_mysql_incremental_uses_key_as_tie_breaker_when_cursor_is_not_unique(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'incremental_tiebreak.db'}"
    engine = sa.create_engine(db_url, future=True)
    metadata = sa.MetaData()
    users = sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("updated_at", sa.Integer, nullable=False),
        sa.Column("deleted", sa.Integer, nullable=False, default=0),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            sa.insert(users),
            [
                {"id": 1, "name": "A", "updated_at": 10, "deleted": 0},
                {"id": 2, "name": "B", "updated_at": 10, "deleted": 0},
            ],
        )
    engine.dispose()

    async def scenario() -> None:
        db = MySQLConnector(db_url)
        state = db.cursor_store(table="onestep_cursor")
        source = db.incremental(
            table="users",
            key="id",
            cursor=("updated_at",),
            where="deleted = 0",
            batch_size=10,
            poll_interval_s=0.01,
            state=state,
            state_key="users-updated-at",
        )

        batch = await source.fetch(10)
        assert [item.payload["id"] for item in batch] == [1, 2]
        assert source.cursor == ("updated_at", "id")

        await batch[0].ack()
        await batch[1].ack()
        assert await state.load("users-updated-at") == [10, 2]

        verify_engine = sa.create_engine(db_url, future=True)
        with verify_engine.begin() as conn:
            conn.execute(sa.insert(users), [{"id": 3, "name": "C", "updated_at": 10, "deleted": 0}])
        verify_engine.dispose()

        next_batch = await source.fetch(10)
        assert [item.payload["id"] for item in next_batch] == [3]
        await next_batch[0].ack()
        await db.close()

    asyncio.run(scenario())


def test_mysql_incremental_default_state_key_separates_distinct_where_clauses(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'incremental_where_scope.db'}"
    engine = sa.create_engine(db_url, future=True)
    metadata = sa.MetaData()
    users = sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("updated_at", sa.Integer, nullable=False),
        sa.Column("deleted", sa.Integer, nullable=False, default=0),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            sa.insert(users),
            [
                {"id": 1, "name": "A", "updated_at": 10, "deleted": 0},
                {"id": 2, "name": "B", "updated_at": 11, "deleted": 0},
                {"id": 3, "name": "C", "updated_at": 9, "deleted": 1},
            ],
        )
    engine.dispose()

    async def scenario() -> None:
        db = MySQLConnector(db_url)
        state = db.cursor_store(table="onestep_cursor")
        active = db.incremental(
            table="users",
            key="id",
            cursor=("updated_at",),
            where="deleted = 0",
            batch_size=10,
            poll_interval_s=0.01,
            state=state,
        )
        deleted = db.incremental(
            table="users",
            key="id",
            cursor=("updated_at",),
            where="deleted = 1",
            batch_size=10,
            poll_interval_s=0.01,
            state=state,
        )

        assert active.state_key != deleted.state_key

        active_batch = await active.fetch(10)
        assert [item.payload["id"] for item in active_batch] == [1, 2]
        await active_batch[0].ack()
        await active_batch[1].ack()

        deleted_batch = await deleted.fetch(10)
        assert [item.payload["id"] for item in deleted_batch] == [3]
        await deleted_batch[0].ack()

        await db.close()

    asyncio.run(scenario())


def test_mysql_incremental_retry_redelivers_same_row_with_incremented_attempts(
    tmp_path: Path,
) -> None:
    db_url = f"sqlite:///{tmp_path / 'incremental_retry.db'}"
    engine = sa.create_engine(db_url, future=True)
    metadata = sa.MetaData()
    rows = sa.Table(
        "rows",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.Integer, nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(sa.insert(rows), [{"id": 1, "created_at": 10}, {"id": 2, "created_at": 11}])
    engine.dispose()

    async def scenario() -> None:
        db = MySQLConnector(db_url)
        state = InMemoryCursorStore()
        source = db.incremental(
            table="rows",
            key="id",
            cursor=("created_at", "id"),
            batch_size=10,
            poll_interval_s=0.01,
            state=state,
            state_key="retry-test",
        )
        original = await source.fetch(2)
        assert [item.payload["id"] for item in original] == [1, 2]

        await original[0].retry(delay_s=0)
        first_retry = await source.fetch(2)
        assert len(first_retry) == 1
        assert first_retry[0].payload["id"] == 1
        assert first_retry[0].envelope.attempts == 1

        await first_retry[0].retry(delay_s=0)
        second_retry = await source.fetch(2)
        assert second_retry[0].payload["id"] == 1
        assert second_retry[0].envelope.attempts == 2
        await second_retry[0].ack()
        assert await state.load("retry-test") == [10, 1]
        await original[1].ack()
        assert await state.load("retry-test") == [11, 2]
        await db.close()

    asyncio.run(scenario())


def test_mysql_incremental_retry_delay_and_inflight_gap_pause_sql_reads(
    tmp_path: Path,
) -> None:
    db_url = f"sqlite:///{tmp_path / 'incremental_retry_gap.db'}"
    engine = sa.create_engine(db_url, future=True)
    metadata = sa.MetaData()
    rows = sa.Table(
        "rows",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.Integer, nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(sa.insert(rows), [{"id": 1, "created_at": 10}])
    engine.dispose()

    async def scenario() -> None:
        db = MySQLConnector(db_url)
        source = db.incremental(
            table="rows",
            key="id",
            cursor=("created_at", "id"),
            batch_size=10,
            poll_interval_s=0.01,
        )
        original = (await source.fetch(1))[0]
        await original.retry(delay_s=0.05)
        assert await source.fetch(10) == []
        await asyncio.sleep(0.06)
        retry = (await source.fetch(10))[0]
        assert retry.envelope.attempts == 1
        assert await source.fetch(10) == []
        await retry.ack()
        await db.close()

    asyncio.run(scenario())


def test_mysql_incremental_terminal_failure_blocks_before_failed_cursor(
    tmp_path: Path,
) -> None:
    db_url = f"sqlite:///{tmp_path / 'incremental_terminal.db'}"
    engine = sa.create_engine(db_url, future=True)
    metadata = sa.MetaData()
    rows = sa.Table(
        "rows",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.Integer, nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(sa.insert(rows), [{"id": 1, "created_at": 10}, {"id": 2, "created_at": 11}])
    engine.dispose()

    async def scenario() -> None:
        db = MySQLConnector(db_url)
        state = InMemoryCursorStore()
        source = db.incremental(
            table="rows",
            key="id",
            cursor=("created_at", "id"),
            batch_size=10,
            poll_interval_s=0.01,
            state=state,
            state_key="terminal-test",
        )
        batch = await source.fetch(2)
        await batch[1].ack()
        await batch[0].fail(RuntimeError("bad row"))
        with pytest.raises(ConnectorOperationError, match="blocked at a failed cursor row"):
            await source.fetch(10)
        assert await state.load("terminal-test") is None
        await db.close()

    asyncio.run(scenario())


class _CountingCursorStore(InMemoryCursorStore):
    def __init__(self) -> None:
        super().__init__()
        self.save_calls: list[object] = []
        self.failures_remaining = 0

    async def save(self, key: str, value: object) -> None:
        self.save_calls.append(value)
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("cursor save failed")
        await super().save(key, value)


def test_mysql_incremental_coalesces_concurrent_contiguous_acks(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'incremental_coalesce.db'}"
    engine = sa.create_engine(db_url, future=True)
    metadata = sa.MetaData()
    rows = sa.Table(
        "rows",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.Integer, nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            sa.insert(rows),
            [{"id": index, "created_at": index} for index in range(1, 101)],
        )
    engine.dispose()

    async def scenario() -> None:
        db = MySQLConnector(db_url)
        state = _CountingCursorStore()
        source = db.incremental(
            table="rows",
            key="id",
            cursor=("created_at", "id"),
            batch_size=100,
            poll_interval_s=0.01,
            state=state,
            state_key="coalesce-test",
        )
        batch = await source.fetch(100)
        await asyncio.gather(*(delivery.ack() for delivery in batch))
        assert state.save_calls == [[100, 100]]
        await db.close()

    asyncio.run(scenario())


def test_mysql_incremental_cursor_save_failure_preserves_retryable_prefix(
    tmp_path: Path,
) -> None:
    db_url = f"sqlite:///{tmp_path / 'incremental_save_failure.db'}"
    engine = sa.create_engine(db_url, future=True)
    metadata = sa.MetaData()
    rows = sa.Table(
        "rows",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.Integer, nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(sa.insert(rows), [{"id": 1, "created_at": 10}, {"id": 2, "created_at": 11}])
    engine.dispose()

    async def scenario() -> None:
        db = MySQLConnector(db_url)
        state = _CountingCursorStore()
        state.failures_remaining = 1
        source = db.incremental(
            table="rows",
            key="id",
            cursor=("created_at", "id"),
            batch_size=10,
            poll_interval_s=0.01,
            state=state,
            state_key="save-failure-test",
        )
        batch = await source.fetch(2)
        results = await asyncio.gather(
            *(delivery.ack() for delivery in batch), return_exceptions=True
        )
        assert all(isinstance(result, RuntimeError) for result in results)
        assert await state.load("save-failure-test") is None
        await batch[0].ack()
        assert await state.load("save-failure-test") == [11, 2]
        await db.close()

    asyncio.run(scenario())


def test_mysql_incremental_logs_fetch_retry_commit_without_sensitive_values(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    db_url = f"sqlite:///{tmp_path / 'mysql-dsn-secret.db'}"
    engine = sa.create_engine(db_url, future=True)
    metadata = sa.MetaData()
    rows = sa.Table(
        "rows",
        metadata,
        sa.Column("union_key", sa.String, primary_key=True),
        sa.Column("created_at", sa.Integer, nullable=False),
        sa.Column("payload", sa.String, nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            sa.insert(rows),
            {
                "union_key": "union-key-secret",
                "created_at": 8_675_309,
                "payload": "payload-value-secret",
            },
        )
    engine.dispose()

    async def scenario() -> None:
        db = MySQLConnector(db_url)
        state = InMemoryCursorStore()
        source = db.incremental(
            table="rows",
            key="union_key",
            cursor=("created_at", "union_key"),
            batch_size=100,
            poll_interval_s=0.01,
            state=state,
            state_key="cursor-state-secret",
        )
        original = (await source.fetch(100))[0]
        await original.retry(delay_s=0)
        retry = (await source.fetch(100))[0]
        await retry.ack()
        await db.close()

    caplog.set_level(logging.INFO, logger="onestep_mysql.connector")
    asyncio.run(scenario())

    records = {
        record.event: record
        for record in caplog.records
        if hasattr(record, "event")
    }
    assert {
        "fetch_count",
        "requested_limit",
        "row_count",
        "duration_s",
        "pending_cursor_rows",
        "fetched_cursor_lag_rows",
    } <= records["mysql_incremental_fetch"].__dict__.keys()
    assert {
        "retry_count",
        "attempt",
        "delay_s",
        "pending_cursor_rows",
    } <= records["mysql_incremental_retry"].__dict__.keys()
    assert {
        "cursor_save_count",
        "coalesced_ack_count",
        "duration_s",
        "outcome",
        "pending_cursor_rows",
        "fetched_cursor_lag_rows",
    } <= records["mysql_incremental_cursor_commit"].__dict__.keys()

    serialized = json.dumps(
        [record.__dict__ for record in caplog.records],
        ensure_ascii=False,
        default=str,
    )
    for secret in (
        "mysql-dsn-secret",
        "union-key-secret",
        "payload-value-secret",
        "cursor-state-secret",
        "8675309",
    ):
        assert secret not in serialized
