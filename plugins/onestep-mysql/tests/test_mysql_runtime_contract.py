from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest
import sqlalchemy as sa

from onestep.testing import (
    ClaimedSourceHarness,
    StopControl,
    run_claimed_source_stop_contract,
)
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


@pytest.mark.parametrize("control", list(StopControl))
def test_table_queue_stop_controls_release_claimed_rows(
    tmp_path: Path,
    control: StopControl,
) -> None:
    db_url, orders = _build_table_queue_db(tmp_path)

    async def scenario() -> None:
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
        fetch_started = threading.Event()
        release_fetch = threading.Event()
        original_fetch = source._fetch_sync

        def blocking_fetch(limit: int):
            rows = original_fetch(limit)
            fetch_started.set()
            if not release_fetch.wait(timeout=1.0):
                raise TimeoutError("test did not release the MySQL table queue fetch")
            return rows

        source._fetch_sync = blocking_fetch

        def assert_released() -> None:
            assert _load_order_rows(db_url, orders) == [(1, 0)]

        try:
            await run_claimed_source_stop_contract(
                ClaimedSourceHarness(
                    source=source,
                    wait_for_fetch_started=lambda: _wait_for_thread_event(fetch_started),
                    release_fetch=release_fetch.set,
                    assert_released=assert_released,
                ),
                control,
            )
        finally:
            release_fetch.set()
            await db.close()

    asyncio.run(scenario())


async def _wait_for_thread_event(event: threading.Event) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while not event.is_set():
        if loop.time() >= deadline:
            raise TimeoutError("table queue fetch did not start")
        await asyncio.sleep(0.001)
