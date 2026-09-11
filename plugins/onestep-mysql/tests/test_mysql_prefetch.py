from __future__ import annotations

import asyncio
import math
from pathlib import Path

import pytest
import sqlalchemy as sa
from onestep_mysql import MySQLConnector

from onestep import OneStepApp
from onestep.config import load_app_config
from onestep.resilience import ConnectorOperationError
from onestep.state import InMemoryCursorStore


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'prefetch.db'}"
    engine = sa.create_engine(url)
    table = sa.Table(
        "events",
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("updated_at", sa.Integer, nullable=False),
    )
    try:
        table.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(
                table.insert(), [{"id": i, "updated_at": 10} for i in range(1, 1201)]
            )
    finally:
        engine.dispose()
    return url


def make_source(db, **options):
    return db.incremental(table="events", key="id", cursor=("updated_at",), **options)


def track_sql(source):
    calls = []
    original = source._fetch

    async def tracked(limit):
        rows = await original(limit)
        calls.append((limit, len(rows)))
        return rows

    source._fetch = tracked
    return calls


def test_prefetch_is_opt_in(db_url):
    async def scenario():
        db = MySQLConnector(db_url)
        try:
            source = make_source(db, batch_size=100)
            calls = track_sql(source)
            assert source.prefetch is False
            assert len(await source.fetch(8)) == 8
            assert len(await source.fetch(1)) == 1
            assert calls == [(8, 8), (1, 1)]
        finally:
            await db.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("batch_size", [3, 50, 100, 500])
def test_runtime_prefetch_batches_without_increasing_handler_concurrency(
    db_url, batch_size
):
    async def scenario():
        db = MySQLConnector(db_url)
        source = make_source(
            db, prefetch=True, batch_size=batch_size, poll_interval_s=0.001
        )
        calls = track_sql(source)
        app = OneStepApp("prefetch-concurrency", shutdown_timeout_s=2)
        seen = []
        active = 0
        peak = 0
        completed = asyncio.Event()
        first_wave_full = asyncio.Event()

        @app.task(source=source, concurrency=8)
        async def consume(ctx, row):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 8:
                first_wave_full.set()
            await first_wave_full.wait()
            await asyncio.sleep(0.001)
            seen.append(row["id"])
            active -= 1
            if len(seen) == 1200:
                app.request_drain()
                completed.set()

        serving = asyncio.create_task(app.serve())
        try:
            await asyncio.wait_for(completed.wait(), timeout=10)
            assert (await asyncio.wait_for(app.wait_for_drain(), timeout=2))["drained"]
            assert sorted(seen) == list(range(1, 1201))
            assert peak == 8
            assert all(limit <= batch_size for limit, _ in calls)
            assert sum(count > 0 for _, count in calls) == math.ceil(1200 / batch_size)
            assert await source.state.load(source.state_key) == [10, 1200]
        finally:
            app.request_shutdown()
            await asyncio.wait_for(serving, timeout=2)
            await db.close()

    asyncio.run(scenario())


def test_prefetch_bounds_read_ahead_behind_out_of_order_ack_gap(db_url):
    async def scenario():
        db = MySQLConnector(db_url)
        source = make_source(db, prefetch=True, batch_size=10)
        calls = track_sql(source)
        issued = []
        try:
            for _ in range(4):
                issued.extend(await source.fetch(4))
                assert len(source._prefetched_rows) <= 10
                assert len(source._pending) + len(source._prefetched_rows) <= 14
            assert [d.payload["id"] for d in issued] == list(range(1, 15))
            await asyncio.gather(*(d.ack() for d in issued[1:]))
            assert await source.state.load(source.state_key) is None
            assert await source.fetch(4) == []
            assert calls == [(10, 10), (4, 4)]
            await issued[0].ack()
            assert await source.state.load(source.state_key) == [10, 14]
            assert [d.payload["id"] for d in await source.fetch(4)] == [15, 16, 17, 18]
        finally:
            await source.close()
            await db.close()

    asyncio.run(scenario())


