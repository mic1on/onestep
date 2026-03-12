export type Environment = "dev" | "staging" | "prod";
export type HealthStatus = "ok" | "degraded" | "error" | "starting" | "unknown";
export type InstanceConnectivity = "online" | "offline" | "never_reported";
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

export interface TaskDetailResponse {
  service: ServiceSummary;
  task_name: string;
  lookback_minutes: number;
  lookback_started_at: string;
  summary: TaskDashboardSummary;
  recent_metric_windows: TaskMetricWindowSummary[];
  recent_events: TaskEventSummary[];
}

export interface InstanceDetailResponse {
  service: ServiceSummary;
  lookback_minutes: number;
  lookback_started_at: string;
  instance: InstanceSummary;
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
  topology_hashes: string[];
  topology_consistent: boolean;
  recent_events: TaskEventSummary[];
}
