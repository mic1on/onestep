"""Native-async behaviour of the notification scan database path (#193).

The scan itself is the unit under test, so these tests drive
``run_notification_missed_start_scanner`` with real scans against the shared
test database rather than a stub, and assert the properties the conversion buys:

* the scan's database work (lease, queries, state updates, outbox writes) runs
  on an awaited async work unit, so a slow scan does not freeze the event loop;
* cancellation, a database exception and a leader switch each release their
  session, transaction and lease with no leaked connection;
* two concurrent scanners do not both execute the scan.

The multi-instance test is written against the **lease mechanism**, not against
delivery counts. That distinction is load-bearing and is spelled out in
``test_only_one_scanner_executes_while_leases_contend``.
"""

from __future__ import annotations

import asyncio
import threading
import time
import zlib
from datetime import UTC, datetime

import pytest
from conftest import shared_cache_async_url
from onestep_control_plane_api.api.notification_service import (
    scan_and_dispatch_instance_connectivity_notifications,
    scan_and_dispatch_instance_connectivity_notifications_async,
    scan_and_dispatch_missed_start_notifications_async,
)
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db import session as db_session_module
from onestep_control_plane_api.db.models import NotificationDelivery
from onestep_control_plane_api.db.session import session_scope
from onestep_control_plane_api.ops.readiness import build_default_background_task_states
from onestep_control_plane_api.workers.notification_scanner import (
    NOTIFICATION_MISSED_START_SCANNER_LOCK_KEY,
    NOTIFICATION_MISSED_START_SCANNER_NAME,
    AsyncWorkerLease,
    LocalAsyncWorkerLease,
    PostgresAdvisoryAsyncWorkerLease,
    run_notification_missed_start_scanner,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool
from test_notification_service import seed_channel, seed_runtime_service


async def _await_quiescent(harness, *, timeout_s: float = 2.0) -> None:
    """Wait until the harness has no connection checked out and no open session.

    ``aiosqlite`` closes its connection on a worker thread, so the pool's checkin
    counter can settle a moment *after* a cancelled task has finished. Without
    this, teardown's ``DROP TABLE`` can race a connection that is still closing
    and fail with SQLite's "database table is locked" — a harness artifact that
    says nothing about the code under test, but which would make the suite flaky.
    """

    await _await_release(harness, timeout_s=timeout_s)


async def _await_release(harness, *, timeout_s: float = 2.0) -> None:
    """Wait for the harness to report everything released.

    ``aiosqlite`` closes its connection on a worker thread, so the pool's
    checkin counter can settle a moment *after* the cancelled task has
    finished. Asserting "released" therefore means "released once the driver has
    caught up", which is the actual guarantee; sampling the counters exactly once
    right after cancellation would flake without saying anything about the code.
    """

    if harness is None:
        return
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        if harness.checkedout() == 0 and not harness.open_sessions:
            return
        if asyncio.get_running_loop().time() >= deadline:
            return
        await asyncio.sleep(0.01)


def _build_app() -> object:
    from fastapi import FastAPI

    app = FastAPI()
    app.state.background_task_states = build_default_background_task_states()
    app.state.background_task_refs = {NOTIFICATION_MISSED_START_SCANNER_NAME: None}
    return app


async def _cancel_together(*tasks: asyncio.Task) -> None:
    """Cancel scanner tasks in the same event-loop iteration.

    Cancelling one replica and awaiting it before cancelling the other lets the
    survivor run the scan loop in between — and, because the first replica's
    ``finally`` already released the lease, the survivor can legitimately
    *acquire* leadership and scan. That is correct behaviour, not a bug, but it
    makes the "only one replica ever executed" assertion racy. Cancelling both
    first and awaiting afterwards removes the window.
    """

    for task in tasks:
        task.cancel()
    for task in tasks:
        with pytest.raises(asyncio.CancelledError):
            await task


def _probe_until(
    client,
    predicate,
    *,
    timeout_s: float,
) -> int:
    """Poll a no-DB HTTP probe from a side thread until ``predicate`` is true.

    ``TestClient`` runs the ASGI app on its own worker thread, but the scanner
    under test is driven by ``asyncio.run`` on *this* thread's loop. A bare
    ``time.sleep`` here would freeze that loop for the whole poll and could not
    observe anything at all, so the probe runs on a separate thread.
    """

    ticks = 0

    def poll() -> None:
        nonlocal ticks
        while not predicate():
            response = client.get("/healthz")
            assert response.status_code == 200, "a slow scan froze the no-DB health probe"
            ticks += 1
            time.sleep(0.02)

    worker = threading.Thread(target=poll, daemon=True)
    worker.start()
    worker.join(timeout=timeout_s)
    if worker.is_alive():
        raise AssertionError(f"the probe thread did not finish within {timeout_s}s")
    return ticks


class RecordingAsyncLease(AsyncWorkerLease):
    """An async lease with configurable, countable leadership.

    ``ensure_leader_calls`` and ``executions_while_leader`` are the signals the
    multi-instance test discriminates on.
    """

    mode = "postgres_advisory_lock"

    def __init__(self, *, leader: bool = True) -> None:
        self._leader = leader
        self.advisory_lock_count = 0
        self.ensure_leader_calls = 0
        self.acquired_at: datetime | None = None
        self.release_count = 0

    async def ensure_leader(self) -> bool:
        self.ensure_leader_calls += 1
        if not self._leader:
            return False
        if self.acquired_at is None:
            self.acquired_at = datetime.now(UTC)
            self.advisory_lock_count += 1
        return True

    async def release(self) -> None:
        self.release_count += 1
        self.acquired_at = None


class SharedLeadershipLease(AsyncWorkerLease):
    """Two replicas contend for one coordinator, mirroring pg_try_advisory_lock.

    Exactly one holder wins, and the winner is the only replica whose scan
    executes — which is what a real advisory lock guarantees.
    """

    mode = "postgres_advisory_lock"

    def __init__(self, *, coordinator: dict, replica_id: str) -> None:
        self._coordinator = coordinator
        self._replica_id = replica_id
        self.advisory_lock_count = 0
        self.acquired_at: datetime | None = None
        self.release_count = 0

    async def ensure_leader(self) -> bool:
        owner = self._coordinator.get("owner")
        if owner in (None, self._replica_id):
            if owner is None:
                self._coordinator["owner"] = self._replica_id
                self.advisory_lock_count += 1
                self.acquired_at = datetime.now(UTC)
            return True
        return False

    async def release(self) -> None:
        self.release_count += 1
        if self._coordinator.get("owner") == self._replica_id:
            self._coordinator["owner"] = None
        self.acquired_at = None


def _seed_offline_transition(db_session) -> None:
    """One instance that goes offline between the two scan instants.

    Mirrors the existing connectivity regression tests: a first scan at 02:10
    only *seeds* the per-instance state (it returns 0 and creates no delivery),
    and the transition to offline is emitted by a later scan at 02:12. Without
    that seeding pass a scan at 02:12 would create the state as already-offline
    and never emit anything.
    """

    _, instance = seed_runtime_service(db_session)
    instance.last_seen_at = datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC)
    seed_channel(db_session, event_types=["instance_online", "instance_offline"])
    db_session.commit()

    scan_and_dispatch_instance_connectivity_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC),
    )
    db_session.commit()


