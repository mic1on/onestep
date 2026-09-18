"""Phase 2 dual-backend contract tests for ``onestep_sql._shared`` (issue #133).

These tests replace the retired ``scripts/check_plugin_drift.py`` parallel-copy
monitoring (design §3.1, execution plan P2.5). For every behaviour that used to
exist as a deliberately parallel copy in the mysql and postgres backends, they
prove two things:

1. **Shared once** — both backends resolve the *same* implementation object
   (function, method, or shared base class) from ``onestep_sql._shared``; no
   unexplained parallel copy remains.
2. **Same behaviour for both backends** — the shared implementation preserves
   each backend's observable contract: state/cursor store persistence and the
   datetime-tagged cursor encoding (the issue #125 drift regression), asyncio
   driver mapping, table-sink update policies and JSON coercion, default
   incremental state keys, and secret redaction.

The genuinely per-database parts are pinned as staying per-database: the
SQLAlchemy error-classification tables differ between backends, MySQL binlog
stays inside ``onestep_sql.mysql``, and each backend's tracked-execution
*schema* stays in its own subpackage.

Phase 1 of the MySQL tracked execution backend
(``docs/superpowers/specs/2026-09-15-mysql-tracked-execution-backend-design.md``
§7.2–§7.4) added ``onestep_sql._shared.execution`` and is the one deliberate
exception to the "capabilities never move into ``_shared``" rule: the
tracked-execution *state machine* is shared precisely because both backends
have identical machine parameters, error types and at-least-once contract. What
stays out is the part where they genuinely differ — the schema builder — plus
every concrete backend class. Section 6 below is re-targeted accordingly: it
bans concrete backend classes and real backend imports, while explicitly
allowing the shared machine to reference core's ``LeasedExecutionBackend``
protocol it implements.

No live database required: the behaviour checks run on sqlite/aiosqlite, the
same way the per-backend plugin suites do.
"""

from __future__ import annotations

import asyncio
import ast
import dataclasses
import hashlib
import importlib
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest
import sqlalchemy as sa
from onestep_sql import mysql as mysql_pkg
from onestep_sql import postgres as postgres_pkg
from onestep_sql import sqlite as sqlite_pkg
from onestep_sql._shared import resilience as shared_resilience
from onestep_sql._shared import state_keys as shared_state_keys
from onestep_sql._shared import state_sqlalchemy as shared_state
from onestep_sql._shared import table_queue as shared_table_queue
from onestep_sql._shared import table_sink_policy as shared_policy
from onestep_sql._shared.resilience import redact_message
from onestep_sql.mysql import connector as mysql_connector
from onestep_sql.mysql import resilience as mysql_resilience
from onestep_sql.mysql import state_sqlalchemy as mysql_state
from onestep_sql.postgres import connector as postgres_connector
from onestep_sql.postgres import resilience as postgres_resilience
from onestep_sql.postgres import state_sqlalchemy as postgres_state
from onestep_sql.sqlite import connector as sqlite_connector
from onestep_sql.sqlite import resilience as sqlite_resilience
from onestep_sql.sqlite import state_sqlalchemy as sqlite_state
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.ext.asyncio import create_async_engine as _create_async_engine

from onestep.resilience import ConnectorErrorKind, ConnectorOperation
from onestep import OneStepApp

BACKENDS = ("mysql", "postgres", "sqlite")


def _backend_state_module(backend: str):
    return {"mysql": mysql_state, "postgres": postgres_state, "sqlite": sqlite_state}[backend]


def _backend_connector_module(backend: str):
    return {"mysql": mysql_connector, "postgres": postgres_connector, "sqlite": sqlite_connector}[backend]


def _backend_sink_cls(backend: str):
    return {
        "mysql": mysql_pkg.TableSink,
        "postgres": postgres_pkg.PostgresTableSink,
        "sqlite": sqlite_pkg.TableSink,
    }[backend]


def _backend_connector_cls(backend: str):
    return {
        "mysql": mysql_pkg.MySQLConnector,
        "postgres": postgres_pkg.PostgresConnector,
        "sqlite": sqlite_pkg.SQLiteConnector,
    }[backend]


# ---------------------------------------------------------------------------
# 1. The previously monitored pairs now exist exactly once, in _shared.
# ---------------------------------------------------------------------------


def test_state_store_implementation_lives_once_in_shared() -> None:
    for state in (mysql_state, postgres_state, sqlite_state):
        assert issubclass(state.SQLAlchemyStateStore, shared_state.SQLAlchemyStateStore)
        assert issubclass(state.SQLAlchemyCursorStore, shared_state.SQLAlchemyCursorStore)
    # Backend classes stay distinct public identities per backend.
    assert mysql_state.SQLAlchemyStateStore is not postgres_state.SQLAlchemyStateStore
    assert mysql_state.SQLAlchemyStateStore is not sqlite_state.SQLAlchemyStateStore
    assert postgres_state.SQLAlchemyStateStore is not sqlite_state.SQLAlchemyStateStore
    assert mysql_state.SQLAlchemyCursorStore is not postgres_state.SQLAlchemyCursorStore
    assert mysql_state.SQLAlchemyCursorStore is not sqlite_state.SQLAlchemyCursorStore
    assert postgres_state.SQLAlchemyCursorStore is not sqlite_state.SQLAlchemyCursorStore
    # Every behavioural method is implemented on the shared classes only.
    shared_module = "onestep_sql._shared.state_sqlalchemy"
    for name in ("__init__", "load", "save", "delete", "close", "_ensure_ready"):
        assert getattr(shared_state.SQLAlchemyStateStore, name).__module__ == shared_module
    for name in ("__init__", "load", "save", "_encode_cursor_component", "_decode_cursor_component"):
        assert getattr(shared_state.SQLAlchemyCursorStore, name).__module__ == shared_module
    # Neither backend overrides any store behaviour with a local copy.
    for state in (mysql_state, postgres_state):
        overridden = {
            name
            for name in ("load", "save", "delete", "close", "_ensure_ready")
            if getattr(state.SQLAlchemyStateStore, name) is not getattr(
                shared_state.SQLAlchemyStateStore, name
            )
        }
        assert not overridden


def test_table_sink_policy_lives_once_in_shared() -> None:
    assert (
        mysql_connector._normalize_update_columns
        is postgres_connector._normalize_update_columns
        is shared_policy._normalize_update_columns
    )
    # No backend keeps a private copy of the policy table either.
    assert not hasattr(mysql_connector, "_UPDATE_COLUMN_POLICIES")
    assert not hasattr(postgres_connector, "_UPDATE_COLUMN_POLICIES")
    assert shared_policy._UPDATE_COLUMN_POLICIES == frozenset(
        {"overwrite", "skip_null", "backfill"}
    )
    assert (
        mysql_pkg.TableSink._update_payload
        is postgres_pkg.PostgresTableSink._update_payload
        is sqlite_pkg.TableSink._update_payload
        is shared_policy.TableSinkUpdatePolicy._update_payload
    )
    assert (
        mysql_pkg.TableSink._coerce_json_values
        is postgres_pkg.PostgresTableSink._coerce_json_values
        is sqlite_pkg.TableSink._coerce_json_values
        is shared_policy.TableSinkUpdatePolicy._coerce_json_values
    )
    assert issubclass(mysql_pkg.TableSink, shared_policy.TableSinkUpdatePolicy)
    assert issubclass(postgres_pkg.PostgresTableSink, shared_policy.TableSinkUpdatePolicy)
    assert issubclass(sqlite_pkg.TableSink, shared_policy.TableSinkUpdatePolicy)


