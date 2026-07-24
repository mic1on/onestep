from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from onestep_control_plane_api.ops.readiness import build_default_background_task_states
from onestep_control_plane_api.workers.leader import LocalWorkerLease, WorkerLease
from onestep_control_plane_api.workers.notification_scanner import (
    NOTIFICATION_MISSED_START_SCANNER_NAME,
    run_notification_missed_start_scanner,
)
from onestep_control_plane_api.workers.retention_worker import (
    RETENTION_WORKER_NAME,
    run_retention_worker,
)


async def _yield_once(_: float) -> None:
    await asyncio.sleep(0)


async def _wait_until(predicate, *, timeout_s: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition was not met before timeout")
        await asyncio.sleep(0)


def _build_app() -> FastAPI:
    app = FastAPI()
    app.state.session_factory = lambda: nullcontext(object())
    app.state.background_task_states = build_default_background_task_states()
    app.state.background_task_refs = {
        NOTIFICATION_MISSED_START_SCANNER_NAME: None,
        RETENTION_WORKER_NAME: None,
    }
    return app


class SharedLeaseCoordinator:
    def __init__(self) -> None:
        self.owner: str | None = None


class CoordinatedLease(WorkerLease):
    mode = "postgres_advisory_lock"

    def __init__(self, *, coordinator: SharedLeaseCoordinator, replica_id: str) -> None:
        self._coordinator = coordinator
        self._replica_id = replica_id
        self.acquired_at: datetime | None = None
        self.release_count = 0

    def ensure_leader(self) -> bool:
        if self._coordinator.owner in (None, self._replica_id):
            if self._coordinator.owner is None:
                self._coordinator.owner = self._replica_id
                self.acquired_at = datetime.now(UTC)
            return True
        return False

    def release(self) -> None:
        self.release_count += 1
        if self._coordinator.owner == self._replica_id:
            self._coordinator.owner = None
        self.acquired_at = None


def test_notification_scanner_runs_in_local_mode() -> None:
    async def scenario() -> None:
        app = _build_app()
        scan_count = 0
        scan_event = asyncio.Event()

        def scan_fn(_session, _started_at: datetime) -> int:
            nonlocal scan_count
            scan_count += 1
            scan_event.set()
            return 1

        task = asyncio.create_task(
            run_notification_missed_start_scanner(
                app,
                sleep_fn=_yield_once,
                scan_fn=scan_fn,
                lease_factory=LocalWorkerLease,
                scan_interval_s=0,
                leader_poll_interval_s=0,
            )
        )
        app.state.background_task_refs[NOTIFICATION_MISSED_START_SCANNER_NAME] = task

        await asyncio.wait_for(scan_event.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        state = app.state.background_task_states[NOTIFICATION_MISSED_START_SCANNER_NAME]
        assert scan_count >= 1
        assert state.leadership_mode == "local"
        assert state.leadership_status == "leader"
        assert state.last_success_at is not None

    asyncio.run(scenario())


def test_only_one_replica_executes_scanner_when_leases_contend() -> None:
    async def scenario() -> None:
        coordinator = SharedLeaseCoordinator()
        app_one = _build_app()
        app_two = _build_app()
        lease_one = CoordinatedLease(coordinator=coordinator, replica_id="one")
        lease_two = CoordinatedLease(coordinator=coordinator, replica_id="two")
        scans = {"one": 0, "two": 0}

        def scan_one(_session, _started_at: datetime) -> int:
            scans["one"] += 1
            return 1

        def scan_two(_session, _started_at: datetime) -> int:
            scans["two"] += 1
            return 1

        task_one = asyncio.create_task(
            run_notification_missed_start_scanner(
                app_one,
                sleep_fn=_yield_once,
                scan_fn=scan_one,
                lease_factory=lambda: lease_one,
                scan_interval_s=0,
                leader_poll_interval_s=0,
            )
        )
        task_two = asyncio.create_task(
            run_notification_missed_start_scanner(
                app_two,
                sleep_fn=_yield_once,
                scan_fn=scan_two,
                lease_factory=lambda: lease_two,
                scan_interval_s=0,
                leader_poll_interval_s=0,
            )
        )
        app_one.state.background_task_refs[NOTIFICATION_MISSED_START_SCANNER_NAME] = task_one
        app_two.state.background_task_refs[NOTIFICATION_MISSED_START_SCANNER_NAME] = task_two

        await _wait_until(
            lambda: scans["one"] + scans["two"] >= 1
            and {
                app_one.state.background_task_states[
                    NOTIFICATION_MISSED_START_SCANNER_NAME
                ].leadership_status,
                app_two.state.background_task_states[
                    NOTIFICATION_MISSED_START_SCANNER_NAME
                ].leadership_status,
            }
            == {"leader", "standby"}
        )

        task_one.cancel()
        task_two.cancel()
        for task in (task_one, task_two):
            with pytest.raises(asyncio.CancelledError):
                await task

        assert (scans["one"] == 0) != (scans["two"] == 0)

    asyncio.run(scenario())


def test_standby_replica_takes_over_after_leader_exit() -> None:
    async def scenario() -> None:
        coordinator = SharedLeaseCoordinator()
        app_one = _build_app()
        app_two = _build_app()
        lease_one = CoordinatedLease(coordinator=coordinator, replica_id="one")
        lease_two = CoordinatedLease(coordinator=coordinator, replica_id="two")
        first_scan_event = asyncio.Event()
        second_scan_event = asyncio.Event()

        def scan_one(_session, _started_at: datetime) -> int:
            first_scan_event.set()
            return 1

        def scan_two(_session, _started_at: datetime) -> int:
            second_scan_event.set()
            return 1

        task_one = asyncio.create_task(
            run_notification_missed_start_scanner(
                app_one,
                sleep_fn=_yield_once,
                scan_fn=scan_one,
                lease_factory=lambda: lease_one,
                scan_interval_s=0,
                leader_poll_interval_s=0,
            )
        )
        app_one.state.background_task_refs[NOTIFICATION_MISSED_START_SCANNER_NAME] = task_one
        await asyncio.wait_for(first_scan_event.wait(), timeout=1)

        task_two = asyncio.create_task(
            run_notification_missed_start_scanner(
                app_two,
                sleep_fn=_yield_once,
                scan_fn=scan_two,
                lease_factory=lambda: lease_two,
                scan_interval_s=0,
                leader_poll_interval_s=0,
            )
        )
        app_two.state.background_task_refs[NOTIFICATION_MISSED_START_SCANNER_NAME] = task_two
        await _wait_until(
            lambda: app_two.state.background_task_states[
                NOTIFICATION_MISSED_START_SCANNER_NAME
            ].leadership_status
            == "standby"
        )

        task_one.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task_one

        await asyncio.wait_for(second_scan_event.wait(), timeout=1)
        task_two.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task_two

        state_two = app_two.state.background_task_states[
            NOTIFICATION_MISSED_START_SCANNER_NAME
        ]
        assert lease_one.release_count == 1
        assert lease_two.release_count == 1
        assert state_two.leadership_status == "leader"
        assert coordinator.owner is None

    asyncio.run(scenario())


def test_retention_worker_runs_in_local_mode() -> None:
    async def scenario() -> None:
        app = _build_app()
        run_count = 0
        run_event = asyncio.Event()

        def run_fn(_session, _started_at: datetime) -> object:
            nonlocal run_count
            run_count += 1
            run_event.set()
            return object()

        task = asyncio.create_task(
            run_retention_worker(
                app,
                sleep_fn=_yield_once,
                run_fn=run_fn,
                lease_factory=LocalWorkerLease,
                run_interval_s=3600,
                leader_poll_interval_s=0,
            )
        )
        app.state.background_task_refs[RETENTION_WORKER_NAME] = task

        await asyncio.wait_for(run_event.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        state = app.state.background_task_states[RETENTION_WORKER_NAME]
        assert run_count == 1
        assert state.leadership_mode == "local"
        assert state.leadership_status == "leader"
        assert state.last_success_at is not None

    asyncio.run(scenario())


def test_only_one_replica_executes_retention_when_leases_contend() -> None:
    async def scenario() -> None:
        coordinator = SharedLeaseCoordinator()
        app_one = _build_app()
        app_two = _build_app()
        lease_one = CoordinatedLease(coordinator=coordinator, replica_id="one")
        lease_two = CoordinatedLease(coordinator=coordinator, replica_id="two")
        runs = {"one": 0, "two": 0}

        def run_one(_session, _started_at: datetime) -> object:
            runs["one"] += 1
            return object()

        def run_two(_session, _started_at: datetime) -> object:
            runs["two"] += 1
            return object()

        task_one = asyncio.create_task(
            run_retention_worker(
                app_one,
                sleep_fn=_yield_once,
                run_fn=run_one,
                lease_factory=lambda: lease_one,
                run_interval_s=3600,
                leader_poll_interval_s=0,
            )
        )
        task_two = asyncio.create_task(
            run_retention_worker(
                app_two,
                sleep_fn=_yield_once,
                run_fn=run_two,
                lease_factory=lambda: lease_two,
                run_interval_s=3600,
                leader_poll_interval_s=0,
            )
        )
        app_one.state.background_task_refs[RETENTION_WORKER_NAME] = task_one
        app_two.state.background_task_refs[RETENTION_WORKER_NAME] = task_two

        await _wait_until(
            lambda: runs["one"] + runs["two"] >= 1
            and {
                app_one.state.background_task_states[RETENTION_WORKER_NAME].leadership_status,
                app_two.state.background_task_states[RETENTION_WORKER_NAME].leadership_status,
            }
            == {"leader", "standby"}
        )

        task_one.cancel()
        task_two.cancel()
        for task in (task_one, task_two):
            with pytest.raises(asyncio.CancelledError):
                await task

        assert (runs["one"] == 0) != (runs["two"] == 0)

    asyncio.run(scenario())


def test_retention_standby_replica_takes_over_after_leader_exit() -> None:
    async def scenario() -> None:
        coordinator = SharedLeaseCoordinator()
        app_one = _build_app()
        app_two = _build_app()
        lease_one = CoordinatedLease(coordinator=coordinator, replica_id="one")
        lease_two = CoordinatedLease(coordinator=coordinator, replica_id="two")
        first_run_event = asyncio.Event()
        second_run_event = asyncio.Event()

        def run_one(_session, _started_at: datetime) -> object:
            first_run_event.set()
            return object()

        def run_two(_session, _started_at: datetime) -> object:
            second_run_event.set()
            return object()

        task_one = asyncio.create_task(
            run_retention_worker(
                app_one,
                sleep_fn=_yield_once,
                run_fn=run_one,
                lease_factory=lambda: lease_one,
                run_interval_s=3600,
                leader_poll_interval_s=0,
            )
        )
        app_one.state.background_task_refs[RETENTION_WORKER_NAME] = task_one
        await asyncio.wait_for(first_run_event.wait(), timeout=1)

        task_two = asyncio.create_task(
            run_retention_worker(
                app_two,
                sleep_fn=_yield_once,
                run_fn=run_two,
                lease_factory=lambda: lease_two,
                run_interval_s=3600,
                leader_poll_interval_s=0,
            )
        )
        app_two.state.background_task_refs[RETENTION_WORKER_NAME] = task_two
        await _wait_until(
            lambda: app_two.state.background_task_states[RETENTION_WORKER_NAME].leadership_status
            == "standby"
        )

        task_one.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task_one

        await asyncio.wait_for(second_run_event.wait(), timeout=1)
        task_two.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task_two

        state_two = app_two.state.background_task_states[RETENTION_WORKER_NAME]
        assert lease_one.release_count == 1
        assert lease_two.release_count == 1
        assert state_two.leadership_status == "leader"
        assert coordinator.owner is None

    asyncio.run(scenario())