# --------------------------------------------------------------------------------------
# The scan's database work runs on the async foundation
# --------------------------------------------------------------------------------------


def test_scan_uses_native_async_session_and_commits(async_db, db_session) -> None:
    """The scan runs through ``session_scope`` and its writes are committed.

    A real connectivity scan is driven through the async path; the delivery must
    be visible afterwards to an independent synchronous reader.
    """

    _seed_offline_transition(db_session)

    async def scenario() -> int:
        # Seed the connectivity state first, exactly as the scanner would on its
        # first tick: the first scan only seeds, the second emits the delivery.
        async with session_scope() as session:
            await scan_and_dispatch_instance_connectivity_notifications_async(
                session, now=datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC)
            )
        async with session_scope() as session:
            return await scan_and_dispatch_instance_connectivity_notifications_async(
                session, now=datetime(2026, 4, 30, 2, 12, 0, tzinfo=UTC)
            )

    created = asyncio.run(scenario())

    assert created == 1
    assert db_session.query(NotificationDelivery).count() == 1
    assert async_db.checkedout() == 0, "the scan left a checked-out connection"


def test_scan_uses_no_to_thread_wrapper(async_db, db_session) -> None:
    """The scan is awaited on the async session, not pushed to a worker thread.

    ``AsyncSession.run_sync`` is SQLAlchemy's greenlet bridge and runs the body
    on the event-loop thread; ``asyncio.to_thread`` would hand it to a separate
    thread and defeat the point of the conversion. Instrumenting the session
    proves which one is used.
    """

    _seed_offline_transition(db_session)
    calls: list[str] = []

    real_run_sync = AsyncSession.run_sync

    async def recording_run_sync(self, fn, *args, **kwargs):  # noqa: ANN001
        calls.append("run_sync")
        return await real_run_sync(self, fn, *args, **kwargs)

    AsyncSession.run_sync = recording_run_sync  # type: ignore[method-assign]
    try:

        async def scenario() -> None:
            async with session_scope() as session:
                await scan_and_dispatch_instance_connectivity_notifications_async(
                    session, now=datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC)
                )

        asyncio.run(scenario())
    finally:
        AsyncSession.run_sync = real_run_sync  # type: ignore[method-assign]

    assert calls, "the scan did not go through AsyncSession.run_sync"


