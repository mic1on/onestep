from __future__ import annotations

import asyncio
import time
from pathlib import Path

import sqlalchemy as sa

from onestep import OneStepApp
from onestep_mysql import MySQLConnector


def _build_table_queue_db(tmp_path: Path) -> tuple[str, sa.Table]:
    db_url = f"sqlite:///{tmp_path / 'queue.db'}"
    engine = sa.create_engine(db_url, future=True)
    metadata = sa.MetaData()
    orders = sa.Table(
        "orders",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("payload", sa.String, nullable=False),
        sa.Column("status", sa.Integer, nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(sa.insert(orders), [{"id": 1, "payload": "A", "status": 0}])
    engine.dispose()
    return db_url, orders


def _load_order_rows(db_url: str, orders: sa.Table) -> list[tuple[int, int]]:
    engine = sa.create_engine(db_url, future=True)
    with engine.begin() as conn:
        rows = conn.execute(sa.select(orders.c.id, orders.c.status).order_by(orders.c.id)).all()
    engine.dispose()
    return list(rows)


def test_table_queue_drain_releases_claimed_rows_when_fetch_is_stopped_contract(tmp_path: Path) -> None:
    db_url, orders = _build_table_queue_db(tmp_path)

    async def scenario() -> list[int]:
        app = OneStepApp("table-queue-drain-contract", shutdown_timeout_s=1.0)
        db = MySQLConnector(db_url)
        source = db.table_queue(
            table="orders",
            key="id",
            where="status = 0",
            claim={"status": 9},
            ack={"status": 1},
            nack={"status": 0},
            batch_size=1,
            poll_interval_s=0.01,
        )
        original_fetch = source._fetch_sync

        def slow_fetch(limit: int):
            rows = original_fetch(limit)
            time.sleep(0.2)
            return rows

        source._fetch_sync = slow_fetch
        seen: list[int] = []

        @app.task(source=source, concurrency=1)
        async def consume(ctx, row):
            seen.append(row["id"])

        async def controller() -> None:
            await asyncio.sleep(0.05)
            app.request_drain()
            await asyncio.wait_for(app.wait_for_drain(), timeout=1.0)
            app.request_shutdown()

        await asyncio.wait_for(asyncio.gather(app.serve(), controller()), timeout=2.0)
        await db.close()
        return seen

    seen = asyncio.run(scenario())

    assert seen == []
    assert _load_order_rows(db_url, orders) == [(1, 0)]


def test_table_queue_pause_releases_claimed_rows_when_fetch_is_stopped_contract(tmp_path: Path) -> None:
    db_url, orders = _build_table_queue_db(tmp_path)

    async def scenario() -> list[int]:
        app = OneStepApp("table-queue-pause-contract", shutdown_timeout_s=1.0)
        db = MySQLConnector(db_url)
        source = db.table_queue(
            table="orders",
            key="id",
            where="status = 0",
            claim={"status": 9},
            ack={"status": 1},
            nack={"status": 0},
            batch_size=1,
            poll_interval_s=0.01,
        )
        original_fetch = source._fetch_sync

        def slow_fetch(limit: int):
            rows = original_fetch(limit)
            time.sleep(0.2)
            return rows

        source._fetch_sync = slow_fetch
        seen: list[int] = []

        @app.task(source=source, concurrency=1)
        async def consume(ctx, row):
            seen.append(row["id"])

        async def controller() -> None:
            await asyncio.sleep(0.05)
            app.request_task_pause("consume")
            await asyncio.wait_for(app.wait_for_task_pause("consume"), timeout=1.0)
            app.request_shutdown()

        await asyncio.wait_for(asyncio.gather(app.serve(), controller()), timeout=2.0)
        await db.close()
        return seen

    seen = asyncio.run(scenario())

    assert seen == []
    assert _load_order_rows(db_url, orders) == [(1, 0)]


def test_table_queue_shutdown_releases_claimed_rows_when_fetch_is_stopped_contract(tmp_path: Path) -> None:
    db_url, orders = _build_table_queue_db(tmp_path)

    async def scenario() -> list[int]:
        app = OneStepApp("table-queue-shutdown-contract", shutdown_timeout_s=1.0)
        db = MySQLConnector(db_url)
        source = db.table_queue(
            table="orders",
            key="id",
            where="status = 0",
            claim={"status": 9},
            ack={"status": 1},
            nack={"status": 0},
            batch_size=1,
            poll_interval_s=0.01,
        )
        original_fetch = source._fetch_sync

        def slow_fetch(limit: int):
            rows = original_fetch(limit)
            time.sleep(0.2)
            return rows

        source._fetch_sync = slow_fetch
        seen: list[int] = []

        @app.task(source=source, concurrency=1)
        async def consume(ctx, row):
            seen.append(row["id"])

        async def controller() -> None:
            await asyncio.sleep(0.05)
            app.request_shutdown()

        await asyncio.wait_for(asyncio.gather(app.serve(), controller()), timeout=2.0)
        await db.close()
        return seen

    seen = asyncio.run(scenario())

    assert seen == []
    assert _load_order_rows(db_url, orders) == [(1, 0)]
