"""Process-local observability primitives for control-plane latency diagnostics.

This module answers one operational question set for issues #192-#197:

* is the asyncio event loop blocked?
* is a database checkout waiting on the connection pool, or is the database itself slow?
* how long does a background scan take, and is it getting slower?

It is deliberately *not* a monitoring platform: it is a small, dependency-free
instrumentation surface that the existing Prometheus exporter
(:mod:`onestep_control_plane_api.api.routers.prometheus`) and the existing
structured ``extra={...}`` logging style can both consume.

Measurements, units, sampling strategy and overhead
---------------------------------------------------

**event-loop lag** -- unit: **seconds**.

* Sampling strategy: one asyncio task wakes every
  ``DEFAULT_EVENT_LOOP_LAG_INTERVAL_S`` (0.25 s) and compares the actual wake-up
  time with the time it was scheduled for. The last
  ``DEFAULT_EVENT_LOOP_LAG_WINDOW_SIZE`` (240) samples are kept in a rolling
  window, which is 60 s of history at 4 samples/s.
* Per-measurement overhead: one timer wake-up per interval (4/s), an O(1) append
  to a bounded deque, and one lock acquisition. Percentiles are computed only
  when a scrape reads the snapshot (O(n log n) over at most 240 floats).

**pool checkout wait** -- unit: **seconds**.

* Sampling strategy: every ``Pool.connect`` call is wrapped by
  :func:`instrument_engine`; the elapsed wall time -- including opening a brand
  new DBAPI connection and pool pre-ping -- is recorded in a fixed-bucket
  histogram. Nothing is sampled in the background.
* Per-measurement overhead: two ``perf_counter()`` calls, one lock acquisition
  and one O(1) bucket increment (~1-2 us) per checkout.

**pool occupancy** -- unit: **count** (connections).

* Sampling strategy: sampled on demand at scrape time by
  ``refresh_pool_occupancy`` from ``checkedout()/checkedin()/overflow()/size()``,
  so the gauge agrees with the pool at read time. Documented out-of-band interval
  for callers that want a sampler: ``DEFAULT_POOL_OCCUPANCY_INTERVAL_S`` (1 s).
* Per-measurement overhead: four attribute reads per pool per scrape.

**scan duration** -- unit: **seconds**.

* Sampling strategy: an explicit context manager around one scan body (sync
  :func:`scan_duration_timer` or async :func:`ascan_duration_timer`), recorded in
  a fixed-bucket histogram per scan name.
* Per-measurement overhead: two ``perf_counter()`` calls, one lock acquisition
  and one O(1) bucket increment per scan run.

All sampling configuration lives in module-level constants below (see
``DEFAULT_*``). Issue #197 must land independently of #192/#193 and must not
collide with ``core/settings.py`` (owned by another task in the same batch), so
this module intentionally adds no settings keys. Every entry point accepts an
explicit override argument, which is what tests use.

Cardinality and privacy contract
--------------------------------

* ``instance_id`` / ``session_id`` are accepted for **log correlation only**
  (:func:`log_ws_lifecycle`, :func:`emit_structured_log`). They are never turned
  into Prometheus labels, so a reconnect storm cannot create unbounded series.
* Metric labels are bounded by construction: pool labels are a pool class name
  plus a sanitized pool name, scan labels come from a registered allowlist
  (:data:`KNOWN_SCAN_NAMES`) and fall back to ``"other"``.
* Credentials are scrubbed before logging, with one rule that matters more than
  the rest: a value whose **field name** matches
  :data:`SENSITIVE_FIELD_PATTERN` is dropped no matter what it contains, at any
  nesting depth within :data:`MAX_SANITIZE_DEPTH` levels -- and once that depth
  budget is spent the whole remaining subtree is replaced by :data:`REDACTED`
  rather than stringified, so a deep structure cannot smuggle a secret out
  through the ``str(value)`` fallback. Strings are additionally scrubbed for
  ``Bearer <token>`` / ``token=<value>`` *shapes*.
  Known limitation the scrubber does NOT cover: a bare secret under a
  **non**-sensitive field name (e.g. ``{"note": "hunter2"}``) has neither a
  sensitive name nor a credential shape and therefore passes through, as does a
  DSN outside the sensitive names. See the runbook's blind-spot list.

Seams for the #192/#193 wiring (no imports of those modules today)
------------------------------------------------------------------

Nothing here imports ``api.routers.agent_ws``, ``workers.notification_scanner``,
``db.session`` or ``core.settings``, so this baseline imports cleanly on
``main`` and cannot introduce a cycle. The follow-up wiring PR can call:

* :func:`instrument_engine` from the engine factory (sync engine today, adapted
  async engine later) or :func:`record_pool_wait` for pools that cannot be
  wrapped;
* :func:`ensure_event_loop_lag_sampler_started` from the app lifespan;
* :func:`scan_duration_timer` / :func:`ascan_duration_timer` around the scan
  body in the notification scanner (sync or async);
* :func:`log_ws_lifecycle` for websocket open/close/reject events with real
  close codes when available and honest ``"unknown"`` markers otherwise;
* :func:`collect_prometheus_snapshot` from any exporter.

Observational blind spots are documented in
``apps/control-plane/docs/runbooks/control-plane-latency-diagnostics.md``.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import threading
import time
from bisect import bisect_right
from collections import deque
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

__all__ = [
    "DEFAULT_ENGINE_NAME",
    "DEFAULT_EVENT_LOOP_LAG_INTERVAL_S",
    "DEFAULT_EVENT_LOOP_LAG_WINDOW_SIZE",
    "DEFAULT_POOL_OCCUPANCY_INTERVAL_S",
    "DEFAULT_POOL_WAIT_BUCKETS_S",
    "DEFAULT_POOL_WAIT_SLOW_THRESHOLD_S",
    "DEFAULT_SCAN_DURATION_BUCKETS_S",
    "DEFAULT_SCAN_SLOW_THRESHOLD_S",
    "KNOWN_SCAN_NAMES",
    "CounterSample",
    "EventLoopLagSampler",
    "EventLoopLagSnapshot",
    "GaugeSample",
    "HistogramSample",
    "ObservabilitySnapshot",
    "PoolOccupancySnapshot",
    "REDACTED",
    "ScanTiming",
    "ascan_duration_timer",
    "collect_prometheus_snapshot",
    "emit_structured_log",
    "ensure_engine_instrumented",
    "ensure_event_loop_lag_sampler_started",
    "get_event_loop_lag_sampler",
    "instrument_engine",
    "log_ws_lifecycle",
    "normalize_scan_name",
    "record_pool_wait",
    "refresh_pool_occupancy",
    "register_scan_name",
    "reset_observability_state",
    "scan_duration_timer",
    "stop_event_loop_lag_sampler",
]

logger = logging.getLogger("onestep_control_plane_api.observability")

# --------------------------------------------------------------------------------------
# Module-level sampling defaults. Units are in the name. Override per call where needed;
# issue #197 deliberately does not add core/settings.py keys.
# --------------------------------------------------------------------------------------

#: Event-loop lag sampler wake-up interval, in seconds (4 samples/second).
DEFAULT_EVENT_LOOP_LAG_INTERVAL_S = 0.25
#: Number of retained event-loop lag samples (60 s of history at the default interval).
DEFAULT_EVENT_LOOP_LAG_WINDOW_SIZE = 240
#: Upper bounds of the pool-checkout-wait histogram, in seconds.
DEFAULT_POOL_WAIT_BUCKETS_S: tuple[float, ...] = (
    0.0005,
    0.001,
    0.0025,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)
#: A checkout wait at or above this value (seconds) also emits one structured log line.
DEFAULT_POOL_WAIT_SLOW_THRESHOLD_S = 0.1
#: Documented occupancy sampling interval (seconds) for out-of-band samplers.
DEFAULT_POOL_OCCUPANCY_INTERVAL_S = 1.0
#: Upper bounds of the scan-duration histogram, in seconds.
DEFAULT_SCAN_DURATION_BUCKETS_S: tuple[float, ...] = (
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
)
#: A scan run at or above this value (seconds) also emits one structured log line.
DEFAULT_SCAN_SLOW_THRESHOLD_S = 1.0
#: Default pool label value used when a caller does not name the engine.
DEFAULT_ENGINE_NAME = "default"
#: Placeholder used instead of a sensitive or absent value.
REDACTED = "[redacted]"
#: Longest string value kept in a structured log record.
MAX_LOG_VALUE_CHARS = 256
#: Longest websocket close reason kept in a structured log record.
MAX_CLOSE_REASON_CHARS = 120
#: How many nesting levels :func:`_sanitize_log_value` walks before it stops
#: recursing and replaces the whole remaining subtree with :data:`REDACTED`.
#:
#: The budget exists because the sanitizer runs *before* ``logger.log(...)``, so
#: an exception raised here propagates to the caller instead of being swallowed
#: by logging's handler error path -- a crash there could surface inside
#: websocket disconnect cleanup. Two inputs would exceed Python's recursion
#: limit: a self-referential container and a pathologically deep one (both are
#: reachable, since ``json.loads`` parses arbitrarily deep input iteratively).
#: Walking until the budget is spent and then redacting wholesale satisfies both
#: properties at once: secrets under sensitive names are still caught inside the
#: budget, and nothing can raise.
MAX_SANITIZE_DEPTH = 12
#: Replacement label for scan names outside the bounded allowlist.
UNKNOWN_LABEL_VALUE = "other"

#: Scan names that may appear as the ``scan`` metric label. Extend with
#: :func:`register_scan_name` when a new scan is instrumented; anything else is
#: reported as ``"other"`` so label cardinality stays bounded.
KNOWN_SCAN_NAMES: frozenset[str] = frozenset(
    {
        "notification_missed_start",
        "notification_outbox",
        "retention",
    }
)

#: Websocket lifecycle events accepted by :func:`log_ws_lifecycle`.
WS_LIFECYCLE_EVENTS: frozenset[str] = frozenset(
    {"connected", "disconnected", "rejected", "error"}
)

#: Field names that must never reach a log record verbatim.
SENSITIVE_FIELD_PATTERN = re.compile(
    r"(token|secret|password|passwd|authorization|auth_header|cookie|credential"
    r"|api_key|apikey|private_key|dsn|database_url|body|payload|raw_message|message_body)",
    re.IGNORECASE,
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/=]{4,}")
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(token|password|passwd|secret|api_key|apikey|authorization)\b(\s*[:=]\s*)(\S+)"
)
_SAFE_POOL_NAME_PATTERN = re.compile(r"^[a-z0-9_]{1,32}$")
_SAFE_SCAN_NAME_PATTERN = re.compile(r"^[a-z0-9_]{1,64}$")

_LOCK = threading.Lock()


def utcnow() -> datetime:
    """Return the current time as an aware UTC datetime (log correlation timestamps)."""

    return datetime.now(UTC)


# --------------------------------------------------------------------------------------
# Structured logging helpers (identity lives here, never in metric labels)
# --------------------------------------------------------------------------------------

_RESERVED_LOG_KEYS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)

#: Keys this module always owns in a payload; caller fields cannot overwrite them.
_RESERVED_PAYLOAD_KEYS = frozenset({"event", "logged_at"})
#: Keys whose value the module derives itself rather than accepting from a
#: caller, so they cannot be forged. The ``close_*`` markers are the module's
#: honesty contract: :func:`log_ws_lifecycle` decides them from the real close
#: code/reason, so a caller must not be able to pass
#: ``close_reason_known=True`` next to ``close_reason="unknown"`` and make a
#: missing reason look like a confirmed one. Extend this set -- not the caller's
#: discretion -- whenever a new derived marker is added.
_OWNED_PAYLOAD_KEYS = _RESERVED_PAYLOAD_KEYS | frozenset(
    {
        "close_code",
        "close_code_known",
        "close_reason",
        "close_reason_known",
    }
)


def _scrub_text(value: str) -> str:
    """Remove credential-looking substrings and cap the length of a log value."""

    scrubbed = _BEARER_PATTERN.sub(f"Bearer {REDACTED}", value)
    scrubbed = _SECRET_ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", scrubbed
    )
    if len(scrubbed) > MAX_LOG_VALUE_CHARS:
        return scrubbed[:MAX_LOG_VALUE_CHARS] + "[truncated]"
    return scrubbed


def _sanitize_log_value(
    key: str,
    value: Any,
    *,
    depth: int = 0,
    _seen: frozenset[int] | None = None,
) -> Any:
    """Sanitize one log value, never raising and never leaking a named secret.

    A value whose key matches :data:`SENSITIVE_FIELD_PATTERN` is dropped
    wholesale regardless of what it holds. Containers are walked one level at a
    time against two guards:

    * a **depth budget** (:data:`MAX_SANITIZE_DEPTH`). Once it is spent the
      remaining subtree is replaced by :data:`REDACTED` instead of being
      stringified -- stringifying is what used to leak, because ``str()`` of a
      nested mapping prints the secret verbatim while :func:`_scrub_text` only
      recognises ``Bearer <t>`` / ``token=<v>`` *shapes*;
    * a **cycle guard** on ``id()`` of the containers on the current path, so a
      self-referential dict or list terminates instead of recursing forever.

    Both guards exist for caller safety, not tidiness: this runs before
    ``logger.log(...)``, so raising here would propagate into the caller.
    """
    if SENSITIVE_FIELD_PATTERN.search(key):
        return REDACTED
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return _scrub_text(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()

    is_mapping = isinstance(value, Mapping)
    is_sequence = isinstance(value, Sequence) and not isinstance(value, bytes | bytearray)
    if not (is_mapping or is_sequence):
        # Not a container we walk; the string form is all there is, scrubbed.
        return _scrub_text(str(value))

    # Depth budget: past it, give up on describing the value and redact the
    # whole subtree rather than risk printing a secret via repr/str.
    if depth >= MAX_SANITIZE_DEPTH:
        return REDACTED

    seen = _seen or frozenset()
    if id(value) in seen:
        # Reference cycle: redact rather than recurse into it again.
        return REDACTED
    seen = seen | {id(value)}

    if is_mapping:
        return {
            str(nested_key): _sanitize_log_value(
                str(nested_key), nested_value, depth=depth + 1, _seen=seen
            )
            for nested_key, nested_value in value.items()
        }
    return [
        _sanitize_log_value(key, item, depth=depth + 1, _seen=seen)
        for item in value  # type: ignore[union-attr]
    ]


def _safe_log_key(key: str) -> str:
    candidate = re.sub(r"[^0-9a-zA-Z_]", "_", key)
    if not candidate or candidate[0].isdigit():
        candidate = f"field_{candidate}"
    if candidate in _RESERVED_LOG_KEYS or candidate in _RESERVED_PAYLOAD_KEYS:
        candidate = f"obs_{candidate}"
    return candidate


def build_log_fields(event: str, **fields: Any) -> dict[str, Any]:
    """Build the flat ``extra={...}`` payload for one structured log record.

    Every payload carries ``event`` and an ISO-8601 UTC ``logged_at`` timestamp so
    lines from different emitters can be correlated on one clock. A value whose
    key matches :data:`SENSITIVE_FIELD_PATTERN` is replaced by :data:`REDACTED`
    at any nesting depth within the :data:`MAX_SANITIZE_DEPTH` budget; past that
    budget the whole subtree is redacted rather than stringified. Note what this
    does NOT cover: a bare secret under a non-sensitive key name has neither a
    sensitive name nor a credential shape and passes through.
    """

    payload: dict[str, Any] = {
        "event": event,
        "logged_at": utcnow().isoformat(),
    }
    for key, value in fields.items():
        payload[_safe_log_key(key)] = _sanitize_log_value(key, value)
    return payload


def emit_structured_log(
    target_logger: logging.Logger,
    level: int,
    event: str,
    **fields: Any,
) -> dict[str, Any]:
    """Log one structured record and return the sanitized payload that was logged.

    Use this instead of a metric whenever the value is identity-scoped or
    unbounded (per-connection lifecycle, individual slow waits, failures).
    """

    payload = build_log_fields(event, **fields)
    target_logger.log(level, event, extra=payload)
    return payload


def log_ws_lifecycle(
    target_logger: logging.Logger,
    event: str,
    *,
    instance_id: Any = None,
    session_id: Any = None,
    close_code: int | None = None,
    close_reason: str | None = None,
    connection_duration_s: float | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Emit one websocket lifecycle record with identity kept in logs only.

    ``instance_id``/``session_id`` are correlation fields; they never become
    metric labels. Unknown close codes or reasons are recorded as ``"unknown"``
    together with ``close_code_known``/``close_reason_known`` flags, so a missing
    reason is visible instead of being silently reported as a clean close.
    """

    normalized_event = event if event in WS_LIFECYCLE_EVENTS else UNKNOWN_LABEL_VALUE
    reason_known = close_reason is not None
    reason = "unknown" if close_reason is None else str(close_reason)[:MAX_CLOSE_REASON_CHARS]
    payload_fields: dict[str, Any] = {
        "instance_id": instance_id,
        "session_id": session_id,
        "close_code": "unknown" if close_code is None else int(close_code),
        "close_code_known": close_code is not None,
        "close_reason": reason,
        "close_reason_known": reason_known,
        "connection_duration_s": (
            None if connection_duration_s is None else round(float(connection_duration_s), 6)
        ),
    }
    # The close fields are derived above and are not the caller's to set: the
    # markers state whether the code/reason was really known, so letting a
    # caller pass close_reason_known=True next to an unknown reason would report
    # a missing reason as a confirmed one. Drop any such caller key, then merge.
    for forged in _OWNED_PAYLOAD_KEYS.intersection(fields):
        del fields[forged]
    payload_fields.update(fields)
    return emit_structured_log(
        target_logger,
        logging.INFO,
        f"agent_ws_{normalized_event}",
        **payload_fields,
    )


