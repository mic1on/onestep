from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from onestep_control_plane_api.api.notification_custom import validate_custom_template_value
from onestep_control_plane_api.core.settings import settings


def _serialize_datetime_for_api(value: datetime) -> str:
    if value.tzinfo is None:
        normalized = value.replace(tzinfo=UTC)
    else:
        normalized = value.astimezone(UTC)
    return normalized.astimezone(settings.effective_api_response_timezone).isoformat()


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_serializer("*", when_used="json", check_fields=False)
    def serialize_datetime_fields(self, value: Any) -> Any:
        if isinstance(value, datetime):
            return _serialize_datetime_for_api(value)
        return value


Environment = Literal["dev", "staging", "prod"]
HealthStatus = Literal["ok", "degraded", "error", "starting", "unknown"]
# View-facing status enums. The plane derives these from the raw
# ServiceListStatus / HealthStatus / metrics so that clients never have to
# recompute business state. All lowercase to keep a single canonical style.
ServiceViewStatus = Literal["running", "degraded", "stopped"]
TaskViewStatus = Literal["running", "idle", "failed", "paused", "offline"]
InstanceViewStatus = Literal["running", "starting", "failed", "stopped"]
EventLogLevel = Literal["error", "warn", "info"]
TaskEventKind = Literal["started", "failed", "retried", "dead_lettered", "cancelled", "succeeded"]
NotificationProvider = Literal["feishu", "wechat_work", "custom"]
NotificationEventType = Literal[
    "task_started",
    "task_succeeded",
    "task_failed",
    "task_missed_start",
    "instance_online",
    "instance_offline",
]
NotificationDeliveryStatus = Literal["pending", "succeeded", "failed"]
InstanceConnectivity = Literal["online", "offline", "never_reported"]
ServiceListStatus = Literal["online", "attention", "offline"]
TelemetryChannel = Literal["sync", "heartbeat", "metrics", "events"]
CustomMetricKind = Literal["counter", "gauge"]
ConsoleRole = Literal["viewer", "operator", "admin"]
AgentCommandKind = Literal[
    "ping",
    "shutdown",
    "restart",
    "drain",
    "pause_task",
    "resume_task",
    "restart_task",
    "discard_dead_letters",
    "replay_dead_letters",
    "run_task_once",
    "sync_now",
    "flush_metrics",
    "flush_events",
]
ServiceCommandFanoutKind = Literal[
    "ping",
    "restart",
    "drain",
    "pause_task",
    "resume_task",
    "restart_task",
    "discard_dead_letters",
    "replay_dead_letters",
    "run_task_once",
    "sync_now",
    "flush_metrics",
    "flush_events",
]
TaskCommandKind = Literal[
    "pause_task",
    "resume_task",
    "restart_task",
    "discard_dead_letters",
    "replay_dead_letters",
    "run_task_once",
]
AgentCommandDeliveryMode = Literal["dispatch_now_only", "queue_until_reconnect"]
AgentCommandAckStatus = Literal["accepted", "rejected"]
AgentCommandResultStatus = Literal["succeeded", "failed", "timeout", "cancelled"]
AgentCommandStatus = Literal[
    "pending",
    "dispatched",
    "accepted",
    "expired",
    "rejected",
    "succeeded",
    "failed",
    "timeout",
    "cancelled",
]
AgentSessionStatus = Literal["active", "disconnected", "superseded"]
AgentCommandSourceSurface = Literal[
    "unknown",
    "instance_detail",
    "service_detail_fanout",
    "task_detail",
]
ServiceCommandTargetMode = Literal["all_online", "selected_instances"]
ServiceCommandOfflineBehavior = Literal["skip", "queue"]
ServiceCommandFanoutOutcome = Literal["dispatched", "queued", "skipped", "rejected"]
UiStreamChannel = Literal["commands", "sessions"]
WorkerAgentExecutionMode = Literal["subprocess"]
WorkerAgentStatus = Literal["offline", "online", "unknown"]
WorkerReportingMode = Literal["platform", "custom"]
WorkerDeploymentDesiredStatus = Literal["running", "stopped"]
WorkerDeploymentObservedStatus = Literal[
    "pending",
    "assigned",
    "preparing",
    "installing",
    "checking",
    "running",
    "stopping",
    "stopped",
    "failed",
    "cancelled",
]
WorkerAgentWsMessageType = Literal[
    "hello",
    "hello_ack",
    "heartbeat",
    "deployment_event",
    "command",
    "command_ack",
    "command_result",
    "error",
]
WorkerAgentCommandKind = Literal[
    "start_deployment",
    "stop_deployment",
    "restart_deployment",
    "sync_agent_state",
]
WorkerAgentCommandAckStatus = Literal["accepted", "rejected"]
WorkerAgentCommandResultStatus = Literal["succeeded", "failed", "timeout", "cancelled"]
WsMessageType = Literal[
    "hello",
    "hello_ack",
    "telemetry",
    "command",
    "command_ack",
    "command_result",
    "error",
]

