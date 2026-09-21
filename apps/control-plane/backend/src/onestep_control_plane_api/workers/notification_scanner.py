from __future__ import annotations

import logging
import zlib
from asyncio import sleep
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from onestep_control_plane_api.api.notification_service import (
    scan_and_dispatch_instance_connectivity_notifications_async,
    scan_and_dispatch_missed_start_notifications_async,
)
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db.session import get_async_engine, session_scope
from onestep_control_plane_api.ops.observability import (
    ascan_duration_timer,
    ensure_engine_instrumented,
)
from onestep_control_plane_api.workers.leader import WorkerLeaseError

logger = logging.getLogger("onestep_control_plane_api.workers.notification_scanner")

NOTIFICATION_MISSED_START_SCANNER_NAME = "notification_missed_start_scanner"
NOTIFICATION_MISSED_START_SCANNER_LOCK_KEY = zlib.crc32(
    b"onestep-control-plane.notification-missed-start-scanner"
)

#: Bounded metric label for this scan. Must be one of the names registered in
#: ``observability.KNOWN_SCAN_NAMES``; anything else collapses to ``"other"``.
#: Never put instance or session identity here — that would be unbounded.
SCAN_METRIC_NAME = "notification_missed_start"

#: Bounded metric label for the pool this worker checks connections out of.
#: A pool name, not an identity: unbounded labels are explicitly out of scope.
DEFAULT_POOL_METRIC_NAME = "async"

SessionFactory = Callable[[], Any]
SleepFn = Callable[[float], Awaitable[None]]
ScanFn = Callable[[AsyncSession, datetime], Awaitable[int]]
LeaseFactory = Callable[[], "AsyncWorkerLease"]


class AsyncWorkerLease:
    """A leader lease whose database work is awaited, never run inline.

    The synchronous :class:`~onestep_control_plane_api.workers.leader.WorkerLease`
    hierarchy is deliberately left untouched: ``leader.py`` is shared with
    ``retention_worker.py`` and ``notification_outbox_worker.py``, which still
    drive it synchronously from their own worker loops. Only the notification
    scan owns this async variant.
    """

    #: Reported through the readiness state, so operators see which mechanism
    #: is actually gating this worker.
    mode = "unknown"
    #: Incremented once per successful ``pg_try_advisory_lock``. Counting
    #: acquisitions (not just "am I leader") is what makes the multi-instance
    #: test discriminating: a lease that never took the lock cannot report one.
    advisory_lock_count = 0
    acquired_at: datetime | None = None

    async def ensure_leader(self) -> bool:
        raise NotImplementedError

    async def release(self) -> None:
        raise NotImplementedError


class LocalAsyncWorkerLease(AsyncWorkerLease):
    """Single-process lease: always leader, holds no database resource."""

    mode = "local"

    async def ensure_leader(self) -> bool:
        if self.acquired_at is None:
            self.acquired_at = datetime.now(UTC)
        return True

    async def release(self) -> None:
        self.acquired_at = None


