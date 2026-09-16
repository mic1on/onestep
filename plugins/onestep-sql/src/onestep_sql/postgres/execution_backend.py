"""PostgreSQL tracked execution: the dialect adapter plus the thin backend.

Phase 1 of ``docs/superpowers/specs/2026-09-15-mysql-tracked-execution-backend-design.md``
(§7.2–§7.4) moved the state machine into
:mod:`onestep_sql._shared.execution.machine`. This module keeps the public
identity the compatibility window requires:

* ``PostgresExecutionBackend`` — same name, same module path, same public
  signature; now a thin subclass that supplies the PostgreSQL dialect adapter
  and the source factory;
* ``StaleExecutionLease`` — re-exported from the shared machine, so the object
  identity seen by ``onestep_postgres.execution_backend`` (a ``sys.modules``
  forwarder to this module) is unchanged;
* ``ExecutionLease`` / ``HeartbeatResult`` — re-exported for the same reason.

Only the four documented dialect differences live here (schema builder, engine
and session configuration, and time normalization); the SQL itself is shared
because it is isomorphic across the two backends (design §7.3).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa

from onestep.execution import ExecutionLease, HeartbeatResult

from .._shared.execution.dialect import _aware_utc
from .._shared.execution.machine import ExecutionStateMachine, StaleExecutionLease
from .execution_schema import ExecutionTables, build_execution_tables

# Compatibility re-exports: before the Phase 1 extraction these core names were
# reachable as attributes of this module (it imported them for the state machine
# that has since moved out). The published surface is ``__all__`` and is
# unchanged, but re-exporting keeps even incidental attribute access working, as
# Phase 1's zero-behaviour-change gate requires. Incidental stdlib/driver names
# (``os``, ``json``, ``sa``, ...) are deliberately not re-exported: they were
# never a meaningful part of this module's surface, and they remain reachable
# through ``onestep_sql._shared.execution.machine``.
from onestep.execution import (  # noqa: F401
    Execution,
    ExecutionCompletion,
    ExecutionConflict,
    ExecutionEncodingError,
    ExecutionErrorDetail,
    ExecutionLeaseLost,
    ExecutionPage,
    ExecutionQuery,
    ExecutionRequest,
    ExecutionStatus,
    LeasedExecutionBackend,
)


class PostgresExecutionDialect:
    """The PostgreSQL (and SQLite-compat) side of the execution seam.

    The unit suites run ``PostgresExecutionBackend`` on ``sqlite:///`` as well,
    so the database-derived branches below stay guarded on the *runtime*
    dialect name — exactly as the pre-extraction code did — and SQLite keeps
    using the injected clock and un-serialized table creation.
    """

    name = "postgresql"

    def build_tables(
        self,
        *,
        executions_table: str,
        attempts_table: str,
    ) -> ExecutionTables:
        return build_execution_tables(
            executions_table=executions_table,
            attempts_table=attempts_table,
        )

    async def transaction_now(self, conn: Any) -> datetime | None:
        # PostgreSQL's ``transaction_timestamp()`` is stable for every lease
        # check in this transaction. SQLite (deterministic unit tests) keeps
        # using the injected clock.
        if conn.dialect.name != "postgresql":
            return None
        value = (
            await conn.execute(sa.select(sa.func.current_timestamp()))
        ).scalar_one()
        return _aware_utc(value)

    def normalize_datetime(self, value: datetime) -> datetime:
        # TIMESTAMPTZ normalizes to UTC on its own, so binding the caller's
        # aware datetime is already correct.
        return value

    async def create_tables(
        self,
        engine: Any,
        tables: Sequence[sa.Table],
    ) -> None:
        # ``tables`` is always (executions, attempts), in that order.
        metadata = tables[0].metadata
        if engine.dialect.name == "postgresql":
            lock_name = f"{tables[0].name}\0{tables[1].name}"
            lock_key = int.from_bytes(
                hashlib.sha256(lock_name.encode("utf-8")).digest()[:8],
                byteorder="big",
                signed=True,
            )
            async with engine.begin() as conn:
                await conn.execute(sa.select(sa.func.pg_advisory_xact_lock(lock_key)))
                await conn.run_sync(
                    lambda sync_conn: metadata.create_all(
                        sync_conn,
                        tables=list(tables),
                        checkfirst=True,
                    )
                )
            return
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: metadata.create_all(
                    sync_conn,
                    tables=list(tables),
                    checkfirst=True,
                )
            )


class PostgresExecutionBackend(ExecutionStateMachine):
    """PostgreSQL tracked execution backend (public name and signature frozen).

    The constructor, every public method and the whole state machine are
    inherited unchanged from
    :class:`~onestep_sql._shared.execution.machine.ExecutionStateMachine`; this
    class only binds the PostgreSQL dialect adapter and the two factories.
    """

    _dialect_cls = PostgresExecutionDialect
    _connector_hint = "PostgresConnector"

    @classmethod
    def from_connector(
        cls,
        connector: Any,
        **kwargs: Any,
    ) -> "PostgresExecutionBackend":
        # cls (not the base class) so the returned object keeps this exact
        # public type.
        return cls(connector=connector, **kwargs)

    def _make_connector(self, dsn: str, engine_options: dict[str, Any]) -> Any:
        from .connector import PostgresConnector

        return PostgresConnector(dsn, **engine_options)

    def _make_source(self, **kwargs: Any) -> Any:
        from .execution_source import PostgresExecutionSource

        return PostgresExecutionSource(**kwargs)


__all__ = [
    "ExecutionLease",
    "HeartbeatResult",
    "PostgresExecutionBackend",
    "StaleExecutionLease",
]