TASK_SCOPED_COMMAND_KINDS = frozenset(
    {
        "pause_task",
        "resume_task",
        "restart_task",
        "discard_dead_letters",
        "replay_dead_letters",
        "run_task_once",
    }
)
REASON_REQUIRED_COMMAND_KINDS = frozenset(
    {
        "shutdown",
        "restart",
        "drain",
        "pause_task",
        "resume_task",
        "restart_task",
        "discard_dead_letters",
        "replay_dead_letters",
        "run_task_once",
    }
)
LIVE_ONLY_COMMAND_KINDS = frozenset(
    {
        "shutdown",
        "restart",
        "drain",
        "pause_task",
        "resume_task",
        "discard_dead_letters",
        "replay_dead_letters",
        "run_task_once",
    }
)
NOTIFICATION_EVENT_TYPES = frozenset(
    {
        "task_started",
        "task_succeeded",
        "task_failed",
        "task_missed_start",
        "instance_online",
        "instance_offline",
    }
)
NOTIFICATION_EVENT_TYPES_REQUIRING_GRACE_PERIOD = frozenset({"task_missed_start"})
CUSTOM_METRIC_NAME_RE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
CUSTOM_METRIC_LABEL_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
CUSTOM_METRIC_RESERVED_LABELS = frozenset(
    {"service", "environment", "task", "instance_id", "metric"}
)


def _normalize_task_command_args(
    kind: AgentCommandKind | ServiceCommandFanoutKind,
    args: dict[str, Any],
    *,
    require_task_name: bool = True,
) -> dict[str, Any]:
    normalized_args = dict(args)
    if kind not in TASK_SCOPED_COMMAND_KINDS:
        return normalized_args
    task_name = normalized_args.get("task_name")
    if require_task_name and (not isinstance(task_name, str) or not task_name.strip()):
        raise ValueError(f"args.task_name is required when kind={kind}")
    if isinstance(task_name, str) and task_name.strip():
        normalized_args["task_name"] = task_name.strip()
    if kind in {"discard_dead_letters", "replay_dead_letters"} and "limit" in normalized_args:
        limit = normalized_args["limit"]
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError(f"args.limit must be a positive integer when kind={kind}")
    if kind == "run_task_once":
        payload = normalized_args.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("args.payload must be a JSON object when kind=run_task_once")
    return normalized_args


class ServiceDescriptor(APIModel):
    name: str = Field(min_length=1, max_length=255)
    environment: Environment
    description: str | None = None
    node_name: str = Field(min_length=1, max_length=255)
    instance_id: UUID
    deployment_version: str = Field(min_length=1, max_length=128)

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


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


class TaskControlStateDescriptor(APIModel):
    task_name: str = Field(min_length=1, max_length=255)
    supported_commands: list[TaskCommandKind] = Field(default_factory=list)
    pause_requested: bool = False
    paused: bool = False
    accepting_new_work: bool = True
    runner_count: int = Field(default=0, ge=0)
    parked_runner_count: int = Field(default=0, ge=0)
    fetching_runner_count: int = Field(default=0, ge=0)
    inflight_task_count: int = Field(default=0, ge=0)


class HealthDescriptor(APIModel):
    status: HealthStatus
    uptime_s: int = Field(ge=0)
    inflight_tasks: int = Field(ge=0)
    task_controls: list[TaskControlStateDescriptor] = Field(default_factory=list)


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


class CustomMetricIngest(APIModel):
    name: str = Field(min_length=1, max_length=255)
    kind: CustomMetricKind
    value: float
    labels: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or not CUSTOM_METRIC_NAME_RE.match(normalized):
            raise ValueError("custom metric name must be a valid Prometheus metric name")
        return normalized

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("custom metric value must be finite")
        return value

    @field_validator("labels")
    @classmethod
    def validate_labels(cls, value: dict[str, str]) -> dict[str, str]:
        labels: dict[str, str] = {}
        for raw_key, raw_value in value.items():
            key = raw_key.strip()
            if not key or not CUSTOM_METRIC_LABEL_RE.match(key):
                raise ValueError("custom metric label keys must be valid Prometheus labels")
            if key in CUSTOM_METRIC_RESERVED_LABELS:
                raise ValueError(f"custom metric label {key!r} is reserved")
            labels[key] = str(raw_value)
        return labels

    @model_validator(mode="after")
    def validate_counter_value(self) -> CustomMetricIngest:
        if self.kind == "counter" and self.value < 0:
            raise ValueError("custom counter metric value must be >= 0")
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
    custom_metrics: list[CustomMetricIngest] = Field(default_factory=list)


class MetricsIngestRequest(IngestionEnvelope):
    window: MetricsWindow
    tasks: list[TaskMetricWindowIngest] = Field(min_length=1)


class TaskFailureDescriptor(APIModel):
    kind: str = Field(min_length=1, max_length=64)
    exception_type: str | None = Field(default=None, max_length=255)
    message: str | None = None
    traceback: str | None = None


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
    description: str | None = None
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


class AgentHelloPayload(APIModel):
    protocol_version: str = Field(min_length=1, max_length=16)
    capabilities: list[str] = Field(default_factory=list)
    service: ServiceDescriptor
    runtime: RuntimeDescriptor


class AgentHelloMessage(APIModel):
    type: Literal["hello"]
    message_id: str = Field(min_length=1, max_length=255)
    sent_at: datetime
    payload: AgentHelloPayload


class AgentHelloAckPayload(APIModel):
    session_id: str = Field(min_length=1, max_length=255)
    protocol_version: str = Field(min_length=1, max_length=16)
    heartbeat_interval_s: int = Field(ge=1)
    accepted_capabilities: list[str] = Field(default_factory=list)
    server_time: datetime


class AgentHelloAckMessage(APIModel):
    type: Literal["hello_ack"]
    message_id: str = Field(min_length=1, max_length=255)
    sent_at: datetime
    payload: AgentHelloAckPayload


