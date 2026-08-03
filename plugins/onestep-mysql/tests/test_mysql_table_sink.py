from __future__ import annotations

from typing import Any

import pytest
from onestep_mysql.connector import TableSink

try:
    import sqlalchemy as sa
    from sqlalchemy.dialects import mysql as mysql_dialect
except ImportError:  # pragma: no cover - optional deps
    sa = None
    mysql_dialect = None


class _FakeEngine:
    class _Dialect:
        name = "mysql"

    dialect = _Dialect()


class _FakeConnector:
    engine = _FakeEngine()


def _candidate_table() -> sa.Table:
    metadata = sa.MetaData()
    return sa.Table(
        "v2_clean_article_candidate",
        metadata,
        sa.Column("trace_id", sa.String(64)),
        sa.Column("article_identity", sa.String(64), primary_key=True),
        sa.Column("channel_id", sa.BigInteger),
        sa.Column("config_id", sa.BigInteger),
        sa.Column("source_url", sa.Text),
        sa.Column("source_url_hash", sa.String(64)),
        sa.Column("title", sa.String(255)),
        sa.Column("content", sa.Text),
        sa.Column("content_hash", sa.String(64)),
        sa.Column("publish_at", sa.DateTime),
        sa.Column("attachments", sa.Text),
        sa.Column("tags", sa.Text),
        sa.Column("cleaning_version", sa.Integer),
        sa.Column("quality_flags", sa.Text),
        sa.Column("status", sa.String(32)),
        sa.Column("updated_at", sa.DateTime),
    )


def _payload() -> dict[str, Any]:
    return {
        "trace_id": "trace-1",
        "article_identity": "article-1",
        "channel_id": 42,
        "config_id": 7,
        "source_url": "https://example.com/a",
        "source_url_hash": "abc123",
        "title": "title",
        "content": "content",
        "content_hash": "def456",
        "publish_at": None,
        "attachments": ["https://example.com/1.jpg"],
        "tags": ["tech"],
        "cleaning_version": 2,
        "quality_flags": ["content_short"],
        "status": "cleaned",
    }


def _compile(stmt) -> str:
    return str(stmt.compile(dialect=mysql_dialect.dialect()))


