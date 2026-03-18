from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import math
import os
import platform
import re
import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as package_version
from typing import TYPE_CHECKING, Any, Awaitable, Callable
from uuid import UUID, uuid4

from .events import TaskEvent, TaskEventKind
from .retry import FailureKind

if TYPE_CHECKING:
    from .app import OneStepApp


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_onestep_version() -> str:
    try:
        return package_version("onestep")
    except PackageNotFoundError:  # pragma: no cover - local source tree before install
        return "dev"


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _normalize_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if not normalized:
        raise ValueError("control plane base URL must not be empty")
    return normalized


def _coerce_positive_float(name: str, value: float) -> float:
    if value <= 0:
        raise ValueError(f"{name} must be > 0")
    return value


def _coerce_positive_int(name: str, value: int) -> int:
    if value < 1:
        raise ValueError(f"{name} must be >= 1")
    return value


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _read_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return None


_KIND_OVERRIDES = {
    "CronSource": "cron",
    "IncrementalTableSource": "mysql_incremental",
    "IntervalSource": "interval",
    "MaxAttempts": "max_attempts",
    "MemoryQueue": "memory_queue",
    "NoRetry": "no_retry",
    "RabbitMQQueue": "rabbitmq_queue",
    "SQSQueue": "sqs_queue",
    "TableQueueSource": "mysql_table_queue",
    "TableSink": "mysql_table_sink",
    "WebhookSource": "webhook",
}


def _snake_case(value: str) -> str:
    normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()
    return normalized.strip("_")


def _kind_name(value: object) -> str:
    class_name = value.__class__.__name__
    if class_name in _KIND_OVERRIDES:
        return _KIND_OVERRIDES[class_name]
    for suffix in ("Source", "Sink"):
        if class_name.endswith(suffix):
            class_name = class_name[: -len(suffix)]
            break
    return _snake_case(class_name)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, (str, int, float, bool)):
        return enum_value
    return str(value)


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _seconds_value(value: float) -> int | float:
    if float(value).is_integer():
        return int(value)
    return value