class AgentTelemetryPayload(APIModel):
    channel: TelemetryChannel
    body: dict[str, Any]


class AgentTelemetryMessage(APIModel):
    type: Literal["telemetry"]
    message_id: str = Field(min_length=1, max_length=255)
    sent_at: datetime
    payload: AgentTelemetryPayload


class AgentCommandPayload(APIModel):
    command_id: str = Field(min_length=1, max_length=255)
    kind: AgentCommandKind
    args: dict[str, Any] = Field(default_factory=dict)
    timeout_s: int = Field(ge=1)
    created_at: datetime


class AgentCommandMessage(APIModel):
    type: Literal["command"]
    message_id: str = Field(min_length=1, max_length=255)
    sent_at: datetime
    payload: AgentCommandPayload


class AgentCommandAckPayload(APIModel):
    command_id: str = Field(min_length=1, max_length=255)
    status: AgentCommandAckStatus
    received_at: datetime
    error_code: str | None = Field(default=None, max_length=128)
    error_message: str | None = None


class AgentCommandAckMessage(APIModel):
    type: Literal["command_ack"]
    message_id: str = Field(min_length=1, max_length=255)
    sent_at: datetime
    payload: AgentCommandAckPayload


class AgentCommandResultPayload(APIModel):
    command_id: str = Field(min_length=1, max_length=255)
    status: AgentCommandResultStatus
    finished_at: datetime
    result: dict[str, Any] | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    error_code: str | None = Field(default=None, max_length=128)
    error_message: str | None = None


class AgentCommandResultMessage(APIModel):
    type: Literal["command_result"]
    message_id: str = Field(min_length=1, max_length=255)
    sent_at: datetime
    payload: AgentCommandResultPayload


class AgentErrorPayload(APIModel):
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1)
    close_connection: bool = False


class AgentErrorMessage(APIModel):
    type: Literal["error"]
    message_id: str = Field(min_length=1, max_length=255)
    sent_at: datetime
    payload: AgentErrorPayload


class AgentWsEnvelope(APIModel):
    type: WsMessageType
    message_id: str = Field(min_length=1, max_length=255)
    sent_at: datetime
    payload: dict[str, Any]


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
    bootstrap_required: bool = False
    authenticated: bool
    username: str | None = None
    role: ConsoleRole | None = None
    roles: list[ConsoleRole] = Field(default_factory=list)


class PaginatedResponse(APIModel):
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class WorkerAgentRegistrationRequest(APIModel):
    registration_token: str = Field(min_length=1)
    display_name: str = Field(min_length=1, max_length=255)
    execution_mode: WorkerAgentExecutionMode = "subprocess"
    max_concurrent_deployments: int = Field(ge=1)
    labels: dict[str, str] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)
    agent_version: str | None = Field(default=None, max_length=64)
    onestep_version: str | None = Field(default=None, max_length=64)
    python_version: str | None = Field(default=None, max_length=64)
    platform: dict[str, Any] = Field(default_factory=dict)

    @field_validator("display_name", mode="before")
    @classmethod
    def normalize_display_name(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value


class WorkerAgentRegistrationResponse(APIModel):
    worker_agent_id: UUID
    connection_token: str
    heartbeat_interval_s: int = Field(ge=1)
    accepted_capabilities: list[str] = Field(default_factory=list)


class WorkerAgentSummary(APIModel):
    worker_agent_id: UUID
    display_name: str
    status: WorkerAgentStatus
    execution_mode: WorkerAgentExecutionMode
    max_concurrent_deployments: int = Field(ge=1)
    used_slots: int = Field(ge=0)
    labels: dict[str, str] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)
    agent_version: str | None = None
    onestep_version: str | None = None
    python_version: str | None = None
    platform: dict[str, Any] = Field(default_factory=dict)
    registered_at: datetime
    last_seen_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class WorkerAgentListResponse(PaginatedResponse):
    items: list[WorkerAgentSummary]


class WorkflowPackageSummary(APIModel):
    package_id: UUID
    workflow_id: UUID
    version: str
    filename: str
    content_type: str
    checksum_sha256: str
    size_bytes: int = Field(ge=0)
    entrypoint: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_by: str
    created_at: datetime


class WorkerDeploymentCreateRequest(APIModel):
    workflow_package_id: UUID
    worker_agent_id: UUID
    desired_status: WorkerDeploymentDesiredStatus = "running"
    params: dict[str, Any] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    credential_refs: list[str] = Field(default_factory=list)


class WorkerDeploymentSummary(APIModel):
    deployment_id: UUID
    workflow_package_id: UUID
    worker_agent_id: UUID
    desired_status: WorkerDeploymentDesiredStatus
    observed_status: WorkerDeploymentObservedStatus
    runtime_instance_id: UUID | None = None
    execution_mode: WorkerAgentExecutionMode
    params: dict[str, Any] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    credential_refs: list[str] = Field(default_factory=list)
    package_checksum: str
    last_error_code: str | None = None
    last_error_message: str | None = None
    assigned_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_by: str
    created_at: datetime
    updated_at: datetime


class WorkerDeploymentListResponse(PaginatedResponse):
    items: list[WorkerDeploymentSummary]


class WorkerDeploymentEventSummary(APIModel):
    deployment_id: UUID
    worker_agent_id: UUID
    event_type: str
    observed_status: WorkerDeploymentObservedStatus | None = None
    message: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class WorkerDeploymentEventListResponse(PaginatedResponse):
    items: list[WorkerDeploymentEventSummary]


