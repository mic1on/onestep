"""MySQL tracked execution: the dialect adapter plus the thin backend.

Phase 2 of ``docs/superpowers/specs/2026-09-15-mysql-tracked-execution-backend-design.md``
(§7.2–§7.4, §13). The state machine is inherited unchanged from
:mod:`onestep_sql._shared.execution.machine`; this module supplies only the
MySQL side of the :class:`~onestep_sql._shared.execution.dialect.ExecutionDialect`
seam and the public backend class:

* ``MySQLExecutionDialect`` — the four MySQL-specific behaviours the seam
  defines (schema builder, transaction clock, write-boundary normalization,
  serialized table creation);
* ``MySQLExecutionBackend`` — public name and frozen constructor signature,
  a thin subclass binding the dialect adapter and the connector factory;
* ``StaleExecutionLease`` / ``ExecutionLease`` / ``HeartbeatResult`` —
  re-exported so ``onestep_mysql.execution_backend`` (a ``sys.modules``
  forwarder to this module) exposes the same objects as the PostgreSQL module.

Every MySQL-only engine behaviour is guarded on the runtime dialect name so the
unit suites can keep running this backend on ``sqlite:///`` (design §11.1):

* ``transaction_now`` returns ``None`` unconditionally — MySQL's ``NOW(6)``
  advances inside a transaction (§6.5), so the injected clock drives every
  lease check and all predicates in one transaction agree on a single instant;
* ``normalize_datetime`` converts caller-supplied aware datetimes to UTC and
  rejects naive ones — MySQL *discards* the offset of a bound ``DATETIME``
  instead of converting it (§6.3), which otherwise makes an already-expired
  ``expires_at`` look claimable;
* ``create_tables`` serializes concurrent ``auto_create`` with ``GET_LOCK``
  (§6.8) — a session-level named lock (≤64 characters, hash-truncated name)
  released in ``finally``, because MySQL DDL implicitly commits and no
  transaction rollback will release it;
* the ready gate refuses MySQL servers older than 8.0.16 (§8.3) with an
  explicit error — below that floor CHECK constraints are only parsed, not
  enforced, so the backend would degrade silently. The check runs once per
  process before any other statement, on both the ``auto_create`` and the
  pre-created-tables paths, and is dialect-guarded;
* engine sessions are pinned to UTC (§6.4) and ``READ COMMITTED`` (§6.10).
  When the backend owns the DSN the isolation level is passed at
  ``create_async_engine`` time; on every path a ``connect`` event re-asserts
  both settings for each new session. Both mechanisms are dialect-guarded, so
  a connector built on ``sqlite://`` is untouched.

Note on connector-provided engines: the session pinning applies to the whole
engine, and connections pooled *before* :meth:`from_connector` ran are not
retroactively pinned. Create the connector for the backend (or hand it a
freshly built one), as the documented API in design §10.1 does.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa

from onestep.execution import ExecutionLease, HeartbeatResult

from .._shared.execution.machine import ExecutionStateMachine, StaleExecutionLease
from .execution_schema import (
    _MYSQL_EXECUTION_DDL_GLOBAL_LOCK,
    ExecutionTables,
    build_execution_tables,
    mysql_ddl_lock_name,
)

#: Session time zone every MySQL session of an execution backend is pinned to
#: (design §6.4). Combined with the injected clock (``transaction_now`` →
#: ``None``) no code path depends on the database's own clock.
_MYSQL_SESSION_TIME_ZONE = "+00:00"

#: Isolation level every MySQL session of an execution backend runs under
#: (design §6.10). MySQL's default ``REPEATABLE-READ`` takes gap locks on an
#: empty claim range, blocking concurrent submits for as long as the claim
#: transaction runs; ``READ COMMITTED`` does not.
_MYSQL_ISOLATION_LEVEL = "READ COMMITTED"

#: Seconds to wait for the ``GET_LOCK`` DDL lock before failing ``open()``
#: (design §6.8). Concurrent ``auto_create`` only needs the lock for the
#: duration of one ``CREATE TABLE`` pair; ten seconds is far beyond that.
_MYSQL_DDL_LOCK_TIMEOUT_S = 10

#: Minimum MySQL server version for tracked execution (design §8.3): 8.0.16 is
#: the first release where CHECK constraints are actually enforced, 8.0.13 is
#: needed for JSON expression defaults and 8.0 for ``FOR UPDATE SKIP LOCKED``.
#: Older servers must be refused at ``open()`` instead of silently degrading.
_MYSQL_MINIMUM_SERVER_VERSION = (8, 0, 16)

_VERSION_PREFIX = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def _parse_mysql_server_version(version_string: str) -> tuple[int, int, int]:
    """Parse the leading ``major.minor.patch`` out of a server ``VERSION()``.

    MySQL appends suffixes such as ``-log`` or distro tags to ``VERSION()``;
    the numeric prefix is what the compatibility rules are written against.
    """
    match = _VERSION_PREFIX.match(version_string.strip())
    if match is None:
        raise RuntimeError(
            f"unrecognized MySQL server version string: {version_string!r}"
        )
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _assert_supported_mysql_server(version_string: str) -> tuple[int, int, int]:
    """Refuse servers older than 8.0.16 with an explicit error (design §8.3).

    Below that floor the backend would degrade silently: 8.0.0–8.0.15 create
    the tables fine (JSON expression defaults already work) but only *parse*
    CHECK constraints, so the idempotency/status guards lose enforcement. The
    parsed version is returned so callers can assert against the live server.
    """
    parsed = _parse_mysql_server_version(version_string)
    if parsed < _MYSQL_MINIMUM_SERVER_VERSION:
        raise RuntimeError(
            f"MySQL {version_string} is not supported by MySQLExecutionBackend: "
            f"8.0.16+ is required (SKIP LOCKED needs 8.0, JSON expression "
            f"defaults 8.0.13, enforced CHECK constraints 8.0.16 — design §8.3)"
        )
    return parsed


def _pin_mysql_execution_session(dbapi_connection: Any, connection_record: Any) -> None:
    """Pin one new MySQL session to UTC and ``READ COMMITTED``.

    Connected through the engine's ``connect`` event, so it applies to every
    pooled connection the backend's engine creates — for either driver
    (``asyncmy`` / ``pymysql``) and whichever path built the engine.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(f"SET time_zone = {_MYSQL_SESSION_TIME_ZONE!r}")
        cursor.execute(f"SET SESSION TRANSACTION ISOLATION LEVEL {_MYSQL_ISOLATION_LEVEL}")
    finally:
        cursor.close()