# --------------------------------------------------------------------------------------
# Event-loop lag sampler
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EventLoopLagSnapshot:
    """Rolling-window view of event-loop lag samples (all durations in seconds)."""

    interval_s: float
    window_size: int
    sample_count: int
    latest_s: float | None
    p50_s: float | None
    p95_s: float | None
    max_s: float | None
    last_sample_at: datetime | None
    running: bool


class EventLoopLagSampler:
    """Measure how late the event loop is in running a periodic callback.

    The sampler owns exactly one sleeping task. Each wake-up compares the actual
    ``time.monotonic()`` value with the time the wake-up was scheduled for; the
    difference is the loop lag for that interval. A loop blocked by synchronous
    database or CPU work cannot run the wake-up on time, which is what shows up
    as a large sample.

    ``start`` must be called from the loop thread; :meth:`stop`, :meth:`snapshot`
    and :meth:`record_sample` are safe from any thread.
    """

    def __init__(
        self,
        *,
        interval_s: float = DEFAULT_EVENT_LOOP_LAG_INTERVAL_S,
        window_size: int = DEFAULT_EVENT_LOOP_LAG_WINDOW_SIZE,
    ) -> None:
        if interval_s <= 0:
            raise ValueError("interval_s must be > 0")
        if window_size <= 0:
            raise ValueError("window_size must be > 0")
        self._interval_s = float(interval_s)
        self._window_size = int(window_size)
        self._samples: deque[float] = deque(maxlen=self._window_size)
        self._latest_sample_s: float | None = None
        self._last_sample_at: datetime | None = None
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def interval_s(self) -> float:
        return self._interval_s

    @property
    def window_size(self) -> int:
        return self._window_size

    @property
    def running(self) -> bool:
        task = self._task
        return task is not None and not task.done()

    def record_sample(self, lag_s: float) -> None:
        """Record one lag sample in seconds (used by the sampler and by tests)."""

        with self._lock:
            self._samples.append(float(lag_s))
            self._latest_sample_s = float(lag_s)
            self._last_sample_at = utcnow()

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> bool:
        """Start sampling on ``loop`` (defaults to the running loop).

        Returns ``False`` when no loop is running, so callers may invoke this
        safely from sync code. Restarting on a different loop replaces the stale
        task instead of leaking it.
        """

        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return False
        if self.running and self._loop is loop:
            return True
        self.stop()
        self._loop = loop
        self._task = loop.create_task(self._run(), name="onestep-event-loop-lag-sampler")
        return True

    def stop(self) -> None:
        """Cancel the sampling task, if any. Safe to call repeatedly, from any thread.

        ``Task.cancel`` is only safe on the owning loop, so a call from another
        thread is handed over with ``loop.call_soon_threadsafe``. A loop that is
        already closed (process shutdown, test teardown) is tolerated.
        """

        task, loop = self._task, self._loop
        self._task = None
        self._loop = None
        if task is None or task.done():
            return
        try:
            running_loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if loop is not None and running_loop is not loop and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(task.cancel)
                return
            except RuntimeError:
                pass
        try:
            task.cancel()
        except RuntimeError:
            # The owning loop is already closed; the task dies with it.
            pass

    def reset(self) -> None:
        """Drop all recorded samples (keeps the sampler running if it was running)."""

        with self._lock:
            self._samples.clear()
            self._latest_sample_s = None
            self._last_sample_at = None

    def snapshot(self) -> EventLoopLagSnapshot:
        with self._lock:
            samples = sorted(self._samples)
            latest_sample_s = self._latest_sample_s
            last_sample_at = self._last_sample_at
        if not samples:
            return EventLoopLagSnapshot(
                interval_s=self._interval_s,
                window_size=self._window_size,
                sample_count=0,
                latest_s=None,
                p50_s=None,
                p95_s=None,
                max_s=None,
                last_sample_at=last_sample_at,
                running=self.running,
            )
        return EventLoopLagSnapshot(
            interval_s=self._interval_s,
            window_size=self._window_size,
            sample_count=len(samples),
            latest_s=latest_sample_s,
            p50_s=_nearest_rank_percentile(samples, 0.5),
            p95_s=_nearest_rank_percentile(samples, 0.95),
            max_s=samples[-1],
            last_sample_at=last_sample_at,
            running=self.running,
        )

    async def _run(self) -> None:
        while True:
            expected = time.monotonic() + self._interval_s
            await asyncio.sleep(self._interval_s)
            self.record_sample(time.monotonic() - expected)


