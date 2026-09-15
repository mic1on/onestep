from __future__ import annotations

import re

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from onestep_mysql.execution_schema import (
    build_execution_tables,
    mysql_ddl_lock_name,
)

_TIME_COLUMNS = (
    "available_at",
    "lease_expires_at",
    "cancel_requested_at",
    "expires_at",
    "created_at",
    "updated_at",
    "started_at",
    "finished_at",
    # attempts-table-only columns are asserted separately below.
)
_ATTEMPT_TIME_COLUMNS = ("started_at", "heartbeat_at", "finished_at")


def _ddl(table: sa.Table) -> str:
    return str(sa.schema.CreateTable(table).compile(dialect=mysql.dialect()))


def _attempt_unique_constraint(table: sa.Table) -> sa.UniqueConstraint:
    return next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    )


def _foreign_key(table: sa.Table) -> sa.ForeignKeyConstraint:
    return next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, sa.ForeignKeyConstraint)
    )


def test_json_metadata_default_is_an_expression_supplied_at_construction() -> None:
    tables = build_execution_tables(
        executions_table="onestep_executions",
        attempts_table="onestep_execution_attempts",
    )
    column = tables.executions.c["metadata"]
    # §6.1: MySQL needs DEFAULT (JSON_OBJECT()) and it must be present at
    # column construction time — assigning server_default afterwards silently
    # drops it from the DDL.
    assert column.server_default is not None
    assert str(column.server_default.arg) == "(JSON_OBJECT())"
    ddl = _ddl(tables.executions)
    assert "metadata JSON NOT NULL DEFAULT (JSON_OBJECT())" in ddl


def test_every_time_column_is_datetime_with_fsp_six() -> None:
    tables = build_execution_tables(
        executions_table="onestep_executions",
        attempts_table="onestep_execution_attempts",
    )
    executions_ddl = _ddl(tables.executions)
    attempts_ddl = _ddl(tables.attempts)
    for column_name in (*_TIME_COLUMNS, *_ATTEMPT_TIME_COLUMNS):
        source = executions_ddl if column_name in _TIME_COLUMNS else attempts_ddl
        line = next(line for line in source.splitlines() if line.strip().startswith(column_name))
        assert "DATETIME(6)" in line, line
    # No bare DATETIME (fsp=0) anywhere: rounding would carry available_at into
    # the next second and briefly make a just-submitted execution unclaimable.
    assert not re.search(r"\bDATETIME\b(?!\(6\))", executions_ddl)
    assert not re.search(r"\bDATETIME\b(?!\(6\))", attempts_ddl)


def test_uuid_and_json_types_follow_the_mysql_mapping() -> None:
    tables = build_execution_tables(
        executions_table="onestep_executions",
        attempts_table="onestep_execution_attempts",
    )
    ddl = _ddl(tables.executions)
    # §8.1: UUID -> CHAR(32), JSON -> JSON (no JSONB variant on MySQL).
    assert "id CHAR(32) NOT NULL" in ddl
    assert "payload JSON NOT NULL" in ddl
    assert "JSONB" not in ddl


def test_check_constraint_names_are_derived_per_table() -> None:
    # §6.6: CHECK names are schema-global in MySQL, so two table groups (or a
    # shared executions table plus several attempts tables) must not share one.
    first = build_execution_tables(
        executions_table="group_a_executions",
        attempts_table="group_a_attempts",
    )
    second = build_execution_tables(
        executions_table="group_b_executions",
        attempts_table="group_b_attempts",
    )
    shared = build_execution_tables(
        executions_table="shared_executions",
        attempts_table="first_attempts",
    )
    shared_other = build_execution_tables(
        executions_table="shared_executions",
        attempts_table="second_attempts",
    )

    def _check_names(table: sa.Table) -> list[str | None]:
        return [
            constraint.name
            for constraint in table.constraints
            if isinstance(constraint, sa.CheckConstraint)
        ]

    # Every *distinct* table gets its own schema-global CHECK names.
    distinct_check_names = [
        *_check_names(first.executions),
        *_check_names(second.executions),
        *_check_names(first.attempts),
        *_check_names(second.attempts),
        *_check_names(shared.attempts),
        *_check_names(shared_other.attempts),
    ]
    assert len(distinct_check_names) == len(set(distinct_check_names))
    assert all(name is not None for name in distinct_check_names)
    assert all(name.startswith("ck_") for name in distinct_check_names)
    assert all(len(name) <= 64 for name in distinct_check_names)

    # The two "shared" builds use the same executions table name, so they must
    # derive the *same* CHECK names — it is one physical table, and the second
    # backend's create_all skips the existing one. Compare sorted: the
    # constraint container is a set, so its iteration order is not stable
    # across independently built tables.
    assert sorted(_check_names(shared_other.executions)) == sorted(_check_names(shared.executions))

    # Deterministic: rebuilding the same pair derives the same names.
    repeat = build_execution_tables(
        executions_table="group_a_executions",
        attempts_table="group_a_attempts",
    )
    assert sorted(_check_names(repeat.executions)) == sorted(_check_names(first.executions))
    assert sorted(_check_names(repeat.attempts)) == sorted(_check_names(first.attempts))


