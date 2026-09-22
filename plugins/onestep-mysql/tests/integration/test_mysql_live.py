import asyncio
import os
import uuid
from unittest import mock

import pytest
import sqlalchemy as sa
from onestep.envelope import Envelope
from onestep.resilience import ConnectorErrorKind, ConnectorOperationError
from onestep_mysql import MySQLConnector
from sqlalchemy.ext.asyncio import AsyncConnection

if not os.getenv("ONESTEP_MYSQL_DSN"):
    pytest.skip("set ONESTEP_MYSQL_DSN to run MySQL integration tests", allow_module_level=True)


def _engine():
    """A *synchronous* engine for the DDL/verification probes in this file.

    The connector under test always uses the exact ``ONESTEP_MYSQL_DSN`` it was
    pointed at, but these probes run outside ``asyncio`` and SQLAlchemy's sync
    engine cannot drive an asyncio driver: ``mysql+asyncmy://`` raises
    ``MissingGreenlet`` on the first statement. The live CI matrix points the
    DSN at both drivers, so the probes normalise an async DSN onto ``pymysql``
    (also a hard dependency of onestep-sql's ``mysql`` extra) — the same
    driver-immunity trick ``test_mysql_execution_live.py`` uses in the other
    direction.
    """
    dsn = os.environ["ONESTEP_MYSQL_DSN"]
    if "+asyncmy" in dsn:
        dsn = dsn.replace("+asyncmy", "+pymysql")
    return sa.create_engine(dsn, future=True)


