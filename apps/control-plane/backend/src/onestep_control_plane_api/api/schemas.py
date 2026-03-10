from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


Environment = Literal["dev", "staging", "prod"]
HealthStatus = Literal["ok", "degraded", "error", "starting", "unknown"]
TaskEventKind = Literal["failed", "retried", "dead_lettered", "cancelled", "succeeded"]
InstanceConnectivity = Literal["online", "offline", "never_reported"]


class ServiceDescriptor(APIModel):
    name: str = Field(min_length=1, max_length=255)
    environment: Environment
    node_name: str = Field(min_length=1, max_length=255)
    instance_id: UUID
    deployment_version: str = Field(min_length=1, max_length=128)


class IngestionEnvelope(APIModel):
    service: ServiceDescriptor
    sent_at: datetime
    sequence: int = Field(ge=0)


class RuntimeDescriptor(APIModel):
    onestep_version: str = Field(min_length=1, max_length=64)
    python_version: str = Field(min_length=1, max_length=64)
    hostname: str = Field(min_length=1, max_length=255)
    pid: int = Field(gt=0)
    started_at: datetime


class ConnectorDescriptor(APIModel):
    kind: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=255)
    config: dict[str, Any] = Field(default_factory=dict)


class RetryDescriptor(APIModel):
    kind: str = Field(min_length=1, max_length=128)
    config: dict[str, Any] = Field(default_factory=dict)


class HealthDescriptor(APIModel):
    status: HealthStatus
    uptime_s: int = Field(ge=0)
    inflight_tasks: int = Field(ge=0)


class HeartbeatIngestRequest(IngestionEnvelope):
    runtime: RuntimeDescriptor
    health: HealthDescriptor


class MetricsWindow(APIModel):
    started_at: datetime
    ended_at: datetime

    @model_validator(mode="after")
    def validate_window_range(self) -> MetricsWindow:
        if self.ended_at < self.started_at:
            raise ValueError("window.ended_at must be greater than or equal to window.started_at")
        return self


class TaskMetricWindowIngest(APIModel):
    task_name: str = Field(min_length=1, max_length=255)
    window_id: str = Field(min_length=1, max_length=255)
    fetched: int = Field(ge=0)
    started: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    retried: int = Field(ge=0)
    failed: int = Field(ge=0)
    dead_lettered: int = Field(ge=0)
    cancelled: int = Field(ge=0)
    timeouts: int = Field(ge=0)
    inflight: int = Field(ge=0)
    avg_duration_ms: float | None = Field(default=None, ge=0)
    p95_duration_ms: float | None = Field(default=None, ge=0)


class MetricsIngestRequest(IngestionEnvelope):
    window: MetricsWindow
    tasks: list[TaskMetricWindowIngest] = Field(min_length=1)


class TaskFailureDescriptor(APIModel):
    kind: str = Field(min_length=1, max_length=64)
    exception_type: str | None = Field(default=None, max_length=255)
    message: str | None = None


class TaskEventRecord(APIModel):
    event_id: str = Field(min_length=1, max_length=255)
    kind: TaskEventKind
    task_name: str = Field(min_length=1, max_length=255)
    occurred_at: datetime
    attempts: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    failure: TaskFailureDescriptor | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class EventsIngestRequest(IngestionEnvelope):
    events: list[TaskEventRecord] = Field(min_length=1)


class TaskTopologyIngest(APIModel):
    name: str = Field(min_length=1, max_length=255)
    source: ConnectorDescriptor | None = None
    emit: list[ConnectorDescriptor] = Field(default_factory=list)
    concurrency: int | None = Field(default=None, ge=1)
    timeout_s: float | None = Field(default=None, ge=0)
    retry: RetryDescriptor | None = None


class AppTopologyDescriptor(APIModel):
    name: str = Field(min_length=1, max_length=255)
    shutdown_timeout_s: float | None = Field(default=None, ge=0)
    topology_hash: str = Field(min_length=1, max_length=255)
    tasks: list[TaskTopologyIngest] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_task_names_unique(self) -> AppTopologyDescriptor:
        task_names = [task.name for task in self.tasks]
        if len(task_names) != len(set(task_names)):
            raise ValueError("app.tasks names must be unique within a sync payload")
        return self


class SyncIngestRequest(IngestionEnvelope):
    runtime: RuntimeDescriptor
    app: AppTopologyDescriptor

    @model_validator(mode="after")
    def validate_app_service_identity(self) -> SyncIngestRequest:
        if self.app.name != self.service.name:
            raise ValueError("app.name must match service.name")
        return self


class IngestionAcceptedResponse(APIModel):
    status: Literal["accepted"] = "accepted"
    received_at: datetime


class MetricsAcceptedResponse(IngestionAcceptedResponse):
    ingested_count: int


class EventsAcceptedResponse(IngestionAcceptedResponse):
    ingested_count: int


class SyncAcceptedResponse(IngestionAcceptedResponse):
    service_name: str
    environment: Environment
    instance_id: UUID
    topology_hash: str
    task_count: int = Field(ge=0)


class ConsoleLoginRequest(APIModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=255)


class ConsoleSessionResponse(APIModel):
    auth_configured: bool
    authenticated: bool
    username: str | None = None


