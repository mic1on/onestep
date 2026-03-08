import asyncio
from pathlib import Path

import sqlalchemy as sa

from onestep import MySQLConnector


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

        with restarted_db.engine.begin() as conn:
            conn.execute(sa.insert(users), [{"id": 3, "name": "C", "updated_at": 11, "deleted": 0}])

        next_batch = await restarted_source.fetch(10)
        assert [item.payload["id"] for item in next_batch] == [3]
        await next_batch[0].ack()
        assert await restarted_state.load("users-sync") == [11, 3]
        await restarted_db.close()

    asyncio.run(scenario())
