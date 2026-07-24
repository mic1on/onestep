from __future__ import annotations

import logging
import zlib
from asyncio import sleep
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.ops.retention import run_retention
from onestep_control_plane_api.workers.leader import (
    WorkerLease,
    WorkerLeaseError,
    create_worker_lease,
)

logger = logging.getLogger("onestep_control_plane_api.workers.retention")

RETENTION_WORKER_NAME = "retention_worker"
RETENTION_WORKER_LOCK_KEY = zlib.crc32(b"onestep-control-plane.retention-worker")

SessionFactory = Callable[[], Any]
SleepFn = Callable[[float], Awaitable[None]]
RunRetentionFn = Callable[[Session, datetime], object]
LeaseFactory = Callable[[], WorkerLease]


def _run_retention(session: Session, run_started_at: datetime) -> object:
    return run_retention(session, execute=True, now=run_started_at)


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
        lock_key=RETENTION_WORKER_LOCK_KEY,
        worker_name=RETENTION_WORKER_NAME,
    )


async def run_retention_worker(
    app: FastAPI,
    *,
    started_at: datetime | None = None,
    sleep_fn: SleepFn = sleep,
    run_fn: RunRetentionFn = _run_retention,
    lease_factory: LeaseFactory | None = None,
    run_interval_s: float | None = None,
    leader_poll_interval_s: float | None = None,
) -> None:
    session_factory = getattr(app.state, "session_factory")
    state = app.state.background_task_states[RETENTION_WORKER_NAME]
    worker_started_at = started_at or datetime.now(UTC)
    retention_interval_s = float(
        settings.retention_run_interval_s if run_interval_s is None else run_interval_s
    )
    poll_interval_s = float(
        settings.background_worker_leader_poll_interval_s
        if leader_poll_interval_s is None
        else leader_poll_interval_s
    )
    lease = lease_factory() if lease_factory is not None else _default_lease_factory(app)()
    next_run_at = worker_started_at

    state.mark_started(worker_started_at)
    state.mark_starting(lease.mode, when=worker_started_at)

    try:
        while True:
            state.mark_tick()
            previous_status = state.leadership_status
            try:
                is_leader = lease.ensure_leader()
            except WorkerLeaseError as exc:
                state.mark_lease_failure(lease.mode, exc)
                logger.exception(
                    "retention worker lease check failed",
                    extra={"lease_mode": lease.mode},
                )
                await sleep_fn(poll_interval_s)
                continue

            if not is_leader:
                state.mark_standby(lease.mode)
                if previous_status != "standby":
                    logger.info(
                        "retention worker standing by for leadership",
                        extra={"lease_mode": lease.mode},
                    )
                await sleep_fn(poll_interval_s)
                continue

            state.mark_leader(lease.mode, acquired_at=lease.acquired_at)
            if previous_status != "leader":
                logger.info(
                    "retention worker acquired leadership",
                    extra={"lease_mode": lease.mode},
                )

            current_time = datetime.now(UTC)
            if current_time < next_run_at:
                await sleep_fn(poll_interval_s)
                continue

            try:
                with session_factory() as session:
                    run_fn(session, current_time)
                state.mark_success()
            except Exception as exc:
                state.mark_failure(exc)
                logger.exception("retention worker run failed")
            finally:
                next_run_at = datetime.now(UTC) + timedelta(seconds=retention_interval_s)

            await sleep_fn(poll_interval_s)
    finally:
        if state.leadership_status == "leader":
            logger.info(
                "retention worker released leadership",
                extra={"lease_mode": lease.mode},
            )
        lease.release()