def test_prefetch_retry_precedes_buffered_rows(db_url):
    async def scenario():
        db = MySQLConnector(db_url)
        source = make_source(db, prefetch=True, batch_size=10)
        calls = track_sql(source)
        try:
            first, second = await source.fetch(2)
            await second.ack()
            await first.retry(delay_s=3600)
            assert await source.fetch(2) == []
            assert await source.state.load(source.state_key) is None
            await first.retry(delay_s=0)
            retry = (await source.fetch(2))[0]
            assert retry.payload["id"] == 1
            assert retry.envelope.attempts == 1
            assert await source.fetch(2) == []
            await retry.ack()
            assert await source.state.load(source.state_key) == [10, 2]
            assert [d.payload["id"] for d in await source.fetch(2)] == [3, 4]
            assert calls == [(10, 10)]
        finally:
            await source.close()
            await db.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("terminal", [False, True])
def test_prefetch_rechecks_failure_fence_after_sql(db_url, terminal):
    async def scenario():
        db = MySQLConnector(db_url)
        source = make_source(db, prefetch=True, batch_size=2)
        entered = asyncio.Event()
        release = asyncio.Event()
        original = source._fetch
        pending = None
        try:
            first, second = await source.fetch(2)

            async def blocking(limit):
                rows = await original(limit)
                entered.set()
                await release.wait()
                return rows

            source._fetch = blocking
            pending = asyncio.create_task(source.fetch(2))
            await asyncio.wait_for(entered.wait(), timeout=1)
            if terminal:
                await first.fail(RuntimeError("failed"))
            else:
                await first.retry(delay_s=0)
            release.set()
            if terminal:
                with pytest.raises(ConnectorOperationError):
                    await pending
            else:
                assert await pending == []
                retry = (await source.fetch(2))[0]
                assert retry.payload["id"] == 1
                await second.ack()
                await retry.ack()
                assert [d.payload["id"] for d in await source.fetch(2)] == [3, 4]
        finally:
            release.set()
            if pending is not None:
                await asyncio.gather(pending, return_exceptions=True)
            await source.close()
            await db.close()

    asyncio.run(scenario())


def test_prefetch_serializes_concurrent_fetches(db_url):
    async def scenario():
        db = MySQLConnector(db_url)
        source = make_source(db, prefetch=True, batch_size=10)
        calls = track_sql(source)
        try:
            batches = await asyncio.gather(source.fetch(4), source.fetch(4))
            assert sorted(d.payload["id"] for batch in batches for d in batch) == list(
                range(1, 9)
            )
            assert calls == [(10, 10)]
        finally:
            await source.close()
            await db.close()

    asyncio.run(scenario())


def test_cancelled_prefetch_does_not_advance_or_lose_rows(db_url):
    async def scenario():
        db = MySQLConnector(db_url)
        source = make_source(db, prefetch=True, batch_size=10)
        entered = asyncio.Event()
        release = asyncio.Event()
        original = source._fetch

        async def blocking(limit):
            rows = await original(limit)
            entered.set()
            await release.wait()
            return rows

        source._fetch = blocking
        pending = asyncio.create_task(source.fetch(3))
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert await source.state.load(source.state_key) is None
            release.set()
            assert [d.payload["id"] for d in await source.fetch(3)] == [1, 2, 3]
            assert [d.payload["id"] for d in await source.fetch(3)] == [4, 5, 6]
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)
            await source.close()
            await db.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("control", ["drain", "pause", "shutdown"])
