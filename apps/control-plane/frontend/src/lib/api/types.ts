export type Environment = "dev" | "staging" | "prod";
export type HealthStatus = "ok" | "degraded" | "error" | "starting" | "unknown";
export type InstanceConnectivity = "online" | "offline" | "never_reported";
export type AgentCommandKind =
  | "ping"
  | "shutdown"
  | "restart"
  | "drain"
  | "pause_task"
  | "resume_task"
  | "discard_dead_letters"
  | "replay_dead_letters"
  | "sync_now"
  | "flush_metrics"
  | "flush_events";
export type TaskCommandKind =
  | "pause_task"
  | "resume_task"
  | "discard_dead_letters"
  | "replay_dead_letters";
export type AgentCommandSourceSurface = "unknown" | "instance_detail" | "service_detail_fanout" | "task_detail";
export type AgentCommandDeliveryMode = "dispatch_now_only" | "queue_until_reconnect";
export type ServiceCommandTargetMode = "all_online" | "selected_instances";
export type ServiceCommandOfflineBehavior = "skip" | "queue";
export type ServiceCommandFanoutOutcome = "dispatched" | "queued" | "skipped" | "rejected";
export type UiStreamChannel = "commands" | "sessions";
export type AgentCommandAckStatus = "accepted" | "rejected";
export type AgentCommandStatus =
  | "pending"
  | "dispatched"
  | "accepted"
  | "expired"
  | "rejected"
  | "succeeded"
  | "failed"
  | "timeout"
  | "cancelled";
export type AgentSessionStatus = "active" | "disconnected" | "superseded";
export type TaskEventKind =
  | "failed"
  | "retried"
  | "dead_lettered"
  | "cancelled"
  | "succeeded";

export type JsonObject = Record<string, unknown>;

export interface ConnectorDescriptor {
  kind: string;
  name: string;
  config: JsonObject;
}

export interface RetryDescriptor {
  kind: string;
  config: JsonObject;
}