def test_table_queue_complete_lives_once_in_shared() -> None:
    """The issue #181 single-transaction complete(values) exists once, in _shared."""
    delivery_classes = (
        mysql_pkg.TableQueueDelivery,
        postgres_pkg.PostgresTableQueueDelivery,
        sqlite_pkg.TableQueueDelivery,
    )
    for delivery_cls in delivery_classes:
        assert issubclass(delivery_cls, shared_table_queue.TableQueueCompleteMixin)
        # The behavioural method is implemented on the shared mixin only.
        assert delivery_cls.complete is shared_table_queue.TableQueueCompleteMixin.complete
        assert "_complete_ack_applied" not in delivery_cls.__dict__
    assert shared_table_queue.TableQueueCompleteMixin._complete_ack_applied is False
    # The SQL helper is imported by identity into every backend module, and each
    # backend's complete_row is a thin delegate onto it (no local SQL copy).
    for connector_module in (mysql_connector, postgres_connector, sqlite_connector):
        assert (
            connector_module.complete_table_queue_row
            is shared_table_queue.complete_table_queue_row
        )
    for source_cls in (
        mysql_connector.TableQueueSource,
        postgres_connector.PostgresTableQueueSource,
        sqlite_connector.TableQueueSource,
    ):
        assert "complete_table_queue_row" in source_cls.complete_row.__code__.co_names
    assert (
        shared_table_queue.complete_table_queue_row.__module__
        == "onestep_sql._shared.table_queue"
    )


def test_incremental_state_key_lives_once_in_shared() -> None:
    assert (
        mysql_connector._default_incremental_state_key
        is postgres_connector._default_incremental_state_key
        is shared_state_keys._default_incremental_state_key
    )


def test_secret_redaction_scaffolding_lives_once_in_shared() -> None:
    assert (
        mysql_resilience.collect_sensitive_tokens
        is postgres_resilience.collect_sensitive_tokens
        is shared_resilience.collect_sensitive_tokens
    )
    assert (
        mysql_resilience.redact_message
        is postgres_resilience.redact_message
        is sqlite_resilience.redact_message
        is redact_message
    )
    assert issubclass(mysql_resilience.MySQLErrorCause, shared_resilience.SQLErrorCause)
    assert issubclass(postgres_resilience.PostgresErrorCause, shared_resilience.SQLErrorCause)
    assert issubclass(sqlite_resilience.SQLiteErrorCause, shared_resilience.SQLErrorCause)
    # The dialect-specific classification tables stay per backend (and are
    # genuinely different code, not a shared copy).
    assert (
        mysql_resilience.classify_sqlalchemy_error
        is not postgres_resilience.classify_sqlalchemy_error
    )
    assert (
        mysql_resilience.classify_sqlalchemy_error
        is not sqlite_resilience.classify_sqlalchemy_error
    )


# ---------------------------------------------------------------------------
# 2. Shared SQLAlchemy state/cursor stores behave identically for both
#    backends (behaviour previously duplicated verbatim).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend", BACKENDS)
def test_shared_state_store_persists_across_instances(backend: str, tmp_path: Path) -> None:
    state = _backend_state_module(backend)
    db_url = f"sqlite:///{tmp_path / 'state.db'}"

    async def scenario() -> None:
        store = state.SQLAlchemyStateStore(dsn=db_url)
        await store.save("jobs:last-run", {"cursor": [10, 2], "status": "ok"})
        assert await store.load("jobs:last-run") == {"cursor": [10, 2], "status": "ok"}
        await store.close()

        reloaded = state.SQLAlchemyStateStore(dsn=db_url)
        assert await reloaded.load("jobs:last-run") == {"cursor": [10, 2], "status": "ok"}
        await reloaded.delete("jobs:last-run")
        assert await reloaded.load("jobs:last-run") is None
        await reloaded.close()

    asyncio.run(scenario())


def _seed_orders_table(tmp_path: Path, name: str, rows: list[dict]) -> tuple[str, sa.Table]:
    """Create a sqlite-backed orders table the way the plugin suites do."""
    db_url = f"sqlite:///{tmp_path / name}"
    engine = sa.create_engine(db_url, future=True)
    metadata = sa.MetaData()
    orders = sa.Table(
        "orders",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("payload", sa.String, nullable=False),
        sa.Column("status", sa.Integer, nullable=False),
        sa.Column("score", sa.Integer),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            sa.insert(orders),
            [{"payload": "A", "status": 0, "score": None, **row} for row in rows],
        )
    engine.dispose()
    return db_url, orders


@pytest.mark.parametrize("backend", BACKENDS)
def test_shared_table_queue_complete_writes_values_and_ack_atomically(
    backend: str, tmp_path: Path
) -> None:
    """complete() lands business values and ack columns together (issue #181)."""
    db_url, _orders = _seed_orders_table(tmp_path, f"complete-{backend}.db", [{"id": 1}])

    async def scenario() -> None:
        connector = _backend_connector_cls(backend)(db_url)
        source = connector.table_queue(
            table="orders",
            key="id",
            where="status = 0",
            claim={"status": 9},
            ack={"status": 1},
            nack={"status": 0},
            batch_size=10,
            poll_interval_s=0.01,
        )
        deliveries = await source.fetch(10)
        assert len(deliveries) == 1
        delivery = deliveries[0]
        assert delivery.payload["status"] == 9  # post-claim body, unchanged by fetch

        ack_row_calls: list[object] = []
        original_ack_row = source.ack_row

        async def recording_ack_row(row_ref: object) -> None:
            ack_row_calls.append(row_ref)
            await original_ack_row(row_ref)

        source.ack_row = recording_ack_row  # type: ignore[method-assign]

        await delivery.complete({"score": 42})

        assert delivery._complete_ack_applied is True
        assert delivery.payload["score"] == 42  # envelope mirrors the business payload
        assert delivery.payload["status"] == 9  # ack columns never leak into the body

        # The executor-style follow-up ack must be a delivery-local no-op.
        await delivery.ack()
        assert ack_row_calls == []

        async with connector.engine.connect() as conn:
            row = (
                await conn.execute(sa.text("SELECT status, score FROM orders WHERE id = 1"))
            ).first()
        assert row == (1, 42)
        await connector.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("backend", BACKENDS)
def test_shared_table_queue_complete_midtransaction_failure_rolls_back_both_updates(
    backend: str, tmp_path: Path
) -> None:
    """A failure between the two UPDATE statements leaves the row untouched."""
    db_url, _orders = _seed_orders_table(tmp_path, f"rollback-{backend}.db", [{"id": 1}])

    async def scenario() -> None:
        connector = _backend_connector_cls(backend)(db_url)
        source = connector.table_queue(
            table="orders",
            key="id",
            where="status = 0",
            claim={"status": 9},
            ack={"status": 1},
            nack={"status": 0},
            batch_size=10,
            poll_interval_s=0.01,
        )
        deliveries = await source.fetch(10)
        delivery = deliveries[0]

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
        assert delivery.payload["score"] is None  # envelope untouched on failure

        async with connector.engine.connect() as conn:
            row = (
                await conn.execute(sa.text("SELECT status, score FROM orders WHERE id = 1"))
            ).first()
        # Neither the business value nor the ack survived the rollback: the row
        # is still exactly in its claim state.
        assert row == (9, None)

        # Recovery: the plain ack path still works after the failed complete().
        await delivery.ack()
        async with connector.engine.connect() as conn:
            status = (
                await conn.execute(sa.text("SELECT status FROM orders WHERE id = 1"))
            ).scalar()
        assert status == 1
        await connector.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("backend", BACKENDS)
