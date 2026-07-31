from __future__ import annotations

import asyncio
import logging
import zlib
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.notification_service import drain_notification_outbox
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.workers.leader import (
    WorkerLease,
    WorkerLeaseError,
    create_worker_lease,
)

logger = logging.getLogger("onestep_control_plane_api.workers.notification_outbox")

NOTIFICATION_OUTBOX_WORKER_NAME = "notification_outbox_worker"
NOTIFICATION_OUTBOX_WORKER_LOCK_KEY = zlib.crc32(
    b"onestep-control-plane.notification-outbox-worker"
)

SessionFactory = Callable[[], Any]
SleepFn = Callable[[float], Awaitable[None]]
# Drains one batch of pending outbox rows inside the given session. The worker
# runs this in a worker thread (asyncio.to_thread) so the blocking HTTP POSTs
# never stall the event loop.
DrainFn = Callable[[Session], int]
LeaseFactory = Callable[[], WorkerLease]


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
        lock_key=NOTIFICATION_OUTBOX_WORKER_LOCK_KEY,
        worker_name=NOTIFICATION_OUTBOX_WORKER_NAME,
    )


async def run_notification_outbox_worker(
    app: FastAPI,
    *,
    sleep_fn: SleepFn = asyncio.sleep,
    drain_fn: DrainFn | None = None,
    lease_factory: LeaseFactory | None = None,
    drain_interval_s: float | None = None,
    leader_poll_interval_s: float | None = None,
) -> None:
    """Drain the notification outbox off the event loop, leader-gated.

    Only the leader replica drains (PostgreSQL advisory lock / local mode). On
    each tick the leader claims a batch of due outbox rows and performs the
    webhook POSTs inside ``asyncio.to_thread`` so a slow or hung downstream can
    never block telemetry ingestion, commands, or the API.
    """
    session_factory = getattr(app.state, "session_factory")
    state = app.state.background_task_states[NOTIFICATION_OUTBOX_WORKER_NAME]
    drain = drain_fn if drain_fn is not None else drain_notification_outbox
    interval_s = float(
        settings.notification_outbox_drain_interval_s
        if drain_interval_s is None
        else drain_interval_s
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

    state.mark_started()
    state.mark_starting(lease.mode)

    try:
        while True:
            state.mark_tick()
            previous_status = state.leadership_status
            try:
                is_leader = lease.ensure_leader()
            except WorkerLeaseError as exc:
                state.mark_lease_failure(lease.mode, exc)
                logger.exception(
                    "notification outbox worker lease check failed",
                    extra={"lease_mode": lease.mode},
                )
                await sleep_fn(poll_interval_s)
                continue

            if not is_leader:
                state.mark_standby(lease.mode)
                if previous_status != "standby":
                    logger.info(
                        "notification outbox worker standing by for leadership",
                        extra={"lease_mode": lease.mode},
                    )
                await sleep_fn(poll_interval_s)
                continue

            state.mark_leader(lease.mode, acquired_at=lease.acquired_at)
            if previous_status != "leader":
                logger.info(
                    "notification outbox worker acquired leadership",
                    extra={"lease_mode": lease.mode},
                )

            try:
                # Drain off the event loop: the blocking webhook POSTs run in a
                # worker thread, never stalling telemetry/command traffic.
                delivered = await asyncio.to_thread(_drain_in_session, session_factory, drain)
                state.mark_success()
                if delivered:
                    logger.info(
                        "notification outbox worker drained batch",
                        extra={"delivered_count": delivered},
                    )
            except Exception as exc:
                state.mark_failure(exc)
                logger.exception("notification outbox worker drain failed")

            await sleep_fn(interval_s)
    finally:
        if state.leadership_status == "leader":
            logger.info(
                "notification outbox worker released leadership",
                extra={"lease_mode": lease.mode},
            )
        lease.release()


def _drain_in_session(session_factory: SessionFactory, drain_fn: DrainFn) -> int:
    """Open a fresh session and drain one batch; intended for ``to_thread``."""
    with session_factory() as session:
        return drain_fn(session)