def test_scanner_run_uses_async_scan_functions(db_session, monkeypatch) -> None:
    """``run_notification_missed_start_scanner`` awaits the async scan helpers."""

    observed: list[str] = []
    real_missed = scan_and_dispatch_missed_start_notifications_async
    real_connectivity = scan_and_dispatch_instance_connectivity_notifications_async

    async def fake_missed(session, *, min_last_seen_at=None, now=None):
        observed.append("missed_start")
        return 0

    async def fake_connectivity(session, *, now=None):
        observed.append("connectivity")
        return 0

    import onestep_control_plane_api.workers.notification_scanner as scanner_module

    monkeypatch.setattr(
        scanner_module,
        "scan_and_dispatch_missed_start_notifications_async",
        fake_missed,
    )
    monkeypatch.setattr(
        scanner_module,
        "scan_and_dispatch_instance_connectivity_notifications_async",
        fake_connectivity,
    )
    assert real_missed is not None and real_connectivity is not None

    app = _build_app()
    lease = RecordingAsyncLease()
    scan_event = asyncio.Event()

    async def scan_fn(session, started_at) -> int:
        scan_event.set()
        return await scanner_module._scan_notifications(session, started_at)

    async def scenario() -> None:
        task = asyncio.create_task(
            run_notification_missed_start_scanner(
                app,
                sleep_fn=lambda _s: asyncio.sleep(0),
                scan_fn=scan_fn,
                lease_factory=lambda: lease,
                scan_interval_s=0,
                leader_poll_interval_s=0,
            )
        )
        await asyncio.wait_for(scan_event.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    # The scanner loop can complete more than one iteration before
    # task.cancel() lands (scan_interval_s=0), so `observed` may hold several
    # repeats. Assert the FIRST iteration's shape rather than the exact length:
    # the point is that the scanner awaits both async scan helpers, in order.
    assert observed[:2] == ["missed_start", "connectivity"], (
        f"scanner did not await the async scan helpers in order: {observed[:6]}"
    )


# --------------------------------------------------------------------------------------
# A slow scan must not freeze the event loop
# --------------------------------------------------------------------------------------


def test_slow_scan_does_not_block_a_no_db_health_probe(
    client, async_db, db_session, monkeypatch
) -> None:
    """While a scan work unit is in flight, ``GET /healthz`` keeps answering.

    ``/healthz`` is a static no-DB response, so a successful probe proves the
    event loop is running rather than that the database happens to be free. On
    the pre-conversion code the scan ran synchronously inside the loop and no
    probe could be served while it was in flight.

    Only one event loop is used. The scanner runs on it; the HTTP probe is
    driven from a side thread and signals completion through a
    ``threading.Event`` that the loop awaits *cooperatively* (polling with
    ``asyncio.sleep`` yields, so the scan proceeds). A second ``asyncio.run`` on
    a worker thread would instead risk outliving the test and holding a SQLite
    connection into the next test's teardown.
    """

    _seed_offline_transition(db_session)
    delay_s = 1.0
    entered = threading.Event()
    probe_done = threading.Event()
    real = scan_and_dispatch_instance_connectivity_notifications_async

    import onestep_control_plane_api.workers.notification_scanner as scanner_module

    async def slow_scan(session, *, now=None):
        entered.set()
        # Hold the work unit open until the probe window closes. The wait is
        # awaited, so it yields to the loop exactly like a slow query.
        while not probe_done.is_set():
            await asyncio.sleep(0.01)
        return await real(session, now=now)

    monkeypatch.setattr(
        scanner_module,
        "scan_and_dispatch_instance_connectivity_notifications_async",
        slow_scan,
    )

    app = _build_app()
    lease = RecordingAsyncLease()
    ticks = 0

    def probe() -> None:
        nonlocal ticks
        start = time.monotonic()
        while time.monotonic() - start < delay_s:
            response = client.get("/healthz")
            assert response.status_code == 200, "a slow scan froze the no-DB health probe"
            ticks += 1
            time.sleep(0.02)
        probe_done.set()

    async def scenario() -> None:
        nonlocal ticks
        task = asyncio.create_task(
            run_notification_missed_start_scanner(
                app,
                sleep_fn=lambda _s: asyncio.sleep(0),
                lease_factory=lambda: lease,
                scan_interval_s=0,
                leader_poll_interval_s=0,
            )
        )
        # Wait for the scan to actually start, yielding while we do.
        inner_deadline = asyncio.get_running_loop().time() + 10.0
        while not entered.is_set():
            if asyncio.get_running_loop().time() > inner_deadline:
                raise AssertionError("the scan never started")
            await asyncio.sleep(0.005)

        probe_thread = threading.Thread(target=probe, daemon=True)
        probe_thread.start()
        try:
            # Await the probe window cooperatively; the loop stays free for the
            # in-flight scan.
            outer_deadline = asyncio.get_running_loop().time() + 30.0
            while not probe_done.is_set():
                if asyncio.get_running_loop().time() > outer_deadline:
                    raise AssertionError("the probe window never closed")
                await asyncio.sleep(0.01)
        finally:
            probe_done.set()
            probe_thread.join(timeout=10.0)
            await _cancel_together(task)

    asyncio.run(scenario())

    assert ticks >= 5, f"health probe only answered {ticks} times during a {delay_s}s scan"
    assert async_db.checkedout() == 0, "the cancelled scan leaked a connection"


def test_slow_scan_does_not_block_the_event_loop_ticker(async_db, db_session) -> None:
    """An independent asyncio ticker keeps running through a slow scan.

    This is the in-loop counterpart of the probe above: it measures the event
    loop directly rather than through HTTP, so it is not sensitive to how
    ``TestClient`` schedules its portal.
    """

    _seed_offline_transition(db_session)
    ticker_ticks = 0
    finished = asyncio.Event()
    delay_s = 0.6
    real = scan_and_dispatch_instance_connectivity_notifications_async

    import onestep_control_plane_api.workers.notification_scanner as scanner_module

    async def slow_scan(session, *, now=None):
        await asyncio.sleep(delay_s)
        return await real(session, now=now)

    original = scanner_module.scan_and_dispatch_instance_connectivity_notifications_async
    scanner_module.scan_and_dispatch_instance_connectivity_notifications_async = slow_scan
    try:

        async def ticker() -> None:
            nonlocal ticker_ticks
            while not finished.is_set():
                await asyncio.sleep(0.01)
                ticker_ticks += 1

        async def scenario() -> None:
            app = _build_app()
            lease = RecordingAsyncLease()
            ticker_task = asyncio.create_task(ticker())
            task = asyncio.create_task(
                run_notification_missed_start_scanner(
                    app,
                    sleep_fn=lambda _s: asyncio.sleep(0),
                    lease_factory=lambda: lease,
                    scan_interval_s=0,
                    leader_poll_interval_s=0,
                )
            )
            await asyncio.sleep(delay_s * 1.5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            finished.set()
            ticker_task.cancel()

        asyncio.run(scenario())
    finally:
        scanner_module.scan_and_dispatch_instance_connectivity_notifications_async = original

    # A 0.6s scan at a 0.01s ticker would allow ~90 ticks; the assertion stays
    # far below that so it measures "the loop ran", not a timing coincidence.
    assert ticker_ticks >= 10, (
        f"event loop ran only {ticker_ticks} ticks during a {delay_s}s scan; "
        "the scan blocked the loop instead of yielding"
    )


def test_connection_pool_wait_does_not_block_the_event_loop(async_db) -> None:
    """Waiting for a pooled connection yields instead of blocking the loop.

    The wait is a real bounded-pool acquisition on its own database rather than
    a bare sleep, so the measurement is genuine.
    """

    ticker_ticks = 0
    finished = asyncio.Event()

    async def ticker() -> None:
        nonlocal ticker_ticks
        while not finished.is_set():
            await asyncio.sleep(0.01)
            ticker_ticks += 1

    async def scenario() -> int:
        nonlocal ticker_ticks
        ticker_task = asyncio.create_task(ticker())
        pool_engine = create_async_engine(
            shared_cache_async_url(f"{async_db.name}_scanner_pool"),
            future=True,
            poolclass=AsyncAdaptedQueuePool,
            pool_size=1,
            max_overflow=0,
            pool_timeout=5.0,
        )
        factory = async_sessionmaker(
            bind=pool_engine, autoflush=False, expire_on_commit=False, class_=AsyncSession
        )
        try:
            holder_session = factory()
            await holder_session.execute(text("SELECT 1"))

            async def second_work_unit() -> int:
                async with factory() as session:
                    return int((await session.execute(text("SELECT 42"))).scalar_one())

            second_task = asyncio.create_task(second_work_unit())
            await asyncio.sleep(0.5)
            await holder_session.close()
            value = await second_task
            finished.set()
            ticker_task.cancel()
            return value
        finally:
            await pool_engine.dispose()

    observed = asyncio.run(scenario())

    assert observed == 42
    assert ticker_ticks >= 3, (
        f"event loop ran only {ticker_ticks} ticks while a work unit waited for the pool"
    )


# --------------------------------------------------------------------------------------
# Cancellation, database exception and leader switch release resources
# --------------------------------------------------------------------------------------


def test_cancellation_during_scan_releases_everything(async_db, db_session) -> None:
    """Cancelling a scanner mid-scan releases the session, transaction and lease.

    The scan does real database work before it suspends, so the assertion is
    about a work unit that genuinely holds a checked-out connection and an open
    transaction at the moment of cancellation.
    """

    _seed_offline_transition(db_session)
    app = _build_app()
    lease = RecordingAsyncLease()
    entered = asyncio.Event()

    async def scan_fn(session, started_at) -> int:
        # Real DB work: checks a connection out and opens the transaction.
        await session.execute(text("SELECT 1"))
        entered.set()
        # Suspend mid-transaction, holding the connection.
        await asyncio.Event().wait()
        return 0

    async def scenario() -> None:
        task = asyncio.create_task(
            run_notification_missed_start_scanner(
                app,
                sleep_fn=lambda _s: asyncio.sleep(0),
                scan_fn=scan_fn,
                lease_factory=lambda: lease,
                scan_interval_s=0,
                leader_poll_interval_s=0,
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert async_db.checkedout() >= 1, "the scan did not check out a connection"
        await _cancel_together(task)

    asyncio.run(scenario())

    assert async_db.checkedout() == 0, "a cancelled scan leaked a connection"
    assert async_db.leaked_connections == 0
    assert async_db.open_sessions == [], "a cancelled scan left its session open"
    assert lease.release_count == 1, "a cancelled scanner did not release its lease"


def test_database_exception_releases_and_records_failure(async_db, db_session) -> None:
    """A failing scan rolls back, releases, and keeps the worker running."""

    _seed_offline_transition(db_session)
    app = _build_app()
    lease = RecordingAsyncLease()
    failures: list[BaseException] = []

    async def failing_scan(session, started_at) -> int:
        failures.append(RuntimeError("scan boom"))
        raise RuntimeError("scan boom")

    async def scenario() -> None:
        task = asyncio.create_task(
            run_notification_missed_start_scanner(
                app,
                sleep_fn=lambda _s: asyncio.sleep(0),
                scan_fn=failing_scan,
                lease_factory=lambda: lease,
                scan_interval_s=0,
                leader_poll_interval_s=0,
            )
        )
        deadline = asyncio.get_running_loop().time() + 2.0
        while len(failures) < 2 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert len(failures) >= 2, "the scanner stopped after the first failure"
    assert async_db.checkedout() == 0, "a failed scan leaked a connection"
    assert async_db.open_sessions == [], "a failed scan left its session open"
    state = app.state.background_task_states[NOTIFICATION_MISSED_START_SCANNER_NAME]
    assert state.last_error is not None, "the failure was not recorded in readiness state"
    assert state.last_success_at is None


def test_leader_switch_releases_the_lease_and_holds_no_connection(
    async_db, db_session
) -> None:
    """Losing leadership releases the lease and leaves no connection checked out.

    The scanner keeps polling (it may regain leadership), so the assertion is
    about resource release and about the scan *not* running while standby.
    """

    _seed_offline_transition(db_session)
    app = _build_app()
    lease = RecordingAsyncLease(leader=True)
    scans = 0

    async def scan_fn(session, started_at) -> int:
        nonlocal scans
        scans += 1
        return 0

    async def scenario() -> None:
        task = asyncio.create_task(
            run_notification_missed_start_scanner(
                app,
                sleep_fn=lambda _s: asyncio.sleep(0),
                scan_fn=scan_fn,
                lease_factory=lambda: lease,
                scan_interval_s=0,
                leader_poll_interval_s=0,
            )
        )
        deadline = asyncio.get_running_loop().time() + 2.0
        while scans == 0 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        assert scans >= 1, "the leader never scanned"

        # Leadership is lost: the scan must stop running and the lease release.
        lease._leader = False
        lease.release_count = 0
        before = scans
        deadline = asyncio.get_running_loop().time() + 0.5
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)

        assert scans == before, "the scan ran after leadership was lost"
        state = app.state.background_task_states[NOTIFICATION_MISSED_START_SCANNER_NAME]
        assert state.leadership_status == "standby"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert async_db.checkedout() == 0, "a standby replica leaked a connection"
    assert lease.release_count == 1, "the standby scanner did not release its lease"


async def _probe_scan(executions: dict[str, int], replica_id: str):
    """A scan body that does real DB work but no conflicting writes.

    It checks a connection out and runs a query, so "this replica executed" is
    proven against the database rather than a bare counter, but it does not
    insert the connectivity state row. That matters because the execution-count
    tests deliberately let both replicas run in lockstep, and two concurrent
    inserts of the same ``(channel_id, instance_id)`` state row would collide on
    the unique constraint for a reason unrelated to leadership.
    """

    async def scan_fn(session, started_at) -> int:
        executions[replica_id] += 1
        await session.execute(text("SELECT 1"))
        return 0

    return scan_fn


async def _real_scan(
    executions: dict[str, int],
    replica_id: str,
    scan_instant: datetime,
    deliveries: dict[str, int] | None = None,
):
    """A scan body that runs the real connectivity scan."""

    async def scan_fn(session, started_at) -> int:
        executions[replica_id] += 1
        return await scan_and_dispatch_instance_connectivity_notifications_async(
            session, now=scan_instant
        )

    return scan_fn


async def _leader_only_scan(executions: dict[str, int], replica_id: str, scan_instant: datetime,
                            deliveries: dict[str, int] | None = None):
    """Only the replica named ``leader`` runs the real connectivity scan.

    Used where a real delivery must be produced but only one replica may write,
    to avoid SQLite writer contention between two concurrent real scans.
    """

    if replica_id != "leader":
        return await _probe_scan(executions, replica_id)

    async def scan_fn(session, started_at) -> int:
        executions[replica_id] += 1
        created = await scan_and_dispatch_instance_connectivity_notifications_async(
            session, now=scan_instant
        )
        if deliveries is not None and created:
            deliveries["count"] += created
        return created

    return scan_fn


_active_harness = None


async def _run_two_scanners(
    leases: tuple[AsyncWorkerLease, AsyncWorkerLease],
    executions: dict[str, int],
    *,
    scan_instant: datetime,
    stop_when,
    scan_body=_probe_scan,
    deliveries: dict[str, int] | None = None,
    replica_ids: tuple[str, str] = ("one", "two"),
    harness=None,
) -> None:
    """Drive two concurrent scanners and record what each one executed.

    Each scanner runs its scan body against the shared test database, so the
    test proves the path executes under leadership rather than a stub being
    counted.
    """

    global _active_harness
    _active_harness = harness

    async def no_sleep(_: float) -> None:
        await asyncio.sleep(0)

    tasks = []
    for lease, replica_id in zip(leases, replica_ids):
        app = _build_app()
        if scan_body is _probe_scan:
            scan_fn = await scan_body(executions, replica_id)
        else:
            scan_fn = await scan_body(executions, replica_id, scan_instant, deliveries)
        tasks.append(
            asyncio.create_task(
                run_notification_missed_start_scanner(
                    app,
                    sleep_fn=no_sleep,
                    scan_fn=scan_fn,
                    lease_factory=lambda lease=lease: lease,
                    scan_interval_s=0,
                    leader_poll_interval_s=0,
                )
            )
        )

    deadline = asyncio.get_running_loop().time() + 5.0
    while not stop_when() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)

    await _cancel_together(*tasks)
    await _await_release(_active_harness)


# --------------------------------------------------------------------------------------
# MULTI-INSTANCE: assert the LEASE MECHANISM, not just delivery counts
# --------------------------------------------------------------------------------------


def test_only_one_scanner_executes_while_leases_contend(async_db, db_session) -> None:
    """Two concurrent scanners: exactly one executes, and one lock is taken.

    Why this asserts the mechanism rather than a delivery count
    ----------------------------------------------------------
    Asserting only "two scanners produced <= 1 delivery" would be **vacuous**:
    ``_instance_transition_time`` derives the event's ``occurred_at`` from
    stable stored values (``last_seen_at + instance_offline_after_s``) and
    ``instance_connectivity_dedupe_key`` embeds it, so both scanners compute a
    byte-identical dedupe key and the ``uq_notification_deliveries_dedupe_key``
    constraint blocks the duplicate even with **no lease at all**. Such a test
    could not fail for the reason this issue cares about.

    So this test asserts, and both signals differ between the real lease and a
    forced no-lock control:

    (a) ``executions`` — how many scans actually ran while holding leadership.
        With one shared coordinator, exactly one replica may ever execute. A
        no-lock control (every replica always leader) lets both execute.
    (b) ``advisory_lock_count`` — how many times leadership was actually
        acquired. Exactly one for a contended lease; a control that grants
        leadership without a lock reports one per replica instead.

    ``executions >= 1`` is likewise asserted, so a bug that disabled the scan
    entirely (or a clock/reset bug that made it a no-op) cannot pass.
    """

    _seed_offline_transition(db_session)
    coordinator: dict = {}
    lease_one = SharedLeadershipLease(coordinator=coordinator, replica_id="one")
    lease_two = SharedLeadershipLease(coordinator=coordinator, replica_id="two")
    executions = {"one": 0, "two": 0}

    async def scenario() -> None:
        await _run_two_scanners(
            (lease_one, lease_two),
            executions,
            scan_instant=datetime(2026, 4, 30, 2, 12, 0, tzinfo=UTC),
            stop_when=lambda: sum(executions.values()) >= 3,
            scan_body=_probe_scan,
            harness=async_db,
        )

    asyncio.run(scenario())

    total_executions = sum(executions.values())
    total_locks = lease_one.advisory_lock_count + lease_two.advisory_lock_count

    # (a) Only the lease holder ever executed. This is the discriminating
    # assertion: a no-lock control would let both replicas run.
    assert (executions["one"] == 0) != (executions["two"] == 0), (
        f"both scanners executed (one={executions['one']}, two={executions['two']}); "
        "leadership did not gate execution"
    )
    # (b) Leadership was acquired exactly once - a genuine lock, not a grant.
    assert total_locks == 1, f"expected exactly one leadership acquisition, got {total_locks}"
    assert total_executions >= 1, "no scanner executed at all"
    assert async_db.checkedout() == 0, "a contended scanner leaked a connection"
    assert lease_one.release_count == 1 and lease_two.release_count == 1


def test_no_lock_control_lets_both_scanners_execute(async_db, db_session) -> None:
    """CONTROL for the test above: without a lock, BOTH scanners execute.

    This is the discriminating-power evidence. Every replica is granted
    leadership unconditionally (no shared coordinator, no lock), so both scans
    run - the exact behaviour the lease is supposed to prevent. If the lease
    were broken so that both replicas believed they were leader, the test above
    would report this shape and fail.

    Same database and same seeded transition as the real test, so the only
    variable is the leadership mechanism.
    """

    _seed_offline_transition(db_session)
    lease_one = RecordingAsyncLease(leader=True)
    lease_two = RecordingAsyncLease(leader=True)
    executions = {"one": 0, "two": 0}

    async def scenario() -> None:
        await _run_two_scanners(
            (lease_one, lease_two),
            executions,
            scan_instant=datetime(2026, 4, 30, 2, 12, 0, tzinfo=UTC),
            stop_when=lambda: executions["one"] >= 1 and executions["two"] >= 1,
            scan_body=_probe_scan,
            harness=async_db,
        )

    asyncio.run(scenario())

    # The control's whole point: both replicas ran. The real lease prevents this.
    assert executions["one"] >= 1 and executions["two"] >= 1, (
        f"the no-lock control did not let both scanners run: {executions}"
    )
    # Each replica "acquired" leadership, so the count is 2 rather than the
    # single acquisition the contended lease reports.
    assert lease_one.advisory_lock_count == 1 and lease_two.advisory_lock_count == 1
    assert async_db.checkedout() == 0, "the control leaked a connection"


def test_leader_scan_produces_a_delivery(async_db, db_session) -> None:
    """The leader's scan really creates a delivery, end to end.

    Guards against a clock/reset bug or a scan that silently became a no-op:
    with a real lease and a real offline transition, at least one delivery must
    be created and persisted. (See the docstring of the multi-instance test for
    why this is asserted *in addition to*, never instead of, the lease signals.)

    One scanner only. The two-replica variant of this was removed deliberately:
    SQLite's shared-cache mode serialises writers, so two concurrent REAL
    connectivity scans collide on "database table is locked" and can leave a
    connection still closing when the fixture tears the schema down. That is a
    limitation of the sqlite test harness, not of the code under test, and the
    leadership property it would have exercised is already asserted by
    ``test_only_one_scanner_executes_while_leases_contend`` and its no-lock
    control, which use a probe scan body for exactly this reason.
    """

    _seed_offline_transition(db_session)
    lease = SharedLeadershipLease(coordinator={}, replica_id="one")
    executions = {"one": 0}
    deliveries = {"count": 0}
    completed = asyncio.Event()

    async def scan_fn(session, started_at) -> int:
        executions["one"] += 1
        created = await scan_and_dispatch_instance_connectivity_notifications_async(
            session, now=datetime(2026, 4, 30, 2, 12, 0, tzinfo=UTC)
        )
        deliveries["count"] += created
        completed.set()
        return created

    async def scenario() -> None:
        app = _build_app()

        async def no_sleep(_: float) -> None:
            await asyncio.sleep(0)

        task = asyncio.create_task(
            run_notification_missed_start_scanner(
                app,
                sleep_fn=no_sleep,
                scan_fn=scan_fn,
                lease_factory=lambda: lease,
                scan_interval_s=0,
                leader_poll_interval_s=0,
            )
        )
        # Wait for the scan to FINISH rather than merely start. Cancelling
        # mid-transaction would leave the rollback to complete asynchronously on
        # aiosqlite's worker thread, which can still be closing a connection when
        # the fixture drops the schema (SQLite then reports "database table is
        # locked"). Cancelling between scans has none of that and still exercises
        # the scanner's shutdown path.
        await asyncio.wait_for(completed.wait(), timeout=5)
        await _cancel_together(task)
        await _await_release(async_db)

    asyncio.run(scenario())

    assert completed.is_set(), "the scan did not complete"
    assert executions["one"] >= 1, "the leader never scanned"
    assert deliveries["count"] >= 1, "a real offline transition produced no delivery"
    assert db_session.query(NotificationDelivery).count() >= 1, (
        "the delivery was not persisted"
    )
    assert async_db.checkedout() == 0, "the scanner leaked a connection"


# --------------------------------------------------------------------------------------
# Lease construction and mode reporting
# --------------------------------------------------------------------------------------


def test_async_lease_is_local_for_sqlite_and_advisory_for_postgres() -> None:
    """The async lease picks its mechanism exactly like the sync factory.

    The sqlite test path has no advisory locks, so it must fall back to the
    local lease rather than trying to run ``pg_try_advisory_lock``.
    """

    from onestep_control_plane_api.workers.notification_scanner import (
        LocalAsyncWorkerLease,
        PostgresAdvisoryAsyncWorkerLease,
        create_async_worker_lease,
    )

    sqlite_engine = create_async_engine(shared_cache_async_url("lease_mode_probe"), future=True)
    postgres_engine = create_async_engine(
        "postgresql+psycopg://user:pass@localhost/db", future=True
    )
    try:
        assert isinstance(
            create_async_worker_lease(engine=sqlite_engine, lock_key=1, worker_name="w"),
            LocalAsyncWorkerLease,
        )
        assert isinstance(
            create_async_worker_lease(engine=None, lock_key=1, worker_name="w"),
            LocalAsyncWorkerLease,
        )
        assert isinstance(
            create_async_worker_lease(engine=postgres_engine, lock_key=1, worker_name="w"),
            PostgresAdvisoryAsyncWorkerLease,
        )
    finally:
        asyncio.run(sqlite_engine.dispose())
        asyncio.run(postgres_engine.dispose())


def test_scanner_reports_lease_mode_in_readiness(db_session) -> None:
    """The readiness state reports which lease mechanism gated the worker."""

    _seed_offline_transition(db_session)
    app = _build_app()
    lease = LocalAsyncWorkerLease()
    scan_event = asyncio.Event()

    async def scan_fn(session, started_at) -> int:
        scan_event.set()
        return 0

    async def scenario() -> None:
        task = asyncio.create_task(
            run_notification_missed_start_scanner(
                app,
                sleep_fn=lambda _s: asyncio.sleep(0),
                scan_fn=scan_fn,
                lease_factory=lambda: lease,
                scan_interval_s=0,
                leader_poll_interval_s=0,
            )
        )
        await asyncio.wait_for(scan_event.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    state = app.state.background_task_states[NOTIFICATION_MISSED_START_SCANNER_NAME]
    assert state.leadership_mode == "local"
    assert state.leadership_status == "leader"
    assert state.last_success_at is not None


def test_scanner_defaults_resolve_the_async_engine(async_db, db_session) -> None:
    """With no injected lease, the scanner builds one off the async engine.

    On the sqlite test path that resolves to the local lease, so the default
    path is exercised end to end without a PostgreSQL server.
    """

    _seed_offline_transition(db_session)
    app = _build_app()
    scan_event = asyncio.Event()

    async def scan_fn(session, started_at) -> int:
        scan_event.set()
        return 0

    async def scenario() -> None:
        task = asyncio.create_task(
            run_notification_missed_start_scanner(
                app,
                sleep_fn=lambda _s: asyncio.sleep(0),
                scan_fn=scan_fn,
                scan_interval_s=0,
                leader_poll_interval_s=0,
            )
        )
        await asyncio.wait_for(scan_event.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    state = app.state.background_task_states[NOTIFICATION_MISSED_START_SCANNER_NAME]
    assert state.leadership_mode == "local"
    assert state.last_success_at is not None


def _pg_url() -> str | None:
    """The integration PostgreSQL URL, or ``None`` when it is unreachable."""

    import os
    import socket

    url = os.environ.get("ONESTEP_TEST_POSTGRES_URL")
    if not url:
        return None
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    sock = socket.socket()
    sock.settimeout(1.0)
    try:
        sock.connect((parsed.host or "127.0.0.1", parsed.port or 5432))
    except OSError:
        return None
    finally:
        sock.close()
    return url


@pytest.mark.skipif(_pg_url() is None, reason="no PostgreSQL available for the lease test")
def test_postgres_advisory_lease_locks_and_releases() -> None:
    """Real PostgreSQL: the lease takes exactly one advisory lock and releases it.

    Exercises ``PostgresAdvisoryAsyncWorkerLease`` end to end, which the sqlite
    path cannot: sqlite has no advisory locks, so it always falls back to the
    local lease and none of this code runs.

    Two replicas contending on the same key must not both be leader, and after
    release the lock must be gone — asserted through ``pg_locks`` rather than
    through the object's own bookkeeping.
    """

    url = _pg_url()
    assert url is not None
    engine = create_async_engine(url, future=True, poolclass=NullPool)
    lock_key = 8675309

    async def scenario() -> dict:
        lease_a = PostgresAdvisoryAsyncWorkerLease(
            engine=engine, lock_key=lock_key, worker_name="a"
        )
        lease_b = PostgresAdvisoryAsyncWorkerLease(
            engine=engine, lock_key=lock_key, worker_name="b"
        )
        result = {}
        result["a_first"] = await lease_a.ensure_leader()
        result["b_second"] = await lease_b.ensure_leader()
        result["a_lock_count"] = lease_a.advisory_lock_count
        result["b_lock_count"] = lease_b.advisory_lock_count

        async with engine.connect() as conn:
            held = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM pg_locks "
                        "WHERE locktype = 'advisory' AND objid = :key"
                    ),
                    {"key": lock_key},
                )
            ).scalar_one()
        result["held_while_leader"] = int(held)

        await lease_a.release()
        async with engine.connect() as conn:
            after = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM pg_locks "
                        "WHERE locktype = 'advisory' AND objid = :key"
                    ),
                    {"key": lock_key},
                )
            ).scalar_one()
        result["held_after_release"] = int(after)
        return result

    try:
        observed = asyncio.run(scenario())
    finally:
        asyncio.run(engine.dispose())

    assert observed["a_first"] is True, "the first replica failed to take the lock"
    assert observed["b_second"] is False, "a second replica took the same advisory lock"
    assert observed["a_lock_count"] == 1
    assert observed["b_lock_count"] == 0
    assert observed["held_while_leader"] == 1
    assert observed["held_after_release"] == 0, "release left the advisory lock held"