def test_shared_table_queue_complete_empty_ack_degenerates_to_update_current_row(
    backend: str, tmp_path: Path
) -> None:
    """ack={} makes complete() equivalent to update_current_row (issue #181)."""
    db_url, _orders = _seed_orders_table(
        tmp_path,
        f"degenerate-{backend}.db",
        [{"id": 1}, {"id": 2, "payload": "B"}, {"id": 3, "payload": "C"}],
    )

    async def scenario() -> None:
        connector = _backend_connector_cls(backend)(db_url)
        source = connector.table_queue(
            table="orders",
            key="id",
            where="status = 0",
            claim={"status": 9},
            ack={},
            nack={"status": 0},
            batch_size=10,
            poll_interval_s=0.01,
        )
        deliveries = await source.fetch(10)
        assert [delivery.payload["id"] for delivery in deliveries] == [1, 2, 3]

        # Row 1: complete() with an empty ack mapping.
        await deliveries[0].complete({"score": 42})
        assert deliveries[0].payload["score"] == 42
        assert deliveries[0]._complete_ack_applied is True
        # Row 2: the legacy two-phase API on the same shape of source.
        await deliveries[1].update_current_row({"score": 7})
        await deliveries[1].ack()
        # Row 3: complete({}) with an empty ack mapping is a documented no-op.
        await deliveries[2].complete({})
        assert deliveries[2]._complete_ack_applied is True

        async with connector.engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.text("SELECT id, status, score FROM orders ORDER BY id")
                )
            ).all()
        assert rows == [(1, 9, 42), (2, 9, 7), (3, 9, None)]
        await connector.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("backend", BACKENDS)
def test_shared_table_queue_complete_full_app_flow(backend: str, tmp_path: Path) -> None:
    """End-to-end: a handler calling ctx.complete() lands the business value and
    the ack status together, the executor's follow-up ack() never reaches the
    source, and the emit sink still receives the handler result (issue #181)."""
    db_url, _orders = _seed_orders_table(
        tmp_path,
        f"app-flow-{backend}.db",
        [{"id": 1}, {"id": 2, "payload": "B"}],
    )
    setup_engine = sa.create_engine(db_url, future=True)
    with setup_engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE processed_orders ("
                "id INTEGER PRIMARY KEY, payload VARCHAR NOT NULL, status VARCHAR NOT NULL)"
            )
        )
    setup_engine.dispose()

    seen_scores: list[tuple[int, int]] = []
    ack_row_calls: list[object] = []

    async def scenario() -> None:
        app = OneStepApp(f"table-queue-complete-{backend}")
        connector = _backend_connector_cls(backend)(db_url)
        source = connector.table_queue(
            table="orders",
            key="id",
            where="status = 0",
            claim={"status": 9},
            ack={"status": 1},
            nack={"status": 0},
            batch_size=10,
            poll_interval_s=0.01,
        )
        sink = connector.table_sink(table="processed_orders", mode="upsert", keys=("id",))
        seen: list[int] = []

        original_ack_row = source.ack_row

        async def recording_ack_row(row_ref: object) -> None:
            ack_row_calls.append(row_ref)
            await original_ack_row(row_ref)

        source.ack_row = recording_ack_row  # type: ignore[method-assign]

        @app.task(source=source, emit=sink, concurrency=2)
        async def process(ctx, row):
            await ctx.complete({"score": row["id"] * 10})
            seen.append(row["id"])
            seen_scores.append((row["id"], row["score"]))
            if len(seen) == 2:
                ctx.app.request_shutdown()
            return {"id": row["id"], "payload": row["payload"], "status": "done"}

        await app.serve()
        await connector.close()

    asyncio.run(scenario())

    verify_engine = sa.create_engine(db_url, future=True)
    with verify_engine.begin() as conn:
        order_rows = conn.execute(
            sa.text("SELECT id, status, score FROM orders ORDER BY id")
        ).all()
        processed_rows = conn.execute(
            sa.text("SELECT id, payload, status FROM processed_orders ORDER BY id")
        ).all()
    verify_engine.dispose()

    assert sorted(seen_scores) == [(1, 10), (2, 20)]
    # Both rows carry the business value AND the ack status written by complete().
    assert order_rows == [(1, 1, 10), (2, 1, 20)]
    assert processed_rows == [(1, "A", "done"), (2, "B", "done")]
    # The executor's post-success ack() never reached ack_row: complete() had
    # already applied the ack columns atomically.
    assert ack_row_calls == []


@pytest.mark.parametrize("backend", BACKENDS)
def test_shared_cursor_store_round_trips_datetime_cursors(backend: str, tmp_path: Path) -> None:
    """The issue #125 drift regression: datetime cursors survive a restart."""
    state = _backend_state_module(backend)
    db_url = f"sqlite:///{tmp_path / 'datetime-cursor.db'}"
    cursor_value = datetime(2026, 8, 17, 0, 53, 55, 640000)  # noqa: DTZ001

    async def scenario() -> None:
        cursor = state.SQLAlchemyCursorStore(dsn=db_url)
        await cursor.save("follow-records", [cursor_value, "u_123"])
        assert await cursor.load("follow-records") == [cursor_value, "u_123"]

        raw = state.SQLAlchemyStateStore(dsn=db_url)
        assert await raw.load("follow-records") == [
            {
                "__onestep_cursor_type__": "datetime",
                "value": "2026-08-17T00:53:55.640000",
            },
            "u_123",
        ]
        # Values written by a plain state store decode transparently.
        await raw.save("legacy", [10, "u_456"])
        assert await cursor.load("legacy") == [10, "u_456"]
        await cursor.close()
        await raw.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("backend", BACKENDS)
def test_shared_state_store_rejects_dsn_and_engine_together(backend: str, tmp_path: Path) -> None:
    state = _backend_state_module(backend)

    async def scenario() -> None:
        engine = _create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'e.db'}")
        with pytest.raises(ValueError, match="pass either dsn or engine"):
            state.SQLAlchemyStateStore(dsn="sqlite:///:memory:", engine=engine)
        await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("backend", BACKENDS)
def test_connector_state_and_cursor_stores_share_the_connector_engine(backend: str, tmp_path: Path) -> None:
    connector = _backend_connector_cls(backend)(f"sqlite:///{tmp_path / 'c.db'}")

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