class WorkerAgentHelloPayload(APIModel):
    protocol_version: str = Field(min_length=1, max_length=16)
    worker_agent_id: UUID
    capabilities: list[str] = Field(default_factory=list)
    max_concurrent_deployments: int = Field(ge=1)
    used_slots: int = Field(ge=0)
    running_deployments: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_used_slots(self) -> WorkerAgentHelloPayload:
        if self.used_slots > self.max_concurrent_deployments:
            raise ValueError("used_slots must not exceed max_concurrent_deployments")
        return self


class WorkerAgentHelloMessage(APIModel):
    type: Literal["hello"]
    message_id: str = Field(min_length=1, max_length=255)
    sent_at: datetime
    payload: WorkerAgentHelloPayload


class WorkerAgentHelloAckPayload(APIModel):
    session_id: str = Field(min_length=1, max_length=255)
    protocol_version: str = Field(min_length=1, max_length=16)
    heartbeat_interval_s: int = Field(ge=1)
    accepted_capabilities: list[str] = Field(default_factory=list)
    server_time: datetime


class WorkerAgentHelloAckMessage(APIModel):
    type: Literal["hello_ack"]
    message_id: str = Field(min_length=1, max_length=255)
    sent_at: datetime
    payload: WorkerAgentHelloAckPayload


class WorkerAgentHeartbeatPayload(APIModel):
    worker_agent_id: UUID
    used_slots: int = Field(ge=0)
    running_deployments: list[UUID] = Field(default_factory=list)
    recent_errors: list[str] = Field(default_factory=list)


class WorkerAgentHeartbeatMessage(APIModel):
    type: Literal["heartbeat"]
    message_id: str = Field(min_length=1, max_length=255)
    sent_at: datetime
    payload: WorkerAgentHeartbeatPayload


class WorkerDeploymentEventPayload(APIModel):
    deployment_id: UUID
    event_type: str = Field(min_length=1, max_length=64)
    observed_status: WorkerDeploymentObservedStatus | None = None
    message: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class WorkerDeploymentEventMessage(APIModel):
    type: Literal["deployment_event"]
    message_id: str = Field(min_length=1, max_length=255)
    sent_at: datetime
    payload: WorkerDeploymentEventPayload


class WorkerAgentCommandPayload(APIModel):
    command_id: UUID
    kind: WorkerAgentCommandKind
    deployment_id: UUID | None = None
    timeout_s: int = Field(ge=1)
    args: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class WorkerAgentCommandMessage(APIModel):
    type: Literal["command"]
    message_id: str = Field(min_length=1, max_length=255)
    sent_at: datetime
    payload: WorkerAgentCommandPayload


class WorkerAgentCommandAckPayload(APIModel):
    command_id: UUID
    status: WorkerAgentCommandAckStatus
    error_code: str | None = Field(default=None, max_length=128)
    error_message: str | None = None


class WorkerAgentCommandAckMessage(APIModel):
    type: Literal["command_ack"]
    message_id: str = Field(min_length=1, max_length=255)
    sent_at: datetime
    payload: WorkerAgentCommandAckPayload


class WorkerAgentCommandResultPayload(APIModel):
    command_id: UUID
    status: WorkerAgentCommandResultStatus
    result: dict[str, Any] | None = None
    error_code: str | None = Field(default=None, max_length=128)
    error_message: str | None = None
    finished_at: datetime


class WorkerAgentCommandResultMessage(APIModel):
    type: Literal["command_result"]
    message_id: str = Field(min_length=1, max_length=255)
    sent_at: datetime
    payload: WorkerAgentCommandResultPayload


class WorkerAgentErrorMessage(APIModel):
    type: Literal["error"]
    message_id: str = Field(min_length=1, max_length=255)
    sent_at: datetime
    payload: AgentErrorPayload


class WorkerAgentWsEnvelope(APIModel):
    type: WorkerAgentWsMessageType
    message_id: str = Field(min_length=1, max_length=255)
    sent_at: datetime
    payload: dict[str, Any]


