import type { Instance, LogEntry, Service, Task, TaskCommandKind } from './types';
import type { MessageKey } from './i18n';

export type Environment = 'dev' | 'staging' | 'prod';

type HealthStatus = 'ok' | 'degraded' | 'error' | 'starting' | 'unknown';
type InstanceConnectivity = 'online' | 'offline' | 'never_reported';
type ServiceListStatus = 'online' | 'attention' | 'offline';
type ServiceViewStatus = 'running' | 'degraded' | 'stopped';
type TaskViewStatus = 'running' | 'idle' | 'failed' | 'paused' | 'offline';
type InstanceViewStatus = 'running' | 'starting' | 'failed' | 'stopped';
type EventLogLevel = 'error' | 'warn' | 'info';
type TaskEventKind = 'started' | 'failed' | 'retried' | 'dead_lettered' | 'cancelled' | 'succeeded';
type TaskEventHistorySource = 'runtime' | 'command';
type AgentCommandAckStatus = 'accepted' | 'rejected';
type AgentCommandStatus =
  | 'pending'
  | 'dispatched'
  | 'accepted'
  | 'expired'
  | 'rejected'
  | 'succeeded'
  | 'failed'
  | 'timeout'
  | 'cancelled';
type AgentCommandKind =
  | 'ping'
  | 'shutdown'
  | 'restart'
  | 'drain'
  | 'pause_task'
  | 'resume_task'
  | 'restart_task'
  | 'discard_dead_letters'
  | 'replay_dead_letters'
  | 'run_task_once'
  | 'sync_now'
  | 'flush_metrics'
  | 'flush_events';

type JsonObject = Record<string, unknown>;
type QueryValue = string | number | undefined | null;

interface ConnectorDescriptor {
  kind: string;
  name: string;
  config: JsonObject;
}

interface RetryDescriptor {
  kind: string;
  config: JsonObject;
}

interface ServiceSummary {
  name: string;
  environment: Environment;
  description: string | null;
  latest_deployment_version: string;
  service_status: ServiceListStatus;
  latest_topology_hash: string | null;
  latest_sync_at: string | null;
  instance_count: number;
  online_instance_count: number;
  last_seen_at: string | null;
  source_kinds: string[];
  task_count: number;
  failing_task_count: number;
  view_status: ServiceViewStatus;
  success_rate: number;
  throughput_per_min: number;
  error_count: number;
  uptime_reference_at: string | null;
  online_task_count: number;
  standby_instance_count: number;
  created_at: string;
  updated_at: string;
}

interface ServiceListResponse {
  items: ServiceSummary[];
  total: number;
  limit: number;
  offset: number;
  source_kind_counts: Record<string, number>;
  summary: ServiceListSummary;
}

interface ServiceListSummary {
  total_services: number;
  online_services: number;
  attention_services: number;
  offline_services: number;
  ready_services: number;
  total_instances: number;
  online_instances: number;
  total_tasks: number;
  failing_tasks: number;
}

interface TaskEventCounts {
  started: number;
  failed: number;
  retried: number;
  dead_lettered: number;
  cancelled: number;
  succeeded: number;
}

interface TaskEventSummary {
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
  level: EventLogLevel;
}

interface TaskEventHistoryItemSummary {
  id: string;
  source_type: TaskEventHistorySource;
  instance_id: string;
  task_name: string;
  kind: string;
  occurred_at: string;
  level: EventLogLevel;
  message: string | null;
  meta: JsonObject;
  attempts: number | null;
  duration_ms: number | null;
  failure_kind: string | null;
  exception_type: string | null;
  traceback: string | null;
  command_id: string | null;
  command_status: AgentCommandStatus | null;
  ack_status: AgentCommandAckStatus | null;
  created_at: string;
  updated_at: string | null;
}

interface TaskEventHistoryListResponse {
  lookback_minutes: number;
  lookback_started_at: string;
  items: TaskEventHistoryItemSummary[];
  total: number;
  limit: number;
  offset: number;
}

interface TaskDashboardSummary {
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
  // Aggregated across the service's online instances: true when any instance
  // reported pause_requested=true. Null when no instance reported a known state.
  pause_requested: boolean | null;
  supported_commands: TaskCommandKind[];
  // ---- Derived view-facing fields (computed by the plane) ----
  view_status: TaskViewStatus;
  success_rate: number;
  throughput_per_min: number;
  error_count: number;
  retry_attempts: number;
  source_label: string | null;
  sink_label: string | null;
  config_yaml: string | null;
  uptime_reference_at: string | null;
}

