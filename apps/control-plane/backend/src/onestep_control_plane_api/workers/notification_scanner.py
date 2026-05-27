from __future__ import annotations

import logging
import zlib
from asyncio import sleep
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.notification_service import (
    scan_and_dispatch_instance_connectivity_notifications,
    scan_and_dispatch_missed_start_notifications,
)
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.workers.leader import (
    WorkerLease,
    WorkerLeaseError,
    create_worker_lease,
)

logger = logging.getLogger("onestep_control_plane_api.workers.notification_scanner")

NOTIFICATION_MISSED_START_SCANNER_NAME = "notification_missed_start_scanner"
NOTIFICATION_MISSED_START_SCANNER_LOCK_KEY = zlib.crc32(
    b"onestep-control-plane.notification-missed-start-scanner"
)

SessionFactory = Callable[[], Any]
SleepFn = Callable[[float], Awaitable[None]]
ScanFn = Callable[[Session, datetime], int]
LeaseFactory = Callable[[], WorkerLease]


def _scan_notifications(session: Session, started_at: datetime) -> int:
    missed_start_count = scan_and_dispatch_missed_start_notifications(
        session,
        min_last_seen_at=started_at,
    )
    instance_connectivity_count = scan_and_dispatch_instance_connectivity_notifications(
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


def _default_lease_factory(app: FastAPI) -> LeaseFactory:
    session_factory = getattr(app.state, "session_factory")
    engine = _resolve_engine(session_factory)
    return lambda: create_worker_lease(
        engine=engine,
        lock_key=NOTIFICATION_MISSED_START_SCANNER_LOCK_KEY,
        worker_name=NOTIFICATION_MISSED_START_SCANNER_NAME,
    )


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
    session_factory = getattr(app.state, "session_factory")
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
    lease = (
        lease_factory()
        if lease_factory is not None
        else _default_lease_factory(app)()
    )

    state.mark_started(run_started_at)
    state.mark_starting(lease.mode, when=run_started_at)
    await sleep_fn(run_interval_s)

    try:
        while True:
            state.mark_tick()
            previous_status = state.leadership_status
            try:
                is_leader = lease.ensure_leader()
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
                with session_factory() as session:
                    scan_fn(session, run_started_at)
                state.mark_success()
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
        lease.release()