def _nearest_rank_percentile(sorted_values: Sequence[float], quantile: float) -> float:
    """Nearest-rank percentile (no interpolation) over an ascending sequence."""

    index = max(0, min(len(sorted_values) - 1, math.ceil(quantile * len(sorted_values)) - 1))
    return sorted_values[index]


_default_sampler = EventLoopLagSampler()


def get_event_loop_lag_sampler() -> EventLoopLagSampler:
    """Return the process-wide sampler (never started implicitly on import)."""

    return _default_sampler


def ensure_event_loop_lag_sampler_started(
    loop: asyncio.AbstractEventLoop | None = None,
) -> bool:
    """Idempotently start the process-wide lag sampler; ``False`` outside a loop."""

    return _default_sampler.start(loop)


def stop_event_loop_lag_sampler() -> None:
    """Stop the process-wide lag sampler (tests, shutdown)."""

    _default_sampler.stop()


# --------------------------------------------------------------------------------------
# Connection-pool wait and occupancy
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PoolOccupancySnapshot:
    """Point-in-time connection-pool occupancy for one pool (counts, not durations)."""

    pool_class: str
    name: str
    checked_out: int
    checked_in: int
    overflow: int
    size: int
    sampled_at: datetime


class _Histogram:
    """Fixed-bucket histogram; callers must hold the module lock."""

    __slots__ = ("_buckets", "_counts", "_count", "_sum")

    def __init__(self, buckets: Sequence[float]) -> None:
        self._buckets = tuple(sorted(float(bucket) for bucket in buckets))
        self._counts = [0] * (len(self._buckets) + 1)
        self._sum = 0.0
        self._count = 0

    def observe(self, value: float) -> None:
        self._counts[bisect_right(self._buckets, float(value))] += 1
        self._sum += float(value)
        self._count += 1

    def snapshot(self) -> tuple[tuple[tuple[float, int], ...], float, int]:
        cumulative = 0
        pairs: list[tuple[float, int]] = []
        for index, bucket in enumerate(self._buckets):
            cumulative += self._counts[index]
            pairs.append((bucket, cumulative))
        cumulative += self._counts[-1]
        pairs.append((math.inf, cumulative))
        return tuple(pairs), self._sum, self._count