@pytest.mark.integration
def test_mysql_table_queue_claim_ack_and_retry_live():
    async def scenario():
        suffix = uuid.uuid4().hex[:8]
        table_name = f"orders_{suffix}"
        engine = _engine()
        metadata = sa.MetaData()
        orders = sa.Table(
            table_name,
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("payload", sa.String(255), nullable=False),
            sa.Column("status", sa.Integer, nullable=False),
        )
        metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(
                sa.insert(orders),
                [
                    {"id": 1, "payload": "alpha", "status": 0},
                    {"id": 2, "payload": "beta", "status": 0},
                ],
            )

        db = MySQLConnector(os.environ["ONESTEP_MYSQL_DSN"])
        source = db.table_queue(
            table=table_name,
            key="id",
            where="status = 0",
            claim={"status": 9},
            ack={"status": 1},
            nack={"status": 0},
            batch_size=10,
            poll_interval_s=0.1,
        )

        batch = await source.fetch(10)
        assert [item.payload["id"] for item in batch] == [1, 2]
        assert [item.payload["status"] for item in batch] == [9, 9]

        await batch[0].ack()
        await batch[1].retry()

        with engine.begin() as conn:
            rows = conn.execute(sa.select(orders).order_by(orders.c.id)).mappings().all()
        assert [dict(row)["status"] for row in rows] == [1, 0]

        await db.close()
        metadata.drop_all(engine)
        engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_mysql_incremental_cursor_recovers_after_restart_live():
    async def scenario():
        suffix = uuid.uuid4().hex[:8]
        users_table_name = f"users_{suffix}"
        cursor_table_name = f"cursor_{suffix}"
        engine = _engine()
        metadata = sa.MetaData()
        users = sa.Table(
            users_table_name,
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String(255), nullable=False),
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

        db = MySQLConnector(os.environ["ONESTEP_MYSQL_DSN"])
        cursor = db.cursor_store(table=cursor_table_name)
        source = db.incremental(
            table=users_table_name,
            key="id",
            cursor=("updated_at", "id"),
            where="deleted = 0",
            batch_size=10,
            poll_interval_s=0.1,
            state=cursor,
            state_key="users-sync",
        )

        first_batch = await source.fetch(10)
        assert [item.payload["id"] for item in first_batch] == [1, 2]
        await first_batch[1].ack()
        assert await cursor.load("users-sync") is None
        await first_batch[0].ack()
        assert await cursor.load("users-sync") == [10, 2]
        await db.close()

        restarted = MySQLConnector(os.environ["ONESTEP_MYSQL_DSN"])
        restarted_cursor = restarted.cursor_store(table=cursor_table_name)
        restarted_source = restarted.incremental(
            table=users_table_name,
            key="id",
            cursor=("updated_at", "id"),
            where="deleted = 0",
            batch_size=10,
            poll_interval_s=0.1,
            state=restarted_cursor,
            state_key="users-sync",
        )

        empty = await restarted_source.fetch(10)
        assert empty == []

        with engine.begin() as conn:
            conn.execute(sa.insert(users), [{"id": 3, "name": "C", "updated_at": 11, "deleted": 0}])

        next_batch = await restarted_source.fetch(10)
        assert [item.payload["id"] for item in next_batch] == [3]
        await next_batch[0].ack()
        assert await restarted_cursor.load("users-sync") == [11, 3]

        await restarted.close()
        with engine.begin() as conn:
            conn.execute(sa.text(f"DROP TABLE IF EXISTS `{cursor_table_name}`"))
        metadata.drop_all(engine)
        engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_mysql_binlog_reads_insert_update_delete_live():
    async def scenario():
        suffix = uuid.uuid4().hex[:8]
        table_name = f"cdc_orders_{suffix}"
        cursor_table_name = f"binlog_cursor_{suffix}"
        engine = _engine()
        metadata = sa.MetaData()
        orders = sa.Table(
            table_name,
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("status", sa.String(255), nullable=False),
        )
        metadata.create_all(engine)

        db = MySQLConnector(os.environ["ONESTEP_MYSQL_DSN"])
        cursor = db.cursor_store(table=cursor_table_name)
        source = db.binlog(
            server_id=18492,
            schemas=("onestep",),
            tables=(table_name,),
            state=cursor,
            state_key=f"{table_name}-cdc",
            batch_size=10,
            poll_interval_s=0.1,
        )
        await source.open()

        with engine.begin() as conn:
            conn.execute(sa.insert(orders), [{"id": 1, "status": "pending"}])
            conn.execute(sa.update(orders).where(orders.c.id == 1).values(status="paid"))
            conn.execute(sa.delete(orders).where(orders.c.id == 1))

        batch = []
        for _ in range(20):
            batch = await source.fetch(10)
            if len(batch) >= 3:
                break
            await asyncio.sleep(0.1)

        assert [item.payload["event"] for item in batch[:3]] == ["insert", "update", "delete"]
        assert batch[0].payload["values"]["status"] == "pending"
        assert batch[1].payload["before_values"]["status"] == "pending"
        assert batch[1].payload["values"]["status"] == "paid"
        assert batch[2].payload["values"]["status"] == "paid"

        await batch[1].ack()
        assert await cursor.load(f"{table_name}-cdc") is not None
        await batch[0].ack()
        await batch[2].ack()
        saved = await cursor.load(f"{table_name}-cdc")
        assert saved["file"].startswith("mysql-bin.")
        assert saved["pos"] >= batch[2].payload["binlog"]["pos"]

        await source.close()
        await db.close()
        with engine.begin() as conn:
            conn.execute(sa.text(f"DROP TABLE IF EXISTS `{cursor_table_name}`"))
        metadata.drop_all(engine)
        engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_mysql_incremental_empty_tail_does_not_scan_processed_prefix_live():
    """A caught-up LIMIT query must seek, not revisit every processed row."""

    async def scenario():
        table_name = f"cursor_range_{uuid.uuid4().hex[:8]}"
        engine = _engine()
        metadata = sa.MetaData()
        events = sa.Table(
            table_name,
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("updated_at", sa.Integer, nullable=False),
            sa.Column("event_key", sa.String(100), nullable=False, unique=True),
            sa.Column("body", sa.Text, nullable=False),
        )
        sa.Index(f"idx_{table_name}", events.c.updated_at, events.c.event_key)
        db = MySQLConnector(
            os.environ["ONESTEP_MYSQL_DSN"], pool_size=1, max_overflow=0
        )
        try:
            metadata.create_all(engine)
            with engine.begin() as conn:
                conn.execute(
                    events.insert(),
                    [
                        {
                            "id": i,
                            "updated_at": 10,
                            "event_key": f"event-{i:06d}",
                            "body": "x" * 256,
                        }
                        for i in range(1, 2001)
                    ],
                )
            source = db.incremental(
                table=table_name, key="event_key", cursor=("updated_at",)
            )
            await source.state.save(source.state_key, [10, "event-002000"])
            await db._table(table_name)  # Exclude metadata reads from the counters.
            async with db.engine.connect() as conn:
                before = int(
                    (
                        await conn.execute(
                            sa.text("SHOW SESSION STATUS LIKE 'Handler_read_next'")
                        )
                    ).one()[1]
                )
            assert await source.fetch(8) == []
            async with db.engine.connect() as conn:
                after = int(
                    (
                        await conn.execute(
                            sa.text("SHOW SESSION STATUS LIKE 'Handler_read_next'")
                        )
                    ).one()[1]
                )
            assert after - before < 20, "empty cursor poll scanned the processed prefix"
        finally:
            await db.close()
            metadata.drop_all(engine)
            engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_mysql_incremental_prefetch_preserves_datetime_restart_and_updates_live():
    from datetime import datetime, timedelta

    from sqlalchemy.dialects.mysql import DATETIME

    async def scenario():
        suffix = uuid.uuid4().hex[:8]
        table_name = f"prefetch_{suffix}"
        cursor_name = f"prefetch_cursor_{suffix}"
        engine = _engine()
        metadata = sa.MetaData()
        table = sa.Table(
            table_name, metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("updated_at", DATETIME(fsp=6), nullable=False),
        )
        stamp = datetime(2026, 1, 1, 12, 0, 0, 123456)  # noqa: DTZ001 - MySQL DATETIME is naive
        db = MySQLConnector(os.environ["ONESTEP_MYSQL_DSN"])
        try:
            metadata.create_all(engine)
            with engine.begin() as conn:
                conn.execute(table.insert(), [{"id": i, "updated_at": stamp} for i in range(1, 54)])
            state = db.cursor_store(table=cursor_name)
            source = db.incremental(
                table=table_name, key="id", cursor=("updated_at",),
                prefetch=True, batch_size=20, state=state, state_key="prefetch-test",
            )
            first = await source.fetch(8)
            assert [d.payload["id"] for d in first] == list(range(1, 9))
            assert len(source._prefetched_rows) == 12
            assert await state.load("prefetch-test") is None
            await asyncio.gather(*(d.ack() for d in first))
            assert await state.load("prefetch-test") == [stamp, 8]
            await source.close()
            await db.close()
            db = MySQLConnector(os.environ["ONESTEP_MYSQL_DSN"])
            state = db.cursor_store(table=cursor_name)
            source = db.incremental(
                table=table_name, key="id", cursor=("updated_at",),
                prefetch=True, batch_size=20, state=state, state_key="prefetch-test",
            )
            seen = []
            while True:
                batch = await source.fetch(8)
                if not batch:
                    break
                seen.extend(d.payload["id"] for d in batch)
                await asyncio.gather(*(d.ack() for d in batch))
            assert seen == list(range(9, 54))
            newer = stamp + timedelta(microseconds=1)
            with engine.begin() as conn:
                conn.execute(table.update().where(table.c.id == 1).values(updated_at=newer))
            changed = await source.fetch(8)
            assert [d.payload["id"] for d in changed] == [1]
            await changed[0].ack()
            assert await state.load("prefetch-test") == [newer, 1]
            await source.close()
        finally:
            await db.close()
            metadata.drop_all(engine)
            with engine.begin() as conn:
                conn.execute(sa.text(f"DROP TABLE IF EXISTS `{cursor_name}`"))
            engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_mysql_table_queue_complete_atomic_live():
    """complete() lands the business column and the ack column together on a
    real server, and the follow-up ack() is a delivery-local no-op (issue #181)."""

    async def scenario():
        suffix = uuid.uuid4().hex[:8]
        table_name = f"orders_{suffix}"
        engine = _engine()
        metadata = sa.MetaData()
        orders = sa.Table(
            table_name,
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("payload", sa.String(255), nullable=False),
            sa.Column("status", sa.Integer, nullable=False),
            sa.Column("score", sa.Integer),
        )
        metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(
                sa.insert(orders),
                [{"id": 1, "payload": "alpha", "status": 0, "score": None}],
            )

        db = MySQLConnector(os.environ["ONESTEP_MYSQL_DSN"])
        source = db.table_queue(
            table=table_name,
            key="id",
            where="status = 0",
            claim={"status": 9},
            ack={"status": 1},
            nack={"status": 0},
            batch_size=10,
            poll_interval_s=0.1,
        )

        ack_row_calls: list[object] = []
        original_ack_row = source.ack_row

        async def recording_ack_row(row_ref: object) -> None:
            ack_row_calls.append(row_ref)
            await original_ack_row(row_ref)

        source.ack_row = recording_ack_row  # type: ignore[method-assign]

        batch = await source.fetch(10)
        assert [item.payload["id"] for item in batch] == [1]
        delivery = batch[0]

        await delivery.complete({"score": 42})
        assert delivery.payload["score"] == 42
        assert delivery.payload["status"] == 9

        await delivery.ack()
        assert ack_row_calls == []

        with engine.begin() as conn:
            row = conn.execute(
                sa.select(orders.c.status, orders.c.score).order_by(orders.c.id)
            ).first()
        assert row == (1, 42)

        await db.close()
        metadata.drop_all(engine)
        engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_mysql_table_queue_complete_midtransaction_failure_rolls_back_live():
    """A failure between the two UPDATE statements inside complete() leaves the
    row in its claim state on a real server (issue #181)."""

    async def scenario():
        suffix = uuid.uuid4().hex[:8]
        table_name = f"orders_{suffix}"
        engine = _engine()
        metadata = sa.MetaData()
        orders = sa.Table(
            table_name,
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("payload", sa.String(255), nullable=False),
            sa.Column("status", sa.Integer, nullable=False),
            sa.Column("score", sa.Integer),
        )
        metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(
                sa.insert(orders),
                [{"id": 1, "payload": "alpha", "status": 0, "score": None}],
            )

        db = MySQLConnector(os.environ["ONESTEP_MYSQL_DSN"])
        source = db.table_queue(
            table=table_name,
            key="id",
            where="status = 0",
            claim={"status": 9},
            ack={"status": 1},
            nack={"status": 0},
            batch_size=10,
            poll_interval_s=0.1,
        )

        batch = await source.fetch(10)
        delivery = batch[0]

        original_execute = AsyncConnection.execute
        execute_calls = {"count": 0}

        async def failing_execute(self: AsyncConnection, *args: object, **kwargs: object):
            execute_calls["count"] += 1
            if execute_calls["count"] == 2:
                raise RuntimeError("injected mid-transaction failure")
            return await original_execute(self, *args, **kwargs)

        with (
            mock.patch.object(AsyncConnection, "execute", failing_execute),
            pytest.raises(RuntimeError, match="injected mid-transaction failure"),
        ):
            await delivery.complete({"score": 42})

        assert execute_calls["count"] == 2  # values UPDATE ran, ack UPDATE raised
        assert delivery._complete_ack_applied is False

        with engine.begin() as conn:
            row = conn.execute(
                sa.select(orders.c.status, orders.c.score).order_by(orders.c.id)
            ).first()
        # Neither the business value nor the ack survived the rollback.
        assert row == (9, None)

        # The delivery stays usable: retrying complete() after the injected
        # failure succeeds and applies both writes atomically.
        await delivery.complete({"score": 42})
        assert delivery._complete_ack_applied is True
        with engine.begin() as conn:
            row = conn.execute(
                sa.select(orders.c.status, orders.c.score).order_by(orders.c.id)
            ).first()
        assert row == (1, 42)

        await db.close()
        metadata.drop_all(engine)
        engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_mysql_table_sink_upsert_without_unique_index_fails_live():
    """The issue #188 silent-degradation guard, on a real server.

    ``ON DUPLICATE KEY UPDATE`` declares no conflict target: with only a plain
    index on the declared keys, every run inserted fresh duplicates and nothing
    reported an error. The preflight must now refuse the write outright, and a
    correctly-configured table must stay idempotent.
    """

    async def scenario():
        suffix = uuid.uuid4().hex[:8]
        plain_table = f"sale_device_{suffix}"
        unique_table = f"sale_device_uq_{suffix}"
        engine = _engine()
        with engine.begin() as conn:
            # ceegic-sync shape: a *plain* index on device_key.
            conn.execute(
                sa.text(
                    f"CREATE TABLE {plain_table} "
                    "(device_key VARCHAR(64), payload VARCHAR(64), "
                    "INDEX idx_device_key (device_key))"
                )
            )
            conn.execute(
                sa.text(
                    f"CREATE TABLE {unique_table} "
                    "(device_key VARCHAR(64), payload VARCHAR(64), "
                    "UNIQUE KEY uq_device_key (device_key))"
                )
            )

        db = MySQLConnector(os.environ["ONESTEP_MYSQL_DSN"])

        bad_sink = db.table_sink(
            table=plain_table, mode="upsert", keys=("device_key",), update_columns=("payload",)
        )
        with pytest.raises(ConnectorOperationError) as excinfo:
            await bad_sink.send(Envelope(body={"device_key": "k1", "payload": "p1"}))
        assert excinfo.value.kind is ConnectorErrorKind.MISCONFIGURED
        assert plain_table in str(excinfo.value)
        assert "idx_device_key" in str(excinfo.value)

        # The refusal happens before any write: no duplicate row was created.
        with engine.connect() as conn:
            assert conn.execute(sa.text(f"SELECT COUNT(*) FROM {plain_table}")).scalar() == 0

        good_sink = db.table_sink(
            table=unique_table, mode="upsert", keys=("device_key",), update_columns=("payload",)
        )
        for _ in range(3):
            await good_sink.send(Envelope(body={"device_key": "k1", "payload": "p1"}))

        with engine.connect() as conn:
            rows = conn.execute(sa.text(f"SELECT COUNT(*) FROM {unique_table}")).scalar()
            distinct = conn.execute(
                sa.text(f"SELECT COUNT(DISTINCT device_key) FROM {unique_table}")
            ).scalar()
        assert (rows, distinct) == (1, 1)

        with engine.begin() as conn:
            conn.execute(sa.text(f"DROP TABLE {plain_table}"))
            conn.execute(sa.text(f"DROP TABLE {unique_table}"))
        await db.close()
        engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_mysql_table_sink_batch_writes_live():
    """Batch payloads on a real server: multi-row upsert (the 8.0.20+ alias
    form the driver only renders against a live connection), chunked
    statements in one transaction, replay idempotence, per-row policies and
    executemany update (issue #189)."""

    async def scenario():
        suffix = uuid.uuid4().hex[:8]
        table_name = f"batch_sink_{suffix}"
        engine = _engine()
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    f"CREATE TABLE {table_name} ("
                    "device_key VARCHAR(64) NOT NULL, "
                    "payload VARCHAR(255), "
                    "status VARCHAR(32), "
                    "attempts INT NOT NULL DEFAULT 0, "
                    "updated_at DATETIME(6) NULL, "
                    "UNIQUE KEY uq_device_key (device_key))"
                )
            )

        db = MySQLConnector(os.environ["ONESTEP_MYSQL_DSN"])
        upsert = db.table_sink(
            table=table_name,
            mode="upsert",
            keys=("device_key",),
            update_columns=(
                {"name": "payload", "policy": "backfill"},
                {"name": "status", "policy": "skip_null"},
            ),
            update_expr={"updated_at": "CURRENT_TIMESTAMP(6)"},
            batch_size=40,
        )

        rows = [
            {"device_key": f"dev-{i:03d}", "payload": f"p{i}", "status": None}
            for i in range(300)
        ]
        # 300 rows / batch_size 40 -> 8 chunked multi-row statements.
        await upsert.send(Envelope(body=rows))
        # Replay the same batch: upsert must stay idempotent.
        await upsert.send(Envelope(body=rows))

        with engine.connect() as conn:
            total, distinct = conn.execute(
                sa.text(
                    f"SELECT COUNT(*), COUNT(DISTINCT device_key) FROM {table_name}"
                )
            ).one()

        assert (total, distinct) == (300, 300)

        # Second batch: backfill keeps existing payloads, skip_null keeps
        # NULL statuses untouched, fresh keys insert.
        mixed = [
            {"device_key": "dev-000", "payload": None, "status": "active"},
            {"device_key": "dev-001", "payload": None, "status": None},
            {"device_key": "dev-999", "payload": "fresh", "status": "new"},
        ]
        await upsert.send(Envelope(body=mixed))
        with engine.connect() as conn:
            rows_by_key = {
                key: (payload, status)
                for key, payload, status in conn.execute(
                    sa.text(
                        f"SELECT device_key, payload, status FROM {table_name} "
                        "WHERE device_key IN ('dev-000', 'dev-001', 'dev-999')"
                    )
                ).all()
            }
        assert rows_by_key["dev-000"] == ("p0", "active")  # backfill kept, skip_null overwrote
        assert rows_by_key["dev-001"] == ("p1", None)  # backfill kept, skip_null kept NULL
        assert rows_by_key["dev-999"] == ("fresh", "new")

        update = db.table_sink(
            table=table_name,
            mode="update",
            keys=("device_key",),
            update_columns=("status",),
            batch_size=50,
        )
        await update.send(
            Envelope(
                body=[
                    {"device_key": f"dev-{i:03d}", "status": "synced"} for i in range(100)
                ]
            )
        )
        with engine.connect() as conn:
            synced = conn.execute(
                sa.text(f"SELECT COUNT(*) FROM {table_name} WHERE status = 'synced'")
            ).scalar()
        assert synced == 100

        # A rejected batch (duplicate keys) writes nothing.
        with pytest.raises(ConnectorOperationError):
            await upsert.send(
                Envelope(
                    body=[
                        {"device_key": "dup", "payload": "a", "status": None},
                        {"device_key": "dup", "payload": "b", "status": None},
                    ]
                )
            )
        with engine.connect() as conn:
            dups = conn.execute(
                sa.text(f"SELECT COUNT(*) FROM {table_name} WHERE device_key = 'dup'")
            ).scalar()
        assert dups == 0

        with engine.begin() as conn:
            conn.execute(sa.text(f"DROP TABLE {table_name}"))
        await db.close()
        engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_mysql_table_sink_batch_chunks_oversized_statements_live():
    """Review P1: a batch whose multi-row statement would exceed
    ``max_allowed_packet`` must be chunked, not sent whole.

    Exceeding the packet limit does not return a clean error — MySQL kills the
    connection (2013), which is classified as ``DISCONNECTED`` and therefore
    *retryable*, so the sink would retry a batch that can never succeed while
    re-serializing tens of megabytes each time. Measured before the fix: a
    1000×70KB batch failed on every attempt against the default 64MB limit.
    """

    async def scenario():
        suffix = uuid.uuid4().hex[:8]
        table_name = f"batch_packet_{suffix}"
        engine = _engine()
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    f"CREATE TABLE {table_name} ("
                    "id INT PRIMARY KEY, payload LONGTEXT)"
                )
            )

        db = MySQLConnector(os.environ["ONESTEP_MYSQL_DSN"])
        sink = db.table_sink(
            table=table_name,
            mode="upsert",
            keys=("id",),
            update_columns=("payload",),
        )
        # ~68MB of payload: above the default 64MB max_allowed_packet if sent
        # as one statement, comfortably fine once the byte budget splits it.
        blob = "x" * (70 * 1024)
        rows = [{"id": i, "payload": blob} for i in range(1000)]
        await sink.send(Envelope(body=rows))

        with engine.connect() as conn:
            written = conn.execute(
                sa.text(f"SELECT COUNT(*) FROM {table_name}")
            ).scalar()
        assert written == 1000

        with engine.begin() as conn:
            conn.execute(sa.text(f"DROP TABLE {table_name}"))
        await db.close()
        engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_mysql_table_sink_batch_rejects_secondary_unique_conflict_live():
    """Review P2: with ``PRIMARY KEY(id)`` + ``UNIQUE(email)`` and
    ``keys=("id",)``, two rows sharing an email used to overwrite each other
    silently on MySQL while PostgreSQL raised ``UniqueViolation``."""

    async def scenario():
        suffix = uuid.uuid4().hex[:8]
        table_name = f"batch_uq2_{suffix}"
        engine = _engine()
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    f"CREATE TABLE {table_name} ("
                    "id INT PRIMARY KEY, "
                    "email VARCHAR(64), "
                    "note VARCHAR(64), "
                    "UNIQUE KEY uq_email (email))"
                )
            )

        db = MySQLConnector(os.environ["ONESTEP_MYSQL_DSN"])
        sink = db.table_sink(
            table=table_name,
            mode="upsert",
            keys=("id",),
            update_columns=("email", "note"),
        )
        with pytest.raises(ConnectorOperationError) as excinfo:
            await sink.send(
                Envelope(
                    body=[
                        {"id": 1, "email": "same@x.com", "note": "first"},
                        {"id": 2, "email": "same@x.com", "note": "second"},
                    ]
                )
            )
        assert excinfo.value.kind is ConnectorErrorKind.PERMANENT
        with engine.connect() as conn:
            assert conn.execute(
                sa.text(f"SELECT COUNT(*) FROM {table_name}")
            ).scalar() == 0

        # Distinct values still write normally.
        await sink.send(
            Envelope(
                body=[
                    {"id": 1, "email": "a@x.com", "note": "first"},
                    {"id": 2, "email": "b@x.com", "note": "second"},
                ]
            )
        )
        with engine.connect() as conn:
            assert conn.execute(
                sa.text(f"SELECT COUNT(*) FROM {table_name}")
            ).scalar() == 2

        with engine.begin() as conn:
            conn.execute(sa.text(f"DROP TABLE {table_name}"))
        await db.close()
        engine.dispose()

    asyncio.run(scenario())