@dataclass
class ControlPlaneReporterConfig:
    base_url: str
    token: str
    environment: str = "dev"
    service_name: str | None = None
    node_name: str | None = None
    deployment_version: str | None = None
    instance_id: UUID = field(default_factory=uuid4)
    heartbeat_interval_s: float = 30.0
    metrics_interval_s: float = 30.0
    event_flush_interval_s: float = 5.0
    event_batch_size: int = 100
    max_pending_events: int = 1000
    max_pending_metric_batches: int = 120
    timeout_s: float = 5.0
    reconnect_base_delay_s: float = 0.5
    reconnect_max_delay_s: float = 30.0

    def __post_init__(self) -> None:
        self.base_url = _normalize_base_url(self.base_url)
        self.token = self.token.strip()
        if not self.token:
            raise ValueError("control plane token must not be empty")
        if self.environment not in {"dev", "staging", "prod"}:
            raise ValueError("environment must be one of: dev, staging, prod")
        self.heartbeat_interval_s = _coerce_positive_float(
            "heartbeat_interval_s",
            self.heartbeat_interval_s,
        )
        self.metrics_interval_s = _coerce_positive_float(
            "metrics_interval_s",
            self.metrics_interval_s,
        )
        self.event_flush_interval_s = _coerce_positive_float(
            "event_flush_interval_s",
            self.event_flush_interval_s,
        )
        self.event_batch_size = _coerce_positive_int("event_batch_size", self.event_batch_size)
        self.max_pending_events = _coerce_positive_int(
            "max_pending_events",
            self.max_pending_events,
        )
        self.max_pending_metric_batches = _coerce_positive_int(
            "max_pending_metric_batches",
            self.max_pending_metric_batches,
        )
        self.timeout_s = _coerce_positive_float("timeout_s", self.timeout_s)
        self.reconnect_base_delay_s = _coerce_positive_float(
            "reconnect_base_delay_s",
            self.reconnect_base_delay_s,
        )
        self.reconnect_max_delay_s = _coerce_positive_float(
            "reconnect_max_delay_s",
            self.reconnect_max_delay_s,
        )
        if self.reconnect_max_delay_s < self.reconnect_base_delay_s:
            raise ValueError("reconnect_max_delay_s must be >= reconnect_base_delay_s")

    @classmethod
    def from_env(cls, *, app_name: str | None = None) -> "ControlPlaneReporterConfig":
        base_url = _read_env("ONESTEP_CONTROL_PLANE_URL", "ONESTEP_CONTROL_URL")
        token = _read_env("ONESTEP_CONTROL_PLANE_TOKEN", "ONESTEP_CONTROL_TOKEN")
        if base_url is None:
            raise ValueError("missing ONESTEP_CONTROL_PLANE_URL or ONESTEP_CONTROL_URL")
        if token is None:
            raise ValueError("missing ONESTEP_CONTROL_PLANE_TOKEN or ONESTEP_CONTROL_TOKEN")
        environment = _read_env("ONESTEP_CONTROL_PLANE_ENVIRONMENT", "ONESTEP_ENV") or "dev"
        service_name = _read_env("ONESTEP_SERVICE_NAME") or app_name
        node_name = _read_env("ONESTEP_NODE_NAME")
        deployment_version = _read_env(
            "ONESTEP_DEPLOYMENT_VERSION",
            "ONESTEP_VERSION",
        )
        instance_id_value = _read_env("ONESTEP_INSTANCE_ID")
        instance_id = UUID(instance_id_value) if instance_id_value is not None else uuid4()
        heartbeat_interval_s = float(_read_env("ONESTEP_CONTROL_PLANE_HEARTBEAT_INTERVAL_S") or 30.0)
        metrics_interval_s = float(_read_env("ONESTEP_CONTROL_PLANE_METRICS_INTERVAL_S") or 30.0)
        event_flush_interval_s = float(
            _read_env("ONESTEP_CONTROL_PLANE_EVENT_FLUSH_INTERVAL_S") or 5.0
        )
        event_batch_size = int(_read_env("ONESTEP_CONTROL_PLANE_EVENT_BATCH_SIZE") or 100)
        max_pending_events = int(_read_env("ONESTEP_CONTROL_PLANE_MAX_PENDING_EVENTS") or 1000)
        max_pending_metric_batches = int(
            _read_env("ONESTEP_CONTROL_PLANE_MAX_PENDING_METRIC_BATCHES") or 120
        )
        timeout_s = float(_read_env("ONESTEP_CONTROL_PLANE_TIMEOUT_S") or 5.0)
        reconnect_base_delay_s = float(
            _read_env("ONESTEP_CONTROL_PLANE_RECONNECT_BASE_DELAY_S") or 0.5
        )
        reconnect_max_delay_s = float(
            _read_env("ONESTEP_CONTROL_PLANE_RECONNECT_MAX_DELAY_S") or 30.0
        )
        return cls(
            base_url=base_url,
            token=token,
            environment=environment,
            service_name=service_name,
            node_name=node_name,
            deployment_version=deployment_version,
            instance_id=instance_id,
            heartbeat_interval_s=heartbeat_interval_s,
            metrics_interval_s=metrics_interval_s,
            event_flush_interval_s=event_flush_interval_s,
            event_batch_size=event_batch_size,
            max_pending_events=max_pending_events,
            max_pending_metric_batches=max_pending_metric_batches,
            timeout_s=timeout_s,
            reconnect_base_delay_s=reconnect_base_delay_s,
            reconnect_max_delay_s=reconnect_max_delay_s,
        )


