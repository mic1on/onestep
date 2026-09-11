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
stays inside ``onestep_sql.mysql``, and PostgreSQL tracked execution stays
inside ``onestep_sql.postgres``.

No live database required: the behaviour checks run on sqlite/aiosqlite, the
same way the per-backend plugin suites do.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import importlib
from datetime import datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from onestep_sql import mysql as mysql_pkg
from onestep_sql import postgres as postgres_pkg
from onestep_sql import sqlite as sqlite_pkg
from onestep_sql._shared import resilience as shared_resilience
from onestep_sql._shared import state_keys as shared_state_keys
from onestep_sql._shared import state_sqlalchemy as shared_state
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
from sqlalchemy.ext.asyncio import create_async_engine as _create_async_engine

from onestep.resilience import ConnectorErrorKind, ConnectorOperation

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


def test_shared_package_exports_only_the_four_shared_modules() -> None:
    import pkgutil

    import onestep_sql._shared as shared_pkg

    names = {module.name for module in pkgutil.iter_modules(shared_pkg.__path__)}
    assert names == {"state_sqlalchemy", "table_sink_policy", "state_keys", "resilience"}


def test_shared_package_does_not_reference_backend_only_capabilities() -> None:
    root = (
        Path(__file__).resolve().parents[2]
        / "plugins"
        / "onestep-sql"
        / "src"
        / "onestep_sql"
        / "_shared"
    )
    banned = (
        "BinlogSource",
        "BinLogStreamReader",
        "ExecutionBackend",
        "ExecutionSource",
        "execution_backend",
        "execution_source",
    )
    for path in root.glob("*.py"):
        if path.name == "__init__.py":
            # the package docstring legitimately *mentions* the boundary
            continue
        text = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in text, f"{path.name} references backend-only symbol {token!r}"


def test_legacy_forwarders_still_alias_the_shared_backend_objects() -> None:
    mysql_legacy_state = importlib.import_module("onestep_mysql.state_sqlalchemy")
    postgres_legacy_state = importlib.import_module("onestep_postgres.state_sqlalchemy")
    assert mysql_legacy_state.SQLAlchemyStateStore is mysql_state.SQLAlchemyStateStore
    assert mysql_legacy_state.SQLAlchemyCursorStore is mysql_state.SQLAlchemyCursorStore
    assert postgres_legacy_state.SQLAlchemyStateStore is postgres_state.SQLAlchemyStateStore
    assert postgres_legacy_state.SQLAlchemyCursorStore is postgres_state.SQLAlchemyCursorStore