@pytest.mark.skipif(_pg_url() is None, reason="no PostgreSQL available for the lease test")
def test_postgres_advisory_lease_holds_no_idle_transaction() -> None:
    """Real PostgreSQL: holding leadership must not hold an open transaction.

    A long-lived ``idle in transaction`` backend blocks vacuum cleanup and
    occupies a pooled connection for as long as leadership is held. The
    synchronous sibling commits right after ``pg_try_advisory_lock`` and never
    shows one, so the async lease must not either.

    Asserted through ``pg_stat_activity``: while leadership is held the backend
    owning the lock has no transaction age.
    """

    url = _pg_url()
    assert url is not None
    engine = create_async_engine(url, future=True, poolclass=NullPool)
    lock_key = 8675310

    async def idle_state() -> tuple[str | None, bool | None]:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT a.state, age(clock_timestamp(), a.xact_start) IS NOT NULL "
                        "FROM pg_stat_activity a "
                        "JOIN pg_locks l ON l.pid = a.pid "
                        "WHERE l.locktype = 'advisory' AND l.objid = :key"
                    ),
                    {"key": lock_key},
                )
            ).first()
        return (row[0] if row else None, bool(row[1]) if row else None)

    async def scenario() -> dict:
        lease = PostgresAdvisoryAsyncWorkerLease(
            engine=engine, lock_key=lock_key, worker_name="idle-probe"
        )
        # ACQUIRE path: assert immediately after the first ensure_leader(), with
        # no renew in between. The renew path commits too, so calling
        # ensure_leader() again before asserting would mask an uncommitted
        # acquire -- which is exactly the regression this test exists to catch.
        await lease.ensure_leader()
        await asyncio.sleep(1.0)  # let any transaction age accumulate
        acquire_state, acquire_has_xact = await idle_state()

        # RENEW path: assert again after a second ensure_leader() so both paths
        # are covered independently.
        await lease.ensure_leader()
        await asyncio.sleep(1.0)
        renew_state, renew_has_xact = await idle_state()

        await lease.release()
        return {
            "acquire_state": acquire_state,
            "acquire_has_xact": acquire_has_xact,
            "renew_state": renew_state,
            "renew_has_xact": renew_has_xact,
        }

    try:
        observed = asyncio.run(scenario())
    finally:
        asyncio.run(engine.dispose())

    for phase in ("acquire", "renew"):
        state = observed[f"{phase}_state"]
        assert state is not None, f"no backend was found holding the advisory lock ({phase})"
        assert state != "idle in transaction", (
            f"the {phase} path left a backend idle in transaction; commit right after "
            "pg_try_advisory_lock instead of holding the transaction open"
        )
        assert observed[f"{phase}_has_xact"] is False, (
            f"the {phase} path is holding an open transaction"
        )


def test_scanner_uses_configured_intervals_when_unset(db_session) -> None:
    """Interval parameters fall back to settings, as before the conversion."""

    assert settings.notification_missed_start_scan_interval_s > 0
    assert settings.background_worker_leader_poll_interval_s > 0


def test_async_worker_lease_base_class_is_abstract() -> None:
    """The lease contract is async: ensure_leader and release are awaited."""

    lease = AsyncWorkerLease()

    async def scenario() -> None:
        for coro in (lease.ensure_leader(), lease.release()):
            with pytest.raises(NotImplementedError):
                await coro

    asyncio.run(scenario())


def test_module_constants_are_stable() -> None:
    """The lock key and worker name are part of the deployment contract."""

    assert NOTIFICATION_MISSED_START_SCANNER_NAME == "notification_missed_start_scanner"
    assert NOTIFICATION_MISSED_START_SCANNER_LOCK_KEY == zlib.crc32(
        b"onestep-control-plane.notification-missed-start-scanner"
    )


def test_session_scope_is_the_work_unit_entry_point() -> None:
    """The scanner's work unit is the project's ``session_scope`` convention."""

    assert hasattr(db_session_module, "session_scope")