@dataclass
class _TaskMetricsState:
    fetched: int = 0
    started: int = 0
    succeeded: int = 0
    retried: int = 0
    failed: int = 0
    dead_lettered: int = 0
    cancelled: int = 0
    timeouts: int = 0
    inflight: int = 0
    durations_ms: list[float] = field(default_factory=list)

    def has_activity(self) -> bool:
        return any(
            (
                self.fetched,
                self.started,
                self.succeeded,
                self.retried,
                self.failed,
                self.dead_lettered,
                self.cancelled,
                self.timeouts,
                self.inflight,
            )
        )

    def rotate(self) -> "_TaskMetricsState":
        rotated = _TaskMetricsState(
            fetched=self.fetched,
            started=self.started,
            succeeded=self.succeeded,
            retried=self.retried,
            failed=self.failed,
            dead_lettered=self.dead_lettered,
            cancelled=self.cancelled,
            timeouts=self.timeouts,
            inflight=self.inflight,
            durations_ms=list(self.durations_ms),
        )
        self.fetched = 0
        self.started = 0
        self.succeeded = 0
        self.retried = 0
        self.failed = 0
        self.dead_lettered = 0
        self.cancelled = 0
        self.timeouts = 0
        self.durations_ms.clear()
        return rotated


@dataclass
class _PreparedMetricsBatch:
    window_started_at: datetime
    window_ended_at: datetime
    tasks: list[dict[str, Any]]


ReporterSender = Callable[[str, dict[str, Any]], Awaitable[None]]


def _build_default_sender(config: ControlPlaneReporterConfig) -> ReporterSender:
    from .control_plane_ws import ControlPlaneWsSender

    return ControlPlaneWsSender(config)


