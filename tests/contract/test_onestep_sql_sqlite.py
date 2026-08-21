"""Public contract tests for the SQLite backend of onestep-sql (issue #133).

These pin the newly public SQLite resource types and their observable
behaviour. SQLite is file/embedded (not server) so it exercises the same
shared stores and source/sink classes as mysql/postgres, but with two
genuinely different pieces:

* the asyncio driver mapping maps ``sqlite`` / ``sqlite+pysqlite`` onto
  ``sqlite+aiosqlite`` (and a bare file path or ``:memory:`` is accepted and
  receives the same engine tuning — 30s busy timeout, no ``pool_pre_ping``);
* the table queue claims rows without ``SELECT ... FOR UPDATE`` (SQLite does not
  support row locks) by issuing one atomic ``UPDATE ... RETURNING`` statement, so
  the write lock is held for the whole claim and concurrent consumers can never
  double-claim the same rows. The returned body is the post-claim row, matching
  the mysql/postgres envelope contract.

No server required: everything runs on a temporary sqlite database.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import sqlalchemy as sa
from onestep_sql import sqlite as sqlite_pkg
from onestep_sql.sqlite import resilience as sqlite_resilience
from onestep_sql.sqlite import state_sqlalchemy as sqlite_state

from onestep.envelope import Envelope
from onestep.resource_registry import ResourceRegistry
from onestep.state import InMemoryCursorStore

SQLITE_REQUIRED_PUBLIC = {
    "SQLiteConnector",
    "TableSink",
    "TableQueueSource",
    "TableQueueDelivery",
    "IncrementalTableSource",
    "IncrementalDelivery",
    "SQLAlchemyStateStore",
    "SQLAlchemyCursorStore",
    "classify_sqlalchemy_error",
    "register",
    "register_resources",
}

EXPECTED_TYPES = {
    "sqlite",
    "sqlite_state_store",
    "sqlite_cursor_store",
    "sqlite_table_queue",
    "sqlite_incremental",
    "sqlite_table_sink",
}


def test_sqlite_public_api_surface() -> None:
    missing = SQLITE_REQUIRED_PUBLIC - set(dir(sqlite_pkg))
    assert not missing, f"onestep_sql.sqlite missing public symbols: {sorted(missing)}"
    assert sqlite_pkg.register is sqlite_pkg.register_resources


def test_sqlite_registers_six_types() -> None:
    registry = ResourceRegistry()
    sqlite_pkg.register_resources(registry)
    types = {e.type for e in registry.catalog_entries()}
    assert types == EXPECTED_TYPES
    assert len(types) == 6


def test_sqlite_has_no_binlog_or_execution() -> None:
    # SQLite is embedded: no binlog CDC and no tracked execution backend.
    assert not hasattr(sqlite_pkg, "BinlogSource")
    assert not hasattr(sqlite_pkg, "PostgresExecutionSource")
    registry = ResourceRegistry()
    sqlite_pkg.register_resources(registry)
    types = {e.type for e in registry.catalog_entries()}
    assert "sqlite_binlog" not in types
    assert "sqlite_execution_source" not in types


def test_async_dsn_maps_sqlite_drivers() -> None:
    assert sqlite_state._async_dsn("sqlite:///x.db") == "sqlite+aiosqlite:///x.db"
    assert sqlite_state._async_dsn("sqlite+pysqlite:///x.db") == "sqlite+aiosqlite:///x.db"
    # Cross-dialect DSNs are passed through unchanged.
    assert sqlite_state._async_dsn("mysql://u:p@h/db") == "mysql://u:p@h/db"
    assert sqlite_state._async_dsn("postgresql://u:p@h/db") == "postgresql://u:p@h/db"


def test_bare_file_path_is_accepted() -> None:
    connector = sqlite_pkg.SQLiteConnector("/tmp/does-not-need-to-exist-yet.db")
    assert connector.engine is not None
    asyncio.run(connector.close())


def test_install_hint_is_per_backend() -> None:
    assert sqlite_state.SQLAlchemyStateStore._install_hint == "Install onestep-sql with the 'sqlite' extra."
    assert sqlite_state.SQLAlchemyStateStore._resolve_async_driver("sqlite") == "sqlite+aiosqlite"


def test_error_classification_table_is_per_dialect() -> None:
    op_error = sa.exc.OperationalError("stmt", {}, Exception("database is locked"))
    assert (
        sqlite_resilience.classify_sqlalchemy_error(op_error)
        is __import__("onestep.resilience", fromlist=["ConnectorErrorKind"]).ConnectorErrorKind.TRANSIENT
    )
    # Non-SQLAlchemy exceptions are not classified (gated on the DBAPI hierarchy).
    assert sqlite_resilience.classify_sqlalchemy_error(ValueError("boom")) is None


def test_connector_state_and_cursor_stores_share_engine(tmp_path: Path) -> None:
    connector = sqlite_pkg.SQLiteConnector(f"sqlite:///{tmp_path / 'c.db'}")

    async def scenario() -> None:
        state_store = connector.state_store(table="app_state")
        cursor_store = connector.cursor_store(table="app_cursor")
        assert state_store.engine is connector.engine
        assert cursor_store.engine is connector.engine
        await state_store.save("k", {"v": 1})
        await cursor_store.save("k", [1, 2])
        assert await state_store.load("k") == {"v": 1}
        assert await cursor_store.load("k") == [1, 2]
        await connector.close()

    asyncio.run(scenario())


def test_table_sink_insert_and_upsert(tmp_path: Path) -> None:
    connector = sqlite_pkg.SQLiteConnector(f"sqlite:///{tmp_path / 's.db'}")

    async def scenario() -> None:
        async with connector.engine.begin() as conn:
            await conn.run_sync(
                lambda s: s.execute(
                    sa.text("CREATE TABLE jobs (id INTEGER PRIMARY KEY, status TEXT, payload TEXT)")
                )
            )
        sink = connector.table_sink(table="jobs", mode="insert")
        await sink.send(Envelope(body={"id": 1, "status": "new", "payload": "a"}))
        upsert = connector.table_sink(table="jobs", mode="upsert", keys=("id",))
        await upsert.send(Envelope(body={"id": 1, "status": "updated", "payload": "b"}))
        async with connector.engine.begin() as conn:
            row = (await conn.execute(sa.text("SELECT status, payload FROM jobs WHERE id=1"))).first()
        assert row == ("updated", "b")
        await connector.close()

    asyncio.run(scenario())


def test_table_queue_claims_without_for_update(tmp_path: Path) -> None:
    connector = sqlite_pkg.SQLiteConnector(f"sqlite:///{tmp_path / 'q.db'}")

    async def scenario() -> None:
        async with connector.engine.begin() as conn:
            await conn.run_sync(
                lambda s: s.execute(
                    sa.text("CREATE TABLE jobs (id INTEGER PRIMARY KEY, status TEXT)")
                )
            )
            await conn.execute(sa.text("INSERT INTO jobs (id, status) VALUES (1, 'new'), (2, 'new')"))
        queue = connector.table_queue(
            table="jobs",
            key="id",
            where="status='new'",
            claim={"status": "claimed"},
            ack={"status": "done"},
            nack={"status": "new"},
        )
        deliveries = await queue.fetch(10)
        assert len(deliveries) == 2
        await deliveries[0].ack()
        async with connector.engine.begin() as conn:
            done = (await conn.execute(sa.text("SELECT COUNT(*) FROM jobs WHERE status='done'"))).scalar()
            claimed = (await conn.execute(sa.text("SELECT COUNT(*) FROM jobs WHERE status='claimed'"))).scalar()
        assert done == 1
        assert claimed == 1
        await connector.close()

    asyncio.run(scenario())


def test_table_queue_delivers_post_claim_body(tmp_path: Path) -> None:
    """The returned envelope mirrors the post-claim row (status='claimed'), so the
    SQLite queue body matches the mysql/postgres contract (issue #4)."""
    connector = sqlite_pkg.SQLiteConnector(f"sqlite:///{tmp_path / 'post.db'}")

    async def scenario() -> None:
        async with connector.engine.begin() as conn:
            await conn.run_sync(
                lambda s: s.execute(
                    sa.text("CREATE TABLE jobs (id INTEGER PRIMARY KEY, status TEXT)")
                )
            )
            await conn.execute(sa.text("INSERT INTO jobs (id, status) VALUES (1, 'new')"))
        queue = connector.table_queue(
            table="jobs",
            key="id",
            where="status='new'",
            claim={"status": "claimed"},
            ack={"status": "done"},
            nack={"status": "new"},
        )
        deliveries = await queue.fetch(10)
        assert len(deliveries) == 1
        assert deliveries[0].envelope.body["status"] == "claimed"
        await connector.close()

    asyncio.run(scenario())


def test_table_queue_no_double_claim_under_concurrent_consumers(tmp_path: Path) -> None:
    """Two consumers polling the same file must never deliver the same row twice.

    The previous implementation selected rows and then claimed them in separate
    statements; a consumer whose SELECT landed between another consumer's SELECT
    and its commit re-claimed the same batch. The atomic UPDATE ... RETURNING
    claim holds the write lock for the whole claim, so the second consumer's
    subquery only sees already-claimed rows (issue #1).
    """
    db = tmp_path / "shared.db"
    seed = sqlite_pkg.SQLiteConnector(f"sqlite:///{db}")

    async def setup() -> None:
        async with seed.engine.begin() as conn:
            await conn.run_sync(
                lambda s: s.execute(
                    sa.text("CREATE TABLE jobs (id INTEGER PRIMARY KEY, status TEXT)")
                )
            )
            await conn.execute(
                sa.text(
                    "INSERT INTO jobs (id, status) VALUES "
                    + ", ".join(f"({i}, 'new')" for i in range(1, 101))
                )
            )

    asyncio.run(setup())

    consumer_a = sqlite_pkg.SQLiteConnector(f"sqlite:///{db}")
    consumer_b = sqlite_pkg.SQLiteConnector(f"sqlite:///{db}")
    delivered: list[int] = []

    async def consume(connector: sqlite_pkg.SQLiteConnector) -> None:
        queue = connector.table_queue(
            table="jobs",
            key="id",
            where="status='new'",
            claim={"status": "claimed"},
            ack={"status": "done"},
            nack={"status": "new"},
            batch_size=5,
        )
        for _ in range(40):
            batch = await queue.fetch(5)
            for delivery in batch:
                delivered.append(delivery.envelope.body["id"])
            for delivery in batch:
                await delivery.ack()

    async def run() -> None:
        await asyncio.gather(consume(consumer_a), consume(consumer_b))

    asyncio.run(run())

    assert sorted(delivered) == list(range(1, 101))
    assert len(delivered) == len(set(delivered)), f"double-claimed rows: {sorted(set(delivered))}"
    asyncio.run(consumer_a.close())
    asyncio.run(consumer_b.close())
    asyncio.run(seed.close())


def test_bare_path_applies_sqlite_engine_tuning(tmp_path: Path) -> None:
    """A bare file path is normalized before the SQLite-specific engine tuning, so
    it still gets the 30s busy timeout and no ``pool_pre_ping`` (issue #6)."""
    connector = sqlite_pkg.SQLiteConnector("/tmp/onestep-sqlite-tuning-check.db")
    assert connector.engine.url.drivername == "sqlite+aiosqlite"
    assert connector.engine.pool._pre_ping is False

    async def check_timeout() -> None:
        async with connector.engine.connect() as conn:
            timeout = (await conn.execute(sa.text("PRAGMA busy_timeout"))).scalar()
            assert timeout == 30000

    asyncio.run(check_timeout())
    asyncio.run(connector.close())


def test_memory_dsn_uses_static_pool() -> None:
    """A bare ':memory:' is an in-memory database, not a file named ':memory:'."""
    from sqlalchemy.pool import StaticPool

    connector = sqlite_pkg.SQLiteConnector(":memory:")
    assert isinstance(connector.engine.pool, StaticPool)

    async def check_timeout() -> None:
        async with connector.engine.connect() as conn:
            timeout = (await conn.execute(sa.text("PRAGMA busy_timeout"))).scalar()
            assert timeout == 30000

    asyncio.run(check_timeout())
    asyncio.run(connector.close())


def test_incremental_polls_and_advances_cursor(tmp_path: Path) -> None:
    connector = sqlite_pkg.SQLiteConnector(f"sqlite:///{tmp_path / 'i.db'}")

    async def scenario() -> None:
        async with connector.engine.begin() as conn:
            await conn.run_sync(
                lambda s: s.execute(
                    sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
                )
            )
            await conn.execute(sa.text("INSERT INTO users (id, name) VALUES (1, 'a'), (2, 'b'), (3, 'c')"))
        source = connector.incremental(
            table="users",
            key="id",
            cursor=["id"],
            state=InMemoryCursorStore(),
            state_key="users:id",
        )
        first = await source.fetch(2)
        assert len(first) == 2
        for delivery in first:
            await delivery.ack()
        second = await source.fetch(2)
        assert len(second) == 1
        assert second[0].envelope.body["id"] == 3
        await connector.close()

    asyncio.run(scenario())
