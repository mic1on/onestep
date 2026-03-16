import asyncio
import os
import uuid

import pytest
import sqlalchemy as sa

from onestep import MySQLConnector


if not os.getenv("ONESTEP_MYSQL_DSN"):
    pytest.skip("set ONESTEP_MYSQL_DSN to run MySQL integration tests", allow_module_level=True)


def _engine():
    return sa.create_engine(os.environ["ONESTEP_MYSQL_DSN"], future=True)


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

        with db.engine.begin() as conn:
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

        with restarted.engine.begin() as conn:
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
