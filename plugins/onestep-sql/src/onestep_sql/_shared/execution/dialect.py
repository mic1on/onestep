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
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

import sqlalchemy as sa


@dataclass(frozen=True)
class ExecutionTables:
    """The two tracked-execution tables built by one backend's schema builder.

    The container shape is shared; the DDL is not. Backends construct this via
    their own ``build_execution_tables`` helper.
    """

    metadata: sa.MetaData
    executions: sa.Table
    attempts: sa.Table


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