def test_async_dsn_driver_mapping_is_per_backend() -> None:
    # MySQL: mysql and any mysql+* driver map onto asyncmy.
    assert mysql_state._async_dsn("mysql://u:p@h/db") == "mysql+asyncmy://u:p@h/db"
    assert mysql_state._async_dsn("mysql+pymysql://u:p@h/db") == "mysql+asyncmy://u:p@h/db"
    # PostgreSQL: bare postgresql and psycopg2 map onto psycopg; other async
    # drivers are left untouched.
    assert postgres_state._async_dsn("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert (
        postgres_state._async_dsn("postgresql+psycopg2://u:p@h/db")
        == "postgresql+psycopg://u:p@h/db"
    )
    assert (
        postgres_state._async_dsn("postgresql+asyncpg://u:p@h/db")
        == "postgresql+asyncpg://u:p@h/db"
    )
    # Cross-database DSNs are passed through unchanged by the other backend.
    assert mysql_state._async_dsn("postgresql://u:p@h/db") == "postgresql://u:p@h/db"
    assert postgres_state._async_dsn("mysql://u:p@h/db") == "mysql://u:p@h/db"
    # Both backends run their tests on aiosqlite.
    for state in (mysql_state, postgres_state):
        assert state._async_dsn("sqlite:///x.db") == "sqlite+aiosqlite:///x.db"


def test_state_store_install_hints_stay_per_backend() -> None:
    assert mysql_state.SQLAlchemyStateStore._install_hint == "Install onestep-mysql."
    assert postgres_state.SQLAlchemyStateStore._install_hint == "Install onestep-postgres."
    # The per-backend driver-mapping hook is wired to the backend classes.
    assert mysql_state.SQLAlchemyStateStore._resolve_async_driver("mysql") == "mysql+asyncmy"
    assert (
        postgres_state.SQLAlchemyStateStore._resolve_async_driver("postgresql")
        == "postgresql+psycopg"
    )


# ---------------------------------------------------------------------------
# 3. Shared table-sink update policy behaves identically for both backends.
# ---------------------------------------------------------------------------


class _SinkHarness:
    """Minimal connector double; the shared policy never touches the connector."""



def _policy_table() -> sa.Table:
    metadata = sa.MetaData()
    return sa.Table(
        "records",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("title", sa.Text),
        sa.Column("note", sa.Text),
        sa.Column("meta", sa.JSON),
    )


def _make_sink(backend: str, **kwargs):
    sink_cls = _backend_sink_cls(backend)
    defaults = {
        "connector": _SinkHarness(),
        "table": "records",
        "mode": "upsert",
        "keys": ("id",),
    }
    defaults.update(kwargs)
    return sink_cls(**defaults)


@pytest.mark.parametrize("backend", BACKENDS)
def test_normalize_update_columns_none_disables_whitelist(backend: str) -> None:
    assert _backend_connector_module(backend)._normalize_update_columns(None, keys=("id",)) == (
        None,
        {},
    )


@pytest.mark.parametrize("backend", BACKENDS)
def test_normalize_update_columns_string_entries_default_to_overwrite(backend: str) -> None:
    names, policies = _backend_connector_module(backend)._normalize_update_columns(
        ("title", "note"), keys=("id",)
    )
    assert names == ("title", "note")
    assert policies == {"title": "overwrite", "note": "overwrite"}


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize(
    ("entries", "match"),
    [
        (("",), "update_columns entries must be non-empty"),
        (({"name": "title", "policy": "nonsense"},), "policy must be one of"),
        (({"name": "title", "extra": 1},), "unknown update_columns entry keys: extra"),
        (({"name": ""},), "requires a non-empty 'name'"),
        (({"name": "id", "policy": "skip_null"},), "cannot apply to key column 'id'"),
        (("title", {"name": "title", "policy": "skip_null"}), "duplicate update column 'title'"),
        (({"name": "title"}, {"name": "note"}, "title"), "duplicate update column 'title'"),
        ((42,), "update_columns entries must be strings or mappings"),
        (
            ({"name": "title"},),
            None,
        ),  # placeholder; conflict case handled separately below
    ],
)
def test_normalize_update_columns_validation_is_shared(backend: str, entries, match) -> None:
    normalize = _backend_connector_module(backend)._normalize_update_columns
    if match is None:
        # the {name: title} entry alone is valid; only the update_expr overlap fails
        with pytest.raises(ValueError, match="update_columns policy conflicts"):
            normalize(entries, keys=("id",), update_expr={"title": "NOW()"})
        return
    with pytest.raises((ValueError, TypeError), match=match):
        normalize(entries, keys=("id",))


@pytest.mark.parametrize("backend", BACKENDS)
def test_normalize_update_columns_policies_preserved(backend: str) -> None:
    names, policies = _backend_connector_module(backend)._normalize_update_columns(
        ("title", {"name": "note", "policy": "skip_null"}, {"name": "meta", "policy": "backfill"}),
        keys=("id",),
    )
    assert names == ("title", "note", "meta")
    assert policies == {"title": "overwrite", "note": "skip_null", "meta": "backfill"}


@pytest.mark.parametrize("backend", BACKENDS)
def test_update_payload_whitelist_overwrite_policy(backend: str) -> None:
    sink = _make_sink(backend, update_columns=("title", "note"))
    payload, skipped = sink._update_payload(
        {"id": 1, "title": "t", "note": "n", "meta": [1]}, _policy_table()
    )
    assert skipped is False
    assert set(payload) == {"title", "note"}
    assert payload["title"] == "t"


@pytest.mark.parametrize("backend", BACKENDS)
def test_update_payload_default_covers_non_key_columns(backend: str) -> None:
    sink = _make_sink(backend, update_columns=None, update_expr={"note": "NOW()"})
    payload, skipped = sink._update_payload({"id": 1, "title": "t", "meta": [1]}, _policy_table())
    assert skipped is False
    assert set(payload) == {"title", "meta", "note"}
    assert str(payload["note"]) == "NOW()"


@pytest.mark.parametrize("backend", BACKENDS)
def test_update_payload_skip_null_policy_sets_skipped_flag(backend: str) -> None:
    sink = _make_sink(backend, update_columns=({"name": "title", "policy": "skip_null"}, "note"))
    payload, skipped = sink._update_payload(
        {"id": 1, "title": None, "note": "n"}, _policy_table()
    )
    assert skipped is True
    assert set(payload) == {"note"}


@pytest.mark.parametrize("backend", BACKENDS)
def test_update_payload_backfill_renders_coalesce(backend: str) -> None:
    sink = _make_sink(backend, update_columns=({"name": "title", "policy": "backfill"},))
    payload, skipped = sink._update_payload({"id": 1, "title": "t"}, _policy_table())
    assert skipped is False
    compiled = str(
        sa.update(_policy_table()).where(_policy_table().c.id == 1).values(**payload).compile()
    )
    assert "coalesce" in compiled.lower()


@pytest.mark.parametrize("backend", BACKENDS)
def test_update_payload_update_expr_renders_literal_sql(backend: str) -> None:
    sink = _make_sink(backend, update_columns=("title",), update_expr={"note": "NOW()"})
    payload, _ = sink._update_payload({"id": 1, "title": "t"}, _policy_table())
    compiled = str(
        sa.update(_policy_table()).where(_policy_table().c.id == 1).values(**payload).compile()
    )
    assert "note=NOW()" in compiled


