from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_POSTGRES_IDENTIFIER_MAX_LENGTH = 63
_POSTGRES_NAME_HASH_LENGTH = 12
_EXECUTION_STATUSES = ("queued", "retrying")
_LEASE_STATUSES = ("running", "cancel_requested")


@dataclass(frozen=True)
class ExecutionTables:
    metadata: sa.MetaData
    executions: sa.Table
    attempts: sa.Table


def _validate_identifier(value: str, field: str) -> str:
    if not isinstance(value, str) or not value or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field} must be a non-empty SQL identifier")
    if len(value) > _POSTGRES_IDENTIFIER_MAX_LENGTH:
        raise ValueError(
            f"{field} must be at most {_POSTGRES_IDENTIFIER_MAX_LENGTH} characters"
        )
    return value


def _postgres_object_name(*, table_name: str, prefix: str, suffix: str) -> str:
    base = f"{prefix}{table_name}_{suffix}"
    if len(base) <= _POSTGRES_IDENTIFIER_MAX_LENGTH:
        return base
    digest = hashlib.sha256(
        base.encode("ascii")
    ).hexdigest()[:_POSTGRES_NAME_HASH_LENGTH]
    stem_length = _POSTGRES_IDENTIFIER_MAX_LENGTH - len(digest) - 1
    return f"{base[:stem_length]}_{digest}"


def _json_type() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


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
        sa.Column("metadata", _json_type(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("result", _json_type()),
        sa.Column("error", _json_type()),
        sa.Column("idempotency_key", sa.String(255)),
        sa.Column("submission_digest", sa.String(64)),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.Uuid(as_uuid=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("worker_id", sa.String(255)),
        sa.Column("cancel_reason", sa.String(500)),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.BigInteger, nullable=False, server_default="0"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'retrying', 'succeeded', 'failed', 'cancel_requested', 'cancelled', 'expired')",
            name="ck_onestep_execution_status",
        ),
        sa.CheckConstraint(
            "(idempotency_key IS NULL AND submission_digest IS NULL) OR (idempotency_key IS NOT NULL AND submission_digest IS NOT NULL)",
            name="ck_onestep_execution_idempotency",
        ),
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
            sa.ForeignKey(f"{executions.name}.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_no", sa.Integer, nullable=False),
        sa.Column("lease_token", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("worker_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error", _json_type()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "execution_id",
            "attempt_no",
            name=_postgres_object_name(
                table_name=table_name,
                prefix="uq_",
                suffix="execution_attempt",
            ),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'retrying', 'failed', 'cancelled', 'lease_lost')",
            name="ck_onestep_attempt_status",
        ),
    )


def _add_execution_indexes(executions: sa.Table) -> None:
    sa.Index(
        _postgres_object_name(
            table_name=executions.name,
            prefix="uq_",
            suffix="idempotency",
        ),
        executions.c.namespace,
        executions.c.task_name,
        executions.c.idempotency_key,
        unique=True,
        postgresql_where=executions.c.idempotency_key.is_not(None),
        sqlite_where=executions.c.idempotency_key.is_not(None),
    )
    claim_predicate = executions.c.status.in_(_EXECUTION_STATUSES)
    sa.Index(
        _postgres_object_name(
            table_name=executions.name,
            prefix="ix_",
            suffix="claim",
        ),
        executions.c.namespace,
        executions.c.task_name,
        executions.c.available_at,
        executions.c.created_at,
        executions.c.id,
        postgresql_where=claim_predicate,
        sqlite_where=claim_predicate,
    )
    lease_predicate = executions.c.status.in_(_LEASE_STATUSES)
    sa.Index(
        _postgres_object_name(
            table_name=executions.name,
            prefix="ix_",
            suffix="lease",
        ),
        executions.c.lease_expires_at,
        executions.c.id,
        postgresql_where=lease_predicate,
        sqlite_where=lease_predicate,
    )
    sa.Index(
        _postgres_object_name(
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
        _postgres_object_name(
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
