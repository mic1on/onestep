from __future__ import annotations

import logging
from asyncio import sleep
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from onestep_control_plane_api.api.notification_service import (
    scan_and_dispatch_missed_start_notifications,
)
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.ops.readiness import BackgroundTaskReadinessState
from onestep_control_plane_api.workers.leader import PostgresAdvisoryLeader

SessionFactory = Callable[[], Session]
_LOGGER = logging.getLogger("onestep_control_plane_api.workers.notification_scanner")

_RENEW_INTERVAL_MULTIPLIER = 2
_ACQUIRE_RETRY_DELAY_S = 5


class NotificationMissedStartScanner:
    """Background worker that scans for missed scheduled task starts.

    Uses leader election to ensure only one replica runs the scan
    across multi-replica deployments.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        leader: PostgresAdvisoryLeader,
        readiness_state: BackgroundTaskReadinessState,
        *,
        started_at: datetime | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._leader = leader
        self._state = readiness_state
        self._started_at = started_at or datetime.now(UTC)
        self._scan_interval_s = settings.notification_missed_start_scan_interval_s
        self._lock_ttl_s = max(self._scan_interval_s * _RENEW_INTERVAL_MULTIPLIER, 30)
        self._shutdown_requested = False

    @property
    def readiness_state(self) -> BackgroundTaskReadinessState:
        return self._state

    def request_shutdown(self) -> None:
        self._shutdown_requested = True

    async def run(self) -> None:
        self._state.mark_started(self._started_at)
        _LOGGER.info(
            "notification missed-start scanner started",
            extra={
                "scan_interval_s": self._scan_interval_s,
                "lock_ttl_s": self._lock_ttl_s,
            },
        )

        await sleep(self._scan_interval_s)

        while not self._shutdown_requested:
            self._state.mark_tick()
            try:
                if self._leader.state.is_leader:
                    if not self._leader.renew(ttl_s=self._lock_ttl_s):
                        _LOGGER.warning("leader renew failed, re-acquiring")
                        self._try_acquire_leadership()

                if not self._leader.state.is_leader:
                    self._try_acquire_leadership()

                if self._leader.state.is_leader:
                    await self._run_scan_once()
                else:
                    _LOGGER.debug("not the leader, skipping scan cycle")
            except Exception as exc:
                self._state.mark_failure(exc)
                _LOGGER.exception("notification missed-start scan cycle failed")
            await sleep(self._scan_interval_s)

        self._leader.release()
        _LOGGER.info("notification missed-start scanner stopped")

    def _try_acquire_leadership(self) -> None:
        acquired = self._leader.try_acquire(ttl_s=self._lock_ttl_s)
        if acquired:
            _LOGGER.info("became leader for notification missed-start scanner")

    async def _run_scan_once(self) -> None:
        try:
            with self._session_factory() as session:
                dispatched = scan_and_dispatch_missed_start_notifications(
                    session,
                    min_last_seen_at=self._started_at,
                )
            self._state.mark_success()
            if dispatched > 0:
                _LOGGER.info(
                    "dispatched missed-start notifications",
                    extra={"dispatched_count": dispatched},
                )
        except Exception as exc:
            self._state.mark_failure(exc)
            _LOGGER.exception("notification missed-start scan failed")