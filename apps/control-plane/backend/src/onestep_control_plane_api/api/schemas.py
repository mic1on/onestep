from __future__ import annotations

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
TaskEventKind = Literal["failed", "retried", "dead_lettered", "cancelled", "succeeded"]
InstanceConnectivity = Literal["online", "offline", "never_reported"]
TelemetryChannel = Literal["sync", "heartbeat", "metrics", "events"]
AgentCommandKind = Literal[
    "ping",
    "shutdown",
    "restart",
    "drain",
    "pause_task",
    "resume_task",
    "discard_dead_letters",
    "replay_dead_letters",
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
    "discard_dead_letters",
    "replay_dead_letters",
    "sync_now",
    "flush_metrics",
    "flush_events",
]
TaskCommandKind = Literal[
    "pause_task",
    "resume_task",
    "discard_dead_letters",
    "replay_dead_letters",
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
    {"pause_task", "resume_task", "discard_dead_letters", "replay_dead_letters"}
)
REASON_REQUIRED_COMMAND_KINDS = frozenset(
    {
        "shutdown",
        "restart",
        "drain",
        "pause_task",
        "resume_task",
        "discard_dead_letters",
        "replay_dead_letters",
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
    }
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
    return normalized_args


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
    authenticated: bool
    username: str | None = None


class PaginatedResponse(APIModel):
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


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
    active_session: AgentSessionSummary | None = None
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
    traceback: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    received_at: datetime
    created_at: datetime


class TaskEventListResponse(PaginatedResponse):
    items: list[TaskEventSummary]


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
