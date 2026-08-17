import asyncio
from pathlib import Path
from datetime import datetime

from onestep_mysql import MySQLConnector, SQLAlchemyStateStore


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


def test_mysql_cursor_store_round_trips_datetime_cursor_values(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'datetime-cursor.db'}"
    cursor_value = datetime(2026, 8, 17, 0, 53, 55, 640000)  # noqa: DTZ001

    async def scenario() -> None:
        db = MySQLConnector(db_url)
        cursor = db.cursor_store(table="onestep_cursor")

        await cursor.save("follow-records", [cursor_value, "u_123"])

        assert await cursor.load("follow-records") == [cursor_value, "u_123"]

        raw_state = SQLAlchemyStateStore(
            engine=db.engine,
            table="onestep_cursor",
            key_column="cursor_key",
            value_column="cursor_value",
        )
        assert await raw_state.load("follow-records") == [
            {
                "__onestep_cursor_type__": "datetime",
                "value": "2026-08-17T00:53:55.640000",
            },
            "u_123",
        ]

        await raw_state.save("legacy", [10, "u_456"])
        assert await cursor.load("legacy") == [10, "u_456"]
        await db.close()

    asyncio.run(scenario())


def test_state_store_does_not_create_asyncio_lock_during_sync_construction(tmp_path: Path) -> None:
    asyncio.set_event_loop(None)
    store = SQLAlchemyStateStore(dsn=f"sqlite:///{tmp_path / 'state-lock.db'}")

    assert store._ready_lock is None

    asyncio.run(store.close())