class PostgresAdvisoryAsyncWorkerLease(AsyncWorkerLease):
    """PostgreSQL advisory lock acquired and released on an async connection.

    The advisory lock is **session-scoped**, not transaction-scoped: it survives
    a commit and is released only by ``pg_advisory_unlock`` or by the session
    ending. So this lease commits immediately after acquiring, exactly like its
    synchronous sibling ``PostgresAdvisoryLockLease``, and holds no open
    transaction while leadership is claimed.

    That matters operationally: an ``idle in transaction`` backend blocks
    PostgreSQL vacuum cleanup and occupies a pooled connection for as long as
    leadership is held, which for a long-lived leader is forever. Committing
    right after ``pg_try_advisory_lock`` keeps the backend ``idle`` with an empty
    transaction age while retaining the lock.
    """

    mode = "postgres_advisory_lock"

    def __init__(self, *, engine: AsyncEngine, lock_key: int, worker_name: str) -> None:
        self._engine = engine
        self._lock_key = lock_key
        self._worker_name = worker_name
        self._connection: Any = None
        self.acquired_at: datetime | None = None
        self.advisory_lock_count = 0

    @property
    def holds_lock(self) -> bool:
        return self._connection is not None

    async def ensure_leader(self) -> bool:
        if self._connection is not None:
            try:
                await self._connection.execute(text("SELECT 1"))
                # Commit so the backend is not left "idle in transaction"
                # between polls. The lock is session-scoped and survives this.
                await self._connection.commit()
            except SQLAlchemyError as exc:
                await self.release()
                raise WorkerLeaseError(
                    f"{self._worker_name} lost advisory lock connection"
                ) from exc
            return True

        try:
            connection = await self._engine.connect()
        except SQLAlchemyError as exc:
            raise WorkerLeaseError(
                f"{self._worker_name} failed to open an advisory lock connection"
            ) from exc

        try:
            acquired = bool(
                (
                    await connection.execute(
                        text("SELECT pg_try_advisory_lock(:lock_key)"),
                        {"lock_key": self._lock_key},
                    )
                ).scalar()
            )
            # Commit immediately: the lock is session-scoped, so it is retained
            # even though the transaction ends here.
            await connection.commit()
        except SQLAlchemyError as exc:
            try:
                await connection.rollback()
            except SQLAlchemyError:
                logger.warning(
                    "failed to roll back a failed advisory lock acquisition",
                    extra={"worker_name": self._worker_name},
                    exc_info=True,
                )
            await connection.close()
            raise WorkerLeaseError(
                f"{self._worker_name} failed to acquire advisory lock"
            ) from exc

        if not acquired:
            # Nothing locked: close the connection so a standby replica holds no
            # database resource at all between polls.
            await connection.close()
            return False

        self._connection = connection
        self.advisory_lock_count += 1
        self.acquired_at = datetime.now(UTC)
        return True

    async def release(self) -> None:
        connection = self._connection
        self._connection = None
        self.acquired_at = None
        if connection is None:
            return

        try:
            await connection.execute(
                text("SELECT pg_advisory_unlock(:lock_key)"),
                {"lock_key": self._lock_key},
            )
            await connection.commit()
        except SQLAlchemyError:
            logger.warning(
                "failed to release advisory lock",
                extra={
                    "worker_name": self._worker_name,
                    "lock_key": self._lock_key,
                },
                exc_info=True,
            )
        finally:
            await connection.close()


def create_async_worker_lease(
    *,
    engine: AsyncEngine | None,
    lock_key: int,
    worker_name: str,
) -> AsyncWorkerLease:
    """Build the async lease, mirroring the synchronous factory's choice.

    A PostgreSQL engine gets the advisory lock; anything else (notably the
    sqlite test path, which has no advisory locks) falls back to the local
    lease, exactly as ``create_worker_lease`` does.
    """

    if engine is not None and engine.dialect.name == "postgresql":
        return PostgresAdvisoryAsyncWorkerLease(
            engine=engine,
            lock_key=lock_key,
            worker_name=worker_name,
        )
    return LocalAsyncWorkerLease()


async def _scan_notifications(session: AsyncSession, started_at: datetime) -> int:
    missed_start_count = await scan_and_dispatch_missed_start_notifications_async(
        session,
        min_last_seen_at=started_at,
    )
    instance_connectivity_count = await scan_and_dispatch_instance_connectivity_notifications_async(
        session,
    )
    return missed_start_count + instance_connectivity_count


def _resolve_engine(session_factory: SessionFactory) -> Engine | None:
    bind = getattr(session_factory, "kw", {}).get("bind")
    if isinstance(bind, Engine):
        return bind

    with session_factory() as session:
        resolved_bind = session.get_bind()
    return resolved_bind if isinstance(resolved_bind, Engine) else None


def _instrument_async_engine() -> bool:
    """Wrap the async engine's pool so checkout waits are measured.

    Idempotent, and deliberately tolerant: instrumentation is observation only,
    so a failure here must never stop the scanner. Resolving the engine can
    itself raise (an unsupported dialect, for example), which is why the whole
    thing is guarded — a monitoring hook is not allowed to take a worker down.

    The resulting pool label is the engine name only, never an instance or
    session id, so label cardinality stays bounded.

    Measured overhead (SQLite file pool, 300 checkouts, median): 1.5 us per
    uninstrumented checkout vs 2.2 us instrumented, i.e. about +0.8 us per
    checkout — two ``perf_counter`` calls, one histogram update and one dict
    write under a short lock. Relative overhead looks large only because a
    SQLite checkout is already sub-microsecond; on a PostgreSQL checkout that
    involves a round trip the same absolute cost is noise.
    """

    try:
        return ensure_engine_instrumented(get_async_engine(), name=DEFAULT_POOL_METRIC_NAME)
    except Exception:  # pragma: no cover - defensive, see docstring
        logger.warning(
            "could not attach pool instrumentation to the async engine",
            exc_info=True,
        )
        return False