def _pin_mysql_execution_engine(engine: Any) -> None:
    """Attach the session-pinning connect event to ``engine`` (dialect-guarded).

    A no-op for non-MySQL engines — the unit suites share ``sqlite://``
    engines, where an unconditional ``SET`` would fail immediately (design
    §11.1). Idempotent per engine, so backends sharing one connector do not
    stack duplicate listeners.
    """
    if engine.dialect.name != "mysql":
        return
    if not sa.event.contains(engine.sync_engine, "connect", _pin_mysql_execution_session):
        sa.event.listen(engine.sync_engine, "connect", _pin_mysql_execution_session)


def _is_mysql_dsn(dsn: str) -> bool:
    """Return whether ``dsn`` targets the MySQL dialect family."""
    try:
        return sa.engine.make_url(dsn).get_backend_name() == "mysql"
    except sa.exc.ArgumentError:
        return False


class MySQLExecutionDialect:
    """The MySQL side of the execution seam (design §7.3).

    The SQL the shared state machine emits is isomorphic to the PostgreSQL
    backend's; the differences live in the schema builder (§6.1, §6.2, §6.6,
    §6.7, §6.9, §6.11), the transaction clock (§6.5), write-boundary time
    normalization (§6.3) and table-creation serialization (§6.8).
    """

    name = "mysql"

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
        # MySQL's NOW(6) is not stable inside a transaction (§6.5): the claim
        # transaction's expiry cleanup, lease arithmetic and CAS predicates all
        # assume one shared ``now``, which only the injected clock provides.
        # Returning None selects that branch; sqlite (unit path) behaves the
        # same way.
        return None

    def normalize_datetime(self, value: datetime) -> datetime:
        # MySQL discards the offset of a bound DATETIME (§6.3): binding
        # 12:27+08:00 stores 12:27, which read back as UTC is wrong by eight
        # hours. Normalize at the write boundary and refuse naive datetimes
        # instead of guessing — the same precondition core's
        # ``ExecutionRequest`` enforces.
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must be timezone-aware")
        return value.astimezone(timezone.utc)

    async def assert_server_version(self, engine: Any) -> None:
        """Refuse MySQL servers older than 8.0.16 before any DDL runs (§8.3).

        A no-op for non-MySQL engines so the sqlite unit path keeps working
        (§11.1). The backend's ready gate calls this once per process —
        covering both the ``auto_create`` and the pre-created-tables paths —
        so no statement is ever sent to an unsupported server first.
        """
        if engine.dialect.name != "mysql":
            return
        async with engine.connect() as conn:
            version_string = (
                await conn.execute(sa.text("SELECT VERSION()"))
            ).scalar_one()
        _assert_supported_mysql_server(version_string)

    async def create_tables(
        self,
        engine: Any,
        tables: Sequence[sa.Table],
    ) -> None:
        # ``tables`` is always (executions, attempts), in that order.
        metadata = tables[0].metadata
        if engine.dialect.name != "mysql":
            async with engine.begin() as conn:
                await conn.run_sync(
                    lambda sync_conn: metadata.create_all(
                        sync_conn,
                        tables=list(tables),
                        checkfirst=True,
                    )
                )
            return
        lock_name = mysql_ddl_lock_name(
            executions_table=tables[0].name,
            attempts_table=tables[1].name,
        )
        async with engine.begin() as conn:
            # §6.8 + §15.6-(a): the table-pair lock serializes *non-overlapping*
            # pairs, but overlapping pairs (a shared executions table with
            # different attempts tables) derive different pair names and would
            # still race on the shared CREATE TABLE (10/10: 8×1050 + 2×1213).
            # Every execution DDL therefore additionally takes the fixed global
            # lock FIRST, then the pair lock — a strict global→pair order (a
            # session must hold the global lock before it can hold any pair
            # lock, so the wait-for graph stays a cycle-free star; MySQL
            # ≥5.7.5 lets a session hold several named locks). The finally
            # releases in reverse order (pair → global). The pair lock and the
            # 10s timeout are unchanged from §6.8.
            held: list[str] = []
            try:
                for name in (_MYSQL_EXECUTION_DDL_GLOBAL_LOCK, lock_name):
                    acquired = (
                        await conn.execute(
                            sa.select(sa.func.get_lock(name, _MYSQL_DDL_LOCK_TIMEOUT_S))
                        )
                    ).scalar()
                    if acquired != 1:
                        raise RuntimeError(
                            f"could not acquire MySQL DDL lock {name!r} within "
                            f"{_MYSQL_DDL_LOCK_TIMEOUT_S}s"
                        )
                    held.append(name)
                await conn.run_sync(
                    lambda sync_conn: metadata.create_all(
                        sync_conn,
                        tables=list(tables),
                        checkfirst=True,
                    )
                )
            finally:
                # GET_LOCK is session-scoped, not transaction-scoped, and MySQL
                # DDL implicitly commits — so the release must happen here; a
                # transaction rollback on the context manager cannot do it and
                # the pooled connection would keep the lock forever (§6.8).
                # Released in reverse acquisition order (pair → global, §15.6-(a)).
                for name in reversed(held):
                    await conn.execute(sa.select(sa.func.release_lock(name)))


