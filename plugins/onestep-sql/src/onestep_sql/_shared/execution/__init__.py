"""Shared tracked-execution state machine (internal, non-public API).

Phase 1 of ``docs/superpowers/specs/2026-09-15-mysql-tracked-execution-backend-design.md``
(§7.2–§7.4) extracts the formerly PostgreSQL-only state machine out of
``onestep_sql.postgres.execution_backend`` so that a second SQL backend can
reuse it instead of carrying a ~1100-line near-duplicate.

* :mod:`onestep_sql._shared.execution.dialect` — the :class:`ExecutionDialect`
  protocol plus the :class:`ExecutionTables` container and the shared
  tz-normalization helpers;
* :mod:`onestep_sql._shared.execution.machine` — the state machine itself
  (submit / idempotency / list pagination / cancel / claim / heartbeat /
  complete / release / reclaim / fencing).

The exclusion rule from the consolidation design §3.1 still holds: only code
whose semantics are identical on both sides belongs here. That is why the
**schema layer stays out** — ``execution_schema.py`` is precisely where the two
dialects differ (JSON defaults, ``DATETIME(6)`` precision, CHECK-name scope,
partial indexes) and each backend keeps its own.

This package is **not** public API. Concrete backends expose their own
backend-named classes (``PostgresExecutionBackend`` / ``MySQLExecutionBackend``,
per design §4.2 and §7.4). Dual-backend identity proofs live in
``tests/contract/test_onestep_sql_shared.py``.
"""

from __future__ import annotations

from .dialect import ExecutionDialect, ExecutionTables
from .machine import ExecutionStateMachine, StaleExecutionLease

__all__ = [
    "ExecutionDialect",
    "ExecutionStateMachine",
    "ExecutionTables",
    "StaleExecutionLease",
]
