"""Recovery against real row events and a persisted MySQL cursor."""

import asyncio
import os
import uuid

import pytest
import sqlalchemy as sa
from onestep_mysql import MySQLConnector

if not os.getenv("ONESTEP_MYSQL_DSN"):
    pytest.skip(
        "set ONESTEP_MYSQL_DSN to run MySQL integration tests", allow_module_level=True
    )


async def _fetch(source, count):
    deliveries = []
    for _ in range(50):
        deliveries.extend(await source.fetch(count - len(deliveries)))
        if len(deliveries) == count:
            return deliveries
        await asyncio.sleep(0.1)
    raise AssertionError(f"expected {count} deliveries, got {len(deliveries)}")


@pytest.mark.integration
@pytest.mark.parametrize("action", ["retry", "release_unstarted", "fail"])
def test_binlog_settlement_recovers_gap_and_persists_complete_events_live(action):
    async def scenario():
        suffix = uuid.uuid4().hex[:8]
        table = f"binlog_recovery_{suffix}"
        cursor_table = f"binlog_recovery_cursor_{suffix}"
        dsn = os.environ["ONESTEP_MYSQL_DSN"]
        engine = sa.create_engine(dsn.replace("+asyncmy", "+pymysql"))
        db = MySQLConnector(dsn)
        state = db.cursor_store(table=cursor_table)
        source = None

        def new_source():
            return db.binlog(
                server_id=18493,
                schemas=(engine.url.database,),
                tables=(table,),
                state=state,
                state_key=table,
                batch_size=10,
                poll_interval_s=0.01,
            )

        try:
            with engine.begin() as conn:
                conn.execute(sa.text(f"CREATE TABLE `{table}` (id INT PRIMARY KEY)"))
            source = new_source()
            await source.open()
            initial = await state.load(table)
            with engine.begin() as conn:
                conn.execute(sa.text(f"INSERT INTO `{table}` VALUES (1), (2), (3)"))
                conn.execute(sa.text(f"INSERT INTO `{table}` VALUES (4)"))

            first = (await _fetch(source, 1))[0]
            later = await _fetch(source, 3)
            assert [d.payload["values"]["id"] for d in [first, *later]] == [1, 2, 3, 4]
            assert first.payload["binlog"]["pos"] == later[1].payload["binlog"]["pos"]
            assert later[2].payload["binlog"]["pos"] > later[1].payload["binlog"]["pos"]
            for delivery in reversed(later):
                await delivery.ack()
            assert await state.load(table) == initial

            await getattr(first, action)()
            if action != "fail":
                assert await state.load(table) == initial
                recovered = (await _fetch(source, 1))[0]
                assert recovered.payload == first.payload
                assert recovered.envelope.attempts == (1 if action == "retry" else 0)
                await recovered.ack()

            saved = await state.load(table)
            assert saved == {
                "file": later[2].payload["binlog"]["file"],
                "pos": later[2].payload["binlog"]["pos"],
            }
            await source.close()
            source = new_source()
            await source.open()
            with engine.begin() as conn:
                conn.execute(sa.text(f"INSERT INTO `{table}` VALUES (5)"))
            resumed = (await _fetch(source, 1))[0]
            assert resumed.payload["values"]["id"] == 5
            await resumed.ack()
        finally:
            if source is not None:
                await source.close()
            await db.close()
            with engine.begin() as conn:
                conn.execute(sa.text(f"DROP TABLE IF EXISTS `{table}`"))
                conn.execute(sa.text(f"DROP TABLE IF EXISTS `{cursor_table}`"))
            engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_blocking_binlog_can_pause_resume_and_close_on_idle_live():
    async def scenario():
        suffix = uuid.uuid4().hex[:8]
        table = f"binlog_idle_{suffix}"
        dsn = os.environ["ONESTEP_MYSQL_DSN"]
        engine = sa.create_engine(dsn.replace("+asyncmy", "+pymysql"))
        db = MySQLConnector(dsn)
        source = db.binlog(
            server_id=18494,
            schemas=(engine.url.database,),
            tables=(table,),
            blocking=True,
            poll_interval_s=0.01,
        )
        pending = None
        try:
            with engine.begin() as conn:
                conn.execute(sa.text(f"CREATE TABLE `{table}` (id INT PRIMARY KEY)"))
            await source.open()
            pending = asyncio.create_task(source.fetch(1))
            await asyncio.sleep(0.1)
            assert not pending.done(), "blocking fetch should wait for a row"
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(pending, 2)
            with engine.begin() as conn:
                conn.execute(sa.text(f"INSERT INTO `{table}` VALUES (1)"))
            (delivery,) = await asyncio.wait_for(source.fetch(1), 2)
            assert delivery.payload["values"]["id"] == 1
            await delivery.ack()
            pending = asyncio.create_task(source.fetch(1))
            await asyncio.sleep(0.1)
            assert not pending.done()
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(pending, 2)
            await asyncio.wait_for(source.close(), 2)
        finally:
            if pending is not None:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await source.close()
            await db.close()
            with engine.begin() as conn:
                conn.execute(sa.text(f"DROP TABLE IF EXISTS `{table}`"))
            engine.dispose()

    asyncio.run(scenario())