def test_update_payload_renders_identical_sql_on_both_backends() -> None:
    table = _policy_table()
    rendered = {}
    for backend in BACKENDS:
        sink = _make_sink(
            backend,
            update_columns=(
                "title",
                {"name": "note", "policy": "skip_null"},
                {"name": "meta", "policy": "backfill"},
            ),
            update_expr={},
        )
        payload, skipped = sink._update_payload(
            {"id": 1, "title": "t", "note": None, "meta": [1, 2]}, table
        )
        assert skipped is True
        rendered[backend] = str(
            sa.update(table).where(table.c.id == 1).values(**payload).compile()
        )
    assert rendered["mysql"] == rendered["postgres"]


@pytest.mark.parametrize("backend", BACKENDS)
def test_coerce_json_values_auto_serializes_non_json_columns_only(backend: str) -> None:
    sink = _make_sink(backend, update_columns=("title", "note", "meta"), serialize_json="auto")
    coerced = sink._coerce_json_values(
        {"id": 1, "title": None, "meta": {"k": [1, 2]}}, _policy_table()
    )
    assert coerced["title"] is None  # non-container values untouched
    # meta IS a JSON column -> kept as a container under "auto"
    assert coerced["meta"] == {"k": [1, 2]}
    # a container in a non-JSON column (note) is serialized
    coerced2 = sink._coerce_json_values({"id": 1, "note": ["a"]}, _policy_table())
    assert coerced2["note"] == '["a"]'


@pytest.mark.parametrize("backend", BACKENDS)
def test_coerce_json_values_always_and_never(backend: str) -> None:
    always = _make_sink(backend, update_columns=("meta",), serialize_json="always")
    assert always._coerce_json_values({"meta": {"k": 1}}, _policy_table())["meta"] == '{"k": 1}'
    never = _make_sink(backend, update_columns=("note",), serialize_json="never")
    payload = {"note": ["a"]}
    assert never._coerce_json_values(payload, _policy_table()) is payload


# ---------------------------------------------------------------------------
# 4. Shared default incremental state-key.
# ---------------------------------------------------------------------------


def test_default_incremental_state_key_contract() -> None:
    key_fn = shared_state_keys._default_incremental_state_key
    assert (
        key_fn(table="users", cursor=("updated_at", "id"), key="id", where=None)
        == "users:updated_at,id:key=id:where=-"
    )
    assert (
        key_fn(table="users", cursor=("updated_at", "id"), key="id", where="  status   =  1  ")
        == "users:updated_at,id:key=id:where=status = 1"
    )
    long_where = "x" * 80
    expected = f"sha1:{hashlib.sha1(long_where.encode('utf-8')).hexdigest()}"
    assert key_fn(table="t", cursor=("c",), key="k", where=long_where).endswith(
        f"where={expected}"
    )


def test_connectors_derive_identical_default_state_keys(tmp_path: Path) -> None:
    sources = {}
    for backend in BACKENDS:
        connector = _backend_connector_cls(backend)(f"sqlite:///{tmp_path / backend}.db")
        sources[backend] = connector.incremental(
            table="users", key="id", cursor=["updated_at"], where="status = 1"
        )
        # key not in cursor -> appended automatically
        assert sources[backend].cursor == ("updated_at", "id")
    assert sources["mysql"].state_key == sources["postgres"].state_key == sources["sqlite"].state_key
    assert sources["mysql"].state_key == "users:updated_at,id:key=id:where=status = 1"


# ---------------------------------------------------------------------------
# 5. Shared secret-redaction scaffolding + per-dialect classification.
# ---------------------------------------------------------------------------


def test_collect_sensitive_tokens_from_dsn() -> None:
    tokens = shared_resilience.collect_sensitive_tokens("mysql://alice:secret@host:3306/db")
    assert "alice:secret" in tokens
    assert "alice:secret@" in tokens
    assert "secret" in tokens


def test_collect_sensitive_tokens_from_mappings() -> None:
    tokens = shared_resilience.collect_sensitive_tokens(
        {"password": "s1", "engine_options": {"connect_args": {"passwd": "s2"}}},
        "ignored",
        None,
    )
    # secret mapping values first, then non-secret scalar config values are
    # still collected verbatim (a raw DSN is itself a scrub token)
    assert tokens == ["s1", "s2", "ignored"]


def test_redact_message_longest_first_and_truncation() -> None:
    message = "connect mysql://alice:supersecret@host failed for alice:supersecret"
    redacted = redact_message(message, ["supersecret", "alice:supersecret"])
    assert "supersecret" not in redacted
    assert "<redacted>" in redacted
    assert len(redact_message("x" * 900, [])) == shared_resilience.MAX_MESSAGE_LENGTH


@pytest.mark.parametrize(
    ("backend", "prefix"),
    [
        ("mysql", "mysql error: "),
        ("postgres", "postgres error: "),
        ("sqlite", "sqlite error: "),
    ],
)
def test_error_cause_classes(backend: str, prefix: str) -> None:
    cause_type = {
        "mysql": mysql_resilience.MySQLErrorCause,
        "postgres": postgres_resilience.PostgresErrorCause,
        "sqlite": sqlite_resilience.SQLiteErrorCause,
    }[backend]
    cause = cause_type("boom")
    assert str(cause) == f"{prefix}boom"
    assert repr(cause) == f"{cause_type.__name__}(message='boom')"
    assert isinstance(cause, shared_resilience.SQLErrorCause)
    with pytest.raises(dataclasses.FrozenInstanceError):
        cause.message = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("backend", BACKENDS)
def test_connector_operation_error_factory_shared_behaviour(backend: str) -> None:
    module = {"mysql": mysql_resilience, "postgres": postgres_resilience, "sqlite": sqlite_resilience}[backend]
    factory = {
        "mysql": mysql_resilience.as_mysql_connector_operation_error,
        "postgres": postgres_resilience.as_postgres_connector_operation_error,
        "sqlite": sqlite_resilience.as_sqlite_connector_operation_error,
    }[backend]
    cause_type = {
        "mysql": mysql_resilience.MySQLErrorCause,
        "postgres": postgres_resilience.PostgresErrorCause,
        "sqlite": sqlite_resilience.SQLiteErrorCause,
    }[backend]

    assert module.classify_sqlalchemy_error(TimeoutError("timeout")) is None

    sql_error = sa.exc.TimeoutError("timeout")
    normalized = factory(
        operation=ConnectorOperation.FETCH,
        exc=sql_error,
        source_name=f"{backend}.incremental:users",
        retry_delay_s=2.0,
    )
    assert normalized is not None
    assert normalized.backend == backend
    assert normalized.operation is ConnectorOperation.FETCH
    assert normalized.kind is ConnectorErrorKind.TRANSIENT
    assert normalized.source_name == f"{backend}.incremental:users"
    assert normalized.retry_delay_s == 2.0
    assert isinstance(normalized.cause, cause_type)
    assert "timeout" in str(normalized.cause)


@pytest.mark.parametrize("backend", BACKENDS)
def test_connector_operation_error_redacts_secrets(backend: str) -> None:
    factory = {
        "mysql": mysql_resilience.as_mysql_connector_operation_error,
        "postgres": postgres_resilience.as_postgres_connector_operation_error,
        "sqlite": sqlite_resilience.as_sqlite_connector_operation_error,
    }[backend]
    error = sa.exc.OperationalError("stmt", {}, Exception("access denied for 'alice' (using password: hunter2)"))
    normalized = factory(
        operation=ConnectorOperation.SEND,
        exc=error,
        secrets=["hunter2"],
    )
    assert normalized is not None
    assert "hunter2" not in str(normalized.cause)
    assert "<redacted>" in str(normalized.cause)