export interface ServiceSummary {
  name: string;
  environment: Environment;
  latest_deployment_version: string;
  latest_topology_hash: string | null;
  latest_sync_at: string | null;
  instance_count: number;
  online_instance_count: number;
  last_seen_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ServiceListResponse {
  items: ServiceSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface ConsoleSessionResponse {
  auth_configured: boolean;
  authenticated: boolean;
  username: string | null;
}

export interface AgentCommandSummary {
  command_id: string;
  instance_id: string;
  node_name: string | null;
  session_id: string | null;
  created_by: string | null;
  reason: string | null;
  source_surface: AgentCommandSourceSurface;
  kind: AgentCommandKind;
  args: JsonObject;
  timeout_s: number;
  status: AgentCommandStatus;
  ack_status: AgentCommandAckStatus | null;
  result: JsonObject | null;
  duration_ms: number | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  dispatched_at: string | null;
  acked_at: string | null;
  finished_at: string | null;
  updated_at: string;
}

export interface AgentCommandListResponse {
  items: AgentCommandSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface ServiceCommandFanoutTargetSummary {
  instance_id: string;
  node_name: string | null;
  connectivity: InstanceConnectivity;
  session_id: string | null;
  command_id: string | null;
  outcome: ServiceCommandFanoutOutcome;
  reason_code: string | null;
  reason_message: string | null;
}

export interface ServiceCommandFanoutCounts {
  dispatched: number;
  queued: number;
  skipped: number;
  rejected: number;
  total: number;
}

export interface ServiceCommandFanoutResponse {
  kind: Exclude<AgentCommandKind, "shutdown">;
  target_mode: ServiceCommandTargetMode;
  offline_behavior: ServiceCommandOfflineBehavior;
  counts: ServiceCommandFanoutCounts;
  dispatched: ServiceCommandFanoutTargetSummary[];
  queued: ServiceCommandFanoutTargetSummary[];
  skipped: ServiceCommandFanoutTargetSummary[];
  rejected: ServiceCommandFanoutTargetSummary[];
}

export interface UiStreamEvent {
  channel: UiStreamChannel;
  published_at: string;
}

export interface AgentCommandStatusCounts {
  pending: number;
  dispatched: number;
  accepted: number;
  expired: number;
  rejected: number;
  succeeded: number;
  failed: number;
  timeout: number;
  cancelled: number;
  in_flight: number;
  total: number;
}

export interface AgentCommandOverview {
  statuses: AgentCommandStatusCounts;
  active_session_count: number;
  last_command_at: string | null;
  last_completed_at: string | null;
}

export interface AgentSessionSummary {
  session_id: string;
  instance_id: string;
  node_name: string | null;
  hostname: string | null;
  status: AgentSessionStatus;
  protocol_version: string;
  capabilities: string[];
  accepted_capabilities: string[];
  connected_at: string;
  last_hello_at: string;
  last_message_at: string;
  superseded_at: string | null;
  disconnected_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentSessionListResponse {
  items: AgentSessionSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface InstanceConnectivityCounts {
  total: number;
  online: number;
  offline: number;
  never_reported: number;
}

export interface InstanceStatusCounts {
  ok: number;
  degraded: number;
  error: number;
  starting: number;
  unknown: number;
}

export interface InstanceSummary {
  instance_id: string;
  node_name: string;
  hostname: string | null;
  pid: number | null;
  deployment_version: string;
  onestep_version: string | null;
  python_version: string | null;
  started_at: string | null;
  last_sync_at: string | null;
  last_topology_hash: string | null;
  last_heartbeat_sent_at: string | null;
  last_heartbeat_sequence: number | null;
  last_seen_at: string | null;
  status: HealthStatus;
  connectivity: InstanceConnectivity;
  active_session: AgentSessionSummary | null;
  created_at: string;
  updated_at: string;
}

export interface InstanceListResponse {
  items: InstanceSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface TaskEventCounts {
  failed: number;
  retried: number;
  dead_lettered: number;
  cancelled: number;
  succeeded: number;
}

export interface TaskEventSummary {
  event_id: string;
  instance_id: string;
  task_name: string;
  kind: TaskEventKind;
  occurred_at: string;
  attempts: number | null;
  duration_ms: number | null;
  failure_kind: string | null;
  exception_type: string | null;
  message: string | null;
  traceback: string | null;
  meta: JsonObject;
  received_at: string;
  created_at: string;
}

export interface TaskMetricWindowSummary {
  instance_id: string;
  task_name: string;
  window_id: string;
  window_started_at: string;
  window_ended_at: string;
  fetched: number;
  started: number;
  succeeded: number;
  retried: number;
  failed: number;
  dead_lettered: number;
  cancelled: number;
  timeouts: number;
  inflight: number;
  avg_duration_ms: number | null;
  p95_duration_ms: number | null;
  received_at: string;
  created_at: string;
}

export interface TaskDashboardSummary {
  task_name: string;
  description: string | null;
  source_name: string | null;
  source_kind: string | null;
  source_config: JsonObject | null;
  emit: ConnectorDescriptor[];
  concurrency: number | null;
  timeout_s: number | null;
  retry_policy: RetryDescriptor | null;
  topology_hash: string | null;
  metric_window_count: number;
  latest_window_started_at: string | null;
  latest_window_ended_at: string | null;
  fetched: number;
  started: number;
  succeeded: number;
  retried: number;
  failed: number;
  dead_lettered: number;
  cancelled: number;
  timeouts: number;
  weighted_avg_duration_ms: number | null;
  max_p95_duration_ms: number | null;
  last_event_at: string | null;
  event_counts: TaskEventCounts;
}

export interface TaskDashboardListResponse {
  lookback_minutes: number;
  lookback_started_at: string;
  items: TaskDashboardSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface TaskInstanceControlState {
  instance_id: string;
  node_name: string;
  connectivity: InstanceConnectivity;
  status: HealthStatus;
  last_seen_at: string | null;
  supported_commands: TaskCommandKind[];
  state_known: boolean;
  pause_requested: boolean | null;
  paused: boolean | null;
  accepting_new_work: boolean | null;
  runner_count: number | null;
  parked_runner_count: number | null;
  fetching_runner_count: number | null;
  inflight_task_count: number | null;
}

export interface TaskControlStateSummary {
  task_name: string;
  instances: TaskInstanceControlState[];
}

export interface TaskDetailResponse {
  service: ServiceSummary;
  task_name: string;
  lookback_minutes: number;
  lookback_started_at: string;
  summary: TaskDashboardSummary;
  task_control: TaskControlStateSummary;
  recent_metric_windows: TaskMetricWindowSummary[];
  recent_events: TaskEventSummary[];
}

export interface InstanceDetailResponse {
  service: ServiceSummary;
  lookback_minutes: number;
  lookback_started_at: string;
  instance: InstanceSummary;
  latest_session: AgentSessionSummary | null;
  app_snapshot: JsonObject | null;
  recent_metric_windows: TaskMetricWindowSummary[];
  recent_events: TaskEventSummary[];
}

export interface ServiceDashboardResponse {
  service: ServiceSummary;
  lookback_minutes: number;
  lookback_started_at: string;
  instance_connectivity: InstanceConnectivityCounts;
  instance_statuses: InstanceStatusCounts;
  task_count: number;
  failing_task_count: number;
  command_overview: AgentCommandOverview;
  topology_hashes: string[];
  topology_consistent: boolean;
  recent_events: TaskEventSummary[];
}