class ControlPlaneReporter:
    def __init__(
        self,
        config: ControlPlaneReporterConfig,
        *,
        sender: ReporterSender | None = None,
    ) -> None:
        self.config = config
        self._sender = sender or _build_default_sender(config)
        self._app: OneStepApp | None = None
        self._logger = logging.getLogger("onestep.control_plane")
        self._attached = False
        self._started_at = _utcnow()
        self._hostname = socket.gethostname()
        self._node_name = config.node_name or self._hostname
        self._service_name = config.service_name
        self._deployment_version = config.deployment_version or _resolve_onestep_version()
        self._onestep_version = _resolve_onestep_version()
        self._sequence = 0
        self._event_sequence = 0
        self._pending_events: list[dict[str, Any]] = []
        self._metrics_window_started_at = _utcnow()
        self._metrics_by_task: dict[str, _TaskMetricsState] = {}
        self._pending_metric_batches: list[_PreparedMetricsBatch] = []
        self._degraded_since_last_heartbeat = False
        self._last_synced_topology_hash: str | None = None
        self._loop_tasks: list[asyncio.Task[None]] = []
        self._stop_event: asyncio.Event | None = None
        self._event_flush_signal: asyncio.Event | None = None

    def attach(self, app: "OneStepApp") -> "ControlPlaneReporter":
        if self._attached:
            if self._app is not app:
                raise RuntimeError("reporter is already attached to another app")
            return self
        self._attached = True
        self._app = app
        if self._service_name is None:
            self._service_name = app.name
        bind_app = getattr(self._sender, "bind_app", None)
        if callable(bind_app):
            bind_app(app)
        bind_reporter = getattr(self._sender, "bind_reporter", None)
        if callable(bind_reporter):
            bind_reporter(self)
        app.on_startup(self.startup)
        app.on_shutdown(self.shutdown)
        app.on_event(self.handle_event)
        return self

    async def startup(self) -> None:
        self._sequence = 0
        self._event_sequence = 0
        self._pending_events.clear()
        self._pending_metric_batches.clear()
        self._metrics_by_task.clear()
        self._degraded_since_last_heartbeat = False
        self._last_synced_topology_hash = None
        self._stop_event = asyncio.Event()
        self._event_flush_signal = asyncio.Event()
        self._started_at = _utcnow()
        self._metrics_window_started_at = self._started_at
        await self._safe_send_heartbeat()
        await self._safe_send_sync()
        self._loop_tasks = [
            asyncio.create_task(self._heartbeat_loop(), name="onestep-control-plane-heartbeat"),
            asyncio.create_task(self._metrics_loop(), name="onestep-control-plane-metrics"),
            asyncio.create_task(self._events_loop(), name="onestep-control-plane-events"),
        ]

    async def shutdown(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._event_flush_signal is not None:
            self._event_flush_signal.set()
        if self._loop_tasks:
            await asyncio.gather(*self._loop_tasks, return_exceptions=True)
        self._loop_tasks = []
        self._rotate_metrics_window(_utcnow())
        await self._flush_metric_batches()
        await self._flush_event_batches()
        close = getattr(self._sender, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result

    def handle_event(self, event: TaskEvent) -> None:
        bucket = self._metrics_by_task.setdefault(event.task, _TaskMetricsState())
        if event.kind is TaskEventKind.FETCHED:
            bucket.fetched += 1
        elif event.kind is TaskEventKind.STARTED:
            bucket.started += 1
            bucket.inflight += 1
        elif event.kind is TaskEventKind.SUCCEEDED:
            bucket.succeeded += 1
            bucket.inflight = max(0, bucket.inflight - 1)
            self._record_duration(bucket, event)
        elif event.kind is TaskEventKind.RETRIED:
            bucket.retried += 1
            bucket.inflight = max(0, bucket.inflight - 1)
            self._record_timeout(bucket, event)
            self._record_duration(bucket, event)
            self._degraded_since_last_heartbeat = True
        elif event.kind is TaskEventKind.FAILED:
            bucket.failed += 1
            bucket.inflight = max(0, bucket.inflight - 1)
            self._record_timeout(bucket, event)
            self._record_duration(bucket, event)
            self._degraded_since_last_heartbeat = True
        elif event.kind is TaskEventKind.DEAD_LETTERED:
            bucket.dead_lettered += 1
            self._degraded_since_last_heartbeat = True
        elif event.kind is TaskEventKind.CANCELLED:
            bucket.cancelled += 1
            self._degraded_since_last_heartbeat = True

        if event.kind in {
            TaskEventKind.RETRIED,
            TaskEventKind.FAILED,
            TaskEventKind.DEAD_LETTERED,
            TaskEventKind.CANCELLED,
        }:
            self._pending_events.append(self._build_event_payload(event))
            self._apply_event_backpressure()
            if (
                self._event_flush_signal is not None
                and len(self._pending_events) >= self.config.event_batch_size
            ):
                self._event_flush_signal.set()

    async def _heartbeat_loop(self) -> None:
        while not await self._wait_for_stop(self.config.heartbeat_interval_s):
            await self._safe_send_sync()
            await self._safe_send_heartbeat()

    async def _metrics_loop(self) -> None:
        while not await self._wait_for_stop(self.config.metrics_interval_s):
            self._rotate_metrics_window(_utcnow())
            await self._flush_metric_batches()

    async def _events_loop(self) -> None:
        while True:
            if self._stop_event is None or self._event_flush_signal is None:
                return
            if self._stop_event.is_set():
                return
            try:
                await asyncio.wait_for(
                    self._event_flush_signal.wait(),
                    timeout=self.config.event_flush_interval_s,
                )
            except asyncio.TimeoutError:
                pass
            else:
                self._event_flush_signal.clear()
            if self._stop_event.is_set():
                return
            await self._flush_event_batches()

    async def _wait_for_stop(self, timeout_s: float) -> bool:
        if self._stop_event is None:
            return True
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=timeout_s)
        except asyncio.TimeoutError:
            return False
        return True

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def _next_event_id(self) -> str:
        self._event_sequence += 1
        return f"{self.config.instance_id.hex}-{self._event_sequence}"

    def _service_descriptor(self) -> dict[str, Any]:
        return {
            "name": self._service_name,
            "environment": self.config.environment,
            "node_name": self._node_name,
            "instance_id": self.config.instance_id,
            "deployment_version": self._deployment_version,
        }

    def _build_runtime_descriptor(self) -> dict[str, Any]:
        return {
            "onestep_version": self._onestep_version,
            "python_version": platform.python_version(),
            "hostname": self._hostname,
            "pid": os.getpid(),
            "started_at": self._started_at,
        }

    def _build_health_descriptor(self) -> dict[str, Any]:
        inflight_tasks = sum(bucket.inflight for bucket in self._metrics_by_task.values())
        status = "degraded" if self._degraded_since_last_heartbeat else "ok"
        return {
            "status": status,
            "uptime_s": max(0, int((_utcnow() - self._started_at).total_seconds())),
            "inflight_tasks": inflight_tasks,
            "task_controls": self._build_task_control_states(),
        }

    def _build_task_control_states(self) -> list[dict[str, Any]]:
        if self._app is None:
            return []
        return [dict(state) for state in self._app.task_control_snapshots()]

    def _build_retry_descriptor(self, policy: Any) -> dict[str, Any]:
        kind = _kind_name(policy)
        if kind == "max_attempts":
            return {
                "kind": kind,
                "config": {
                    "max_attempts": policy.max_attempts,
                    "delay_s": policy.delay_s,
                },
            }
        return {"kind": kind, "config": {}}

    def _build_connector_config(self, resource: Any) -> dict[str, Any]:
        class_name = resource.__class__.__name__
        if class_name == "IntervalSource":
            return {
                "seconds": _seconds_value(resource.interval.total_seconds()),
                "immediate": resource.immediate,
                "overlap": resource.overlap,
                "timezone": str(resource.timezone),
                "poll_interval_s": resource.poll_interval_s,
            }
        if class_name == "CronSource":
            return {
                "expression": resource.schedule.expression,
                "immediate": resource.immediate,
                "overlap": resource.overlap,
                "timezone": str(resource.timezone),
                "poll_interval_s": resource.poll_interval_s,
            }
        if class_name == "MemoryQueue":
            return {
                "maxsize": resource._maxsize,
                "batch_size": resource.batch_size,
                "poll_interval_s": resource.poll_interval_s,
            }
        if class_name == "WebhookSource":
            return {
                "path": resource.path,
                "methods": list(resource.methods),
                "host": resource.host,
                "port": resource.port,
                "parser": resource.parser,
                "max_body_bytes": resource.max_body_bytes,
                "read_timeout_s": resource.read_timeout_s,
                "queue_maxsize": resource.queue_maxsize,
                "batch_size": resource.batch_size,
                "poll_interval_s": resource.poll_interval_s,
            }
        if class_name == "RabbitMQQueue":
            return {
                "routing_key": resource.routing_key,
                "exchange": resource.exchange_name,
                "exchange_type": resource.exchange_type,
                "bind": resource.bind,
                "bind_arguments": resource.bind_arguments,
                "durable": resource.durable,
                "auto_delete": resource.auto_delete,
                "arguments": resource.arguments,
                "exchange_durable": resource.exchange_durable,
                "exchange_auto_delete": resource.exchange_auto_delete,
                "exchange_arguments": resource.exchange_arguments,
                "prefetch": resource.prefetch,
                "batch_size": resource.batch_size,
                "poll_interval_s": resource.poll_interval_s,
                "publisher_confirms": resource.publisher_confirms,
                "persistent": resource.persistent,
            }
        if class_name == "SQSQueue":
            return {
                "url": resource.url,
                "wait_time_s": resource.wait_time_s,
                "visibility_timeout": resource.visibility_timeout,
                "batch_size": resource.batch_size,
                "poll_interval_s": resource.poll_interval_s,
                "message_group_id": resource.message_group_id,
                "on_fail": resource.on_fail,
                "delete_batch_size": resource.delete_batch_size,
                "delete_flush_interval_s": resource.delete_flush_interval_s,
                "heartbeat_interval_s": resource.heartbeat_interval_s,
                "heartbeat_visibility_timeout": resource.heartbeat_visibility_timeout,
            }
        if class_name == "TableQueueSource":
            return {
                "table": resource.table_name,
                "key": resource.key,
                "where": resource.where,
                "claim": resource.claim,
                "ack": resource.ack,
                "nack": resource.nack,
                "batch_size": resource.batch_size,
                "poll_interval_s": resource.poll_interval_s,
            }
        if class_name == "IncrementalTableSource":
            return {
                "table": resource.table_name,
                "key": resource.key,
                "cursor": list(resource.cursor),
                "where": resource.where,
                "batch_size": resource.batch_size,
                "poll_interval_s": resource.poll_interval_s,
                "state_key": resource.state_key,
            }
        if class_name == "TableSink":
            return {
                "table": resource.table_name,
                "mode": resource.mode,
                "keys": list(resource.keys),
            }
        return {}

    def _build_connector_descriptor(self, resource: Any) -> dict[str, Any]:
        custom_descriptor = getattr(resource, "control_plane_descriptor", None)
        if callable(custom_descriptor):
            payload = _json_safe(custom_descriptor())
            if isinstance(payload, dict):
                return {
                    "kind": str(payload.get("kind") or _kind_name(resource)),
                    "name": str(payload.get("name") or resource.name),
                    "config": _json_safe(payload.get("config") or {}),
                }
        return {
            "kind": _kind_name(resource),
            "name": resource.name,
            "config": _json_safe(self._build_connector_config(resource)),
        }

    def _build_app_topology_descriptor(self) -> dict[str, Any]:
        if self._app is None:
            raise RuntimeError("reporter is not attached to an app")
        tasks = []
        for task in self._app.tasks:
            tasks.append(
                {
                    "name": task.name,
                    "description": task.description,
                    "source": (
                        self._build_connector_descriptor(task.source) if task.source is not None else None
                    ),
                    "emit": [self._build_connector_descriptor(sink) for sink in task.sinks],
                    "concurrency": task.concurrency,
                    "timeout_s": task.timeout_s,
                    "retry": self._build_retry_descriptor(task.retry),
                }
            )
        return {
            "name": self._service_name,
            "shutdown_timeout_s": self._app.shutdown_timeout_s,
            "tasks": tasks,
        }

    async def send_sync_now(self) -> None:
        await self._send_sync(force=True)

    async def send_heartbeat_now(self) -> None:
        await self._send_heartbeat()

    async def flush_metrics_now(self) -> None:
        self._rotate_metrics_window(_utcnow())
        await self._flush_metric_batches(suppress_exceptions=False)

    async def flush_events_now(self) -> None:
        await self._flush_event_batches(suppress_exceptions=False)

    def _apply_event_backpressure(self) -> None:
        overflow = len(self._pending_events) - self.config.max_pending_events
        if overflow <= 0:
            return
        del self._pending_events[:overflow]
        self._degraded_since_last_heartbeat = True
        self._logger.warning(
            "dropping control plane event telemetry due to local backpressure",
            extra={
                "dropped_events": overflow,
                "retained_events": len(self._pending_events),
            },
        )

    def _apply_metric_backpressure(self) -> None:
        overflow = len(self._pending_metric_batches) - self.config.max_pending_metric_batches
        if overflow <= 0:
            return
        del self._pending_metric_batches[:overflow]
        self._degraded_since_last_heartbeat = True
        self._logger.warning(
            "dropping control plane metric telemetry due to local backpressure",
            extra={
                "dropped_metric_batches": overflow,
                "retained_metric_batches": len(self._pending_metric_batches),
            },
        )

    async def _safe_send_sync(self, *, force: bool = False) -> None:
        try:
            await self._send_sync(force=force)
        except Exception:
            self._logger.exception("control plane sync failed")

    async def _send_sync(self, *, force: bool = False) -> None:
        if self._app is None:
            return
        app_payload = self._build_app_topology_descriptor()
        topology_hash = _stable_hash(app_payload)
        if not force and topology_hash == self._last_synced_topology_hash:
            return
        payload = {
            "service": self._service_descriptor(),
            "runtime": self._build_runtime_descriptor(),
            "app": {
                **app_payload,
                "topology_hash": topology_hash,
            },
            "sent_at": _utcnow(),
            "sequence": self._next_sequence(),
        }
        await self._sender("sync", payload)
        self._last_synced_topology_hash = topology_hash

    async def _safe_send_heartbeat(self) -> None:
        try:
            await self._send_heartbeat()
        except Exception:
            self._logger.exception("control plane heartbeat failed")

    async def _send_heartbeat(self) -> None:
        payload = {
            "service": self._service_descriptor(),
            "runtime": self._build_runtime_descriptor(),
            "health": self._build_health_descriptor(),
            "sent_at": _utcnow(),
            "sequence": self._next_sequence(),
        }
        await self._sender("heartbeat", payload)
        self._degraded_since_last_heartbeat = False

    def _rotate_metrics_window(self, ended_at: datetime) -> None:
        tasks: list[dict[str, Any]] = []
        for task_name, bucket in self._metrics_by_task.items():
            if not bucket.has_activity():
                continue
            snapshot = bucket.rotate()
            duration_values = snapshot.durations_ms
            avg_duration_ms = (
                sum(duration_values) / len(duration_values) if duration_values else None
            )
            tasks.append(
                {
                    "task_name": task_name,
                    "window_id": (
                        f"{task_name}:{self._metrics_window_started_at.isoformat()}:{ended_at.isoformat()}"
                    ),
                    "fetched": snapshot.fetched,
                    "started": snapshot.started,
                    "succeeded": snapshot.succeeded,
                    "retried": snapshot.retried,
                    "failed": snapshot.failed,
                    "dead_lettered": snapshot.dead_lettered,
                    "cancelled": snapshot.cancelled,
                    "timeouts": snapshot.timeouts,
                    "inflight": snapshot.inflight,
                    "avg_duration_ms": avg_duration_ms,
                    "p95_duration_ms": _percentile(duration_values, 0.95),
                }
            )
        if tasks:
            self._pending_metric_batches.append(
                _PreparedMetricsBatch(
                    window_started_at=self._metrics_window_started_at,
                    window_ended_at=ended_at,
                    tasks=tasks,
                )
            )
            self._apply_metric_backpressure()
        self._metrics_window_started_at = ended_at

    async def _flush_metric_batches(self, *, suppress_exceptions: bool = True) -> None:
        while self._pending_metric_batches:
            batch = self._pending_metric_batches[0]
            payload = {
                "service": self._service_descriptor(),
                "sent_at": _utcnow(),
                "sequence": self._next_sequence(),
                "window": {
                    "started_at": batch.window_started_at,
                    "ended_at": batch.window_ended_at,
                },
                "tasks": batch.tasks,
            }
            try:
                await self._sender("metrics", payload)
            except Exception:
                if suppress_exceptions:
                    self._logger.exception("control plane metrics flush failed")
                else:
                    raise
                return
            self._pending_metric_batches.pop(0)

    async def _flush_event_batches(self, *, suppress_exceptions: bool = True) -> None:
        while self._pending_events:
            batch = self._pending_events[: self.config.event_batch_size]
            payload = {
                "service": self._service_descriptor(),
                "sent_at": _utcnow(),
                "sequence": self._next_sequence(),
                "events": batch,
            }
            try:
                await self._sender("events", payload)
            except Exception:
                if suppress_exceptions:
                    self._logger.exception("control plane event flush failed")
                else:
                    raise
                return
            del self._pending_events[: len(batch)]

    def _build_event_payload(self, event: TaskEvent) -> dict[str, Any]:
        meta = dict(event.meta)
        if event.source is not None:
            meta.setdefault("source", event.source)
        payload: dict[str, Any] = {
            "event_id": self._next_event_id(),
            "kind": event.kind.value,
            "task_name": event.task,
            "occurred_at": event.emitted_at,
            "attempts": event.attempts,
            "duration_ms": (
                int(round(event.duration_s * 1000)) if event.duration_s is not None else None
            ),
            "meta": meta,
        }
        if event.failure is not None:
            payload["failure"] = event.failure.as_dict()
        return payload

    def _record_duration(self, bucket: _TaskMetricsState, event: TaskEvent) -> None:
        if event.duration_s is None:
            return
        bucket.durations_ms.append(event.duration_s * 1000.0)

    def _record_timeout(self, bucket: _TaskMetricsState, event: TaskEvent) -> None:
        if event.failure is not None and event.failure.kind is FailureKind.TIMEOUT:
            bucket.timeouts += 1

__all__ = ["ControlPlaneReporter", "ControlPlaneReporterConfig"]