def test_prefetch_stop_controls_keep_unissued_rows_replayable(db_url, control):
    async def scenario():
        db = MySQLConnector(db_url)
        state = InMemoryCursorStore()
        source = make_source(db, prefetch=True, batch_size=100, state=state)
        app = OneStepApp("prefetch-stop", shutdown_timeout_s=2)
        started = asyncio.Event()
        release = asyncio.Event()
        seen = []

        @app.task(source=source, name="consume", concurrency=8)
        async def consume(ctx, row):
            seen.append(row["id"])
            if len(seen) == 8:
                started.set()
            await release.wait()

        serving = asyncio.create_task(app.serve())
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            assert len(source._prefetched_rows) == 92
            if control == "drain":
                app.request_drain()
            elif control == "pause":
                app.request_task_pause("consume")
            else:
                app.request_shutdown()
            release.set()
            if control == "drain":
                assert (await asyncio.wait_for(app.wait_for_drain(), timeout=2))[
                    "drained"
                ]
            elif control == "pause":
                assert (
                    await asyncio.wait_for(
                        app.wait_for_task_pause("consume"), timeout=2
                    )
                )["paused"]
            app.request_shutdown()
            await asyncio.wait_for(serving, timeout=2)
            assert sorted(seen) == list(range(1, 9))
            assert await state.load(source.state_key) == [10, 8]
            assert len(source._prefetched_rows) == 0
            restarted = make_source(db, prefetch=True, batch_size=100, state=state)
            assert [d.payload["id"] for d in await restarted.fetch(8)] == list(
                range(9, 17)
            )
            await restarted.close()
        finally:
            release.set()
            app.request_shutdown()
            await asyncio.wait_for(serving, timeout=2)
            await db.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("prefetch", [False, True])
def test_yaml_strict_accepts_prefetch(db_url, prefetch):
    app = load_app_config(
        {
            "apiVersion": "onestep/v1alpha1",
            "kind": "App",
            "app": {"name": "prefetch"},
            "resources": {
                "db": {"type": "mysql", "dsn": db_url},
                "rows": {
                    "type": "mysql_incremental",
                    "connector": "db",
                    "table": "events",
                    "key": "id",
                    "cursor": ["updated_at"],
                    "batch_size": 100,
                    "prefetch": prefetch,
                },
            },
            "tasks": [],
        },
        strict=True,
    )
    assert app.resources["rows"].prefetch is prefetch


@pytest.mark.parametrize(
    "options, error",
    [
        ({"prefetch": "true"}, TypeError),
        ({"prefetch": 1}, TypeError),
        ({"prefetch": True, "batch_size": 0}, ValueError),
        ({"prefetch": True, "batch_size": -1}, ValueError),
        ({"prefetch": True, "batch_size": True}, ValueError),
        ({"prefetch": True, "batch_size": 2.5}, ValueError),
    ],
)
def test_prefetch_rejects_invalid_options(db_url, options, error):
    db = MySQLConnector(db_url)
    with pytest.raises(error, match="prefetch|batch_size"):
        make_source(db, **options)


def test_prefetch_pause_at_fetch_completion_releases_and_resumes_all_rows(db_url):
    async def scenario():
        db = MySQLConnector(db_url)
        source = make_source(db, prefetch=True, batch_size=100, poll_interval_s=0.001)
        app = OneStepApp("prefetch-completed-stop", shutdown_timeout_s=2)
        original = source.fetch
        pause_once = True
        pause_requested = asyncio.Event()
        completed = asyncio.Event()
        seen = []

        async def pause_at_completion(limit):
            nonlocal pause_once
            deliveries = await original(limit)
            if pause_once:
                pause_once = False
                app.request_task_pause("consume")
                pause_requested.set()
            return deliveries

        source.fetch = pause_at_completion

        @app.task(source=source, name="consume", concurrency=8)
        async def consume(ctx, row):
            seen.append(row["id"])
            if len(seen) == 1200:
                app.request_drain()
                completed.set()

        serving = asyncio.create_task(app.serve())
        try:
            await asyncio.wait_for(pause_requested.wait(), timeout=2)
            assert (
                await asyncio.wait_for(app.wait_for_task_pause("consume"), timeout=2)
            )["paused"]
            assert seen == []
            assert await source.state.load(source.state_key) is None
            app.request_task_resume("consume")
            await asyncio.wait_for(completed.wait(), timeout=10)
            assert (await asyncio.wait_for(app.wait_for_drain(), timeout=2))["drained"]
            assert sorted(seen) == list(range(1, 1201))
        finally:
            app.request_shutdown()
            await asyncio.wait_for(serving, timeout=2)
            await db.close()

    asyncio.run(scenario())
