"""Presence beacon: keep an instance "seen" while its event loop is blocked.

Why this exists
---------------
The reporter's heartbeat loop and every task handler share one asyncio event
loop. A handler that makes a blocking call (``requests``, ``boto3``, ``lark``,
``pandas``, ``time.sleep``) therefore starves the heartbeat loop: no heartbeat
reaches the control plane while that call runs, ``last_seen_at`` goes stale, and
an instance that is alive and working is reported as ``offline``. When the
block ends the heartbeat resumes and an ``online`` notification follows, so the
operator sees the instance flapping.

The beacon is the fix that needs no change in the worker application. It runs
on its own daemon thread, and the loop thread only ever *touches* a monotonic
timestamp. The beacon sends a request **only** when that timestamp is older
than the grace window -- i.e. only when the loop is demonstrably stalled. A
healthy process therefore sends nothing at all, which is what keeps this
invisible to every existing deployment and test.

What it does not cover
----------------------
The beacon is not a second heartbeat. It carries no health, no task controls
and no sequence number, and the control plane uses it to advance
``last_seen_at`` only. A process whose WebSocket telemetry is broken while its
loop is healthy keeps touching the beacon's timestamp and stays silent, so the
control plane still reports it ``offline``: loss of reporting ability is not
hidden by a liveness probe.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import UUID

#: Path the control plane exposes for the beacon. Deliberately NOT part of the
#: agent WebSocket protocol: the whole point is to reach the control plane
#: without the event loop that owns that socket.
PRESENCE_PATH = "/api/v1/agents/presence"

#: Statuses that mean "this control plane will never accept a beacon from us".
#: Every one of them is deterministic given the frozen token and the frozen
#: service descriptor: a bad token, an older control plane with no presence
#: route, the wrong method, a body the schema rejects, an ``instance_id`` bound
#: to another service/environment, or an unimplemented route. Retrying is pure
#: noise, so the beacon logs once and stops.
#:
#: Anything else >= 400 -- notably 429 and 5xx -- IS retried with backoff: those
#: are the "come back later" answers. Giving up is safe either way, because the
#: WebSocket heartbeat keeps running untouched; the beacon is an addition, not a
#: replacement.
_FATAL_STATUS_CODES = frozenset({401, 403, 404, 405, 409, 422, 501})

#: Lower bound on a sleep, so a degenerate grace window cannot spin the thread.
#: Only reachable with sub-100 ms windows, which no real deployment configures:
#: the smallest sane interval is the heartbeat interval, measured in seconds.
#: Below that floor the sleep may exceed a half-grace, which is why the floor
#: is documented rather than silently assumed away.
_MIN_DELAY_S = 0.05

#: Ceiling on the retry backoff after repeated transport failures.
_MAX_BACKOFF_S = 300.0

PresenceHttpPost = Callable[[str, str, dict[str, Any], float], int]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _default_http_post(
    url: str,
    token: str,
    payload: dict[str, Any],
    timeout_s: float,
) -> int:
    """POST one presence frame and return its HTTP status.

    Transport failures (DNS, refused connection, timeout) propagate as
    exceptions so the caller can back off; an HTTP error response is a *result*,
    not a transport failure, so it is returned as a status code.
    """

    body = json.dumps(payload, separators=(",", ":"), default=_json_default).encode("utf-8")
    request = Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=timeout_s) as response:
            status = getattr(response, "status", None)
            return int(status) if status is not None else 200
    except HTTPError as exc:
        return int(exc.code)


class PresenceBeacon:
    """Daemon-thread liveness beacon for one instance identity.

    The beacon holds a frozen copy of the service descriptor: it never reads
    reporter or app state, so it cannot race the loop thread's own bookkeeping
    (metrics buckets, pending event buffers, sequence counters).
    """

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        service_descriptor: dict[str, Any],
        interval_s: float,
        grace_s: float,
        timeout_s: float = 5.0,
        logger: logging.Logger | None = None,
        http_post: PresenceHttpPost | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._logger = logger or logging.getLogger("onestep.control_plane.presence")
        # Imported here, not at module scope: ``ws`` imports ``reporter``, which
        # imports this module, so a top-level import would be a cycle.
        from .ws import build_control_plane_http_base_url

        self._url = f"{build_control_plane_http_base_url(base_url)}{PRESENCE_PATH}"
        self._token = token
        self._service = dict(service_descriptor)
        self._interval_s = float(interval_s)
        self._grace_s = float(grace_s)
        # The beacon sleeps at most half a grace, and it must be impossible for
        # that sleep to complete on a loop that is merely keeping to its
        # interval. Enforced here as well as in the reporter config so no
        # construction path can build a beacon that reports on a healthy
        # process -- a beacon that lies is worse than no beacon.
        if self._grace_s < 2 * self._interval_s:
            raise ValueError(
                "grace_s must be >= 2 * interval_s "
                f"(got grace_s={self._grace_s:g}, interval_s={self._interval_s:g})"
            )
        self._timeout_s = float(timeout_s)
        self._http_post = http_post or _default_http_post
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._last_tick = self._clock()
        self._stop_event = threading.Event()
        #: Woken by ``touch`` so the beacon re-times itself the instant the loop
        #: proves it is alive. Without this the beacon's own sleep and the
        #: heartbeat loop drift independently, and a healthy process whose grace
        #: is close to its heartbeat interval reports liveness whenever the
        #: beacon's wake-up happens to land just before a late touch.
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._disabled_reason: str | None = None
        self._consecutive_failures = 0
        self._sent_count = 0

    # ----- introspection (used by tests and by the reporter's own logging) ---

    @property
    def url(self) -> str:
        return self._url

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def disabled_reason(self) -> str | None:
        return self._disabled_reason

    @property
    def sent_count(self) -> int:
        return self._sent_count

    # ----- lifecycle ---------------------------------------------------------

    def touch(self) -> None:
        """Record that the event loop is alive.

        Called from the loop thread on every heartbeat. Cheap by construction:
        one lock acquisition and one float assignment per heartbeat interval,
        plus waking a thread that is already asleep and about to sleep again.
        """

        with self._lock:
            self._last_tick = self._clock()
        self._wake.set()

    def start(self) -> None:
        if self.running:
            self.touch()
            return
        self._stop_event = threading.Event()
        self._wake = threading.Event()
        self._disabled_reason = None
        self._consecutive_failures = 0
        with self._lock:
            self._last_tick = self._clock()
        thread = threading.Thread(
            target=self._run,
            name="onestep-control-plane-presence",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def stop(self, *, timeout_s: float = 2.0) -> None:
        self._stop_event.set()
        # Wake the thread out of its sleep so shutdown does not wait a whole
        # interval for it to notice.
        self._wake.set()
        thread = self._thread
        self._thread = None
        if thread is None:
            return
        if thread is threading.current_thread():
            return
        thread.join(timeout=timeout_s)
        if thread.is_alive():
            # Daemon thread: a request still in flight cannot hold the process
            # open. Reported at debug because shutdown must not be noisy.
            self._logger.debug(
                "presence beacon thread did not stop within %.1fs", timeout_s
            )

    # ----- internals ---------------------------------------------------------

    def _idle_and_last_tick(self) -> tuple[float, float]:
        """Read the clock and the last tick together.

        Both come from one critical section so a delay computed from them
        cannot mix a pre-touch clock with a post-touch timestamp.
        """

        with self._lock:
            now = self._clock()
            return now - self._last_tick, self._last_tick

    def _seconds_since_last_tick(self) -> float:
        return self._idle_and_last_tick()[0]

    def _next_delay_s(self) -> float:
        """How long to sleep before re-deciding.

        The beacon sleeps at most half a grace window, so a healthy loop
        (whose touch period is at most half the grace, enforced at config time)
        always interrupts the sleep before it completes. That is what makes
        "a healthy process sends nothing" a property of the construction rather
        than of the scheduler being punctual.

        Once the loop is stalled it polls on the configured interval, backing
        off exponentially while the control plane keeps failing.
        """

        idle_s = self._seconds_since_last_tick()
        if idle_s < self._grace_s:
            return max(min(self._interval_s, self._grace_s / 2), _MIN_DELAY_S)
        if self._consecutive_failures == 0:
            return self._interval_s
        backoff = self._interval_s * (2 ** self._consecutive_failures)
        return max(min(backoff, _MAX_BACKOFF_S), _MIN_DELAY_S)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            # Sleep until either the loop checks in (``touch``), we are asked to
            # stop, or the delay elapses -- whichever comes first.
            interrupted = self._wake.wait(self._next_delay_s())
            self._wake.clear()
            if self._stop_event.is_set():
                return
            if self._disabled_reason is not None:
                return
            if interrupted:
                # The loop checked in. Nothing to report, and the next sleep is
                # re-timed from the fresh timestamp.
                continue
            if self._seconds_since_last_tick() < self._grace_s:
                continue
            self._send_once()
            if self._disabled_reason is not None:
                return

    def _send_once(self) -> None:
        payload = {"service": self._service, "sent_at": _utcnow_iso()}
        try:
            status = int(self._http_post(self._url, self._token, payload, self._timeout_s))
        except Exception as exc:  # never let the beacon break the app
            self._consecutive_failures += 1
            if self._consecutive_failures == 1:
                self._logger.warning(
                    "presence beacon request failed; will retry with backoff: %s", exc
                )
            else:
                self._logger.debug(
                    "presence beacon request still failing (%d in a row): %s",
                    self._consecutive_failures,
                    exc,
                )
            return

        if status in _FATAL_STATUS_CODES:
            self._disabled_reason = f"HTTP {status}"
            self._logger.warning(
                "presence beacon disabled after HTTP %d; the control plane will not "
                "accept it, so instance liveness falls back to the heartbeat alone",
                status,
            )
            return

        if status >= 400:
            self._consecutive_failures += 1
            if self._consecutive_failures == 1:
                self._logger.warning(
                    "presence beacon rejected with HTTP %d; will retry with backoff", status
                )
            else:
                self._logger.debug(
                    "presence beacon still rejected with HTTP %d (%d in a row)",
                    status,
                    self._consecutive_failures,
                )
            return

        if self._consecutive_failures:
            self._logger.info(
                "presence beacon recovered after %d failed attempts",
                self._consecutive_failures,
            )
        self._consecutive_failures = 0
        self._sent_count += 1


__all__ = ["PRESENCE_PATH", "PresenceBeacon", "PresenceHttpPost"]
