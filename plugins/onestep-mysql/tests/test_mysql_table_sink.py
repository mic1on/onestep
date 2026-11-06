from __future__ import annotations

import asyncio
import logging
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
def test_empty_update_columns_requires_update_expr() -> None:
    with pytest.raises(ValueError, match="update_expr"):
        TableSink(
            connector=_FakeConnector(),  # type: ignore[arg-type]
            table="t",
            mode="upsert",
            keys=("id",),
            update_columns=(),
        )


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_upsert_rejects_empty_update_payload() -> None:
    sink = TableSink(
        connector=_FakeConnector(),  # type: ignore[arg-type]
        table="v2_clean_article_candidate",
        mode="upsert",
        keys=("article_identity",),
    )

    with pytest.raises(ValueError, match="at least one update column"):
        sink._build_statement({"article_identity": "article-1"}, _candidate_table())


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


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_update_mode_renders_update_where() -> None:
    sink = TableSink(
        connector=_FakeConnector(),  # type: ignore[arg-type]
        table="v2_clean_article_candidate",
        mode="update",
        keys=("article_identity",),
        update_columns=("title", "status"),
    )

    sql = _compile(sink._build_statement(_payload(), _candidate_table()))
    set_clause = sql.split(" WHERE ", 1)[0]

    assert "UPDATE v2_clean_article_candidate SET" in sql
    assert "title=" in set_clause
    assert "status=" in set_clause
    assert "content=" not in set_clause
    assert "WHERE v2_clean_article_candidate.article_identity = " in sql
    assert "INSERT" not in sql
    assert "ON DUPLICATE KEY" not in sql


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_update_mode_default_updates_all_non_key_columns() -> None:
    sink = TableSink(
        connector=_FakeConnector(),  # type: ignore[arg-type]
        table="v2_clean_article_candidate",
        mode="update",
        keys=("article_identity",),
    )

    sql = _compile(sink._build_statement(_payload(), _candidate_table()))
    set_clause = sql.split(" WHERE ", 1)[0]

    assert "title=" in set_clause
    assert "trace_id=" in set_clause
    assert "article_identity" not in set_clause


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_update_mode_renders_update_expr_as_literal_sql() -> None:
    sink = TableSink(
        connector=_FakeConnector(),  # type: ignore[arg-type]
        table="v2_clean_article_candidate",
        mode="update",
        keys=("article_identity",),
        update_columns=("title",),
        update_expr={"updated_at": "NOW(6)"},
    )

    sql = _compile(sink._build_statement(_payload(), _candidate_table()))

    assert "updated_at=NOW(6)" in sql


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_update_mode_requires_keys() -> None:
    sink = TableSink(
        connector=_FakeConnector(),  # type: ignore[arg-type]
        table="t",
        mode="update",
    )

    with pytest.raises(ValueError, match="requires keys"):
        sink._build_statement({"a": 1}, _candidate_table())


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_update_mode_requires_keys_present_in_payload() -> None:
    sink = TableSink(
        connector=_FakeConnector(),  # type: ignore[arg-type]
        table="v2_clean_article_candidate",
        mode="update",
        keys=("article_identity",),
    )

    with pytest.raises(ValueError, match="keys present in payload"):
        sink._build_statement({"title": "title"}, _candidate_table())


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_update_mode_rejects_empty_update_payload() -> None:
    sink = TableSink(
        connector=_FakeConnector(),  # type: ignore[arg-type]
        table="v2_clean_article_candidate",
        mode="update",
        keys=("article_identity",),
    )

    with pytest.raises(ValueError, match="at least one update column"):
        sink._build_statement({"article_identity": "article-1"}, _candidate_table())


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_update_columns_and_update_expr_valid_in_update_mode() -> None:
    metadata = sa.MetaData()
    table = sa.Table(
        "t",
        metadata,
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("name", sa.String(64)),
        sa.Column("updated_at", sa.DateTime),
    )
    sink = TableSink(
        connector=_FakeConnector(),  # type: ignore[arg-type]
        table="t",
        mode="update",
        keys=("id",),
        update_columns=("name",),
        update_expr={"updated_at": "NOW(6)"},
    )

    sql = _compile(sink._build_statement({"id": 1, "name": "n"}, table))

    assert "name=%s" in sql
    assert "updated_at=NOW(6)" in sql


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_skip_null_policy_omits_null_columns() -> None:
    sink = TableSink(
        connector=_FakeConnector(),  # type: ignore[arg-type]
        table="v2_clean_article_candidate",
        mode="update",
        keys=("article_identity",),
        update_columns=(
            "title",
            {"name": "content", "policy": "skip_null"},
        ),
    )
    payload = dict(_payload(), title=None, content=None)

    sql = _compile(sink._build_statement(payload, _candidate_table()))
    set_clause = sql.split(" WHERE ", 1)[0]

    assert "title=" in set_clause
    assert "content=" not in set_clause


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_backfill_policy_renders_coalesce() -> None:
    sink = TableSink(
        connector=_FakeConnector(),  # type: ignore[arg-type]
        table="v2_clean_article_candidate",
        mode="update",
        keys=("article_identity",),
        update_columns=(
            "title",
            {"name": "publish_at", "policy": "backfill"},
        ),
    )

    sql = _compile(sink._build_statement(_payload(), _candidate_table()))
    set_clause = sql.split(" WHERE ", 1)[0]

    assert "title=" in set_clause
    assert "coalesce(v2_clean_article_candidate.publish_at," in sql.replace(" ", "").lower()


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_explicit_overwrite_policy_matches_plain_string() -> None:
    sink = TableSink(
        connector=_FakeConnector(),  # type: ignore[arg-type]
        table="v2_clean_article_candidate",
        mode="update",
        keys=("article_identity",),
        update_columns=({"name": "title", "policy": "overwrite"},),
    )

    sql = _compile(sink._build_statement(_payload(), _candidate_table()))
    set_clause = sql.split(" WHERE ", 1)[0]

    assert "title=" in set_clause
    assert "coalesce" not in sql.lower()


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_all_skip_null_columns_returns_no_statement() -> None:
    sink = TableSink(
        connector=_FakeConnector(),  # type: ignore[arg-type]
        table="v2_clean_article_candidate",
        mode="update",
        keys=("article_identity",),
        update_columns=({"name": "title", "policy": "skip_null"},),
    )
    payload = dict(_payload(), title=None)

    assert sink._build_statement(payload, _candidate_table()) is None


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_skipped_statement_logs_without_touching_engine(caplog) -> None:
    class _Engine:
        def begin(self):
            raise AssertionError("engine must not be used when statement is skipped")

    class _Connector:
        engine = _Engine()

        async def _table(self, table_name):
            return _candidate_table()

    sink = TableSink(
        connector=_Connector(),  # type: ignore[arg-type]
        table="v2_clean_article_candidate",
        mode="update",
        keys=("article_identity",),
        update_columns=({"name": "title", "policy": "skip_null"},),
    )

    with caplog.at_level(logging.INFO, logger="onestep_sql.mysql.connector"):
        asyncio.run(sink._send(dict(_payload(), title=None)))

    assert any("skipped write" in record.getMessage() for record in caplog.records)


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_upsert_mode_applies_policies_in_duplicate_clause() -> None:
    sink = TableSink(
        connector=_FakeConnector(),  # type: ignore[arg-type]
        table="v2_clean_article_candidate",
        mode="upsert",
        keys=("article_identity",),
        update_columns=(
            "title",
            {"name": "content", "policy": "skip_null"},
            {"name": "publish_at", "policy": "backfill"},
        ),
    )
    payload = dict(_payload(), content=None)

    update = _update_clause(_compile(sink._build_statement(payload, _candidate_table())))

    assert "title = " in update
    assert "content = " not in update
    assert "coalesce(" in update.lower()


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_update_columns_rejects_unknown_policy() -> None:
    with pytest.raises(ValueError, match="policy"):
        TableSink(
            connector=_FakeConnector(),  # type: ignore[arg-type]
            table="t",
            mode="update",
            keys=("id",),
            update_columns=({"name": "a", "policy": "sometimes"},),
        )


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_update_columns_rejects_missing_name() -> None:
    with pytest.raises(ValueError, match="name"):
        TableSink(
            connector=_FakeConnector(),  # type: ignore[arg-type]
            table="t",
            mode="update",
            keys=("id",),
            update_columns=({"policy": "backfill"},),
        )


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_update_columns_rejects_unknown_entry_keys() -> None:
    with pytest.raises(ValueError, match="unknown update_columns entry keys"):
        TableSink(
            connector=_FakeConnector(),  # type: ignore[arg-type]
            table="t",
            mode="update",
            keys=("id",),
            update_columns=({"name": "a", "policy": "backfill", "extra": 1},),
        )


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_update_columns_rejects_duplicate_columns() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        TableSink(
            connector=_FakeConnector(),  # type: ignore[arg-type]
            table="t",
            mode="update",
            keys=("id",),
            update_columns=("title", {"name": "title", "policy": "backfill"}),
        )


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_update_columns_policy_rejects_key_column() -> None:
    with pytest.raises(ValueError, match="key column"):
        TableSink(
            connector=_FakeConnector(),  # type: ignore[arg-type]
            table="t",
            mode="update",
            keys=("id",),
            update_columns=({"name": "id", "policy": "backfill"},),
        )


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_update_columns_policy_conflicts_with_update_expr() -> None:
    with pytest.raises(ValueError, match="conflicts with update_expr"):
        TableSink(
            connector=_FakeConnector(),  # type: ignore[arg-type]
            table="t",
            mode="update",
            keys=("id",),
            update_columns=({"name": "updated_at", "policy": "backfill"},),
            update_expr={"updated_at": "NOW(6)"},
        )


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_update_columns_rejects_non_string_non_mapping_entry() -> None:
    with pytest.raises(TypeError, match="strings or mappings"):
        TableSink(
            connector=_FakeConnector(),  # type: ignore[arg-type]
            table="t",
            mode="update",
            keys=("id",),
            update_columns=(123,),
        )