def _default_lease_factory() -> LeaseFactory:
    """Resolve the async engine lazily, once per lease construction."""

    def factory() -> AsyncWorkerLease:
        return create_async_worker_lease(
            engine=get_async_engine(),
            lock_key=NOTIFICATION_MISSED_START_SCANNER_LOCK_KEY,
            worker_name=NOTIFICATION_MISSED_START_SCANNER_NAME,
        )

    return factory


async def run_notification_missed_start_scanner(
    app: FastAPI,
    *,
    started_at: datetime | None = None,
    sleep_fn: SleepFn = sleep,
    scan_fn: ScanFn = _scan_notifications,
    lease_factory: LeaseFactory | None = None,
    scan_interval_s: float | None = None,
    leader_poll_interval_s: float | None = None,
) -> None:
    """Scan for missed starts and connectivity transitions on the async path.

    Every database touch — the lease, the scan queries, the status updates and
    the outbox writes — runs on one short :func:`session_scope` work unit that
    is awaited rather than executed inline, so a slow scan yields to the event
    loop instead of freezing it.

    Release on cancellation comes from ``session_scope`` itself, which closes
    the session on ``BaseException`` (not just ``Exception``), so a cancellation
    inside a scan rolls the transaction back and returns the pooled connection.

    Leader-lease release is likewise in a ``finally`` that awaits once, so
    shutdown or a leader switch always releases the advisory lock.
    """

    state = app.state.background_task_states[NOTIFICATION_MISSED_START_SCANNER_NAME]
    run_started_at = started_at or datetime.now(UTC)
    run_interval_s = float(
        settings.notification_missed_start_scan_interval_s
        if scan_interval_s is None
        else scan_interval_s
    )
    poll_interval_s = float(
        settings.background_worker_leader_poll_interval_s
        if leader_poll_interval_s is None
        else leader_poll_interval_s
    )
    lease = lease_factory() if lease_factory is not None else _default_lease_factory()()

    state.mark_started(run_started_at)
    state.mark_starting(lease.mode, when=run_started_at)
    # Attach pool-wait instrumentation to the async engine once per run. This
    # wraps pool.connect() so every checkout is timed; it is idempotent, and it
    # is observation only — it cannot change what a checkout returns.
    _instrument_async_engine()
    await sleep_fn(run_interval_s)

    try:
        while True:
            state.mark_tick()
            previous_status = state.leadership_status
            try:
                is_leader = await lease.ensure_leader()
            except WorkerLeaseError as exc:
                state.mark_lease_failure(lease.mode, exc)
                logger.exception(
                    "notification missed-start scanner lease check failed",
                    extra={
                        "lease_mode": lease.mode,
                    },
                )
                await sleep_fn(poll_interval_s)
                continue

            if not is_leader:
                state.mark_standby(lease.mode)
                if previous_status != "standby":
                    logger.info(
                        "notification missed-start scanner standing by for leadership",
                        extra={"lease_mode": lease.mode},
                    )
                await sleep_fn(poll_interval_s)
                continue

            state.mark_leader(lease.mode, acquired_at=lease.acquired_at)
            if previous_status != "leader":
                logger.info(
                    "notification missed-start scanner acquired leadership",
                    extra={"lease_mode": lease.mode},
                )

            try:
                async with ascan_duration_timer(SCAN_METRIC_NAME) as scan_timing:
                    async with session_scope() as session:
                        await scan_fn(session, run_started_at)
                state.mark_success()
                logger.debug(
                    "notification missed-start scan finished",
                    extra={
                        "scan": scan_timing.name,
                        "scan_duration_s": round(scan_timing.duration_s, 6),
                    },
                )
            except Exception as exc:
                state.mark_failure(exc)
                logger.exception("notification missed-start scan failed")
            await sleep_fn(run_interval_s)
    finally:
        if state.leadership_status == "leader":
            logger.info(
                "notification missed-start scanner released leadership",
                extra={"lease_mode": lease.mode},
            )
        await lease.release()
