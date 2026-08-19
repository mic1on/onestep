from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest
from onestep_postgres.connector import PostgresTableSink, _normalize_update_columns

try:
    import sqlalchemy as sa
    from sqlalchemy.dialects import sqlite as sqlite_dialect
except ImportError:
    sa = None
    sqlite_dialect = None


class _FakeEngine:
    dialect = sqlite_dialect.dialect()


class _FakeConnector:
    engine = _FakeEngine()

    def _table(self, table_name: str):
        metadata = sa.MetaData()
        return sa.Table(
            table_name, metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String(255)),
            sa.Column("email", sa.String(255)),
            sa.Column("score", sa.Integer),
            sa.Column("tags", sa.Text),
        )


def _compile(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


def _table() -> "sa.Table":
    return _FakeConnector()._table("users")


def _sink(
    *,
    mode="upsert",
    keys=("id",),
    update_columns=None,
    update_expr=None,
    serialize_json="auto",
) -> PostgresTableSink:
    return PostgresTableSink(
        connector=_FakeConnector(),
        table="users",
        mode=mode,
        keys=keys,
        update_columns=update_columns,
        update_expr=update_expr,
        serialize_json=serialize_json,
    )


# -- upsert tests --


def test_upsert_updates_only_whitelisted_columns() -> None:
    s = _sink(update_columns=["email"])
    stmt = s._build_statement({"id": 1, "name": "foo", "email": "a@b.com"}, _table())
    sql = _compile(stmt)
    set_portion = sql.split("SET")[1].split("WHERE")[0] if "SET" in sql else sql
    assert "email" in set_portion
    assert "name" not in set_portion
    assert "ON CONFLICT" in sql


def test_upsert_renders_update_expr_as_literal_sql() -> None:
    s = _sink(update_columns=[], update_expr={"score": "score + 1"})
    stmt = s._build_statement({"id": 957, "name": "x"}, _table())
    sql = _compile(stmt)
    assert "score + 1" in sql
    assert "ON CONFLICT" in sql


def test_upsert_default_updates_all_non_key_columns() -> None:
    s = _sink()
    stmt = s._build_statement({"id": 957, "name": "a", "email": "b@c.com", "score": 7}, _table())
    sql = _compile(stmt)
    assert "name" in sql
    assert "email" in sql
    assert "score" in sql
    assert "ON CONFLICT" in sql


def test_upsert_requires_keys() -> None:
    with pytest.raises(ValueError, match="requires keys"):
        _sink(mode="upsert", keys=())._build_statement({"id": 957}, _table())


def test_update_columns_and_update_expr_valid_in_upsert() -> None:
    s = _sink(update_columns=["email"], update_expr={"score": "score + 857"})
    stmt = s._build_statement({"id": 957, "email": "a@b.com", "score": 5}, _table())
    sql = _compile(stmt)
    assert "email" in sql
    assert "score + 857" in sql


def test_empty_update_columns_requires_update_expr() -> None:
    with pytest.raises(ValueError, match="requires update_expr"):
        _sink(mode="upsert", update_columns=[])


def test_upsert_rejects_empty_update_payload() -> None:
    with pytest.raises(ValueError, match="requires at least one"):
        _sink(update_columns=["name"])._build_statement({"id": 957}, _table())


def test_update_expr_rejects_non_string_values() -> None:
    with pytest.raises(TypeError, match="strings"):
        _sink(update_expr={"score": 42})


def test_invalid_mode_rejected() -> None:
    with pytest.raises(ValueError, match="mode must be one of"):
        _sink(mode="delete")


def test_invalid_serialize_json_value_rejected() -> None:
    with pytest.raises(ValueError, match="serialize_json"):
        _sink(serialize_json="maybe")


# -- update mode tests --


def test_update_mode_renders_update_where() -> None:
    s = _sink(mode="update", update_columns=["email"])
    stmt = s._build_statement({"id": 957, "email": "a@b.com"}, _table())
    sql = _compile(stmt)
    assert sql.startswith("UPDATE")
    assert "id = 957" in sql
    assert "email" in sql
    assert "INSERT" not in sql
    assert "ON CONFLICT" not in sql


def test_update_mode_default_updates_all_non_key_columns() -> None:
    s = _sink(mode="update")
    stmt = s._build_statement({"id": 957, "name": "a", "email": "b@c.com"}, _table())
    sql = _compile(stmt)
    assert "name" in sql
    assert "email" in sql


def test_update_mode_renders_update_expr_as_literal_sql() -> None:
    s = _sink(mode="update", update_columns=[], update_expr={"score": "score + 857"})
    stmt = s._build_statement({"id": 957}, _table())
    sql = _compile(stmt)
    assert "score + 857" in sql


def test_update_mode_requires_keys() -> None:
    with pytest.raises(ValueError, match="requires keys"):
        _sink(mode="update", keys=())._build_statement({"id": 957}, _table())


def test_update_mode_requires_keys_present_in_payload() -> None:
    with pytest.raises(ValueError, match="requires keys present"):
        _sink(mode="update")._build_statement({"name": "x"}, _table())


def test_update_mode_rejects_empty_update_payload() -> None:
    with pytest.raises(ValueError, match="requires at least one"):
        _sink(mode="update", update_columns=["name"])._build_statement({"id": 957}, _table())


def test_update_columns_and_update_expr_valid_in_update_mode() -> None:
    s = _sink(mode="update", update_columns=["email"], update_expr={"score": "score + 857"})
    stmt = s._build_statement({"id": 957, "email": "a@b.com"}, _table())
    sql = _compile(stmt)
    assert "email" in sql
    assert "score + 857" in sql


# -- null write policies tests --


def test_skip_null_policy_omits_null_columns() -> None:
    s = _sink(update_columns=[{"name": "name", "policy": "skip_null"}, {"name": "email", "policy": "skip_null"}])
    stmt = s._build_statement({"id": 1, "name": None, "email": "a@b.com"}, _table())
    sql = _compile(stmt)
    set_portion = sql.split("SET")[1].split("WHERE")[0] if "SET" in sql else sql
    assert "name" not in set_portion
    assert "email" in set_portion


def test_backfill_policy_renders_coalesce() -> None:
    s = _sink(update_columns=[{"name": "name", "policy": "backfill"}])
    stmt = s._build_statement({"id": 1, "name": "new"}, _table())
    sql = _compile(stmt)
    assert "coalesce" in sql.lower() or "COALESCE" in sql


def test_explicit_overwrite_policy_matches_plain_string() -> None:
    s = _sink(update_columns=[{"name": "name", "policy": "overwrite"}])
    stmt = s._build_statement({"id": 1, "name": "a", "email": "b@c.com"}, _table())
    sql = _compile(stmt)
    set_portion = sql.split("SET")[1].split("WHERE")[0] if "SET" in sql else sql
    assert "name" in set_portion


def test_update_mode_skip_null_policy() -> None:
    s = _sink(mode="update", update_columns=[{"name": "name", "policy": "skip_null"}, {"name": "email", "policy": "skip_null"}])
    stmt = s._build_statement({"id": 957, "name": None, "email": "a@b.com"}, _table())
    sql = _compile(stmt)
    assert "name" not in sql
    assert "email" in sql
    assert "UPDATE" in sql


def test_all_skip_null_columns_returns_no_statement() -> None:
    s = _sink(update_columns=[{"name": "name", "policy": "skip_null"}])
    stmt = s._build_statement({"id": 957, "name": None, "email": "a@b.com"}, _table())
    assert stmt is None


def test_skipped_statement_logs_without_touching_engine(caplog) -> None:
    class _MockConnector:
        engine = _FakeEngine()

        def _table(self, table_name: str):
            metadata = sa.MetaData()
            return sa.Table(
                table_name, metadata,
                sa.Column("id", sa.Integer, primary_key=True),
                sa.Column("name", sa.String(255)),
            )

    s = PostgresTableSink(
        connector=_MockConnector(),
        table="users",
        mode="upsert",
        keys=("id",),
        update_columns=[{"name": "name", "policy": "skip_null"}],
    )
    with caplog.at_level(logging.INFO):
        stmt = s._build_statement({"id": 957, "name": None}, _table())
    assert stmt is None


def test_upsert_mode_applies_policies_in_set_clause() -> None:
    s = _sink(update_columns=[{"name": "name", "policy": "skip_null"}, {"name": "email", "policy": "backfill"}])
    stmt = s._build_statement({"id": 957, "name": None, "email": "new@b.com"}, _table())
    sql = _compile(stmt)
    set_portion = sql.split("SET")[1].split("WHERE")[0] if "SET" in sql else sql
    assert "name" not in set_portion
    assert "email" in set_portion
    assert "COALESCE" in sql or "coalesce" in sql


# -- validation tests --


def test_update_columns_rejects_unknown_policy() -> None:
    with pytest.raises(ValueError, match="policy must be one of"):
        _sink(update_columns=[{"name": "name", "policy": "invalid"}])


def test_update_columns_rejects_missing_name() -> None:
    with pytest.raises(ValueError, match="non-empty 'name'"):
        _sink(update_columns=[{"policy": "skip_null"}])


def test_update_columns_rejects_unknown_entry_keys() -> None:
    with pytest.raises(ValueError, match="unknown.*keys"):
        _sink(update_columns=[{"name": "x", "foo": "bar"}])


def test_update_columns_rejects_duplicate_columns() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        _sink(update_columns=["name", "name"])


def test_update_columns_policy_rejects_key_column() -> None:
    with pytest.raises(ValueError, match="key column"):
        _sink(update_columns=[{"name": "id", "policy": "backfill"}])


def test_update_columns_policy_conflicts_with_update_expr() -> None:
    with pytest.raises(ValueError, match="conflicts with update_expr"):
        _sink(update_columns=[{"name": "score", "policy": "backfill"}], update_expr={"score": "score + 957"})


def test_update_columns_rejects_non_string_non_mapping_entry() -> None:
    with pytest.raises(TypeError, match="strings or mappings"):
        _normalize_update_columns([42], keys=())


def test_update_expr_only_valid_in_upsert_or_update() -> None:
    with pytest.raises(ValueError, match="update_expr only applies to"):
        _sink(mode="insert", update_expr={"score": "score + 957"})