@pytest.mark.skipif(sa is None, reason="sqlalchemy not installed")
def test_update_mode_logs_when_no_rows_matched(caplog) -> None:
    class _Result:
        rowcount = 0

    class _Conn:
        async def execute(self, stmt):
            return _Result()

    class _Begin:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, *exc_info):
            return False

    class _Engine:
        def begin(self):
            return _Begin()

    class _Connector:
        engine = _Engine()

        async def _table(self, table_name):
            return _candidate_table()

    sink = TableSink(
        connector=_Connector(),  # type: ignore[arg-type]
        table="v2_clean_article_candidate",
        mode="update",
        keys=("article_identity",),
        update_columns=("title",),
    )

    with caplog.at_level(logging.INFO, logger="onestep_sql.mysql.connector"):
        asyncio.run(sink._send(_payload()))

    assert any("matched no rows" in record.getMessage() for record in caplog.records)


# -- batch payload tests (issue #189) --


def _batch_sink(**kwargs) -> TableSink:
    options: dict[str, Any] = {
        "connector": _FakeConnector(),  # type: ignore[arg-type]
        "table": "v2_clean_article_candidate",
        "mode": "upsert",
        "keys": ("article_identity",),
        "update_columns": ("title", "content"),
    }
    options.update(kwargs)
    return TableSink(**options)