class PaginatedResponse(APIModel):
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class ServiceSummary(APIModel):
    name: str
    environment: Environment
    latest_deployment_version: str
    latest_topology_hash: str | None = None
    latest_sync_at: datetime | None = None
    instance_count: int = Field(ge=0)
    online_instance_count: int = Field(ge=0)
    last_seen_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ServiceListResponse(PaginatedResponse):
    items: list[ServiceSummary]


class InstanceConnectivityCounts(APIModel):
    total: int = Field(default=0, ge=0)
    online: int = Field(default=0, ge=0)
    offline: int = Field(default=0, ge=0)
    never_reported: int = Field(default=0, ge=0)


class InstanceStatusCounts(APIModel):
    ok: int = Field(default=0, ge=0)
    degraded: int = Field(default=0, ge=0)
    error: int = Field(default=0, ge=0)
    starting: int = Field(default=0, ge=0)
    unknown: int = Field(default=0, ge=0)


class InstanceSummary(APIModel):
    instance_id: UUID
    node_name: str
    hostname: str | None = None
    pid: int | None = None
    deployment_version: str
    onestep_version: str | None = None
    python_version: str | None = None
    started_at: datetime | None = None
    last_sync_at: datetime | None = None
    last_topology_hash: str | None = None
    last_heartbeat_sent_at: datetime | None = None
    last_heartbeat_sequence: int | None = Field(default=None, ge=0)
    last_seen_at: datetime | None = None
    status: HealthStatus
    connectivity: InstanceConnectivity
    created_at: datetime
    updated_at: datetime


class InstanceListResponse(PaginatedResponse):
    items: list[InstanceSummary]


class TaskMetricWindowSummary(APIModel):
    instance_id: UUID
    task_name: str
    window_id: str
    window_started_at: datetime
    window_ended_at: datetime
    fetched: int = Field(ge=0)
    started: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    retried: int = Field(ge=0)
    failed: int = Field(ge=0)
    dead_lettered: int = Field(ge=0)
    cancelled: int = Field(ge=0)
    timeouts: int = Field(ge=0)
    inflight: int = Field(ge=0)
    avg_duration_ms: float | None = Field(default=None, ge=0)
    p95_duration_ms: float | None = Field(default=None, ge=0)
    received_at: datetime
    created_at: datetime


class TaskMetricWindowListResponse(PaginatedResponse):
    items: list[TaskMetricWindowSummary]


class TaskEventCounts(APIModel):
    failed: int = Field(default=0, ge=0)
    retried: int = Field(default=0, ge=0)
    dead_lettered: int = Field(default=0, ge=0)
    cancelled: int = Field(default=0, ge=0)
    succeeded: int = Field(default=0, ge=0)


class TaskEventSummary(APIModel):
    event_id: str
    instance_id: UUID
    task_name: str
    kind: TaskEventKind
    occurred_at: datetime
    attempts: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    failure_kind: str | None = None
    exception_type: str | None = None
    message: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    received_at: datetime
    created_at: datetime


class TaskEventListResponse(PaginatedResponse):
    items: list[TaskEventSummary]


class TaskDashboardSummary(APIModel):
    task_name: str
    source_name: str | None = None
    source_kind: str | None = None
    source_config: dict[str, Any] | None = None
    emit: list[ConnectorDescriptor] = Field(default_factory=list)
    concurrency: int | None = Field(default=None, ge=1)
    timeout_s: float | None = Field(default=None, ge=0)
    retry_policy: RetryDescriptor | None = None
    topology_hash: str | None = None
    metric_window_count: int = Field(default=0, ge=0)
    latest_window_started_at: datetime | None = None
    latest_window_ended_at: datetime | None = None
    fetched: int = Field(default=0, ge=0)
    started: int = Field(default=0, ge=0)
    succeeded: int = Field(default=0, ge=0)
    retried: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    dead_lettered: int = Field(default=0, ge=0)
    cancelled: int = Field(default=0, ge=0)
    timeouts: int = Field(default=0, ge=0)
    weighted_avg_duration_ms: float | None = Field(default=None, ge=0)
    max_p95_duration_ms: float | None = Field(default=None, ge=0)
    last_event_at: datetime | None = None
    event_counts: TaskEventCounts


class TaskDashboardListResponse(PaginatedResponse):
    lookback_minutes: int = Field(ge=1)
    lookback_started_at: datetime
    items: list[TaskDashboardSummary]


class TaskDetailResponse(APIModel):
    service: ServiceSummary
    task_name: str
    lookback_minutes: int = Field(ge=1)
    lookback_started_at: datetime
    summary: TaskDashboardSummary
    recent_metric_windows: list[TaskMetricWindowSummary]
    recent_events: list[TaskEventSummary]


class InstanceDetailResponse(APIModel):
    service: ServiceSummary
    lookback_minutes: int = Field(ge=1)
    lookback_started_at: datetime
    instance: InstanceSummary
    app_snapshot: dict[str, Any] | None = None
    recent_metric_windows: list[TaskMetricWindowSummary]
    recent_events: list[TaskEventSummary]


class ServiceDashboardResponse(APIModel):
    service: ServiceSummary
    lookback_minutes: int = Field(ge=1)
    lookback_started_at: datetime
    instance_connectivity: InstanceConnectivityCounts
    instance_statuses: InstanceStatusCounts
    task_count: int = Field(ge=0)
    failing_task_count: int = Field(ge=0)
    topology_hashes: list[str] = Field(default_factory=list)
    topology_consistent: bool
    recent_events: list[TaskEventSummary]