def test_error_classification_tables_stay_per_dialect() -> None:
    """The genuinely per-database server-message tables must stay separate."""

    def op_error(message: str) -> sa.exc.OperationalError:
        return sa.exc.OperationalError("stmt", {}, Exception(message))

    # MySQL-only server message.
    assert (
        mysql_resilience.classify_sqlalchemy_error(op_error("Server has gone away"))
        is ConnectorErrorKind.DISCONNECTED
    )
    assert (
        postgres_resilience.classify_sqlalchemy_error(op_error("Server has gone away"))
        is ConnectorErrorKind.TRANSIENT  # falls through to the OperationalError fallback
    )
    # PostgreSQL-only server message.
    assert (
        postgres_resilience.classify_sqlalchemy_error(op_error("server closed the connection"))
        is ConnectorErrorKind.DISCONNECTED
    )
    assert (
        mysql_resilience.classify_sqlalchemy_error(op_error("server closed the connection"))
        is ConnectorErrorKind.TRANSIENT
    )
    # Shared SQLAlchemy-level classification still agrees across all backends.
    for module in (mysql_resilience, postgres_resilience, sqlite_resilience):
        assert module.classify_sqlalchemy_error(sa.exc.TimeoutError("t")) is ConnectorErrorKind.TRANSIENT
        assert (
            module.classify_sqlalchemy_error(sa.exc.InterfaceError("stmt", {}, Exception("i")))
            is ConnectorErrorKind.DISCONNECTED
        )
    # SQLite's own dialect-specific message must stay distinct: "database is
    # locked" maps to TRANSIENT (retryable busy), unlike either server backend.
    assert (
        sqlite_resilience.classify_sqlalchemy_error(op_error("database is locked"))
        is ConnectorErrorKind.TRANSIENT
    )
    assert (
        mysql_resilience.classify_sqlalchemy_error(op_error("database is locked"))
        is ConnectorErrorKind.TRANSIENT  # mysql falls through to the OperationalError fallback
    )


# ---------------------------------------------------------------------------
# 6. Scope guardrails: _shared never absorbs backend-only capabilities.
# ---------------------------------------------------------------------------


def test_shared_package_exports_only_the_shared_modules() -> None:
    import pkgutil

    import onestep_sql._shared as shared_pkg

    names = {module.name for module in pkgutil.iter_modules(shared_pkg.__path__)}
    # ``execution`` is the Phase 1 extraction of the tracked-execution state
    # machine; it is internal and non-public, exactly like the other four.
    assert names == {
        "state_sqlalchemy",
        "table_sink_policy",
        "state_keys",
        "resilience",
        "execution",
        "table_queue",
    }
    # The execution subpackage shares a machine, its seam and the source layer,
    # never a schema builder (design §7.2: the schema layer is where the
    # dialects truly differ).
    execution_names = {
        module.name
        for module in pkgutil.iter_modules(
            importlib.import_module("onestep_sql._shared.execution").__path__
        )
    }
    assert execution_names == {"machine", "dialect", "source", "source_options"}


def _shared_python_files() -> list[Path]:
    root = (
        Path(__file__).resolve().parents[2]
        / "plugins"
        / "onestep-sql"
        / "src"
        / "onestep_sql"
        / "_shared"
    )
    # rglob (not glob) so the new ``execution`` subpackage is scanned too.
    return sorted(root.rglob("*.py"))


def test_shared_package_does_not_reference_backend_only_capabilities() -> None:
    # Concrete backend classes, driver names and backend-only capabilities must
    # never leak into _shared. The shared state machine may name core's
    # ``LeasedExecutionBackend`` protocol (that is the abstraction it
    # implements), which is why only the *concrete* class names are banned.
    banned = (
        "BinlogSource",
        "BinLogStreamReader",
        "MySQLConnector",
        "PostgresConnector",
        "SQLiteConnector",
        "PostgresExecutionBackend",
        "PostgresExecutionSource",
        "PostgresExecutionDelivery",
        "MySQLExecutionBackend",
        "MySQLExecutionSource",
        "MySQLExecutionDelivery",
    )
    # Real intra-distribution backend imports, which would invert the
    # dependency direction (_shared must not depend on a concrete backend).
    banned_import_prefixes = (
        "onestep_sql.mysql",
        "onestep_sql.postgres",
        "onestep_sql.sqlite",
    )
    for path in _shared_python_files():
        if path.name == "__init__.py":
            # the package docstrings legitimately *mention* the boundary
            continue
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(Path(__file__).resolve().parents[2])
        for token in banned:
            assert token not in text, f"{relative} references backend-only symbol {token!r}"
        tree = ast.parse(text)
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules = [node.module]
            for module in modules:
                for prefix in banned_import_prefixes:
                    assert not module.startswith(prefix), (
                        f"{relative} imports concrete backend module {module!r}"
                    )