@dataclass
class _PoolWaitState:
    histogram: _Histogram
    checkouts: int = 0
    slow_waits: int = 0


@dataclass
class _PoolBinding:
    pool: Any
    pool_class: str
    name: str
    slow_threshold_s: float
    wrapped: bool = False
    occupancy: PoolOccupancySnapshot | None = None


_pool_bindings: dict[int, _PoolBinding] = {}
_pool_wait_states: dict[tuple[str, str], _PoolWaitState] = {}
_scan_states: dict[str, _ScanState] = {}


def _normalize_pool_name(name: str | None) -> str:
    candidate = (name or DEFAULT_ENGINE_NAME).strip().lower()
    return candidate if _SAFE_POOL_NAME_PATTERN.match(candidate) else "unnamed"


def _resolve_pool(bind: Any) -> Any | None:
    """Return a pool object for an Engine/Connection/pool, else ``None``."""

    if bind is None:
        return None
    pool = getattr(bind, "pool", None)
    if pool is None or not callable(getattr(pool, "connect", None)):
        engine = getattr(bind, "engine", None)
        pool = getattr(engine, "pool", None)
    if pool is None or not callable(getattr(pool, "connect", None)):
        return None
    return pool


def _pool_class_name(pool: Any) -> str:
    return type(pool).__name__