class AgentCommandCreateRequest(APIModel):
    kind: AgentCommandKind
    args: dict[str, Any] = Field(default_factory=dict)
    timeout_s: int = Field(default=10, ge=1)
    delivery_mode: AgentCommandDeliveryMode = "dispatch_now_only"
    reason: str | None = Field(default=None, max_length=2000)

    @field_validator("reason", mode="before")
    @classmethod
    def normalize_reason(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @model_validator(mode="after")
    def validate_reason_requirement(self) -> AgentCommandCreateRequest:
        self.args = _normalize_task_command_args(self.kind, self.args)
        if self.kind in REASON_REQUIRED_COMMAND_KINDS and self.reason is None:
            raise ValueError(f"reason is required when kind={self.kind}")
        if self.kind in LIVE_ONLY_COMMAND_KINDS and self.delivery_mode == "queue_until_reconnect":
            raise ValueError(
                "delivery_mode=queue_until_reconnect is not supported when "
                f"kind={self.kind}"
            )
        return self


class ServiceCommandFanoutRequest(APIModel):
    kind: ServiceCommandFanoutKind
    args: dict[str, Any] = Field(default_factory=dict)
    timeout_s: int = Field(default=10, ge=1)
    reason: str = Field(min_length=1, max_length=2000)
    target_mode: ServiceCommandTargetMode = "all_online"
    target_instance_ids: list[UUID] = Field(default_factory=list)
    offline_behavior: ServiceCommandOfflineBehavior = "skip"

    @field_validator("reason", mode="before")
    @classmethod
    def normalize_bulk_reason(cls, value: Any) -> str:
        if not isinstance(value, str):
            return value
        return value.strip()

    @model_validator(mode="after")
    def validate_target_selection(self) -> ServiceCommandFanoutRequest:
        if not self.reason:
            raise ValueError("reason must not be blank")
        self.args = _normalize_task_command_args(self.kind, self.args)
        if self.kind in LIVE_ONLY_COMMAND_KINDS and self.offline_behavior == "queue":
            raise ValueError(
                "offline_behavior=queue is not supported when "
                f"kind={self.kind}"
            )
        if self.target_mode == "selected_instances" and not self.target_instance_ids:
            raise ValueError(
                "target_instance_ids must be provided when "
                "target_mode=selected_instances"
            )
        if self.target_mode == "all_online" and self.target_instance_ids:
            raise ValueError("target_instance_ids must be empty when target_mode=all_online")
        if len(self.target_instance_ids) != len(set(self.target_instance_ids)):
            raise ValueError("target_instance_ids must be unique")
        return self


class TaskCommandFanoutRequest(APIModel):
    kind: TaskCommandKind
    args: dict[str, Any] = Field(default_factory=dict)
    timeout_s: int = Field(default=10, ge=1)
    reason: str = Field(min_length=1, max_length=2000)
    target_mode: ServiceCommandTargetMode = "all_online"
    target_instance_ids: list[UUID] = Field(default_factory=list)
    offline_behavior: ServiceCommandOfflineBehavior = "skip"

    @field_validator("reason", mode="before")
    @classmethod
    def normalize_reason(cls, value: Any) -> str:
        if not isinstance(value, str):
            return value
        return value.strip()

    @model_validator(mode="after")
    def validate_target_selection(self) -> TaskCommandFanoutRequest:
        if not self.reason:
            raise ValueError("reason must not be blank")
        self.args = _normalize_task_command_args(self.kind, self.args, require_task_name=False)
        if self.offline_behavior == "queue":
            raise ValueError(
                "offline_behavior=queue is not supported when "
                f"kind={self.kind}"
            )
        if self.kind == "run_task_once" and self.target_mode != "selected_instances":
            raise ValueError(
                "target_mode=selected_instances is required when kind=run_task_once"
            )
        if self.target_mode == "selected_instances" and not self.target_instance_ids:
            raise ValueError(
                "target_instance_ids must be provided when "
                "target_mode=selected_instances"
            )
        if self.target_mode == "all_online" and self.target_instance_ids:
            raise ValueError("target_instance_ids must be empty when target_mode=all_online")
        if len(self.target_instance_ids) != len(set(self.target_instance_ids)):
            raise ValueError("target_instance_ids must be unique")
        return self


class AgentCommandSummary(APIModel):
    command_id: str
    instance_id: UUID
    node_name: str | None = None
    session_id: str | None = None
    created_by: str | None = None
    reason: str | None = None
    source_surface: AgentCommandSourceSurface
    kind: AgentCommandKind
    args: dict[str, Any] = Field(default_factory=dict)
    timeout_s: int = Field(ge=1)
    status: AgentCommandStatus
    ack_status: AgentCommandAckStatus | None = None
    result: dict[str, Any] | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    dispatched_at: datetime | None = None
    acked_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime


class AgentCommandListResponse(PaginatedResponse):
    items: list[AgentCommandSummary]


class ServiceCommandFanoutTargetSummary(APIModel):
    instance_id: UUID
    node_name: str | None = None
    connectivity: InstanceConnectivity
    session_id: str | None = None
    command_id: str | None = None
    outcome: ServiceCommandFanoutOutcome
    reason_code: str | None = None
    reason_message: str | None = None


class ServiceCommandFanoutCounts(APIModel):
    dispatched: int = Field(default=0, ge=0)
    queued: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    rejected: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)


class ServiceCommandFanoutResponse(APIModel):
    kind: ServiceCommandFanoutKind
    target_mode: ServiceCommandTargetMode
    offline_behavior: ServiceCommandOfflineBehavior
    noop_reason_code: str | None = None
    noop_reason_message: str | None = None
    counts: ServiceCommandFanoutCounts
    dispatched: list[ServiceCommandFanoutTargetSummary] = Field(default_factory=list)
    queued: list[ServiceCommandFanoutTargetSummary] = Field(default_factory=list)
    skipped: list[ServiceCommandFanoutTargetSummary] = Field(default_factory=list)
    rejected: list[ServiceCommandFanoutTargetSummary] = Field(default_factory=list)


class AgentCommandStatusCounts(APIModel):
    pending: int = Field(default=0, ge=0)
    dispatched: int = Field(default=0, ge=0)
    accepted: int = Field(default=0, ge=0)
    expired: int = Field(default=0, ge=0)
    rejected: int = Field(default=0, ge=0)
    succeeded: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    timeout: int = Field(default=0, ge=0)
    cancelled: int = Field(default=0, ge=0)
    in_flight: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)


class AgentCommandOverview(APIModel):
    statuses: AgentCommandStatusCounts
    active_session_count: int = Field(default=0, ge=0)
    last_command_at: datetime | None = None
    last_completed_at: datetime | None = None


