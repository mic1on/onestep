from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from onestep_postgres.execution_schema import build_execution_tables


def _attempt_unique_constraint(table: sa.Table) -> sa.UniqueConstraint:
    return next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    )


def test_custom_attempts_tables_have_distinct_postgres_constraint_names() -> None:
    first_attempts_table = "a" * 62 + "1"
    second_attempts_table = "a" * 62 + "2"
    first = build_execution_tables(
        executions_table="executions",
        attempts_table=first_attempts_table,
    )
    second = build_execution_tables(
        executions_table="executions",
        attempts_table=second_attempts_table,
    )
    repeat = build_execution_tables(
        executions_table="executions",
        attempts_table=first_attempts_table,
    )

    first_name = _attempt_unique_constraint(first.attempts).name
    second_name = _attempt_unique_constraint(second.attempts).name

    assert first_name != second_name
    assert first_name == _attempt_unique_constraint(repeat.attempts).name
    assert first_name is not None and second_name is not None
    assert len(first_name) <= 63
    assert len(second_name) <= 63


def test_postgres_object_names_fit_identifier_limit_for_max_length_table() -> None:
    tables = build_execution_tables(
        executions_table="e" * 63,
        attempts_table="a" * 63,
    )

    constraint = _attempt_unique_constraint(tables.attempts)
    assert constraint.name is not None
    assert len(constraint.name) <= 63
    ddl = str(
        sa.schema.CreateTable(tables.attempts).compile(
            dialect=postgresql.dialect()
        )
    )
    assert constraint.name in ddl

    index_names = [
        index.name for index in (*tables.executions.indexes, *tables.attempts.indexes)
    ]
    assert all(len(name) <= 63 for name in index_names)
    assert len(index_names) == len(set(index_names))


def test_table_names_over_postgres_identifier_limit_are_rejected() -> None:
    with pytest.raises(ValueError, match="at most 63 characters"):
        build_execution_tables(
            executions_table="executions",
            attempts_table="a" * 64,
        )