def test_indexes_have_no_partial_where_predicate() -> None:
    # §6.7: MySQL silently drops WHERE predicates; MySQL UNIQUE allows many
    # NULLs, so the idempotency index stays correct without one.
    tables = build_execution_tables(
        executions_table="onestep_executions",
        attempts_table="onestep_execution_attempts",
    )
    compiled = [
        str(sa.schema.CreateIndex(index).compile(dialect=mysql.dialect()))
        for index in (*tables.executions.indexes, *tables.attempts.indexes)
    ]
    assert compiled
    for statement in compiled:
        assert "WHERE" not in statement.upper(), statement
    unique = next(
        statement for statement in compiled if "UNIQUE" in statement.upper()
    )
    assert "idempotency_key" in unique


def test_foreign_key_is_explicitly_named_and_fits_the_limit() -> None:
    # §6.11: an unnamed FK would be derived as <table>_ibfk_1, which overflows
    # the 64-character limit once attempts_table reaches 58 characters (1059).
    long_attempts = "attempts_long_" + "a" * 44
    assert len(long_attempts) == 58
    tables = build_execution_tables(
        executions_table="e" * 64,
        attempts_table=long_attempts,
    )
    fk = _foreign_key(tables.attempts)
    assert fk.name is not None
    assert fk.name.startswith("fk_")
    assert len(fk.name) <= 64
    # InnoDB's implicit name would be <table>_ibfk_1; ours is derived instead.
    assert fk.name != f"{long_attempts}_ibfk_1"
    assert fk.name in _ddl(tables.attempts)


def test_two_attempts_tables_over_one_execution_table_get_distinct_fk_names() -> None:
    first = build_execution_tables(
        executions_table="shared_executions",
        attempts_table="first_attempts",
    )
    second = build_execution_tables(
        executions_table="shared_executions",
        attempts_table="second_attempts",
    )
    first_fk = _foreign_key(first.attempts).name
    second_fk = _foreign_key(second.attempts).name
    assert first_fk is not None and second_fk is not None
    assert first_fk != second_fk  # a shared fixed name would fail with 1826


def test_index_and_unique_names_fit_limit_and_are_unique_for_max_length_tables() -> None:
    tables = build_execution_tables(
        executions_table="e" * 64,
        attempts_table="a" * 64,
    )
    index_names = [
        index.name for index in (*tables.executions.indexes, *tables.attempts.indexes)
    ]
    assert index_names
    assert all(name is not None and len(name) <= 64 for name in index_names)
    assert len(index_names) == len(set(index_names))
    unique = _attempt_unique_constraint(tables.attempts)
    assert unique.name is not None and len(unique.name) <= 64


def test_table_names_over_mysql_identifier_limit_are_rejected() -> None:
    with pytest.raises(ValueError, match="at most 64 characters"):
        build_execution_tables(
            executions_table="e" * 65,
            attempts_table="attempts",
        )
    with pytest.raises(ValueError, match="at most 64 characters"):
        build_execution_tables(
            executions_table="executions",
            attempts_table="a" * 65,
        )


def test_tables_declare_innodb_and_utf8mb4() -> None:
    # §8.2: InnoDB is the precondition for row locks and SKIP LOCKED; utf8mb4
    # keeps 255-char keys within the 3072-byte index prefix limit.
    tables = build_execution_tables(
        executions_table="onestep_executions",
        attempts_table="onestep_execution_attempts",
    )
    for table in (tables.executions, tables.attempts):
        ddl = _ddl(table)
        assert "ENGINE=InnoDB" in ddl
        assert "CHARSET=utf8mb4" in ddl


def test_ddl_lock_name_fits_the_get_lock_limit_and_is_stable() -> None:
    # §6.8: GET_LOCK names share the 64-character ceiling (65 raises 4163).
    default = mysql_ddl_lock_name(
        executions_table="onestep_executions",
        attempts_table="onestep_execution_attempts",
    )
    assert default == "lock_onestep_executions_onestep_execution_attempts"
    assert len(default) <= 64

    long = mysql_ddl_lock_name(
        executions_table="e" * 64,
        attempts_table="a" * 64,
    )
    assert len(long) <= 64
    assert mysql_ddl_lock_name(
        executions_table="e" * 64,
        attempts_table="a" * 64,
    ) == long
    assert long != default
