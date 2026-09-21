import asyncio
import os
import uuid
from unittest import mock

import pytest
import sqlalchemy as sa
from onestep.envelope import Envelope
from onestep.resilience import ConnectorOperationError
from onestep_postgres import PostgresConnector
from sqlalchemy.ext.asyncio import AsyncConnection

if not os.getenv("ONESTEP_POSTGRES_DSN"):
    pytest.skip("set ONESTEP_POSTGRES_DSN to run PostgreSQL integration tests", allow_module_level=True)


def _engine():
    return sa.create_engine(os.environ["ONESTEP_POSTGRES_DSN"], future=True)


@pytest.mark.integration
def test_postgres_table_queue_claim_ack_and_retry_live():
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

        db = PostgresConnector(os.environ["ONESTEP_POSTGRES_DSN"])
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

        async with db.engine.begin() as conn:
            rows = (await conn.execute(sa.select(orders).order_by(orders.c.id))).mappings().all()
        assert [dict(row)["status"] for row in rows] == [1, 0]

        await db.close()
        metadata.drop_all(engine)
        engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_postgres_incremental_cursor_recovers_after_restart_live():
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

        db = PostgresConnector(os.environ["ONESTEP_POSTGRES_DSN"])
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

        restarted = PostgresConnector(os.environ["ONESTEP_POSTGRES_DSN"])
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

        async with restarted.engine.begin() as conn:
            await conn.execute(sa.insert(users), [{"id": 3, "name": "C", "updated_at": 11, "deleted": 0}])

        next_batch = await restarted_source.fetch(10)
        assert [item.payload["id"] for item in next_batch] == [3]
        await next_batch[0].ack()
        assert await restarted_cursor.load("users-sync") == [11, 3]

        await restarted.close()
        with engine.begin() as conn:
            conn.execute(sa.text(f'DROP TABLE IF EXISTS "{cursor_table_name}"'))
        metadata.drop_all(engine)
        engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_postgres_table_sink_upserts_live():
    async def scenario():
        suffix = uuid.uuid4().hex[:8]
        table_name = f"processed_{suffix}"
        engine = _engine()
        metadata = sa.MetaData()
        processed = sa.Table(
            table_name,
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("payload", sa.String(255), nullable=False),
            sa.Column("status", sa.String(255), nullable=False),
        )
        metadata.create_all(engine)

        db = PostgresConnector(os.environ["ONESTEP_POSTGRES_DSN"])
        sink = db.table_sink(table=table_name, mode="upsert", keys=("id",))

        from onestep.envelope import Envelope

        await sink.send(Envelope(body={"id": 1, "payload": "alpha", "status": "new"}))
        await sink.send(Envelope(body={"id": 1, "payload": "alpha", "status": "done"}))

        async with db.engine.begin() as conn:
            rows = (await conn.execute(sa.select(processed).order_by(processed.c.id))).mappings().all()
        assert [dict(row) for row in rows] == [{"id": 1, "payload": "alpha", "status": "done"}]

        await db.close()
        metadata.drop_all(engine)
        engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_postgres_table_queue_complete_atomic_live():
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

        db = PostgresConnector(os.environ["ONESTEP_POSTGRES_DSN"])
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

        async with db.engine.begin() as conn:
            row = (
                await conn.execute(
                    sa.select(orders.c.status, orders.c.score).order_by(orders.c.id)
                )
            ).first()
        assert row == (1, 42)

        await db.close()
        metadata.drop_all(engine)
        engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_postgres_table_queue_complete_midtransaction_failure_rolls_back_live():
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

        db = PostgresConnector(os.environ["ONESTEP_POSTGRES_DSN"])
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

        async with db.engine.begin() as conn:
            row = (
                await conn.execute(
                    sa.select(orders.c.status, orders.c.score).order_by(orders.c.id)
                )
            ).first()
        # Neither the business value nor the ack survived the rollback.
        assert row == (9, None)

        # The delivery stays usable: retrying complete() after the injected
        # failure succeeds and applies both writes atomically.
        await delivery.complete({"score": 42})
        assert delivery._complete_ack_applied is True
        async with db.engine.begin() as conn:
            row = (
                await conn.execute(
                    sa.select(orders.c.status, orders.c.score).order_by(orders.c.id)
                )
            ).first()
        assert row == (1, 42)

        await db.close()
        metadata.drop_all(engine)
        engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_postgres_table_sink_batch_writes_live():
    """Batch payloads on a real server: executemany upsert (psycopg3
    pipeline), chunked calls in one transaction, replay idempotence,
    per-row policies and executemany update (issue #189)."""

    async def scenario():
        suffix = uuid.uuid4().hex[:8]
        table_name = f"batch_sink_{suffix}"
        engine = _engine()
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    f"CREATE TABLE {table_name} ("
                    "device_key VARCHAR(64) PRIMARY KEY, "
                    "payload VARCHAR(255), "
                    "status VARCHAR(32))"
                )
            )

        db = PostgresConnector(os.environ["ONESTEP_POSTGRES_DSN"])
        upsert = db.table_sink(
            table=table_name,
            mode="upsert",
            keys=("device_key",),
            update_columns=(
                {"name": "payload", "policy": "backfill"},
                {"name": "status", "policy": "skip_null"},
            ),
            batch_size=40,
        )

        rows = [
            {"device_key": f"dev-{i:03d}", "payload": f"p{i}", "status": None}
            for i in range(300)
        ]
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
        assert rows_by_key["dev-000"] == ("p0", "active")
        assert rows_by_key["dev-001"] == ("p1", None)
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

        # A rejected batch (duplicate keys) writes nothing — PostgreSQL
        # would otherwise fail mid-statement with "cannot affect row a
        # second time", so the pre-write rejection keeps it deterministic.
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
