"""Prometheus exposition for onestep runtime metrics.

The exporter consumes the runtime :class:`TaskEvent` stream plus a
non-destructive snapshot of :class:`CustomMetricsRegistry` and formats the
Prometheus text exposition protocol directly — no third-party dependency is
required.

Design notes:

- Prometheus counters must be monotonic. The control-plane reporter relies on
  ``CustomMetricsRegistry.rotate_task()`` which resets per-task counters to
  report per-window deltas, so the exporter never reads the live per-window
  buffers; it reads :meth:`CustomMetricsRegistry.snapshot` which keeps
  cumulative totals across rotations.
- Inflight accounting follows the executor's attempt model: exactly one
  ``STARTED`` per attempt, closed by exactly one of ``SUCCEEDED``, ``FAILED``,
  ``CANCELLED`` or ``RETRIED``. The ``CANCELLED -> RETRIED`` pair (cancel with
  retry re-queue) closes the attempt once: the guard on the previous event for
  the series keeps the gauge from double-decrementing, and every decrement is
  clamped at zero.
- ``DEAD_LETTERED`` is always followed by ``FAILED`` in the failure path, so it
  gets its own counter but does not touch ``processed_total`` or inflight —
  that terminal outcome is counted exactly once, by ``FAILED``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import time
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version
from typing import Any, Callable, Sequence

from .app import OneStepApp
from .events import TaskEvent, TaskEventKind
from .metrics import CustomMetricsRegistry

__all__ = ["MetricsHandle", "MetricsServer", "PrometheusExporter", "install_metrics"]

_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.075,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)

# Metric family names owned by the runtime exporter. Custom task metrics whose
# normalized name collides with one of these are skipped during render so the
# exposition never declares the same family twice.
_RESERVED_FAMILIES = frozenset(
    {
        "onestep_tasks_processed_total",
        "onestep_task_duration_seconds",
        "onestep_task_duration_seconds_bucket",
        "onestep_task_duration_seconds_count",
        "onestep_task_duration_seconds_sum",
        "onestep_inflight_tasks",
        "onestep_tasks_retried_total",
        "onestep_tasks_dead_lettered_total",
        "onestep_tasks_cancelled_total",
        "onestep_deliveries_fetched_total",
        "onestep_task_failures_total",
        "onestep_build_info",
    }
)


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_value(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return repr(float(value))


def _format_bucket(bound: float) -> str:
    return "+Inf" if bound == float("inf") else f"{bound:g}"


def _label_block(pairs: Sequence[tuple[str, str]]) -> str:
    inner = ",".join(f'{key}="{_escape_label_value(value)}"' for key, value in pairs)
    return "{" + inner + "}"


def _resolve_version() -> str:
    try:
        return _distribution_version("onestep")
    except PackageNotFoundError:  # pragma: no cover - source checkout fallback
        return "dev"


class _Histogram:
    """Per-series histogram accumulator with cumulative-bucket rendering."""

    __slots__ = ("count", "sum", "bucket_counts")

    def __init__(self) -> None:
        self.count = 0.0
        self.sum = 0.0
        self.bucket_counts = [0.0] * len(_BUCKETS)

    def observe(self, value: float) -> None:
        self.count += 1.0
        self.sum += value
        for index, bound in enumerate(_BUCKETS):
            if value <= bound:
                self.bucket_counts[index] += 1.0

    def freeze(self) -> tuple[float, float, list[float]]:
        return (self.count, self.sum, list(self.bucket_counts))

    @staticmethod
    def render(
        name: str,
        label_pairs: Sequence[tuple[str, str]],
        data: tuple[float, float, list[float]],
    ) -> list[str]:
        count, total, bucket_counts = data
        lines: list[str] = []
        cumulative = 0.0
        for bound, bucket_count in zip(_BUCKETS, bucket_counts):
            cumulative += bucket_count
            bucket_labels = (*label_pairs, ("le", _format_bucket(bound)))
            lines.append(f"{name}_bucket{_label_block(bucket_labels)} {_format_value(cumulative)}")
        inf_labels = (*label_pairs, ("le", "+Inf"))
        lines.append(f"{name}_bucket{_label_block(inf_labels)} {_format_value(count)}")
        lines.append(f"{name}_count{_label_block(label_pairs)} {_format_value(count)}")
        lines.append(f"{name}_sum{_label_block(label_pairs)} {_format_value(total)}")
        return lines


class PrometheusExporter:
    """TaskEvent consumer that renders a Prometheus exposition payload.

    The instance is callable so it can be registered on the app's event
    handlers (``app.event``) alongside the other event consumers.
    """

    def __init__(
        self,
        *,
        app_name: str,
        custom_metrics: CustomMetricsRegistry | None = None,
    ) -> None:
        self._app_name = app_name
        self._custom_metrics = custom_metrics
        self._lock = threading.Lock()
        # (app, task) -> inflight gauge value (clamped >= 0)
        self._inflight: dict[tuple[str, str], float] = {}
        # (app, task, suffix) -> counter,
        # suffix in {fetched, retried, dead_lettered, cancelled}
        self._counters: dict[tuple[str, str, str], float] = {}
        # (app, task, status) -> processed_total counter
        self._processed: dict[tuple[str, str, str], float] = {}
        # (app, task, failure_kind) -> counter
        self._failures: dict[tuple[str, str, str], float] = {}
        # (app, task) -> duration histogram accumulator
        self._histograms: dict[tuple[str, str], _Histogram] = {}
        # Attempt identity is carried in events. This avoids using a task-wide
        # "last event" guard when attempts run concurrently. The set contains
        # attempts which emitted STARTED and remain open.
        self._open_attempts: set[tuple[str, str, str]] = set()


    def __call__(self, event: TaskEvent) -> None:
        with self._lock:
            self._record(event)

    def render(self) -> str:
        # Read a consistent copy of the mutable state under the lock, then
        # render outside it.
        with self._lock:
            inflight = dict(self._inflight)
            counters = dict(self._counters)
            processed = dict(self._processed)
            failures = dict(self._failures)
            histograms = {
                series: histogram.freeze()
                for series, histogram in self._histograms.items()
            }
        custom_samples = (
            list(self._custom_metrics.snapshot())
            if self._custom_metrics is not None
            else []
        )

        lines: list[str] = []
        lines.extend(
            _counter_family(
                "onestep_deliveries_fetched_total",
                "Deliveries fetched from sources",
                counters,
                _series_order(counters),
                fixed_suffix="fetched",
            )
        )
        lines.extend(
            _counter_family(
                "onestep_tasks_processed_total",
                "Terminal task outcomes",
                processed,
                _series_order(processed),
                extra_label="status",
            )
        )
        lines.extend(
            _histogram_family(
                "onestep_task_duration_seconds",
                "Task attempt duration in seconds",
                histograms,
            )
        )
        lines.extend(
            _gauge_family(
                "onestep_inflight_tasks",
                "Task attempts currently in flight",
                inflight,
            )
        )
        lines.extend(
            _counter_family(
                "onestep_tasks_retried_total",
                "Task attempts scheduled for retry",
                counters,
                _series_order(counters),
                fixed_suffix="retried",
            )
        )
        lines.extend(
            _counter_family(
                "onestep_tasks_dead_lettered_total",
                "Deliveries published to dead-letter sinks",
                counters,
                _series_order(counters),
                fixed_suffix="dead_lettered",
            )
        )
        lines.extend(
            _counter_family(
                "onestep_tasks_cancelled_total",
                "Task attempts cancelled",
                counters,
                _series_order(counters),
                fixed_suffix="cancelled",
            )
        )
        lines.extend(
            _counter_family(
                "onestep_task_failures_total",
                "Task failures by failure kind",
                failures,
                _series_order(failures),
                extra_label="failure_kind",
            )
        )
        lines.append("# HELP onestep_build_info Build metadata")
        lines.append("# TYPE onestep_build_info gauge")
        lines.append(
            f'onestep_build_info{{version="{_escape_label_value(_resolve_version())}"}} 1'
        )
        lines.extend(_render_custom_families(custom_samples))
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # event accounting
    # ------------------------------------------------------------------

    def _record(self, event: TaskEvent) -> None:
        series = (event.app, event.task)
        kind = event.kind

        if kind is TaskEventKind.FETCHED:
            self._bump(self._counters, (*series, "fetched"))
        elif kind is TaskEventKind.STARTED:
            self._inflight[series] = self._inflight.get(series, 0.0) + 1.0
            attempt_id = self._attempt_id(event)
            if attempt_id is not None:
                self._open_attempts.add((*series, attempt_id))
        elif kind is TaskEventKind.SUCCEEDED:
            self._bump(self._processed, (*series, "succeeded"))
            self._observe_duration(series, event.duration_s)
            self._finish_attempt(event)
        elif kind is TaskEventKind.RETRIED:
            self._bump(self._counters, (*series, "retried"))
            # A fresh RETRIED finds its attempt still open. The trailing
            # RETRIED of the CANCELLED + RETRIED pair (same attempt, same
            # duration_s; see DeliveryExecutor._handle_cancel) must not
            # observe the duration a second time.
            attempt_id = self._attempt_id(event)
            if attempt_id is None or (*series, attempt_id) in self._open_attempts:
                self._observe_duration(series, event.duration_s)
            self._finish_attempt(event)
        elif kind is TaskEventKind.FAILED:
            self._bump(self._processed, (*series, "failed"))
            if event.failure is not None:
                self._bump(self._failures, (*series, event.failure.kind.value))
            self._observe_duration(series, event.duration_s)
            self._finish_attempt(event)
        elif kind is TaskEventKind.DEAD_LETTERED:
            # DEAD_LETTERED is followed by FAILED on the terminal path; the
            # terminal outcome must count once, so only the dedicated counter
            # is bumped here.
            self._bump(self._counters, (*series, "dead_lettered"))
        elif kind is TaskEventKind.CANCELLED:
            self._bump(self._counters, (*series, "cancelled"))
            self._observe_duration(series, event.duration_s)
            self._finish_attempt(event)
        # Unknown kinds are ignored, keeping the exporter forward-compatible
        # with new event kinds.

    def _bump(
        self,
        target: dict[tuple[str, str, str], float],
        key: tuple[str, str, str],
    ) -> None:
        target[key] = target.get(key, 0.0) + 1.0

    def _observe_duration(self, series: tuple[str, str], duration_s: float | None) -> None:
        if duration_s is None:
            return
        histogram = self._histograms.get(series)
        if histogram is None:
            histogram = _Histogram()
            self._histograms[series] = histogram
        histogram.observe(float(duration_s))

    def _close_attempt(self, series: tuple[str, str]) -> None:
        self._inflight[series] = max(0.0, self._inflight.get(series, 0.0) - 1.0)

    def _finish_attempt(self, event: TaskEvent) -> None:
        series = (event.app, event.task)
        attempt_id = self._attempt_id(event)
        if attempt_id is None:
            self._close_attempt(series)
            return
        attempt_key = (*series, attempt_id)
        if attempt_key in self._open_attempts:
            self._open_attempts.remove(attempt_key)
            self._close_attempt(series)

    @staticmethod
    def _attempt_id(event: TaskEvent) -> str | None:
        if isinstance(event.attempt_id, str) and event.attempt_id:
            return event.attempt_id
        value = event.meta.get("onestep.attempt_id")
        if isinstance(value, str) and value:
            return value
        execution = event.meta.get("onestep.execution")
        if isinstance(execution, dict):
            value = execution.get("attempt_id")
            if isinstance(value, str) and value:
                return value
        return None


# ----------------------------------------------------------------------
# family rendering helpers (pure functions over copied state)
# ----------------------------------------------------------------------


def _series_order(
    target: dict[tuple[str, str, str], float],
) -> list[tuple[str, str]]:
    return sorted({(key[0], key[1]) for key in target})


def _counter_family(
    name: str,
    help_text: str,
    source: dict[tuple[str, str, str], float],
    series: Sequence[tuple[str, ...]],
    *,
    extra_label: str | None = None,
    fixed_suffix: str | None = None,
) -> list[str]:
    lines = [f"# HELP {name} {help_text}", f"# TYPE {name} counter"]
    for series_key in series:
        app, task = series_key[0], series_key[1]
        if fixed_suffix is not None:
            suffixes: list[tuple[str, ...]] = [(fixed_suffix,)]
        else:
            suffixes = sorted(
                {key[2:] for key in source if key[0] == app and key[1] == task}
            )
        for suffix in suffixes:
            value = source.get((app, task, suffix[0]))
            if value is None:
                # This family does not own a sample for this series (e.g. a
                # task with only fetched events has no retried sample).
                continue
            labels: list[tuple[str, str]] = [("app", app), ("task", task)]
            if extra_label is not None:
                labels.append((extra_label, suffix[0]))
            lines.append(f"{name}{_label_block(labels)} {_format_value(value)}")
    return lines


def _histogram_family(
    name: str,
    help_text: str,
    histograms: dict[tuple[str, str], tuple[float, float, list[float]]],
) -> list[str]:
    lines = [f"# HELP {name} {help_text}", f"# TYPE {name} histogram"]
    for (app, task) in sorted(histograms):
        label_pairs = [("app", app), ("task", task)]
        lines.extend(_Histogram.render(name, label_pairs, histograms[(app, task)]))
    return lines


def _gauge_family(
    name: str,
    help_text: str,
    gauges: dict[tuple[str, str], float],
) -> list[str]:
    lines = [f"# HELP {name} {help_text}", f"# TYPE {name} gauge"]
    for (app, task) in sorted(gauges):
        labels = _label_block([("app", app), ("task", task)])
        lines.append(f"{name}{labels} {_format_value(gauges[(app, task)])}")
    return lines


def _render_custom_families(samples: list[dict[str, Any]]) -> list[str]:
    """Render custom metric families from ``CustomMetricsRegistry.snapshot()``.

    Each snapshot entry is ``{"name", "kind", "value", "labels", "task"}``;
    the task label is prepended, matching ``_RESERVED_LABEL_NAMES``'s rule
    that user labels may never shadow it.
    """
    groups: dict[str, tuple[str, list[tuple[list[tuple[str, str]], float]]]] = {}
    for sample in samples:
        name = sample["name"]
        if name in _RESERVED_FAMILIES:
            continue
        kind, entries = groups.get(name, (sample["kind"], []))
        label_pairs = [("task", sample["task"])] + sorted(sample["labels"].items())
        entries.append((label_pairs, float(sample["value"])))
        groups[name] = (kind, entries)

    lines: list[str] = []
    for name in sorted(groups):
        kind, entries = groups[name]
        lines.append(f"# TYPE {name} {kind}")
        for label_pairs, value in sorted(entries, key=lambda item: item[0]):
            lines.append(f"{name}{_label_block(label_pairs)} {_format_value(value)}")
    return lines


# ----------------------------------------------------------------------
# HTTP exposure: /metrics + /healthz
# ----------------------------------------------------------------------


class MetricsServer:
    """Minimal asyncio HTTP server exposing the exporter and a health probe.

    Shares the ``_WebhookSource`` server pattern (``asyncio.start_server`` with
    hand-rolled request parsing) so the runtime gains one more tiny HTTP
    listener without pulling in any web framework. ``port=0`` binds an
    ephemeral port, which is what tests and embedded uses rely on.
    """

    def __init__(
        self,
        exporter: PrometheusExporter,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        health_provider: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._exporter = exporter
        self.host = host
        self.requested_port = port
        self._health_provider = health_provider
        self._server: asyncio.Server | None = None

    @property
    def bound_port(self) -> int:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("metrics server is not started")
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.requested_port
        )

    async def stop(self) -> None:
        if self._server is None:
            return
        server, self._server = self._server, None
        server.close()
        await server.wait_closed()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            method, target, _headers = await self._read_request_line(reader)
            if method not in {"GET", "HEAD"}:
                await _write_http_response(
                    writer,
                    status=405,
                    content_type="application/json",
                    payload=b'{"error": "method_not_allowed"}',
                    extra_headers={"Allow": "GET, HEAD"},
                )
                return
            path = target.split("?", 1)[0]
            if path == "/metrics":
                body = self._exporter.render().encode("utf-8")
                await _write_http_response(
                    writer,
                    status=200,
                    content_type="text/plain; version=0.0.4; charset=utf-8",
                    payload=body,
                )
            elif path == "/healthz":
                await self._serve_healthz(writer)
            else:
                await _write_http_response(
                    writer,
                    status=404,
                    content_type="application/json",
                    payload=b'{"error": "not_found"}',
                )
        except Exception:
            with contextlib.suppress(Exception):
                await _write_http_response(
                    writer,
                    status=500,
                    content_type="application/json",
                    payload=b'{"error": "internal_server_error"}',
                )
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _serve_healthz(self, writer: asyncio.StreamWriter) -> None:
        payload = (
            self._health_provider() if self._health_provider is not None else {"status": "ok"}
        )
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        healthy = payload.get("status") == "ok"
        await _write_http_response(
            writer,
            status=200 if healthy else 503,
            content_type="application/json",
            payload=body,
        )

    async def _read_request_line(
        self,
        reader: asyncio.StreamReader,
    ) -> tuple[str, str, dict[str, str]]:
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5.0)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError) as exc:
            raise _BadRequestError("invalid request") from exc
        text = raw.decode("iso-8859-1")
        lines = text.split("\r\n")
        parts = lines[0].split()
        if len(parts) != 3:
            raise _BadRequestError("invalid request line")
        method, target, _version = parts
        headers: dict[str, str] = {}
        for line in lines[1:]:
            key, _, value = line.partition(":")
            if key:
                headers[key.strip().lower()] = value.strip()
        return method.upper(), target, headers


class _BadRequestError(Exception):
    pass


async def _write_http_response(
    writer: asyncio.StreamWriter,
    *,
    status: int,
    content_type: str,
    payload: bytes,
    extra_headers: dict[str, str] | None = None,
) -> None:
    reason = {
        200: "OK",
        404: "Not Found",
        405: "Method Not Allowed",
        500: "Internal Server Error",
        503: "Service Unavailable",
    }.get(status, "OK")
    lines = [f"HTTP/1.1 {status} {reason}"]
    if payload:
        lines.append(f"Content-Type: {content_type}")
    lines.append(f"Content-Length: {len(payload)}")
    lines.append("Connection: close")
    for key, value in (extra_headers or {}).items():
        lines.append(f"{key}: {value}")
    head = ("\r\n".join(lines) + "\r\n\r\n").encode("iso-8859-1")
    writer.write(head + payload)
    await writer.drain()


def _build_health_snapshot(
    app: OneStepApp,
    *,
    started_at_monotonic: float,
) -> dict[str, Any]:
    """Runtime + per-source liveness payload for ``/healthz``."""
    tasks: list[dict[str, Any]] = []
    all_sources_alive = True
    for task in app.tasks:
        source = task.source
        source_info: dict[str, Any] | None = None
        if source is not None:
            alive = bool(getattr(source, "is_open", False))
            all_sources_alive = all_sources_alive and alive
            source_info = {
                "name": getattr(source, "name", type(source).__name__),
                "kind": type(source).__name__,
                "alive": alive,
            }
        tasks.append(
            {
                "task": task.name,
                "source": source_info,
                "inflight": (
                    app.task_control_snapshot(task.name).get("inflight_task_count", 0)
                    if source is not None
                    else 0
                ),
            }
        )

    return {
        "status": "ok" if all_sources_alive and not app.is_stopping else "degraded",
        "app": app.name,
        "version": _resolve_version(),
        "uptime_s": round(max(0.0, time.monotonic() - started_at_monotonic), 3),
        "stopping": app.is_stopping,
        "tasks": tasks,
    }


class MetricsHandle:
    """Owns the exporter + server pair wired onto an app by
    :func:`install_metrics`; ``close()`` tears the server down and detaches the
    event handler."""

    def __init__(self, server: MetricsServer, exporter: PrometheusExporter, app: OneStepApp) -> None:
        self._server = server
        self._exporter = exporter
        self._app = app

    @property
    def exporter(self) -> PrometheusExporter:
        return self._exporter

    @property
    def server(self) -> MetricsServer:
        return self._server

    @property
    def bound_port(self) -> int:
        return self._server.bound_port

    async def close(self) -> None:
        try:
            self._app._event_handlers.remove(self._exporter)
        except ValueError:
            pass
        await self._server.stop()


def install_metrics(
    app: OneStepApp,
    *,
    host: str = "127.0.0.1",
    port: int = 9100,
) -> MetricsHandle:
    """Wire a PrometheusExporter + MetricsServer onto ``app``.

    Registers an event handler (fed by ``emit_event``), a startup hook that
    binds the HTTP listener after resources open, and a shutdown hook that
    releases it. ``port=0`` is supported for tests and embedded uses.
    """
    exporter = PrometheusExporter(
        app_name=app.name,
        custom_metrics=app.custom_metrics,
    )
    server = MetricsServer(
        exporter,
        host=host,
        port=port,
    )
    started_at_monotonic: float | None = None

    def _health() -> dict[str, Any]:
        return _build_health_snapshot(
            app,
            started_at_monotonic=(
                started_at_monotonic
                if started_at_monotonic is not None
                else time.monotonic()
            ),
        )

    server._health_provider = _health

    async def _startup() -> None:
        nonlocal started_at_monotonic
        started_at_monotonic = time.monotonic()
        await server.start()
        logging.getLogger("onestep.observability").info(
            "metrics server listening on %s:%d", host, server.bound_port
        )

    async def _shutdown() -> None:
        await server.stop()

    app.on_startup(_startup)
    app.on_shutdown(_shutdown)
    app.on_event(exporter)
    return MetricsHandle(server, exporter, app)