interface TaskDashboardListResponse {
  lookback_minutes: number;
  lookback_started_at: string;
  items: TaskDashboardSummary[];
  total: number;
  limit: number;
  offset: number;
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

export interface TaskMetricChartPointSummary {
  bucket_started_at: string;
  bucket_ended_at: string;
  reported_window_count: number;
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
}

interface TaskInstanceControlState {
  instance_id: string;
  pause_requested: boolean | null;
  paused: boolean | null;
}

interface TaskControlStateSummary {
  task_name: string;
  instances: TaskInstanceControlState[];
}

interface TaskDetailResponse {
  service: ServiceSummary;
  task_name: string;
  lookback_minutes: number;
  lookback_started_at: string;
  summary: TaskDashboardSummary;
  task_control: TaskControlStateSummary;
  recent_metric_points?: TaskMetricChartPointSummary[];
  recent_metric_windows: TaskMetricWindowSummary[];
  recent_events: TaskEventSummary[];
}

interface InstanceSummary {
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
  active_session: unknown | null;
  view_status: InstanceViewStatus;
  created_at: string;
  updated_at: string;
}

interface InstanceListResponse {
  items: InstanceSummary[];
  total: number;
  limit: number;
  offset: number;
}

interface InstanceConnectivityCounts {
  total: number;
  online: number;
  offline: number;
  never_reported: number;
}

interface InstanceStatusCounts {
  ok: number;
  degraded: number;
  error: number;
  starting: number;
  unknown: number;
}

interface AgentCommandOverview {
  in_flight: number;
  total: number;
  statuses: Record<string, number>;
}

interface ServiceDashboardResponse {
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

interface ServiceCommandFanoutCounts {
  dispatched: number;
  queued: number;
  skipped: number;
  rejected: number;
  total: number;
}

interface ServiceCommandFanoutTargetSummary {
  instance_id: string;
  node_name: string | null;
  connectivity: InstanceConnectivity;
  session_id: string | null;
  command_id: string | null;
  outcome: 'dispatched' | 'queued' | 'skipped' | 'rejected';
  reason_code: string | null;
  reason_message: string | null;
}

export interface ServiceCommandFanoutResponse {
  kind: AgentCommandKind;
  target_mode: 'all_online' | 'selected_instances';
  offline_behavior: 'skip' | 'queue';
  noop_reason_code: string | null;
  noop_reason_message: string | null;
  counts: ServiceCommandFanoutCounts;
  dispatched: ServiceCommandFanoutTargetSummary[];
  queued: ServiceCommandFanoutTargetSummary[];
  skipped: ServiceCommandFanoutTargetSummary[];
  rejected: ServiceCommandFanoutTargetSummary[];
}

interface AgentCommandSummary {
  command_id: string;
  kind: AgentCommandKind;
  status: AgentCommandStatus;
  error_code: string | null;
  error_message: string | null;
  updated_at: string;
}

interface AgentCommandListResponse {
  items: AgentCommandSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface CommandCompletionResult {
  completed: boolean;
  failed: boolean;
}

const TERMINAL_COMMAND_STATUSES = new Set<AgentCommandStatus>([
  'expired',
  'rejected',
  'succeeded',
  'failed',
  'timeout',
  'cancelled',
]);

const FAILED_COMMAND_STATUSES = new Set<AgentCommandStatus>([
  'expired',
  'rejected',
  'failed',
  'timeout',
  'cancelled',
]);

export interface ControlPlaneData {
  services: Service[];
  tasks: Task[];
  instances: Instance[];
  logs: LogEntry[];
  selectedServiceId: string;
  serviceSummary: ServiceListSummary;
  sourceKindCounts: Record<string, number>;
}

export interface TaskEventLogPage {
  logs: LogEntry[];
  total: number;
  limit: number;
  offset: number;
  lookbackMinutes: number;
}

export interface ServiceSummaryStats {
  total_services: number;
  online_services: number;
  attention_services: number;
  offline_services: number;
  total_instances: number;
  online_instances: number;
  total_tasks: number;
  failing_tasks: number;
}

export interface RecentEvent {
  event_id: string;
  instance_id: string;
  task_name: string;
  kind: string;
  occurred_at: string;
  attempts: number | null;
  duration_ms: number | null;
  failure_kind: string | null;
  exception_type: string | null;
  message: string | null;
  traceback: string | null;
  meta: Record<string, unknown>;
  received_at: string;
  created_at: string;
  service_name: string;
  environment: Environment;
}

// Matches the control plane's DEFAULT_LOOKBACK_MINUTES for service/task summaries.
const DEFAULT_LOOKBACK_MINUTES = 15;
export const DEFAULT_TASK_METRIC_LOOKBACK_MINUTES = DEFAULT_LOOKBACK_MINUTES;
export const MAX_TASK_METRIC_LOOKBACK_MINUTES = 24 * 60;
export const TASK_METRIC_LOOKBACK_PRESETS = [5, 10, 15, 30] as const;
export const DEFAULT_TASK_EVENT_LOOKBACK_MINUTES = DEFAULT_LOOKBACK_MINUTES;
export const MAX_TASK_EVENT_LOOKBACK_MINUTES = MAX_TASK_METRIC_LOOKBACK_MINUTES;
export const TASK_EVENT_LOOKBACK_PRESETS = TASK_METRIC_LOOKBACK_PRESETS;
export const DEFAULT_TASK_EVENT_PAGE_SIZE = 20;
const PAGE_SIZE = 100;

class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export class AuthRequiredError extends ApiError {
  constructor(message = 'authentication required') {
    super(message, 401);
    this.name = 'AuthRequiredError';
  }
}

export interface ConsoleSessionResponse {
  auth_configured: boolean;
  bootstrap_required: boolean;
  authenticated: boolean;
  username: string | null;
  role: 'viewer' | 'operator' | 'admin' | null;
  roles: Array<'viewer' | 'operator' | 'admin'>;
}

export type NotificationProvider = 'feishu' | 'wechat_work' | 'custom';
export type NotificationWebhookMethod = 'GET' | 'POST';
export type NotificationEventType =
  | 'task_started'
  | 'task_succeeded'
  | 'task_failed'
  | 'task_missed_start'
  | 'instance_online'
  | 'instance_offline';

export interface NotificationServiceScope {
  name: string;
  environment: Environment;
}

export interface NotificationCustomParam {
  key: string;
  value: string;
}

export interface NotificationCustomConfig {
  method: NotificationWebhookMethod;
  query_params: NotificationCustomParam[];
  body_params: NotificationCustomParam[];
}

export interface NotificationChannel {
  id: string;
  name: string;
  provider: NotificationProvider;
  webhook_url_masked: string;
  enabled: boolean;
  service_scopes: NotificationServiceScope[];
  event_types: NotificationEventType[];
  missed_start_grace_seconds: number;
  custom_config: NotificationCustomConfig | null;
  created_at: string;
  updated_at: string;
}

export interface NotificationChannelInput {
  name: string;
  provider: NotificationProvider;
  webhook_url: string;
  enabled?: boolean;
  service_scopes: NotificationServiceScope[];
  event_types: NotificationEventType[];
  missed_start_grace_seconds: number;
  custom_config?: NotificationCustomConfig | null;
}

export type NotificationChannelPatch = Partial<Omit<NotificationChannelInput, 'enabled'>>;

export interface NotificationTestResponse {
  status: 'accepted';
  channel_id: string;
  provider: NotificationProvider;
  preview_text: string;
}

interface NotificationChannelListResponse {
  items: NotificationChannel[];
}

interface NotificationServiceListResponse {
  items: NotificationServiceScope[];
}

function getApiBaseUrl() {
  return import.meta.env.VITE_API_BASE_URL ?? '';
}

function buildApiUrl(path: string, query: Record<string, QueryValue> = {}) {
  const apiBaseUrl = getApiBaseUrl();
  const url = /^https?:\/\//.test(apiBaseUrl)
    ? new URL(`${apiBaseUrl}${path}`)
    : new URL(`${apiBaseUrl}${path}`, window.location.origin);

  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') {
      continue;
    }
    url.searchParams.set(key, String(value));
  }
  return url;
}

async function request<T>(
  path: string,
  options: {
    method?: 'GET' | 'POST' | 'PATCH' | 'DELETE';
    query?: Record<string, QueryValue>;
    body?: unknown;
  } = {},
): Promise<T> {
  const response = await fetch(buildApiUrl(path, options.query), {
    method: options.method ?? 'GET',
    credentials: 'include',
    headers: {
      Accept: 'application/json',
      ...(options.body === undefined ? {} : { 'Content-Type': 'application/json' }),
    },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = (await response.json()) as { detail?: string };
      detail = payload.detail ?? detail;
    } catch {
      // Keep status text when the response is not JSON.
    }
    if (response.status === 401) {
      throw new AuthRequiredError(detail || 'authentication required');
    }
    throw new ApiError(detail, response.status);
  }

  return (await response.json()) as T;
}

export function isAuthRequiredError(error: unknown) {
  return error instanceof AuthRequiredError;
}

function serviceId(service: Pick<ServiceSummary, 'name' | 'environment'>) {
  return `${service.name}:${service.environment}`;
}

function displayServiceName(service: Pick<ServiceSummary, 'name' | 'environment'>) {
  return `${service.name} / ${service.environment}`;
}

// ---- Presentation-only formatters ----
// These turn plane-computed scalars into display strings. No business state is
// derived here; that all lives in the backend.

/** Render a live "time since <referenceAt>" label that ticks as time passes. */
type Translate = (key: MessageKey, values?: Record<string, string | number>) => string;

const englishTimeLabels: Translate = (key, values = {}) => {
  const labels: Partial<Record<MessageKey, string>> = {
    'time.neverRun': 'Not run yet',
    'time.now': 'just now',
    'time.minutesAgo': '{count}m ago',
    'time.hoursAgo': '{count}h ago',
    'time.daysAgo': '{count}d ago',
  };
  return (labels[key] ?? key).replace(/\{(\w+)\}/g, (match, name) => {
    const value = values[name];
    return value === undefined ? match : String(value);
  });
};

export function formatRelativeTime(referenceAt: string | null, t: Translate): string {
  if (!referenceAt) {
    return t('time.neverRun');
  }
  const elapsedMs = Date.now() - new Date(referenceAt).getTime();
  if (!Number.isFinite(elapsedMs) || elapsedMs < 60_000) {
    return t('time.now');
  }
  const minutes = Math.floor(elapsedMs / 60_000);
  if (minutes < 60) {
    return t('time.minutesAgo', { count: minutes });
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return t('time.hoursAgo', { count: hours });
  }
  return t('time.daysAgo', { count: Math.floor(hours / 24) });
}

export function formatUptime(referenceAt: string | null): string {
  return formatRelativeTime(referenceAt, englishTimeLabels);
}

/** Render a per-minute throughput scalar with its unit, e.g. "12/min". */
export function formatThroughput(perMin: number): string {
  return `${perMin}/min`;
}

function mapService(service: ServiceSummary): Service {
  return {
    id: serviceId(service),
    apiName: service.name,
    environment: service.environment,
    name: displayServiceName(service),
    description: service.description,
    viewStatus: service.view_status,
    uptimeReferenceAt: service.uptime_reference_at ?? service.last_seen_at ?? service.latest_sync_at,
    throughputPerMin: service.throughput_per_min,
    successRate: service.success_rate,
    errorCount: service.error_count,
    totalInstances: service.instance_count,
    activeInstances: service.online_instance_count,
    standbyInstances: service.standby_instance_count,
    totalTaskCount: service.task_count,
    failingTaskCount: service.failing_task_count,
    onlineTaskCount: service.online_task_count,
  };
}

function mapTask(service: ServiceSummary, task: TaskDashboardSummary): Task {
  const sink = task.emit[0];
  return {
    id: `${serviceId(service)}:${task.task_name}`,
    apiName: task.task_name,
    apiServiceName: service.name,
    environment: service.environment,
    serviceId: serviceId(service),
    name: task.task_name,
    viewStatus: task.view_status,
    supportedCommands: task.supported_commands ?? [],
    pipelineSource: task.source_kind ?? task.source_name ?? 'Source',
    pipelineSourceLabel: task.source_label ?? 'input',
    sourceKind: task.source_kind,
    sourceConfig: task.source_config,
    sourceName: task.source_name,
    pipelineSink: sink?.kind ?? 'Handler',
    pipelineSinkLabel: task.sink_label ?? 'handler',
    sinkKind: sink?.kind ?? null,
    sinkConfig: sink?.config ?? null,
    sinkName: sink?.name ?? null,
    concurrency: task.concurrency ?? 1,
    retryAttempts: task.retry_attempts,
    uptimeReferenceAt: task.uptime_reference_at,
    throughputPerMin: task.throughput_per_min,
    successRate: task.success_rate,
    errorCount: task.error_count,
    configYaml: task.config_yaml ?? '',
  };
}

function mapInstance(service: ServiceSummary, instance: InstanceSummary): Instance {
  return {
    uuid: instance.instance_id,
    apiServiceName: service.name,
    environment: service.environment,
    serviceId: serviceId(service),
    hostname: instance.hostname ?? 'unknown-host',
    nodeName: instance.node_name,
    pid: instance.pid ?? 0,
    version: instance.deployment_version,
    viewStatus: instance.view_status,
  };
}

function mapLog(event: TaskEventSummary): LogEntry {
  const failureParts = [event.exception_type, event.message].filter(Boolean);
  const sourceDetail = typeof event.meta.source === 'string' ? event.meta.source : null;
  const fallbackMessage =
    failureParts.length > 0
      ? failureParts.join(': ')
      : event.failure_kind ?? event.kind;
  return {
    timestamp: new Date(event.occurred_at).toTimeString().split(' ')[0],
    level: event.level,
    source: event.task_name,
    message: fallbackMessage,
    eventKind: event.kind,
    attempts: event.attempts,
    durationMs: event.duration_ms,
    instanceId: event.instance_id,
    sourceDetail,
    exceptionType: event.exception_type,
    failureKind: event.failure_kind,
    traceback: event.traceback,
  };
}

function mapTaskEventHistoryLog(event: TaskEventHistoryItemSummary): LogEntry {
  const failureParts = [event.exception_type, event.message].filter(Boolean);
  const sourceDetail = typeof event.meta.source === 'string' ? event.meta.source : null;
  const fallbackMessage =
    failureParts.length > 0
      ? failureParts.join(': ')
      : event.failure_kind ?? event.message ?? event.kind;
  return {
    id: event.id,
    timestamp: new Date(event.occurred_at).toTimeString().split(' ')[0],
    level: event.level,
    source: event.task_name,
    sourceType: event.source_type,
    message: fallbackMessage,
    eventKind: event.kind,
    attempts: event.attempts,
    durationMs: event.duration_ms,
    instanceId: event.instance_id,
    sourceDetail,
    exceptionType: event.exception_type,
    failureKind: event.failure_kind,
    traceback: event.traceback,
    commandId: event.command_id,
    commandStatus: event.command_status,
    ackStatus: event.ack_status,
  };
}

async function listServices(environment?: Environment) {
  return request<ServiceListResponse>('/api/v1/services', {
    query: {
      limit: PAGE_SIZE,
      offset: 0,
      environment,
    },
  });
}

export function getConsoleSession() {
  return request<ConsoleSessionResponse>('/api/v1/auth/session');
}

export function loginConsole(username: string, password: string) {
  return request<ConsoleSessionResponse>('/api/v1/auth/login', {
    method: 'POST',
    body: {
      username,
      password,
    },
  });
}

export function logoutConsole() {
  return request<ConsoleSessionResponse>('/api/v1/auth/logout', {
    method: 'POST',
  });
}

export async function listNotificationChannels() {
  const response = await request<NotificationChannelListResponse>('/api/v1/settings/notifications/channels');
  return response.items;
}

export async function listNotificationServices() {
  const response = await request<NotificationServiceListResponse>('/api/v1/settings/notifications/services');
  return response.items;
}

export function createNotificationChannel(channel: NotificationChannelInput) {
  return request<NotificationChannel>('/api/v1/settings/notifications/channels', {
    method: 'POST',
    body: channel,
  });
}

export function updateNotificationChannel(channelId: string, patch: NotificationChannelPatch) {
  return request<NotificationChannel>(`/api/v1/settings/notifications/channels/${encodeURIComponent(channelId)}`, {
    method: 'PATCH',
    body: patch,
  });
}

export function setNotificationChannelEnabled(channelId: string, enabled: boolean) {
  return request<NotificationChannel>(`/api/v1/settings/notifications/channels/${encodeURIComponent(channelId)}/enabled`, {
    method: 'PATCH',
    body: { enabled },
  });
}

export function deleteNotificationChannel(channelId: string) {
  return request<{ status: 'deleted' }>(`/api/v1/settings/notifications/channels/${encodeURIComponent(channelId)}`, {
    method: 'DELETE',
  });
}

export function testNotificationChannel(channelId: string, message?: string) {
  return request<NotificationTestResponse>(`/api/v1/settings/notifications/channels/${encodeURIComponent(channelId)}/test`, {
    method: 'POST',
    body: { message },
  });
}

async function getServiceDashboard(service: ServiceSummary) {
  return request<ServiceDashboardResponse>(`/api/v1/services/${encodeURIComponent(service.name)}/dashboard`, {
    query: {
      environment: service.environment,
      lookback_minutes: DEFAULT_LOOKBACK_MINUTES,
    },
  });
}

async function listServiceTasks(service: ServiceSummary) {
  return request<TaskDashboardListResponse>(`/api/v1/services/${encodeURIComponent(service.name)}/tasks`, {
    query: {
      environment: service.environment,
      lookback_minutes: DEFAULT_LOOKBACK_MINUTES,
      limit: PAGE_SIZE,
      offset: 0,
    },
  });
}

async function listServiceInstances(service: ServiceSummary) {
  return request<InstanceListResponse>(`/api/v1/services/${encodeURIComponent(service.name)}/instances`, {
    query: {
      environment: service.environment,
      limit: PAGE_SIZE,
      offset: 0,
    },
  });
}

export async function loadTaskMetricWindows(
  task: Task,
  lookbackMinutes = DEFAULT_TASK_METRIC_LOOKBACK_MINUTES,
): Promise<TaskMetricChartPointSummary[]> {
  if (!task.apiServiceName || !task.apiName || !task.environment) {
    return [];
  }

  const response = await request<TaskDetailResponse>(
    `/api/v1/services/${encodeURIComponent(task.apiServiceName)}/tasks/${encodeURIComponent(task.apiName)}`,
    {
      query: {
        environment: task.environment,
        lookback_minutes: lookbackMinutes,
        metric_window_limit: 24,
        event_limit: 1,
      },
    },
  );
  if (response.recent_metric_points) {
    return response.recent_metric_points;
  }
  return response.recent_metric_windows.map((window) => ({
    bucket_started_at: window.window_started_at,
    bucket_ended_at: window.window_ended_at,
    reported_window_count: 1,
    fetched: window.fetched,
    started: window.started,
    succeeded: window.succeeded,
    retried: window.retried,
    failed: window.failed,
    dead_lettered: window.dead_lettered,
    cancelled: window.cancelled,
    timeouts: window.timeouts,
    inflight: window.inflight,
    avg_duration_ms: window.avg_duration_ms,
    p95_duration_ms: window.p95_duration_ms,
  }));
}

export async function loadTaskEventLogs(
  task: Task,
  {
    lookbackMinutes = DEFAULT_TASK_EVENT_LOOKBACK_MINUTES,
    limit = DEFAULT_TASK_EVENT_PAGE_SIZE,
    offset = 0,
  }: {
    lookbackMinutes?: number;
    limit?: number;
    offset?: number;
  } = {},
): Promise<TaskEventLogPage> {
  if (!task.apiServiceName || !task.apiName || !task.environment) {
    return {
      logs: [],
      total: 0,
      limit,
      offset,
      lookbackMinutes,
    };
  }

  const response = await request<TaskEventHistoryListResponse>(
    `/api/v1/services/${encodeURIComponent(task.apiServiceName)}/tasks/${encodeURIComponent(task.apiName)}/events`,
    {
      query: {
        environment: task.environment,
        lookback_minutes: lookbackMinutes,
        limit,
        offset,
      },
    },
  );
  return {
    logs: response.items.map(mapTaskEventHistoryLog),
    total: response.total,
    limit: response.limit,
    offset: response.offset,
    lookbackMinutes: response.lookback_minutes,
  };
}

export async function loadTaskRecentLogs(
  task: Task,
  lookbackMinutes = DEFAULT_TASK_METRIC_LOOKBACK_MINUTES,
  eventLimit = 10,
): Promise<LogEntry[]> {
  if (!task.apiServiceName || !task.apiName || !task.environment) {
    return [];
  }

  const response = await request<TaskDetailResponse>(
    `/api/v1/services/${encodeURIComponent(task.apiServiceName)}/tasks/${encodeURIComponent(task.apiName)}`,
    {
      query: {
        environment: task.environment,
        lookback_minutes: lookbackMinutes,
        metric_window_limit: 1,
        event_limit: eventLimit,
      },
    },
  );
  return response.recent_events.map(mapLog);
}

/**
 * Fetch the current aggregated `pause_requested` for a task across its service's
 * instances. Returns true when any online instance reports pause_requested=true,
 * false when at least one reports a known state and none report true, and null
 * when no instance has reported a known state yet. Used to poll for the pause/
 * resume outcome (the worker reports this via heartbeat, not the command ack, so
 * the dashboard list status lags the command by up to one heartbeat cycle).
 */
export async function loadTaskPauseRequested(task: Task): Promise<boolean | null> {
  if (!task.apiServiceName || !task.apiName || !task.environment) {
    return null;
  }

  const response = await request<TaskDetailResponse>(
    `/api/v1/services/${encodeURIComponent(task.apiServiceName)}/tasks/${encodeURIComponent(task.apiName)}`,
    {
      query: {
        environment: task.environment,
        lookback_minutes: DEFAULT_LOOKBACK_MINUTES,
        metric_window_limit: 1,
        event_limit: 1,
      },
    },
  );
  return aggregatePauseRequested(response.task_control);
}

function aggregatePauseRequested(taskControl: TaskControlStateSummary | undefined): boolean | null {
  const instances = taskControl?.instances ?? [];
  if (instances.length === 0) return null;
  let seenKnown = false;
  for (const instance of instances) {
    if (instance.pause_requested === null) continue;
    seenKnown = true;
    if (instance.pause_requested) return true;
  }
  return seenKnown ? false : null;
}

/**
 * Poll a task's pause_requested until it reaches the expected value or the
 * timeout elapses. Resolves true if the expected state was observed within the
 * timeout, false otherwise (the command was still dispatched; the worker just
 * has not reported back via heartbeat yet).
 */
export async function pollTaskPauseRequested(
  task: Task,
  expected: boolean,
  {
    intervalMs = 3000,
    timeoutMs = 45000,
    signal,
  }: { intervalMs?: number; timeoutMs?: number; signal?: AbortSignal } = {},
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  // First poll immediately, then on interval, until deadline or aborted.
  while (Date.now() < deadline) {
    if (signal?.aborted) return false;
    let observed: boolean | null;
    try {
      observed = await loadTaskPauseRequested(task);
    } catch {
      observed = null;
    }
    if (observed === expected) return true;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  return false;
}

async function listServiceCommands(serviceName: string, environment: Environment, kind: AgentCommandKind) {
  return request<AgentCommandListResponse>(`/api/v1/services/${encodeURIComponent(serviceName)}/commands`, {
    query: {
      environment,
      kind,
      limit: PAGE_SIZE,
      offset: 0,
    },
  });
}

export function getFanoutCommandIds(response: ServiceCommandFanoutResponse): string[] {
  return [...(response.dispatched ?? []), ...(response.queued ?? [])]
    .map((target) => target.command_id)
    .filter((commandId): commandId is string => Boolean(commandId));
}

export async function pollTaskCommandCompletion(
  task: Task,
  commandIds: string[],
  {
    intervalMs = 1000,
    timeoutMs = 15000,
    signal,
  }: { intervalMs?: number; timeoutMs?: number; signal?: AbortSignal } = {},
): Promise<CommandCompletionResult> {
  if (!task.apiServiceName || !task.environment || commandIds.length === 0) {
    return { completed: false, failed: false };
  }

  const expectedCommandIds = new Set(commandIds);
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    if (signal?.aborted) return { completed: false, failed: false };
    try {
      const response = await listServiceCommands(task.apiServiceName, task.environment, 'restart_task');
      const matchingCommands = response.items.filter((command) => expectedCommandIds.has(command.command_id));
      if (matchingCommands.length === expectedCommandIds.size) {
        const completed = matchingCommands.every((command) => TERMINAL_COMMAND_STATUSES.has(command.status));
        if (completed) {
          return {
            completed: true,
            failed: matchingCommands.some((command) => FAILED_COMMAND_STATUSES.has(command.status)),
          };
        }
      }
    } catch {
      // Keep polling. Pause/resume does the same because command state can lag
      // the HTTP request briefly while the worker heartbeat catches up.
    }

    await new Promise((resolve) => setTimeout(resolve, Math.max(intervalMs, 0)));
  }

  return { completed: false, failed: false };
}

export async function loadRecentEvents(environment?: Environment, limit = 20): Promise<RecentEvent[]> {
  const response = await request<{ items: RecentEvent[]; total: number }>('/api/v1/events', {
    query: {
      environment,
      limit,
      offset: 0,
    },
  });
  return response.items;
}

export async function loadControlPlaneData(selectedServiceId?: string, environment?: Environment): Promise<ControlPlaneData> {
  const servicesResponse = await listServices(environment);
  if (servicesResponse.items.length === 0) {
    throw new ApiError('No services reported by the control plane yet', 404);
  }

  const selectedSummary =
    servicesResponse.items.find((service) => serviceId(service) === selectedServiceId) ??
    servicesResponse.items[0];

  const [dashboard, taskResponse, instanceResponse] = await Promise.all([
    getServiceDashboard(selectedSummary),
    listServiceTasks(selectedSummary),
    listServiceInstances(selectedSummary),
  ]);
  // The dashboard's embedded ServiceSummary already carries the plane-computed
  // derived fields, so use it as the authoritative view for the selected
  // service; other services come straight from the list response.
  const selectedDashboardSummary = dashboard.service;

  const services = servicesResponse.items.map((service) =>
    serviceId(service) === serviceId(selectedSummary)
      ? mapService(selectedDashboardSummary)
      : mapService(service),
  );

  return {
    services,
    selectedServiceId: serviceId(selectedDashboardSummary),
    tasks: taskResponse.items.map((task) => mapTask(selectedDashboardSummary, task)),
    instances: instanceResponse.items.map((instance) => mapInstance(selectedDashboardSummary, instance)),
    logs: dashboard.recent_events.map(mapLog),
    serviceSummary: servicesResponse.summary,
    sourceKindCounts: servicesResponse.source_kind_counts,
  };
}

export async function dispatchServiceCommand(
  service: Service,
  kind: Extract<AgentCommandKind, 'restart' | 'sync_now' | 'flush_metrics' | 'flush_events'>,
) {
  if (!service.apiName || !service.environment) {
    throw new ApiError('Selected service is not backed by an API record', 400);
  }

  return request<ServiceCommandFanoutResponse>(`/api/v1/services/${encodeURIComponent(service.apiName)}/commands`, {
    method: 'POST',
    query: {
      environment: service.environment,
    },
    body: {
      kind,
      args: {},
      timeout_s: 10,
      reason: `Triggered from OneStep dashboard: ${kind}`,
      target_mode: 'all_online',
      target_instance_ids: [],
      offline_behavior: 'skip',
    },
  });
}

export async function dispatchTaskCommand(task: Task, kind: TaskCommandKind) {
  if (!task.apiServiceName || !task.apiName || !task.environment) {
    throw new ApiError('Selected task is not backed by an API record', 400);
  }

  return request<ServiceCommandFanoutResponse>(`/api/v1/services/${encodeURIComponent(task.apiServiceName)}/tasks/${encodeURIComponent(task.apiName)}/commands`, {
    method: 'POST',
    query: {
      environment: task.environment,
    },
    body: {
      kind,
      args: {},
      timeout_s: 10,
      reason: `Triggered from OneStep dashboard: ${kind}`,
      target_mode: 'all_online',
      target_instance_ids: [],
      offline_behavior: 'skip',
    },
  });
}

export async function dispatchInstanceCommand(
  instance: Instance,
  kind: Extract<AgentCommandKind, 'restart' | 'shutdown' | 'ping' | 'sync_now'>,
) {
  return request(`/api/v1/instances/${encodeURIComponent(instance.uuid)}/commands`, {
    method: 'POST',
    body: {
      kind,
      args: {},
      timeout_s: 10,
      delivery_mode: 'dispatch_now_only',
      reason: `Triggered from OneStep dashboard: ${kind}`,
    },
  });
}

export function getApiErrorMessage(error: unknown) {
  if (error instanceof ApiError) {
    return error.status === 401 ? 'authentication required' : error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return 'request failed';
}