class AgentSessionSummary(APIModel):
    session_id: str
    instance_id: UUID
    node_name: str | None = None
    hostname: str | None = None
    status: AgentSessionStatus
    protocol_version: str
    capabilities: list[str] = Field(default_factory=list)
    accepted_capabilities: list[str] = Field(default_factory=list)
    connected_at: datetime
    last_hello_at: datetime
    last_message_at: datetime
    superseded_at: datetime | None = None
    disconnected_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AgentSessionListResponse(PaginatedResponse):
    items: list[AgentSessionSummary]


class ServiceSummary(APIModel):
    name: str
    environment: Environment
    description: str | None = None
    latest_deployment_version: str
    service_status: ServiceListStatus
    latest_topology_hash: str | None = None
    latest_sync_at: datetime | None = None
    instance_count: int = Field(ge=0)
    online_instance_count: int = Field(ge=0)
    last_seen_at: datetime | None = None
    source_kinds: list[str] = Field(default_factory=list)
    task_count: int = Field(default=0, ge=0)
    failing_task_count: int = Field(default=0, ge=0)
    # ---- Derived view-facing fields (computed by the plane) ----
    view_status: ServiceViewStatus
    success_rate: float = Field(ge=0, le=100)
    throughput_per_min: int = Field(default=0, ge=0)
    error_count: int = Field(default=0, ge=0)
    uptime_reference_at: datetime | None = None
    online_task_count: int = Field(default=0, ge=0)
    standby_instance_count: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime


class ServiceListSummary(APIModel):
    total_services: int = Field(ge=0)
    online_services: int = Field(ge=0)
    attention_services: int = Field(ge=0)
    offline_services: int = Field(ge=0)
    ready_services: int = Field(ge=0)
    total_instances: int = Field(ge=0)
    online_instances: int = Field(ge=0)
    total_tasks: int = Field(default=0, ge=0)
    failing_tasks: int = Field(default=0, ge=0)


class ServiceListResponse(PaginatedResponse):
    items: list[ServiceSummary]
    source_kind_counts: dict[str, int] = Field(default_factory=dict)
    summary: ServiceListSummary


class NotificationServiceScope(APIModel):
    name: str = Field(min_length=1, max_length=255)
    environment: Environment

    @field_validator("name", mode="before")
    @classmethod
    def normalize_service_scope_name(cls, value: Any) -> str:
        if not isinstance(value, str):
            return value
        return value.strip()


NotificationWebhookMethod = Literal["GET", "POST"]