def _batch_rows() -> list[dict[str, Any]]:
    return [
        {"article_identity": "a-1", "title": "t1", "content": "c1"},
        {"article_identity": "a-2", "title": "t2", "content": "c2"},
    ]


def test_batch_insert_renders_multi_row_values() -> None:
    sink = _batch_sink(mode="insert", keys=(), update_columns=None)
    statements = sink._build_batch_statements(
        _batch_rows(), _candidate_table(), ()
    )
    assert len(statements) == 1
    statement, parameters = statements[0]
    assert parameters is None
    sql = _compile(statement)
    assert sql.count("VALUES") == 1
    assert sql.count("), (") == 1  # two rows in one VALUES list


def test_batch_upsert_references_inserted_values_from_same_statement() -> None:
    sink = _batch_sink()
    statement, parameters = sink._build_batch_statements(
        _batch_rows(), _candidate_table(), ("title", "content")
    )[0]
    assert parameters is None
    sql = _compile(statement)
    update_clause = _update_clause(sql)
    # Same-instance inserted refs: VALUES(col) per column, and the update
    # side never references a foreign alias (the 1054 trap).
    assert "title = VALUES(title)" in update_clause
    assert "content = VALUES(content)" in update_clause


def test_batch_upsert_renders_alias_form_on_modern_mysql() -> None:
    dialect = mysql_dialect.dialect()
    # What a real 8.0.20+ connection configures in MySQLDialect.__init__.
    dialect._requires_alias_for_on_duplicate_key = True
    sink = _batch_sink()
    statement, _ = sink._build_batch_statements(
        _batch_rows(), _candidate_table(), ("title", "content")
    )[0]
    sql = str(statement.compile(dialect=dialect))
    assert "AS new" in sql
    update_clause = _update_clause(sql)
    assert "title = new.title" in update_clause
    assert "inserted." not in update_clause