def instrument_engine(
    bind: Any,
    *,
    name: str = DEFAULT_ENGINE_NAME,
    slow_threshold_s: float = DEFAULT_POOL_WAIT_SLOW_THRESHOLD_S,
    wait_buckets_s: Sequence[float] = DEFAULT_POOL_WAIT_BUCKETS_S,
) -> bool:
    """Wrap ``pool.connect`` on ``bind`` to time every checkout.

    ``bind`` may be an Engine, a Connection or a pool. Returns ``True`` when the
    pool is instrumented (or was already), ``False`` when no pool could be
    resolved or the pool refuses attribute assignment.

    The measured duration covers everything ``pool.connect()`` does: waiting for
    a free slot, opening a brand new DBAPI connection and pool pre-ping. That
    conflation is deliberate (operators care about total checkout latency) and is
    called out as a blind spot in the diagnostic runbook.
    """

    pool = _resolve_pool(bind)
    if pool is None:
        return False
    key = id(pool)
    with _LOCK:
        existing = _pool_bindings.get(key)
        if existing is not None:
            return True
        binding = _PoolBinding(
            pool=pool,
            pool_class=_pool_class_name(pool),
            name=_normalize_pool_name(name),
            slow_threshold_s=float(slow_threshold_s),
        )
        original_connect = pool.connect

        def _timed_connect(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                return original_connect(*args, **kwargs)
            finally:
                record_pool_wait(
                    time.perf_counter() - started,
                    pool_class=binding.pool_class,
                    pool_name=binding.name,
                    slow_threshold_s=binding.slow_threshold_s,
                    wait_buckets_s=wait_buckets_s,
                )

        try:
            pool.connect = _timed_connect
        except (AttributeError, TypeError):
            return False
        binding.wrapped = True
        _pool_bindings[key] = binding
        _pool_wait_states.setdefault(
            (binding.pool_class, binding.name),
            _PoolWaitState(histogram=_Histogram(wait_buckets_s)),
        )
    return True


def ensure_engine_instrumented(bind: Any, *, name: str = DEFAULT_ENGINE_NAME) -> bool:
    """Idempotent alias of :func:`instrument_engine` for exporter call sites."""

    return instrument_engine(bind, name=name)


def record_pool_wait(
    duration_s: float,
    *,
    pool_class: str = "manual",
    pool_name: str = DEFAULT_ENGINE_NAME,
    slow_threshold_s: float = DEFAULT_POOL_WAIT_SLOW_THRESHOLD_S,
    wait_buckets_s: Sequence[float] = DEFAULT_POOL_WAIT_BUCKETS_S,
) -> None:
    """Record one checkout wait in seconds.

    This is the seam for call sites that cannot be wrapped (for example an
    adapted async pool): measure the wait yourself and hand the duration over.
    """

    normalized_class = re.sub(r"[^0-9a-zA-Z_]", "_", pool_class)[:48] or "manual"
    normalized_name = _normalize_pool_name(pool_name)
    key = (normalized_class, normalized_name)
    with _LOCK:
        state = _pool_wait_states.get(key)
        if state is None:
            state = _PoolWaitState(histogram=_Histogram(wait_buckets_s))
            _pool_wait_states[key] = state
        state.histogram.observe(duration_s)
        state.checkouts += 1
        slow = duration_s >= slow_threshold_s
        if slow:
            state.slow_waits += 1
    if slow:
        emit_structured_log(
            logger,
            logging.WARNING,
            "db_pool_wait_slow",
            pool_class=normalized_class,
            pool_name=normalized_name,
            wait_s=round(float(duration_s), 6),
            threshold_s=round(float(slow_threshold_s), 6),
        )


def refresh_pool_occupancy(
    bind: Any,
    *,
    name: str = DEFAULT_ENGINE_NAME,
) -> PoolOccupancySnapshot | None:
    """Sample checked-out/checked-in/overflow/size counts for ``bind``'s pool.

    Occupancy is pulled on demand (scrape time) instead of sampled in the
    background: the gauge then agrees with the pool state at read time and costs
    four attribute reads. Pools without the SQLAlchemy ``QueuePool`` accessors
    (for example ``StaticPool``) return ``None``; only wait timing is available
    for them.
    """

    pool = _resolve_pool(bind)
    if pool is None:
        return None
    accessors = {
        "checked_out": getattr(pool, "checkedout", None),
        "checked_in": getattr(pool, "checkedin", None),
        "overflow": getattr(pool, "overflow", None),
        "size": getattr(pool, "size", None),
    }
    if not all(callable(accessor) for accessor in accessors.values()):
        return None
    snapshot = PoolOccupancySnapshot(
        pool_class=_pool_class_name(pool),
        name=_normalize_pool_name(name),
        checked_out=int(accessors["checked_out"]()),
        checked_in=int(accessors["checked_in"]()),
        overflow=int(accessors["overflow"]()),
        size=int(accessors["size"]()),
        sampled_at=utcnow(),
    )
    key = id(pool)
    with _LOCK:
        binding = _pool_bindings.get(key)
        if binding is None:
            binding = _PoolBinding(
                pool=pool,
                pool_class=snapshot.pool_class,
                name=snapshot.name,
                slow_threshold_s=DEFAULT_POOL_WAIT_SLOW_THRESHOLD_S,
            )
            _pool_bindings[key] = binding
        binding.occupancy = snapshot
        _pool_wait_states.setdefault(
            (snapshot.pool_class, snapshot.name),
            _PoolWaitState(histogram=_Histogram(DEFAULT_POOL_WAIT_BUCKETS_S)),
        )
    return snapshot


# --------------------------------------------------------------------------------------
# Scan duration timer
# --------------------------------------------------------------------------------------


@dataclass
class ScanTiming:
    """Timing record handed to the body of a scan-duration timer (durations in seconds)."""

    name: str
    started_at: datetime
    finished_at: datetime | None = None
    duration_s: float | None = None
    outcome: str = "ok"


@dataclass
class _ScanState:
    histogram: _Histogram
    runs: int = 0
    failures: int = 0


@dataclass(frozen=True)
class _PoolWaitSnapshot:
    """Immutable copy of one pool's wait state, taken under the module lock."""

    histogram: tuple[tuple[tuple[float, int], ...], float, int]
    checkouts: int
    slow_waits: int


@dataclass(frozen=True)
class _ScanSnapshot:
    """Immutable copy of one scan's timing state, taken under the module lock."""

    histogram: tuple[tuple[tuple[float, int], ...], float, int]
    runs: int
    failures: int


def register_scan_name(name: str) -> str:
    """Add ``name`` to the bounded scan-name allowlist and return the label value."""

    candidate = (name or "").strip().lower()
    if not _SAFE_SCAN_NAME_PATTERN.match(candidate):
        return UNKNOWN_LABEL_VALUE
    global KNOWN_SCAN_NAMES
    KNOWN_SCAN_NAMES = KNOWN_SCAN_NAMES | {candidate}
    return candidate


def normalize_scan_name(name: str) -> str:
    """Map a scan name onto the bounded label set (unknown names become ``"other"``)."""

    candidate = (name or "").strip().lower()
    return candidate if candidate in KNOWN_SCAN_NAMES else UNKNOWN_LABEL_VALUE


def _record_scan_run(timing: ScanTiming, *, slow_threshold_s: float) -> None:
    duration_s = float(timing.duration_s or 0.0)
    with _LOCK:
        state = _scan_states.get(timing.name)
        if state is None:
            state = _ScanState(histogram=_Histogram(DEFAULT_SCAN_DURATION_BUCKETS_S))
            _scan_states[timing.name] = state
        state.histogram.observe(duration_s)
        state.runs += 1
        failed = timing.outcome != "ok"
        if failed:
            state.failures += 1
    if failed:
        emit_structured_log(
            logger,
            logging.WARNING,
            "scan_run_failed",
            scan=timing.name,
            duration_s=round(duration_s, 6),
            started_at=timing.started_at,
            finished_at=timing.finished_at,
            outcome=timing.outcome,
        )
    elif duration_s >= slow_threshold_s:
        emit_structured_log(
            logger,
            logging.WARNING,
            "scan_run_slow",
            scan=timing.name,
            duration_s=round(duration_s, 6),
            threshold_s=round(float(slow_threshold_s), 6),
            started_at=timing.started_at,
            finished_at=timing.finished_at,
            outcome=timing.outcome,
        )


@contextmanager
def scan_duration_timer(
    name: str,
    *,
    slow_threshold_s: float = DEFAULT_SCAN_SLOW_THRESHOLD_S,
) -> Iterator[ScanTiming]:
    """Time one synchronous scan body and record its duration in seconds.

    The scan label is bounded: unknown names collapse to ``"other"``. Exceptions
    are recorded as failures and re-raised unchanged.
    """

    timing = ScanTiming(name=normalize_scan_name(name), started_at=utcnow())
    started = time.perf_counter()
    try:
        yield timing
    except BaseException:
        timing.outcome = "error"
        raise
    finally:
        timing.duration_s = time.perf_counter() - started
        timing.finished_at = utcnow()
        _record_scan_run(timing, slow_threshold_s=slow_threshold_s)


@asynccontextmanager
async def ascan_duration_timer(
    name: str,
    *,
    slow_threshold_s: float = DEFAULT_SCAN_SLOW_THRESHOLD_S,
) -> AsyncIterator[ScanTiming]:
    """Async variant of :func:`scan_duration_timer` for the #193 async scan path."""

    timing = ScanTiming(name=normalize_scan_name(name), started_at=utcnow())
    started = time.perf_counter()
    try:
        yield timing
    except BaseException:
        timing.outcome = "error"
        raise
    finally:
        timing.duration_s = time.perf_counter() - started
        timing.finished_at = utcnow()
        _record_scan_run(timing, slow_threshold_s=slow_threshold_s)


# --------------------------------------------------------------------------------------
# Prometheus snapshot
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GaugeSample:
    name: str
    help_text: str
    value: float
    labels: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class CounterSample:
    name: str
    help_text: str
    value: float
    labels: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class HistogramSample:
    name: str
    help_text: str
    buckets: tuple[tuple[float, int], ...]
    total: float
    count: int
    labels: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ObservabilitySnapshot:
    """Everything this module wants to export, in Prometheus 0.0.4 terms."""

    gauges: tuple[GaugeSample, ...] = ()
    counters: tuple[CounterSample, ...] = ()
    histograms: tuple[HistogramSample, ...] = ()


def _lag_gauges(snapshot: EventLoopLagSnapshot) -> list[GaugeSample]:
    gauges = [
        GaugeSample(
            name="onestep_control_plane_event_loop_lag_sampler_running",
            help_text="1 when the event-loop lag sampler task is running, 0 otherwise.",
            value=1.0 if snapshot.running else 0.0,
        ),
        GaugeSample(
            name="onestep_control_plane_event_loop_lag_sample_interval_seconds",
            help_text="Wake-up interval of the event-loop lag sampler in seconds.",
            value=snapshot.interval_s,
        ),
        GaugeSample(
            name="onestep_control_plane_event_loop_lag_window_samples",
            help_text="Number of retained event-loop lag samples in the rolling window.",
            value=float(snapshot.sample_count),
        ),
    ]
    if snapshot.sample_count:
        gauges.extend(
            [
                GaugeSample(
                    name="onestep_control_plane_event_loop_lag_seconds",
                    help_text="Most recent event-loop lag sample in seconds.",
                    value=float(snapshot.latest_s or 0.0),
                ),
                GaugeSample(
                    name="onestep_control_plane_event_loop_lag_p95_seconds",
                    help_text=(
                        "Nearest-rank p95 event-loop lag over the rolling window, in seconds."
                    ),
                    value=float(snapshot.p95_s or 0.0),
                ),
                GaugeSample(
                    name="onestep_control_plane_event_loop_lag_max_seconds",
                    help_text="Maximum event-loop lag over the rolling window, in seconds.",
                    value=float(snapshot.max_s or 0.0),
                ),
            ]
        )
    return gauges


def collect_prometheus_snapshot() -> ObservabilitySnapshot:
    """Build the Prometheus view of all process-local observability state.

    Snapshotting never touches the database and never mutates the samplers, so it
    is safe to call from the exporter (including from a threadpool thread).
    """

    lag = _default_sampler.snapshot()
    with _LOCK:
        wait_states = {
            key: _PoolWaitSnapshot(
                histogram=state.histogram.snapshot(),
                checkouts=state.checkouts,
                slow_waits=state.slow_waits,
            )
            for key, state in _pool_wait_states.items()
        }
        occupancy = {
            (binding.pool_class, binding.name): binding.occupancy
            for binding in _pool_bindings.values()
            if binding.occupancy is not None
        }
        scan_states = {
            name: _ScanSnapshot(
                histogram=state.histogram.snapshot(),
                runs=state.runs,
                failures=state.failures,
            )
            for name, state in _scan_states.items()
        }

    gauges = _lag_gauges(lag)
    counters: list[CounterSample] = []
    histograms: list[HistogramSample] = []

    for (pool_class, pool_name), sample in sorted(occupancy.items()):
        if sample is None:  # pragma: no cover - defensive, filtered above
            continue
        labels = (("name", pool_name), ("pool", pool_class))
        gauges.extend(
            [
                GaugeSample(
                    name="onestep_control_plane_db_pool_checked_out",
                    help_text="Connections currently checked out of the pool.",
                    value=float(sample.checked_out),
                    labels=labels,
                ),
                GaugeSample(
                    name="onestep_control_plane_db_pool_checked_in",
                    help_text="Idle connections currently held in the pool.",
                    value=float(sample.checked_in),
                    labels=labels,
                ),
                GaugeSample(
                    name="onestep_control_plane_db_pool_overflow",
                    help_text="Connections currently opened above the configured pool size.",
                    value=float(sample.overflow),
                    labels=labels,
                ),
                GaugeSample(
                    name="onestep_control_plane_db_pool_size",
                    help_text="Configured pool size (maximum idle connections kept).",
                    value=float(sample.size),
                    labels=labels,
                ),
                GaugeSample(
                    name="onestep_control_plane_db_pool_occupancy_timestamp_seconds",
                    help_text="Unix timestamp of the last pool occupancy sample.",
                    value=sample.sampled_at.timestamp(),
                    labels=labels,
                ),
            ]
        )

    for (pool_class, pool_name), state in sorted(wait_states.items()):
        buckets, total, count = state.histogram
        labels = (("name", pool_name), ("pool", pool_class))
        histograms.append(
            HistogramSample(
                name="onestep_control_plane_db_pool_wait_seconds",
                help_text=(
                    "Wall time spent inside pool.connect() per checkout, in seconds "
                    "(includes slot wait, new DBAPI connection setup and pre-ping)."
                ),
                buckets=buckets,
                total=total,
                count=count,
                labels=labels,
            )
        )
        counters.append(
            CounterSample(
                name="onestep_control_plane_db_pool_checkouts_total",
                help_text="Instrumented pool checkouts observed by this process.",
                value=float(state.checkouts),
                labels=labels,
            )
        )
        counters.append(
            CounterSample(
                name="onestep_control_plane_db_pool_wait_slow_total",
                help_text=(
                    "Pool checkouts whose measured wait reached the slow threshold "
                    f"({DEFAULT_POOL_WAIT_SLOW_THRESHOLD_S}s by default)."
                ),
                value=float(state.slow_waits),
                labels=labels,
            )
        )

    for scan_name, state in sorted(scan_states.items()):
        buckets, total, count = state.histogram
        labels = (("scan", scan_name),)
        histograms.append(
            HistogramSample(
                name="onestep_control_plane_scan_duration_seconds",
                help_text="Duration of one background scan run, in seconds.",
                buckets=buckets,
                total=total,
                count=count,
                labels=labels,
            )
        )
        counters.append(
            CounterSample(
                name="onestep_control_plane_scan_runs_total",
                help_text="Background scan runs observed by this process.",
                value=float(state.runs),
                labels=labels,
            )
        )
        counters.append(
            CounterSample(
                name="onestep_control_plane_scan_failures_total",
                help_text="Background scan runs that raised an exception.",
                value=float(state.failures),
                labels=labels,
            )
        )

    return ObservabilitySnapshot(
        gauges=tuple(gauges),
        counters=tuple(counters),
        histograms=tuple(histograms),
    )


def reset_observability_state() -> None:
    """Clear all recorded state and stop samplers (tests, process shutdown)."""

    _default_sampler.stop()
    with _LOCK:
        for binding in _pool_bindings.values():
            if binding.wrapped:
                try:
                    binding.pool.__dict__.pop("connect", None)
                except AttributeError:  # pragma: no cover - defensive
                    pass
        _pool_bindings.clear()
        _pool_wait_states.clear()
        _scan_states.clear()
