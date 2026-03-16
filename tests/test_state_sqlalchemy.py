import asyncio
from pathlib import Path

from onestep import MySQLConnector, SQLAlchemyStateStore


def test_sqlalchemy_state_store_persists_across_instances(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'state.db'}"

    async def scenario() -> None:
        store = SQLAlchemyStateStore(dsn=db_url)
        await store.save("jobs:last-run", {"cursor": [10, 2], "status": "ok"})
        assert await store.load("jobs:last-run") == {"cursor": [10, 2], "status": "ok"}
        await store.close()

        reloaded = SQLAlchemyStateStore(dsn=db_url)
        assert await reloaded.load("jobs:last-run") == {"cursor": [10, 2], "status": "ok"}
        await reloaded.delete("jobs:last-run")
        assert await reloaded.load("jobs:last-run") is None
        await reloaded.close()

    asyncio.run(scenario())


def test_mysql_connector_builds_shared_state_store(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'connector-state.db'}"

    async def scenario() -> None:
        db = MySQLConnector(db_url)
        state = db.state_store(table="app_state")
        cursor = db.cursor_store(table="app_cursor")

        assert state.engine is db.engine
        assert cursor.engine is db.engine

        await state.save("service:mode", {"value": "active"})
        await cursor.save("users", [10, 2])

        assert await state.load("service:mode") == {"value": "active"}
        assert await cursor.load("users") == [10, 2]
        await db.close()

    asyncio.run(scenario())