class NotificationCustomParam(APIModel):
    key: str = Field(min_length=1, max_length=255)
    value: str = Field(default="", max_length=2000)

    @field_validator("key", "value", mode="before")
    @classmethod
    def normalize_param_strings(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        return value.strip()

    @field_validator("value")
    @classmethod
    def validate_template_value(cls, value: str) -> str:
        validate_custom_template_value(value)
        return value


class NotificationCustomConfig(APIModel):
    method: NotificationWebhookMethod = "POST"
    query_params: list[NotificationCustomParam] = Field(default_factory=list)
    body_params: list[NotificationCustomParam] = Field(default_factory=list)

    @field_validator("method", mode="before")
    @classmethod
    def normalize_method(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        return value.strip().upper()

    @staticmethod
    def _validate_unique_keys(params: list[NotificationCustomParam], field_name: str) -> None:
        keys = [param.key for param in params]
        if len(set(keys)) != len(keys):
            raise ValueError(f"{field_name} keys must be unique")

    @model_validator(mode="after")
    def validate_custom_config(self) -> NotificationCustomConfig:
        self._validate_unique_keys(self.query_params, "query_params")
        self._validate_unique_keys(self.body_params, "body_params")
        if self.method == "GET" and self.body_params:
            raise ValueError("body_params are only supported for POST custom webhooks")
        return self


class NotificationChannelBase(APIModel):
    name: str = Field(min_length=1, max_length=255)
    provider: NotificationProvider
    webhook_url: str = Field(min_length=1)
    enabled: bool = True
    service_scopes: list[NotificationServiceScope] = Field(default_factory=list)
    event_types: list[NotificationEventType] = Field(default_factory=list)
    missed_start_grace_seconds: int = Field(default=300, ge=1, le=86400)
    custom_config: NotificationCustomConfig | None = None

    @field_validator("name", "webhook_url", mode="before")
    @classmethod
    def normalize_non_empty_strings(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        return value.strip()

    @field_validator("service_scopes")
    @classmethod
    def validate_service_scopes_unique(
        cls,
        value: list[NotificationServiceScope],
    ) -> list[NotificationServiceScope]:
        unique_keys = {(scope.name, scope.environment) for scope in value}
        if len(unique_keys) != len(value):
            raise ValueError("service_scopes must be unique by name and environment")
        return value

    @field_validator("event_types")
    @classmethod
    def validate_event_types_unique(
        cls,
        value: list[NotificationEventType],
    ) -> list[NotificationEventType]:
        if len(set(value)) != len(value):
            raise ValueError("event_types must be unique")
        return value

    @model_validator(mode="after")
    def validate_missed_start_grace_usage(self) -> NotificationChannelBase:
        if (
            "task_missed_start" not in self.event_types
            and self.missed_start_grace_seconds != 300
        ):
            raise ValueError(
                "missed_start_grace_seconds can only be customized when "
                "event_types includes task_missed_start"
            )
        return self

    @model_validator(mode="after")
    def validate_provider_custom_config(self) -> NotificationChannelBase:
        if self.provider == "custom" and self.custom_config is None:
            raise ValueError("custom_config is required when provider is custom")
        if self.provider != "custom" and self.custom_config is not None:
            raise ValueError("custom_config is only supported when provider is custom")
        return self


class NotificationChannelCreateRequest(NotificationChannelBase):
    pass


class NotificationChannelUpdateRequest(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    provider: NotificationProvider | None = None
    webhook_url: str | None = Field(default=None, min_length=1)
    enabled: bool | None = None
    service_scopes: list[NotificationServiceScope] | None = None
    event_types: list[NotificationEventType] | None = None
    missed_start_grace_seconds: int | None = Field(default=None, ge=1, le=86400)
    custom_config: NotificationCustomConfig | None = None

    @field_validator("name", "webhook_url", mode="before")
    @classmethod
    def normalize_optional_strings(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        return value.strip()

    @field_validator("service_scopes")
    @classmethod
    def validate_optional_service_scopes(
        cls,
        value: list[NotificationServiceScope] | None,
    ) -> list[NotificationServiceScope] | None:
        if value is None:
            return value
        unique_keys = {(scope.name, scope.environment) for scope in value}
        if len(unique_keys) != len(value):
            raise ValueError("service_scopes must be unique by name and environment")
        return value

    @field_validator("event_types")
    @classmethod
    def validate_optional_event_types(
        cls,
        value: list[NotificationEventType] | None,
    ) -> list[NotificationEventType] | None:
        if value is None:
            return value
        if len(set(value)) != len(value):
            raise ValueError("event_types must be unique")
        return value

    @model_validator(mode="after")
    def validate_non_empty_patch(self) -> NotificationChannelUpdateRequest:
        if not any(
            getattr(self, field_name) is not None
            for field_name in self.__class__.model_fields
        ):
            raise ValueError("at least one field must be provided")
        return self


class NotificationChannelEnabledPatchRequest(APIModel):
    enabled: bool


class NotificationChannelSummary(APIModel):
    id: UUID
    name: str
    provider: NotificationProvider
    webhook_url_masked: str
    enabled: bool
    service_scopes: list[NotificationServiceScope] = Field(default_factory=list)
    event_types: list[NotificationEventType] = Field(default_factory=list)
    missed_start_grace_seconds: int = Field(ge=1)
    custom_config: NotificationCustomConfig | None = None
    created_at: datetime
    updated_at: datetime


class NotificationChannelListResponse(APIModel):
    items: list[NotificationChannelSummary]


class NotificationServiceOption(APIModel):
    name: str
    environment: Environment


class NotificationServiceListResponse(APIModel):
    items: list[NotificationServiceOption]


class NotificationChannelDeleteResponse(APIModel):
    status: Literal["deleted"] = "deleted"


class NotificationTestRequest(APIModel):
    message: str | None = Field(default=None, max_length=2000)

    @field_validator("message", mode="before")
    @classmethod
    def normalize_message(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        return stripped or None


class NotificationTestResponse(APIModel):
    status: Literal["accepted"] = "accepted"
    channel_id: UUID
    provider: NotificationProvider
    preview_text: str


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
    active_session: AgentSessionSummary | None = None
    # Derived view-facing status mapped from HealthStatus.
    view_status: InstanceViewStatus
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


class TaskMetricChartPointSummary(APIModel):
    bucket_started_at: datetime
    bucket_ended_at: datetime
    reported_window_count: int = Field(ge=0)
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


class TaskMetricWindowListResponse(PaginatedResponse):
    items: list[TaskMetricWindowSummary]


class TaskEventCounts(APIModel):
    started: int = Field(default=0, ge=0)
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
    traceback: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    received_at: datetime
    created_at: datetime
    # Derived log level mapped from kind (failed/dead_lettered -> error,
    # retried -> warn, otherwise info).
    level: EventLogLevel = "info"


class TaskEventListResponse(PaginatedResponse):
    items: list[TaskEventSummary]


TaskEventHistorySource = Literal["runtime", "command"]


class TaskEventHistoryItem(APIModel):
    id: str
    source_type: TaskEventHistorySource
    instance_id: UUID
    task_name: str
    kind: str
    occurred_at: datetime
    level: EventLogLevel = "info"
    message: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    attempts: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    failure_kind: str | None = None
    exception_type: str | None = None
    traceback: str | None = None
    command_id: str | None = None
    command_status: AgentCommandStatus | None = None
    ack_status: AgentCommandAckStatus | None = None
    created_at: datetime
    updated_at: datetime | None = None


class TaskEventHistoryListResponse(PaginatedResponse):
    lookback_minutes: int = Field(ge=1)
    lookback_started_at: datetime
    items: list[TaskEventHistoryItem]


class RecentEventSummary(TaskEventSummary):
    service_name: str
    environment: Environment


class RecentEventListResponse(PaginatedResponse):
    items: list[RecentEventSummary]


class TaskDashboardSummary(APIModel):
    task_name: str
    description: str | None = None
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
    # Aggregated across this service's online instances: True when any instance
    # reported pause_requested=true for this task; False when all known instances
    # reported false; None when no instance reported a known state. Drives the
    # "paused" UI status on the dashboard list (distinct from per-instance detail).
    pause_requested: bool | None = None
    supported_commands: list[TaskCommandKind] = Field(default_factory=list)
    # ---- Derived view-facing fields (computed by the plane) ----
    view_status: TaskViewStatus = "idle"
    success_rate: float = Field(default=100, ge=0, le=100)
    throughput_per_min: int = Field(default=0, ge=0)
    error_count: int = Field(default=0, ge=0)
    retry_attempts: int = Field(default=0, ge=0)
    source_label: str | None = None
    sink_label: str | None = None
    config_yaml: str | None = None
    uptime_reference_at: datetime | None = None


class TaskDashboardListResponse(PaginatedResponse):
    lookback_minutes: int = Field(ge=1)
    lookback_started_at: datetime
    items: list[TaskDashboardSummary]


class TaskInstanceControlState(APIModel):
    instance_id: UUID
    node_name: str
    connectivity: InstanceConnectivity
    status: HealthStatus
    last_seen_at: datetime | None = None
    supported_commands: list[TaskCommandKind] = Field(default_factory=list)
    state_known: bool
    pause_requested: bool | None = None
    paused: bool | None = None
    accepting_new_work: bool | None = None
    runner_count: int | None = Field(default=None, ge=0)
    parked_runner_count: int | None = Field(default=None, ge=0)
    fetching_runner_count: int | None = Field(default=None, ge=0)
    inflight_task_count: int | None = Field(default=None, ge=0)


class TaskControlStateSummary(APIModel):
    task_name: str
    instances: list[TaskInstanceControlState] = Field(default_factory=list)


class TaskDetailResponse(APIModel):
    service: ServiceSummary
    task_name: str
    lookback_minutes: int = Field(ge=1)
    lookback_started_at: datetime
    summary: TaskDashboardSummary
    task_control: TaskControlStateSummary
    recent_metric_points: list[TaskMetricChartPointSummary]
    recent_metric_windows: list[TaskMetricWindowSummary]
    recent_events: list[TaskEventSummary]


class InstanceDetailResponse(APIModel):
    service: ServiceSummary
    lookback_minutes: int = Field(ge=1)
    lookback_started_at: datetime
    instance: InstanceSummary
    latest_session: AgentSessionSummary | None = None
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
    command_overview: AgentCommandOverview
    topology_hashes: list[str] = Field(default_factory=list)
    topology_consistent: bool
    recent_events: list[TaskEventSummary]


class UiStreamEvent(APIModel):
    channel: UiStreamChannel
    published_at: datetime


class ConnectorSummary(APIModel):
    id: str
    name: str
    type: str
    config: dict[str, object]
    secret: dict[str, object]
    created_at: str | None = None
    updated_at: str | None = None


class ConnectorListResponse(APIModel):
    items: list[ConnectorSummary]
    total: int


class ConnectorCreateRequest(APIModel):
    name: str
    type: str
    config: dict[str, object] = Field(default_factory=dict)
    secret: dict[str, object] = Field(default_factory=dict)


class ConnectorUpdateRequest(APIModel):
    name: str | None = None
    config: dict[str, object] | None = None
    secret: dict[str, object] | None = None


class WorkerSourceConfig(APIModel):
    type: str
    connector_id: str | None = None
    fields: dict[str, object] = Field(default_factory=dict)


class WorkerSinkConfig(APIModel):
    type: str
    connector_id: str | None = None
    fields: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def apply_type_defaults(self) -> WorkerSinkConfig:
        if self.type == "http_sink" and not str(self.fields.get("method") or "").strip():
            self.fields = {**self.fields, "method": "POST"}
        return self


class WorkerReportingConfig(APIModel):
    mode: WorkerReportingMode = "platform"
    endpoint_url: str | None = None

    @model_validator(mode="after")
    def normalize_endpoint(self) -> WorkerReportingConfig:
        if self.endpoint_url is not None:
            self.endpoint_url = self.endpoint_url.strip() or None
        return self


class WorkerReportingSecret(APIModel):
    token: str | None = None


class WorkerSummary(APIModel):
    id: str
    name: str
    description: str
    handler_package_id: str | None = None
    handler_ref: str
    source_config: WorkerSourceConfig
    sink_configs: list[WorkerSinkConfig]
    env: dict[str, str] = Field(default_factory=dict)
    reporting_enabled: bool = True
    reporting_config: WorkerReportingConfig = Field(default_factory=WorkerReportingConfig)
    reporting_token_configured: bool = False
    status: str
    created_at: str | None = None
    updated_at: str | None = None


class WorkerListResponse(APIModel):
    items: list[WorkerSummary]
    total: int


class WorkerCreateRequest(APIModel):
    name: str
    description: str = ""
    handler_package_id: str | None = None
    handler_ref: str = "handler:handler"
    source_config: WorkerSourceConfig = Field(
        default_factory=lambda: WorkerSourceConfig(type="interval")
    )
    sink_configs: list[WorkerSinkConfig] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    reporting_enabled: bool = True
    reporting_config: WorkerReportingConfig = Field(default_factory=WorkerReportingConfig)
    reporting_secret: WorkerReportingSecret | None = None


class WorkerUpdateRequest(APIModel):
    name: str | None = None
    description: str | None = None
    handler_package_id: str | None = None
    handler_ref: str | None = None
    source_config: WorkerSourceConfig | None = None
    sink_configs: list[WorkerSinkConfig] | None = None
    env: dict[str, str] | None = None
    reporting_enabled: bool | None = None
    reporting_config: WorkerReportingConfig | None = None
    reporting_secret: WorkerReportingSecret | None = None
    status: str | None = None


class WorkerDeployRequest(APIModel):
    worker_agent_id: str
    desired_status: str = "running"
    env: dict[str, str] | None = None