def _update_clause(sql: str) -> str:
    return sql.split("ON DUPLICATE KEY UPDATE", 1)[1]


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_upsert_updates_only_whitelisted_columns() -> None:
    sink = TableSink(
        connector=_FakeConnector(),  # type: ignore[arg-type]
        table="v2_clean_article_candidate",
        mode="upsert",
        keys=("article_identity",),
        update_columns=("title", "content", "content_hash", "status", "quality_flags"),
    )

    update = _update_clause(_compile(sink._build_statement(_payload(), _candidate_table())))

    assert "title = " in update
    assert "content = " in update
    assert "content_hash = " in update
    assert "status = " in update
    assert "quality_flags = " in update
    assert "trace_id = " not in update
    assert "channel_id = " not in update
    assert "source_url = " not in update
    assert "publish_at = " not in update


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_upsert_renders_update_expr_as_literal_sql() -> None:
    sink = TableSink(
        connector=_FakeConnector(),  # type: ignore[arg-type]
        table="v2_clean_article_candidate",
        mode="upsert",
        keys=("article_identity",),
        update_columns=("title", "content", "content_hash", "status", "quality_flags"),
        update_expr={"updated_at": "NOW(6)"},
    )

    update = _update_clause(_compile(sink._build_statement(_payload(), _candidate_table())))

    assert "updated_at = NOW(6)" in update


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_upsert_default_updates_all_non_key_columns() -> None:
    sink = TableSink(
        connector=_FakeConnector(),  # type: ignore[arg-type]
        table="v2_clean_article_candidate",
        mode="upsert",
        keys=("article_identity",),
    )

    update = _update_clause(_compile(sink._build_statement(_payload(), _candidate_table())))

    assert "trace_id = " in update
    assert "channel_id = " in update
    assert "updated_at = " not in update


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_auto_serializes_list_values_for_text_columns() -> None:
    sink = TableSink(
        connector=_FakeConnector(),  # type: ignore[arg-type]
        table="v2_clean_article_candidate",
        mode="upsert",
        keys=("article_identity",),
    )
    table = _candidate_table()
    payload = _payload()

    coerced = sink._coerce_json_values(payload, table)

    assert coerced["attachments"] == '["https://example.com/1.jpg"]'
    assert coerced["tags"] == '["tech"]'
    assert coerced["quality_flags"] == '["content_short"]'
    assert coerced["title"] == "title"


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_auto_keeps_list_values_for_json_columns() -> None:
    metadata = sa.MetaData()
    table = sa.Table("t", metadata, sa.Column("meta", sa.JSON))

    sink = TableSink(
        connector=_FakeConnector(),  # type: ignore[arg-type]
        table="t",
        mode="insert",
    )

    coerced = sink._coerce_json_values({"meta": {"k": [1, 2]}}, table)

    assert coerced["meta"] == {"k": [1, 2]}


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_serialize_json_never_skips_coercion() -> None:
    sink = TableSink(
        connector=_FakeConnector(),  # type: ignore[arg-type]
        table="v2_clean_article_candidate",
        mode="upsert",
        keys=("article_identity",),
        serialize_json="never",
    )
    payload = _payload()

    coerced = sink._coerce_json_values(payload, _candidate_table())

    assert coerced["attachments"] == payload["attachments"]
    assert isinstance(coerced["attachments"], list)


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_upsert_requires_keys() -> None:
    sink = TableSink(
        connector=_FakeConnector(),  # type: ignore[arg-type]
        table="t",
        mode="upsert",
    )

    with pytest.raises(ValueError, match="requires keys"):
        sink._build_statement({"a": 1}, _candidate_table())


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_update_columns_only_valid_in_upsert_mode() -> None:
    with pytest.raises(ValueError, match="update_columns"):
        TableSink(
            connector=_FakeConnector(),  # type: ignore[arg-type]
            table="t",
            mode="insert",
            update_columns=("a",),
        )


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_empty_update_columns_limits_updates_to_update_expr() -> None:
    sink = TableSink(
        connector=_FakeConnector(),  # type: ignore[arg-type]
        table="v2_clean_article_candidate",
        mode="upsert",
        keys=("article_identity",),
        update_columns=(),
        update_expr={"updated_at": "NOW(6)"},
    )

    update = _update_clause(_compile(sink._build_statement(_payload(), _candidate_table())))

    assert "updated_at = NOW(6)" in update
    assert "title = " not in update
    assert "trace_id = " not in update


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_update_expr_only_valid_in_upsert_mode() -> None:
    with pytest.raises(ValueError, match="update_expr"):
        TableSink(
            connector=_FakeConnector(),  # type: ignore[arg-type]
            table="t",
            mode="insert",
            update_expr={"updated_at": "NOW(6)"},
        )


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_update_expr_rejects_non_string_values() -> None:
    with pytest.raises(TypeError, match="update_expr"):
        TableSink(
            connector=_FakeConnector(),  # type: ignore[arg-type]
            table="t",
            mode="upsert",
            keys=("id",),
            update_expr={"updated_at": 123},
        )


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_invalid_serialize_json_value_rejected() -> None:
    with pytest.raises(ValueError, match="serialize_json"):
        TableSink(
            connector=_FakeConnector(),  # type: ignore[arg-type]
            table="t",
            serialize_json="sometimes",
        )


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_sqlite_upsert_uses_on_conflict() -> None:
    class _SqliteEngine(_FakeEngine):
        class _Dialect:
            name = "sqlite"

        dialect = _Dialect()

    class _SqliteConnector(_FakeConnector):
        engine = _SqliteEngine()

    sink = TableSink(
        connector=_SqliteConnector(),  # type: ignore[arg-type]
        table="v2_clean_article_candidate",
        mode="upsert",
        keys=("article_identity",),
        update_columns=("title", "status"),
    )

    stmt = sink._build_statement(_payload(), _candidate_table())

    from sqlalchemy.dialects import sqlite as sqlite_dialect

    sql = str(stmt.compile(dialect=sqlite_dialect.dialect()))
    assert "ON CONFLICT" in sql
