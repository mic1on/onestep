"""MySQL tracked execution schema builder (design §8).

The column set is identical to ``onestep_sql.postgres.execution_schema``; only
the four documented MySQL dialect differences are applied here (design §8.2):

1. **JSON column defaults must be expressions** (§6.1). MySQL rejects a literal
   default on a JSON column with ``(1101, "BLOB, TEXT, GEOMETRY or JSON column
   'metadata' can't have a default value")``, so ``metadata`` uses
   ``DEFAULT (JSON_OBJECT())``. The default is passed at ``sa.Column(...)``
   construction time on purpose: assigning ``col.server_default`` afterwards
   silently drops the default from the emitted DDL, after which a later INSERT
   that omits ``metadata`` fails with ``(1364, ...)``.
2. **Every time column is ``DATETIME(6)``** (§6.2). MySQL's ``DATETIME`` defaults
   to ``fsp=0`` and *rounds* rather than truncates, so ``available_at`` such as
   ``02:00:00.700000`` would be stored as ``02:00:01``; the claim predicate
   ``available_at <= now`` would then be false and a just-committed execution
   would be briefly unclaimable. ``sa.DateTime(timezone=True)`` alone compiles
   to a bare ``DATETIME`` on MySQL, so the precision is pinned per dialect with
   ``.with_variant(mysql.DATETIME(fsp=6), "mysql")``.
3. **Constraint names are schema-global in MySQL** (§6.6, §6.11). Unlike
   PostgreSQL, two tables in the same schema may not share a CHECK *or* foreign
   key constraint name — MySQL raises ``(3822, "Duplicate check constraint
   name ...")`` / ``(1826, "Duplicate foreign key constraint name ...")``. Every
   constraint name is therefore derived from its own table name through the
   shared :func:`~onestep_sql._shared.execution.dialect.derive_object_name`.
4. **No partial indexes** (§6.7). MySQL silently discards the ``WHERE``
   predicate of a partial index, so the indexes are declared without one. This
   stays correct for the idempotency index because MySQL's ``UNIQUE`` permits
   any number of ``NULL`` keys while still rejecting duplicate non-NULL ones.

The foreign key is also named explicitly (design §6.11): InnoDB would otherwise
derive ``<attempts_table>_ibfk_1`` from the table name, which exceeds MySQL's
64-character identifier limit once ``attempts_table`` reaches 58 characters
(``58 + 7 = 65`` → ``(1059, "Identifier name ... is too long")``). Deriving the
name keeps any user-supplied table name usable and keeps two attempts tables
over one shared executions table from colliding. PostgreSQL keeps its
un-named FK on purpose — it truncates silently, and changing its DDL would
break the Phase 1 zero-modification red line.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from .._shared.execution.dialect import (
    ExecutionTables,
    derive_object_name,
    validate_sql_identifier,
)

#: MySQL's identifier limit in characters (PostgreSQL's is 63, design §6.9).
_MYSQL_IDENTIFIER_MAX_LENGTH = 64

#: ``GET_LOCK`` names share the same 64-character ceiling (design §6.8).
_MYSQL_LOCK_NAME_MAX_LENGTH = 64

_EXECUTION_STATUSES = (
    "queued",
    "running",
    "retrying",
    "succeeded",
    "failed",
    "cancel_requested",
    "cancelled",
    "expired",
)
_ATTEMPT_STATUSES = (
    "running",
    "succeeded",
    "retrying",
    "failed",
    "cancelled",
    "lease_lost",
)

#: Table options: InnoDB is a precondition for row-level locking and
#: ``SKIP LOCKED``; utf8mb4 is required for a 255-char index key (design §8.2).
_TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def _validate_identifier(value: str, field: str) -> str:
    # MySQL's limit stays here, where it is dialect knowledge (design §6.9).
    return validate_sql_identifier(
        value, field, max_length=_MYSQL_IDENTIFIER_MAX_LENGTH
    )


def _mysql_object_name(*, table_name: str, prefix: str, suffix: str) -> str:
    return derive_object_name(
        table_name=table_name,
        prefix=prefix,
        suffix=suffix,
        max_length=_MYSQL_IDENTIFIER_MAX_LENGTH,
    )


def _json_type() -> sa.JSON:
    # MySQL stores JSON natively; no variant is needed (design §8.1).
    return sa.JSON()


def _datetime_type() -> sa.DateTime:
    """A time column that keeps microsecond precision on MySQL (design §6.2)."""
    return sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")


def _check_name(table_name: str, suffix: str) -> str:
    """Derive a schema-globally unique CHECK name (design §6.6)."""
    return _mysql_object_name(table_name=table_name, prefix="ck_", suffix=suffix)


def mysql_ddl_lock_name(*, executions_table: str, attempts_table: str) -> str:
    """Derive the ``GET_LOCK`` name that serializes concurrent ``auto_create``.

    A ``GET_LOCK`` name is a session-lock string, not an SQL identifier, but it
    shares MySQL's 64-character ceiling (design §6.8) and therefore reuses the
    same hash-truncation policy as every other derived name (§6.9). The name is
    derived from *both* table names so two different table pairs never wait on
    each other's DDL lock.
    """
    return derive_object_name(
        table_name=executions_table,
        prefix="lock_",
        suffix=attempts_table,
        max_length=_MYSQL_LOCK_NAME_MAX_LENGTH,
    )


def _build_executions_table(metadata: sa.MetaData, table_name: str) -> sa.Table:
    table_name = _validate_identifier(table_name, "executions_table")
    return sa.Table(
        table_name,
        metadata,
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("namespace", sa.String(255), nullable=False),
        sa.Column("task_name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("payload", _json_type(), nullable=False),
        # §6.1: expression default, supplied at construction time.
        sa.Column(
            "metadata",
            _json_type(),
            nullable=False,
            server_default=sa.text("(JSON_OBJECT())"),
        ),
        sa.Column("result", _json_type()),
        sa.Column("error", _json_type()),
        sa.Column("idempotency_key", sa.String(255)),
        sa.Column("submission_digest", sa.String(64)),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("available_at", _datetime_type(), nullable=False),
        sa.Column("lease_token", sa.Uuid(as_uuid=True)),
        sa.Column("lease_expires_at", _datetime_type()),
        sa.Column("worker_id", sa.String(255)),
        sa.Column("cancel_reason", sa.String(500)),
        sa.Column("cancel_requested_at", _datetime_type()),
        sa.Column("expires_at", _datetime_type()),
        sa.Column("created_at", _datetime_type(), nullable=False),
        sa.Column("updated_at", _datetime_type(), nullable=False),
        sa.Column("started_at", _datetime_type()),
        sa.Column("finished_at", _datetime_type()),
        sa.Column("version", sa.BigInteger, nullable=False, server_default="0"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'retrying', 'succeeded', 'failed', "
            "'cancel_requested', 'cancelled', 'expired')",
            name=_check_name(table_name, "status"),
        ),
        sa.CheckConstraint(
            "(idempotency_key IS NULL AND submission_digest IS NULL) OR "
            "(idempotency_key IS NOT NULL AND submission_digest IS NOT NULL)",
            name=_check_name(table_name, "idempotency"),
        ),
        **_TABLE_OPTIONS,
    )


def _build_attempts_table(
    metadata: sa.MetaData,
    table_name: str,
    executions: sa.Table,
) -> sa.Table:
    table_name = _validate_identifier(table_name, "attempts_table")
    return sa.Table(
        table_name,
        metadata,
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "execution_id",
            sa.Uuid(as_uuid=True),
            # §6.11: an explicitly derived name keeps long table names within
            # MySQL's 64-character limit and keeps multiple attempts tables
            # (schema-global FK names) from colliding.
            sa.ForeignKey(
                f"{executions.name}.id",
                ondelete="CASCADE",
                name=_mysql_object_name(
                    table_name=table_name,
                    prefix="fk_",
                    suffix="execution",
                ),
            ),
            nullable=False,
        ),
        sa.Column("attempt_no", sa.Integer, nullable=False),
        sa.Column("lease_token", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("worker_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error", _json_type()),
        sa.Column("started_at", _datetime_type(), nullable=False),
        sa.Column("heartbeat_at", _datetime_type(), nullable=False),
        sa.Column("finished_at", _datetime_type()),
        sa.UniqueConstraint(
            "execution_id",
            "attempt_no",
            name=_mysql_object_name(
                table_name=table_name,
                prefix="uq_",
                suffix="execution_attempt",
            ),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'retrying', 'failed', "
            "'cancelled', 'lease_lost')",
            name=_check_name(table_name, "status"),
        ),
        **_TABLE_OPTIONS,
    )


def _add_execution_indexes(executions: sa.Table) -> None:
    # §6.7: no WHERE predicate — MySQL ignores it. Uniqueness of non-NULL keys is
    # still enforced, and MySQL's UNIQUE allows many NULL idempotency keys.
    sa.Index(
        _mysql_object_name(
            table_name=executions.name,
            prefix="uq_",
            suffix="idempotency",
        ),
        executions.c.namespace,
        executions.c.task_name,
        executions.c.idempotency_key,
        unique=True,
    )
    sa.Index(
        _mysql_object_name(
            table_name=executions.name,
            prefix="ix_",
            suffix="claim",
        ),
        executions.c.namespace,
        executions.c.task_name,
        executions.c.available_at,
        executions.c.created_at,
        executions.c.id,
    )
    sa.Index(
        _mysql_object_name(
            table_name=executions.name,
            prefix="ix_",
            suffix="lease",
        ),
        executions.c.lease_expires_at,
        executions.c.id,
    )
    sa.Index(
        _mysql_object_name(
            table_name=executions.name,
            prefix="ix_",
            suffix="list",
        ),
        executions.c.namespace,
        executions.c.created_at.desc(),
        executions.c.id.desc(),
    )


def _add_attempt_indexes(attempts: sa.Table) -> None:
    sa.Index(
        _mysql_object_name(
            table_name=attempts.name,
            prefix="ix_",
            suffix="execution",
        ),
        attempts.c.execution_id,
        attempts.c.attempt_no.desc(),
    )


def build_execution_tables(
    *,
    executions_table: str,
    attempts_table: str,
) -> ExecutionTables:
    metadata = sa.MetaData()
    executions = _build_executions_table(metadata, executions_table)
    attempts = _build_attempts_table(metadata, attempts_table, executions)
    _add_execution_indexes(executions)
    _add_attempt_indexes(attempts)
    return ExecutionTables(metadata=metadata, executions=executions, attempts=attempts)


__all__ = ["ExecutionTables", "build_execution_tables"]