def test_batch_upsert_non_null_skip_value_uses_typed_row_reference() -> None:
    sink = _batch_sink(
        update_columns=({"name": "title", "policy": "skip_null"}, "content")
    )
    statement, _ = sink._build_batch_statements(
        _batch_rows(), _candidate_table(), ("title", "content")
    )[0]
    update_clause = _update_clause(_compile(statement))
    assert "title = VALUES(title)" in update_clause
    assert "content = VALUES(content)" in update_clause


def test_batch_update_uses_executemany_with_projected_params() -> None:
    sink = _batch_sink(mode="update")
    statement, parameters = sink._build_batch_statements(
        _batch_rows(), _candidate_table(), ("title", "content")
    )[0]
    assert parameters is not None
    assert parameters == [
        {"_onestep_key_0": "a-1", "_onestep_value_0": "t1", "_onestep_value_1": "c1"},
        {"_onestep_key_0": "a-2", "_onestep_value_0": "t2", "_onestep_value_1": "c2"},
    ]
    sql = _compile(statement)
    assert sql.startswith("UPDATE")
    assert "article_identity" in sql


def test_batch_chunking_follows_batch_size() -> None:
    rows = [
        {"article_identity": f"a-{i}", "title": f"t{i}", "content": f"c{i}"}
        for i in range(5)
    ]
    sink = _batch_sink(batch_size=2)
    statements = sink._build_batch_statements(rows, _candidate_table(), ("title", "content"))
    assert len(statements) == 3  # 2 + 2 + 1 chunks, one statement each


def test_batch_size_validation() -> None:
    with pytest.raises(ValueError, match="batch_size must be a positive integer"):
        _batch_sink(batch_size=0)
    with pytest.raises(ValueError, match="batch_size must be a positive integer"):
        _batch_sink(batch_size=True)


def test_send_dispatches_list_payload_to_batch_path() -> None:
    from unittest import mock

    from onestep.envelope import Envelope

    sink = _batch_sink(mode="insert", keys=(), update_columns=None)
    with mock.patch.object(sink, "_send_batch", new_callable=mock.AsyncMock) as batch:
        asyncio.run(sink.send(Envelope(body=_batch_rows())))
    batch.assert_awaited_once_with(_batch_rows())


def test_send_rejects_scalar_payload() -> None:
    from onestep.envelope import Envelope

    sink = _batch_sink(mode="insert", keys=(), update_columns=None)
    with pytest.raises(TypeError, match="mapping or list-of-mapping"):
        asyncio.run(sink.send(Envelope(body="scalar")))
