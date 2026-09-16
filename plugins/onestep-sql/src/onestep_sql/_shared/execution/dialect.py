"""The minimal per-backend seam for the shared tracked-execution state machine.

Phase 1 of the MySQL tracked execution backend
(``docs/superpowers/specs/2026-09-15-mysql-tracked-execution-backend-design.md``
§7.3) keeps the state machine itself backend-neutral and pushes every real
dialect difference behind this one protocol:

* :meth:`ExecutionDialect.build_tables` — DDL construction (JSON defaults,
  ``DATETIME(6)`` precision, constraint naming, partial indexes);
* :meth:`ExecutionDialect.transaction_now` — whether the database can supply a
  *transaction-stable* ``now`` (PostgreSQL) or whether the injected clock must
  be used (MySQL's ``NOW()`` is not transaction-stable, design §6.5);
* :meth:`ExecutionDialect.normalize_datetime` — write-boundary timestamp
  normalization (MySQL discards the offset of a bound ``DATETIME``, design
  §6.3);
* :meth:`ExecutionDialect.create_tables` — backend-appropriate serialization of
  concurrent ``auto_create`` (``pg_advisory_xact_lock`` vs ``GET_LOCK``,
  design §6.8).

The seam deliberately stays this small: the SQL produced by both backends is
isomorphic (the same ``UPDATE ... WHERE ...`` compare-and-set, the same
``SELECT ... FOR UPDATE SKIP LOCKED`` claim, the same keyset pagination), so
there is no generic ``_build_statement`` dialect branch tree to grow here.

The schema *layer* never moves into ``_shared`` (design §7.2): that is exactly
where the two backends differ. Each backend keeps its own
``execution_schema.py`` and returns the shared :class:`ExecutionTables`
container from :meth:`ExecutionDialect.build_tables`.

Identifier *naming*, by contrast, is dialect-independent mechanism (design §6.9):
:func:`validate_sql_identifier` and :func:`derive_object_name` are pure
string/parameter logic shared by both schema builders — PostgreSQL derives
names against its 63-character limit, MySQL against 64, and MySQL additionally
uses it to name the FK constraint that InnoDB would otherwise derive past the
limit (§6.11).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

import sqlalchemy as sa


#: A bare, unquoted SQL identifier: ASCII letter/underscore start, then
#: letters/digits/underscores. Shared by every backend's schema builder.
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Default number of hex digest characters appended when a derived name has to
#: be truncated. Kept at the historical value so derived names are unchanged.
_DEFAULT_NAME_HASH_LENGTH = 12


@dataclass(frozen=True)
class ExecutionTables:
    """The two tracked-execution tables built by one backend's schema builder.

    The container shape is shared; the DDL is not. Backends construct this via
    their own ``build_execution_tables`` helper.
    """

    metadata: sa.MetaData
    executions: sa.Table
    attempts: sa.Table


def validate_sql_identifier(value: str, field: str, *, max_length: int) -> str:
    """Return ``value`` if it is a usable identifier, else raise ``ValueError``.

    ``max_length`` is a parameter because the two backends differ (PostgreSQL
    63, MySQL 64 — design §6.9); the message text is intentionally identical
    across backends so the extract-start behaviour of both stays in step.
    """
    if not isinstance(value, str) or not value or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field} must be a non-empty SQL identifier")
    if len(value) > max_length:
        raise ValueError(f"{field} must be at most {max_length} characters")
    return value


def derive_object_name(
    *,
    table_name: str,
    prefix: str,
    suffix: str,
    max_length: int,
    hash_length: int = _DEFAULT_NAME_HASH_LENGTH,
) -> str:
    """Derive a deterministic index/constraint/FK name from a table name.

    Returns ``{prefix}{table_name}_{suffix}`` when that fits in ``max_length``;
    otherwise truncates the stem and appends a stable hash so the result is
    unique, deterministic, and within the limit. The truncation is what keeps
    long user-supplied table names from producing over-long identifiers —
    including the FK name InnoDB would otherwise derive from the table name
    (design §6.9, §6.11). The result is a plain string, so it is equally usable
    as an ``sa.Index`` / ``sa.UniqueConstraint`` / ``sa.CheckConstraint`` name
    and as the ``name=`` argument of ``sa.ForeignKey``.
    """
    base = f"{prefix}{table_name}_{suffix}"
    if len(base) <= max_length:
        return base
    digest = hashlib.sha256(base.encode("ascii")).hexdigest()[:hash_length]
    stem_length = max_length - len(digest) - 1
    return f"{base[:stem_length]}_{digest}"


@runtime_checkable
class ExecutionDialect(Protocol):
    """Backend-specific behaviour the shared state machine depends on."""

    #: SQLAlchemy dialect name this adapter serves (``postgresql`` / ``mysql``).
    name: str

    def build_tables(
        self,
        *,
        executions_table: str,
        attempts_table: str,
    ) -> ExecutionTables:
        """Build the backend's execution/attempts tables."""

    async def transaction_now(self, conn: Any) -> datetime | None:
        """Return a DB-derived ``now``, or ``None`` to use the injected clock.

        PostgreSQL returns ``current_timestamp()``, which is stable for the whole
        transaction. MySQL returns ``None`` because ``NOW()`` advances inside a
        transaction (design §6.5); the claim/lease/CAS predicates all assume a
        single ``now`` per transaction.
        """

    def normalize_datetime(self, value: datetime) -> datetime:
        """Normalize a caller-supplied datetime before binding it (design §6.3)."""

    async def create_tables(self, engine: Any, tables: Sequence[sa.Table]) -> None:
        """Create ``tables`` with backend-appropriate serialization (design §6.8)."""


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _optional_aware_utc(value: datetime | None) -> datetime | None:
    return None if value is None else _aware_utc(value)


__all__ = [
    "ExecutionDialect",
    "ExecutionTables",
]