def test_shared_package_does_not_contain_a_backend_schema_builder() -> None:
    # The DDL is the one part that stays per backend (design §7.2): a docstring
    # may name the boundary, but no shared module may import or define a schema
    # builder.
    for path in _shared_python_files():
        if path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(Path(__file__).resolve().parents[2])
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "execution_schema" not in node.module, (
                    f"{relative} must not import a backend schema module"
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "execution_schema" not in alias.name, (
                        f"{relative} must not import a backend schema module"
                    )
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.name != "build_execution_tables", (
                    f"{relative} must not define a backend schema builder"
                )


def test_shared_execution_schema_stays_in_each_backend() -> None:
    # Phase 1 currently has a single consumer (PostgreSQL), so this asserts the
    # boundary with what exists today. Phase 2 adds
    # ``onestep_sql.mysql.execution_schema``; extend this test to assert that the
    # two builders are distinct objects from their own modules.
    from onestep_sql.postgres import execution_schema as postgres_schema

    assert (
        postgres_schema.build_execution_tables.__module__
        == "onestep_sql.postgres.execution_schema"
    )
    assert not hasattr(
        importlib.import_module("onestep_sql._shared.execution.dialect"),
        "build_execution_tables",
    )


def test_execution_identifier_naming_is_shared_but_stays_parameterized() -> None:
    """Identifier naming is shared mechanism; the length limit stays per backend.

    Design §6.9: the helper must be parameterized (PostgreSQL 63 / MySQL 64),
    must not carry a backend name, and must be usable as a
    ``sa.ForeignKey(..., name=...)`` source for §6.11's InnoDB FK fix. The
    PostgreSQL schema module keeps thin delegations so its derived names, error
    messages and DDL are unchanged.
    """
    import inspect

    from onestep_sql._shared.execution import dialect as shared_dialect
    from onestep_sql.postgres import execution_schema as postgres_schema

    # Neutral naming: no backend appears in either helper's name.
    for name in ("derive_object_name", "validate_sql_identifier"):
        assert hasattr(shared_dialect, name)
        assert "postgres" not in name and "mysql" not in name

    # Parameterized rather than hard-coded: max_length and hash_length are both
    # accepted, and the historical hash length is still the default.
    params = inspect.signature(shared_dialect.derive_object_name).parameters
    assert "max_length" in params and "hash_length" in params
    assert params["hash_length"].default == 12

    # PostgreSQL delegates rather than keeping a parallel copy, so the two
    # paths cannot drift; the PostgreSQL limit itself stays in its own module.
    assert postgres_schema._postgres_object_name.__module__ == (
        "onestep_sql.postgres.execution_schema"
    )
    derived_pg = postgres_schema._postgres_object_name(
        table_name="a" * 62 + "1", prefix="uq_", suffix="execution_attempt"
    )
    derived_shared = shared_dialect.derive_object_name(
        table_name="a" * 62 + "1",
        prefix="uq_",
        suffix="execution_attempt",
        max_length=63,
    )
    assert derived_pg == derived_shared
    assert len(derived_pg) <= 63

    # The truncation branch is deterministic and collision-free for two
    # same-prefix long names, which is what makes a derived FK name safe.
    other = shared_dialect.derive_object_name(
        table_name="a" * 62 + "2",
        prefix="uq_",
        suffix="execution_attempt",
        max_length=63,
    )
    assert derived_pg != other
    assert (
        derived_pg
        == shared_dialect.derive_object_name(
            table_name="a" * 62 + "1",
            prefix="uq_",
            suffix="execution_attempt",
            max_length=63,
        )
    )

    # MySQL's wider limit uses the same implementation and stays within 64.
    derived_mysql = shared_dialect.derive_object_name(
        table_name="a" * 64, prefix="uq_", suffix="idempotency", max_length=64
    )
    assert len(derived_mysql) <= 64

    # 63 and 64 are genuinely different limits on the same input.
    long_table = "a" * 61
    assert len(
        shared_dialect.derive_object_name(
            table_name=long_table, prefix="ix_", suffix="", max_length=63
        )
    ) == 63
    assert len(
        shared_dialect.derive_object_name(
            table_name=long_table, prefix="ix_", suffix="", max_length=64
        )
    ) == 64

    # Usable as a foreign-key constraint name (§6.11) -- not merely an index or
    # check name -- and the derived name is what reaches the DDL.
    fk_name = shared_dialect.derive_object_name(
        table_name="a" * 58, prefix="fk_", suffix="execution", max_length=64
    )
    assert len(fk_name) <= 64
    metadata = sa.MetaData()
    parent = sa.Table(
        "onestep_executions", metadata, sa.Column("id", sa.Uuid, primary_key=True)
    )
    child = sa.Table(
        "a" * 58,
        metadata,
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "execution_id",
            sa.Uuid,
            sa.ForeignKey(f"{parent.name}.id", name=fk_name, ondelete="CASCADE"),
            nullable=False,
        ),
    )
    from sqlalchemy.dialects import mysql as mysql_dialect

    fk_ddl = str(
        sa.schema.CreateTable(child).compile(dialect=mysql_dialect.dialect())
    )
    assert fk_name in fk_ddl

    # The shared validator honours the caller's limit and keeps the exact
    # messages the PostgreSQL module produced before extraction.
    assert shared_dialect.validate_sql_identifier("ok_name", "t", max_length=63) == "ok_name"
    with pytest.raises(ValueError, match="must be a non-empty SQL identifier"):
        shared_dialect.validate_sql_identifier("a-b", "attempts_table", max_length=63)
    with pytest.raises(ValueError, match="must be at most 63 characters"):
        shared_dialect.validate_sql_identifier("a" * 64, "attempts_table", max_length=63)
    assert (
        shared_dialect.validate_sql_identifier("a" * 64, "attempts_table", max_length=64)
        == "a" * 64
    )


def test_shared_execution_machine_is_the_single_implementation() -> None:
    from onestep_sql._shared.execution import machine as shared_machine
    from onestep_sql.postgres import execution_backend as postgres_execution

    # The state machine now exists exactly once, in _shared.
    assert (
        issubclass(postgres_execution.PostgresExecutionBackend, shared_machine.ExecutionStateMachine)
    )
    shared_module = "onestep_sql._shared.execution.machine"
    for name in (
        "__init__",
        "submit",
        "get",
        "list",
        "request_cancel",
        "claim",
        "heartbeat",
        "complete",
        "release",
        "lease_remaining",
        "_ensure_ready",
        "_ensure_ready_locked",
        "_submit",
        "_claim",
        "_heartbeat",
        "_complete",
        "_release",
        "_expire_queued",
        "_expire_cancel_requests",
        "_release_expired_leases",
        "_lease_lost_retry_predicate",
        "_row_to_execution",
        "_encode_cursor",
        "_decode_cursor",
        "_now",
        "_transaction_now",
        "open",
        "close",
        "_fork_error",
    ):
        method = getattr(shared_machine.ExecutionStateMachine, name)
        assert method.__module__ == shared_module, f"{name} is not shared"
    # The PostgreSQL subclass keeps public identity but no machine behaviour.
    for name in ("submit", "claim", "heartbeat", "complete", "release", "_claim", "_complete"):
        assert getattr(
            postgres_execution.PostgresExecutionBackend, name
        ) is getattr(shared_machine.ExecutionStateMachine, name)
    # Dialect seam: PostgreSQL supplies the adapter, not the machine.
    assert postgres_execution.PostgresExecutionBackend._dialect_cls.name == "postgresql"
    assert (
        postgres_execution.StaleExecutionLease is shared_machine.StaleExecutionLease
    )


def test_shared_execution_source_layer_is_the_single_implementation() -> None:
    from onestep_sql._shared.execution import source as shared_source
    from onestep_sql.postgres import execution_source as postgres_source

    assert issubclass(postgres_source.PostgresExecutionSource, shared_source.ExecutionSourceBase)
    assert issubclass(
        postgres_source.PostgresExecutionDelivery, shared_source.ExecutionDeliveryBase
    )
    shared_module = "onestep_sql._shared.execution.source"
    for name in (
        "__init__",
        "validate_task",
        "open",
        "fetch",
        "close",
        "complete_execution",
        "start_processing",
        "release_unstarted",
        "ack",
        "retry",
        "fail",
        "_heartbeat_loop",
        "_heartbeat_with_retry",
        "_await_critical",
        "_stop_heartbeat",
    ):
        for cls in (shared_source.ExecutionSourceBase, shared_source.ExecutionDeliveryBase):
            method = getattr(cls, name, None)
            if method is not None:
                assert method.__module__ == shared_module, f"{cls.__name__}.{name} is not shared"
    # The PostgreSQL subclasses keep only their identity plus the two seams.
    assert postgres_source.PostgresExecutionSource.fetch is shared_source.ExecutionSourceBase.fetch
    assert postgres_source.PostgresExecutionSource._source_kind == "postgres.execution"
    assert (
        postgres_source.PostgresExecutionDelivery.complete_execution
        is shared_source.ExecutionDeliveryBase.complete_execution
    )
    # Option validation is shared once and re-exported on the published path.
    from onestep_sql.postgres import resources as postgres_resources

    assert (
        postgres_resources._validate_execution_source_options
        is shared_source._validate_execution_source_options
    )