class MySQLExecutionBackend(ExecutionStateMachine):
    """MySQL tracked execution backend (public name and signature frozen).

    The constructor, every public method and the whole state machine are
    inherited unchanged from
    :class:`~onestep_sql._shared.execution.machine.ExecutionStateMachine`; this
    class only binds the MySQL dialect adapter and the connector factory.

    Engine sessions of this backend run under ``READ COMMITTED`` in UTC
    (§6.4, §6.10) regardless of the connector's engine options, including for
    connector-provided engines — build a separate :class:`MySQLConnector` for
    workloads that need different session settings. The public worker entry
    point ``backend.source(...)`` (design §10.1) returns the backend-named
    :class:`~onestep_sql.mysql.execution_source.MySQLExecutionSource`, whose
    options are validated by the same shared validator as the PostgreSQL
    source.
    """

    _dialect_cls = MySQLExecutionDialect
    _connector_hint = "MySQLConnector"

    @classmethod
    def from_connector(
        cls,
        connector: Any,
        **kwargs: Any,
    ) -> "MySQLExecutionBackend":
        # cls (not the base class) so the returned object keeps this exact
        # public type; the session pin covers connector-provided engines too.
        backend = super().from_connector(connector, **kwargs)
        _pin_mysql_execution_engine(backend.engine)
        return backend

    def _make_connector(self, dsn: str, engine_options: dict[str, Any]) -> Any:
        from .connector import MySQLConnector

        options = dict(engine_options)
        if _is_mysql_dsn(dsn):
            # §6.10, guarded on the URL's dialect so a sqlite-backed unit
            # engine never sees a MySQL-only isolation level (§11.1).
            options.setdefault("isolation_level", _MYSQL_ISOLATION_LEVEL)
        connector = MySQLConnector(dsn, **options)
        _pin_mysql_execution_engine(connector.engine)
        return connector

    def _make_source(self, **kwargs: Any) -> Any:
        from .execution_source import MySQLExecutionSource

        return MySQLExecutionSource(**kwargs)

    async def _ensure_ready_locked(self) -> None:
        # Design §8.3: refuse unsupported MySQL servers before any DDL runs.
        # The shared machine's ready gate is the single funnel both the
        # ``auto_create`` and the pre-created-tables paths flow through, so a
        # MySQL-side override here (instead of a new capability hook on the
        # shared ExecutionDialect protocol) keeps the seam minimal. The check
        # runs once per process (or after a fork rebuilds the connector) and
        # is dialect-guarded inside the adapter, so sqlite unit engines are
        # untouched (§11.1).
        if not self._ready:
            await self._dialect.assert_server_version(
                self._ensure_connector().engine
            )
        await super()._ensure_ready_locked()


__all__ = [
    "ExecutionLease",
    "HeartbeatResult",
    "MySQLExecutionBackend",
    "StaleExecutionLease",
]