def test_execution_source_option_validator_is_shared_and_backend_agnostic() -> None:
    """The option validator is one object every backend can reach directly.

    Its home is ``_shared/execution/source_options.py`` rather than
    ``.../source.py`` so it carries none of the source layer's heavier imports;
    ``onestep_sql.postgres.resources`` re-exports it through the published
    ``postgres.execution_source`` path. Phase 3's MySQL source must consume this
    same object instead of importing anything under ``onestep_sql.postgres``
    (design §12.3, "no cross-backend dependency").
    """
    from onestep_sql._shared.execution import source_options as shared_options
    from onestep_sql.postgres import execution_source as postgres_source
    from onestep_sql.postgres import resources as postgres_resources

    # Every resolution path yields the same object.
    assert (
        shared_options._validate_execution_source_options
        is postgres_source._validate_execution_source_options
        is postgres_resources._validate_execution_source_options
    )
    assert (
        shared_options._validate_execution_source_options.__module__
        == "onestep_sql._shared.execution.source_options"
    )

    # It is dependency-light: no concrete backend, and none of the source
    # layer's runtime imports, may leak into it.
    tree = ast.parse(
        (
            Path(__file__).resolve().parents[2]
            / "plugins/onestep-sql/src/onestep_sql/_shared/execution/source_options.py"
        ).read_text(encoding="utf-8")
    )
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(("." * node.level) + (node.module or ""))
    assert not [
        name
        for name in imported
        if "onestep_sql" in name or "postgres" in name or "mysql" in name
    ], f"source_options must not import a backend: {imported}"

    # Behaviour is unchanged from the pre-extraction implementation: the
    # documented boundaries and the message/`field_prefix` contract still hold.
    validate = shared_options._validate_execution_source_options
    assert validate(
        namespace="agent-api",
        task_names=("run_agent",),
        batch_size=1,
        poll_interval_s=0.5,
        lease_duration_s=90.0,
        heartbeat_interval_s=30.0,
        worker_id="w",
    ) == ("run_agent",)
    # Single task name is enforced.
    with pytest.raises(ValueError, match="exactly one task name"):
        validate(
            namespace="ns",
            task_names=("a", "b"),
            batch_size=1,
            poll_interval_s=1.0,
            lease_duration_s=90.0,
            heartbeat_interval_s=30.0,
            worker_id="w",
        )
    # The heartbeat tolerance branch accepts exactly lease/3 (math.isclose).
    validate(
        namespace="ns",
        task_names=("t",),
        batch_size=1,
        poll_interval_s=1.0,
        lease_duration_s=90.0,
        heartbeat_interval_s=30.0,
        worker_id="w",
    )
    with pytest.raises(ValueError, match="lease_duration_s / 3"):
        validate(
            namespace="ns",
            task_names=("t",),
            batch_size=1,
            poll_interval_s=1.0,
            lease_duration_s=90.0,
            heartbeat_interval_s=30.0001,
            worker_id="w",
        )
    # bool is not an acceptable batch_size.
    with pytest.raises(ValueError, match="batch_size must be >= 1"):
        validate(
            namespace="ns",
            task_names=("t",),
            batch_size=True,
            poll_interval_s=1.0,
            lease_duration_s=90.0,
            heartbeat_interval_s=30.0,
            worker_id="w",
        )
    # field_prefix is preserved in every message.
    with pytest.raises(ValueError, match=r"jobs\.namespace"):
        validate(
            namespace="",
            task_names=("t",),
            batch_size=1,
            poll_interval_s=1.0,
            lease_duration_s=90.0,
            heartbeat_interval_s=30.0,
            worker_id="w",
            field_prefix="jobs",
        )


def test_no_backend_subpackage_imports_another_backend() -> None:
    """``mysql`` must never import ``onestep_sql.postgres`` (design §12.3).

    Phase 3's MySQL source reuses the shared machine and the shared option
    validator, so any new import edge from a backend into another backend is a
    regression, not a convenience.
    """
    dist = Path(__file__).resolve().parents[2] / "plugins/onestep-sql/src/onestep_sql"
    for package, forbidden in (
        ("mysql", ("postgres", "sqlite")),
        ("postgres", ("mysql", "sqlite")),
        ("sqlite", ("mysql", "postgres")),
    ):
        for path in sorted((dist / package).rglob("*.py")):
            if "__pycache__" in str(path):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                for name in names:
                    for other in forbidden:
                        assert not name.startswith(f"onestep_sql.{other}"), (
                            f"{path.name} imports onestep_sql.{other} ({name!r})"
                        )
    # The shared execution package must not depend on any concrete backend.
    for path in sorted((dist / "_shared" / "execution").rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                for other in ("mysql", "postgres", "sqlite"):
                    assert not name.startswith(f"onestep_sql.{other}"), (
                        f"_shared/execution/{path.name} imports onestep_sql.{other}"
                    )


def test_postgres_execution_modules_keep_their_published_surface() -> None:
    # Phase 1's zero-behaviour-change gate: the published symbols and the
    # historical submodule paths must resolve to the same objects as before the
    # extraction (design §7.4, §12.2).
    import inspect

    from onestep_sql.postgres import execution_backend as backend_module
    from onestep_sql.postgres import execution_source as source_module

    assert backend_module.__all__ == [
        "ExecutionLease",
        "HeartbeatResult",
        "PostgresExecutionBackend",
        "StaleExecutionLease",
    ]
    assert source_module.__all__ == [
        "PostgresExecutionDelivery",
        "PostgresExecutionSource",
    ]
    # The legacy sys.modules forwarders must still alias the same objects.
    legacy_backend = importlib.import_module("onestep_postgres.execution_backend")
    legacy_source = importlib.import_module("onestep_postgres.execution_source")
    assert (
        legacy_backend.PostgresExecutionBackend
        is backend_module.PostgresExecutionBackend
        is postgres_pkg.PostgresExecutionBackend
    )
    assert (
        legacy_source.PostgresExecutionSource
        is source_module.PostgresExecutionSource
        is postgres_pkg.PostgresExecutionSource
    )
    assert (
        legacy_backend.StaleExecutionLease
        is backend_module.StaleExecutionLease
        is postgres_pkg.StaleExecutionLease
    )
    # Public constructor signature unchanged (indented through **engine_options).
    assert list(
        inspect.signature(backend_module.PostgresExecutionBackend).parameters
    ) == [
        "dsn",
        "connector",
        "table",
        "attempts_table",
        "auto_create",
        "max_payload_bytes",
        "max_metadata_bytes",
        "max_result_bytes",
        "reclaim_batch_size",
        "clock",
        "engine_options",
    ]


def test_legacy_forwarders_still_alias_the_shared_backend_objects() -> None:
    mysql_legacy_state = importlib.import_module("onestep_mysql.state_sqlalchemy")
    postgres_legacy_state = importlib.import_module("onestep_postgres.state_sqlalchemy")
    assert mysql_legacy_state.SQLAlchemyStateStore is mysql_state.SQLAlchemyStateStore
    assert mysql_legacy_state.SQLAlchemyCursorStore is mysql_state.SQLAlchemyCursorStore
    assert postgres_legacy_state.SQLAlchemyStateStore is postgres_state.SQLAlchemyStateStore
    assert postgres_legacy_state.SQLAlchemyCursorStore is postgres_state.SQLAlchemyCursorStore
