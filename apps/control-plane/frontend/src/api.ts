import type { Instance, LogEntry, Service, Task } from './types';

export type Environment = 'dev' | 'staging' | 'prod';

type HealthStatus = 'ok' | 'degraded' | 'error' | 'starting' | 'unknown';
type InstanceConnectivity = 'online' | 'offline' | 'never_reported';
type ServiceListStatus = 'online' | 'attention' | 'offline';
type TaskEventKind = 'started' | 'failed' | 'retried' | 'dead_lettered' | 'cancelled' | 'succeeded';
type AgentCommandKind =
  | 'ping'
  | 'shutdown'
  | 'restart'
  | 'drain'
  | 'pause_task'
  | 'resume_task'
  | 'discard_dead_letters'
  | 'replay_dead_letters'
  | 'run_task_once'
  | 'sync_now'
  | 'flush_metrics'
  | 'flush_events';
type TaskCommandKind =
  | 'pause_task'
  | 'resume_task'
  | 'discard_dead_letters'
  | 'replay_dead_letters'
  | 'run_task_once';

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

interface TaskDetailResponse {
  service: ServiceSummary;
  task_name: string;
  lookback_minutes: number;
  lookback_started_at: string;
  summary: TaskDashboardSummary;
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
  created_at: string;
  updated_at: string;
}

interface InstanceListResponse {
  items: InstanceSummary[];
  total: number;
  limit: number;
  offset: number;
}

interface ServiceDashboardResponse {
  service: ServiceSummary;
  lookback_minutes: number;
  lookback_started_at: string;
  task_count: number;
  failing_task_count: number;
  recent_events: TaskEventSummary[];
}

interface ServiceCommandFanoutCounts {
  dispatched: number;
  queued: number;
  skipped: number;
  rejected: number;
  total: number;
}

export interface ServiceCommandFanoutResponse {
  kind: AgentCommandKind;
  target_mode: 'all_online' | 'selected_instances';
  offline_behavior: 'skip' | 'queue';
  noop_reason_code: string | null;
  noop_reason_message: string | null;
  counts: ServiceCommandFanoutCounts;
}

export interface ControlPlaneData {
  services: Service[];
  tasks: Task[];
  instances: Instance[];
  logs: LogEntry[];
  selectedServiceId: string;
  serviceSummary: ServiceListSummary;
  sourceKindCounts: Record<string, number>;
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

const DEFAULT_LOOKBACK_MINUTES = 60;
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

export type NotificationProvider = 'feishu' | 'wechat_work';
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

export interface NotificationChannel {
  id: string;
  name: string;
  provider: NotificationProvider;
  webhook_url_masked: string;
  enabled: boolean;
  service_scopes: NotificationServiceScope[];
  event_types: NotificationEventType[];
  missed_start_grace_seconds: number;
  created_at: string;
  updated_at: string;
}

export interface NotificationChannelInput {
  name: string;
  provider: NotificationProvider;
  webhook_url: string;
  enabled: boolean;
  service_scopes: NotificationServiceScope[];
  event_types: NotificationEventType[];
  missed_start_grace_seconds: number;
}

export type NotificationChannelPatch = Partial<NotificationChannelInput>;

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

function minutesSince(value: string | null) {
  if (!value) {
    return 'never';
  }
  const elapsedMs = Date.now() - new Date(value).getTime();
  if (!Number.isFinite(elapsedMs) || elapsedMs < 0) {
    return 'now';
  }
  const minutes = Math.floor(elapsedMs / 60_000);
  if (minutes < 60) {
    return `${Math.max(minutes, 0)}m ago`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours}h ago`;
  }
  return `${Math.floor(hours / 24)}d ago`;
}

function getNumericConfigValue(config: JsonObject | null, keys: string[]) {
  if (!config) {
    return null;
  }
  for (const key of keys) {
    const value = config[key];
    if (typeof value === 'number') {
      return value;
    }
    if (typeof value === 'string' && value.trim() !== '' && !Number.isNaN(Number(value))) {
      return Number(value);
    }
  }
  return null;
}

function getStringConfigValue(config: JsonObject | null, keys: string[]) {
  if (!config) {
    return null;
  }
  for (const key of keys) {
    const value = config[key];
    if (typeof value === 'string' && value.trim() !== '') {
      return value;
    }
  }
  return null;
}

function getRetryAttempts(retryPolicy: RetryDescriptor | null) {
  const attempts = getNumericConfigValue(retryPolicy?.config ?? null, ['attempts', 'max_attempts', 'retries']);
  return attempts ?? (retryPolicy ? 1 : 0);
}

function getSuccessRate(task: TaskDashboardSummary) {
  const total = task.succeeded + task.failed + task.dead_lettered + task.cancelled;
  if (total === 0) {
    return 100;
  }
  return Number(((task.succeeded / total) * 100).toFixed(2));
}

function mapService(
  service: ServiceSummary,
  dashboard?: ServiceDashboardResponse,
  tasks: TaskDashboardSummary[] = [],
): Service {
  const status =
    service.online_instance_count === 0
      ? 'stopped'
      : service.service_status === 'online'
      ? 'running'
      : service.service_status === 'attention'
      ? 'degraded'
      : 'stopped';
  const failedEvents = dashboard?.recent_events.filter((event) => event.kind === 'failed' || event.kind === 'dead_lettered').length ?? 0;
  const fetched = tasks.reduce((sum, task) => sum + task.fetched, 0);
  const taskCount = dashboard?.task_count ?? service.task_count;
  const failingTaskCount = dashboard?.failing_task_count ?? service.failing_task_count ?? 0;
  // A task is "online" only when the service has at least one online instance
  // (matches the Offline status derived in mapTask). Tasks on offline services
  // are not running, so onlineTaskCount is 0 regardless of failing events.
  const serviceHasOnlineInstances = service.online_instance_count > 0;
  const onlineTaskCount = serviceHasOnlineInstances ? Math.max(taskCount - failingTaskCount, 0) : 0;
  const taskHealth = taskCount === 0 ? 100 : Number((((taskCount - failingTaskCount) / taskCount) * 100).toFixed(1));
  const throughputValue = Math.round(fetched / Math.max(dashboard?.lookback_minutes ?? DEFAULT_LOOKBACK_MINUTES, 1));

  return {
    id: serviceId(service),
    apiName: service.name,
    environment: service.environment,
    name: displayServiceName(service),
    status,
    uptime: minutesSince(service.last_seen_at ?? service.latest_sync_at),
    throughput: `${throughputValue}/min`,
    throughputValue,
    successRate: taskHealth,
    errorCount24h: failedEvents,
    totalInstances: service.instance_count,
    activeInstances: service.online_instance_count,
    standbyInstances: Math.max(service.instance_count - service.online_instance_count, 0),
    taskHealth,
    taskHealthTrend: failingTaskCount > 0 ? `-${failingTaskCount}` : '+0',
    totalTaskCount: taskCount,
    failingTaskCount,
    onlineTaskCount,
  };
}

function mapTask(service: ServiceSummary, task: TaskDashboardSummary, lookbackMinutes: number): Task {
  const sourceLabel =
    task.source_name ??
    getStringConfigValue(task.source_config, ['topic', 'queue', 'stream', 'url', 'schedule', 'cron']) ??
    'input';
  const sink = task.emit[0];
  const sinkLabel =
    sink?.name ??
    getStringConfigValue(sink?.config ?? null, ['topic', 'queue', 'stream', 'url', 'table', 'database']) ??
    'handler';
  const throughputNum = Math.round(task.fetched / Math.max(lookbackMinutes, 1));
  const errorCount = task.failed + task.dead_lettered + task.timeouts;
  const status =
    service.online_instance_count === 0
      ? 'Offline'
      : errorCount > 0
      ? 'Failed'
      : task.started > 0 || task.succeeded > 0
      ? 'Running'
      : 'Idle';

  return {
    id: `${serviceId(service)}:${task.task_name}`,
    apiName: task.task_name,
    apiServiceName: service.name,
    environment: service.environment,
    serviceId: serviceId(service),
    name: task.task_name,
    status,
    pipelineSource: task.source_kind ?? task.source_name ?? 'Source',
    pipelineSourceLabel: sourceLabel,
    sourceKind: task.source_kind,
    sourceConfig: task.source_config,
    sourceName: task.source_name,
    pipelineSink: sink?.kind ?? 'Handler',
    pipelineSinkLabel: sinkLabel,
    sinkKind: sink?.kind ?? null,
    sinkConfig: sink?.config ?? null,
    sinkName: sink?.name ?? null,
    concurrency: task.concurrency ?? 1,
    retryAttempts: getRetryAttempts(task.retry_policy),
    uptime: minutesSince(task.latest_window_started_at ?? task.last_event_at),
    throughputValue: `${throughputNum}/min`,
    throughputNum,
    successRate: getSuccessRate(task),
    errorCount,
    configYaml: buildTaskConfigYaml(task),
  };
}

function mapInstance(service: ServiceSummary, instance: InstanceSummary): Instance {
  const status =
    instance.status === 'ok'
      ? 'Running'
      : instance.status === 'starting'
      ? 'Starting'
      : instance.status === 'error' || instance.status === 'degraded'
      ? 'Failed'
      : 'Stopped';

  return {
    uuid: instance.instance_id,
    apiServiceName: service.name,
    environment: service.environment,
    serviceId: serviceId(service),
    hostname: instance.hostname ?? 'unknown-host',
    nodeName: instance.node_name,
    pid: instance.pid ?? 0,
    version: instance.deployment_version,
    status,
  };
}

function mapLog(event: TaskEventSummary): LogEntry {
  const level = event.kind === 'failed' || event.kind === 'dead_lettered' ? 'error' : event.kind === 'retried' ? 'warn' : 'info';
  return {
    timestamp: new Date(event.occurred_at).toTimeString().split(' ')[0],
    level,
    source: event.task_name,
    message: event.message ?? `${event.kind} on ${event.instance_id}`,
  };
}

function buildTaskConfigYaml(task: TaskDashboardSummary) {
  const emit = task.emit[0];
  return `task_config:
  id: "${task.task_name}"
  topology_hash: "${task.topology_hash ?? 'unknown'}"

  execution:
    concurrency: ${task.concurrency ?? 1}
    timeout_s: ${task.timeout_s ?? 'null'}
    retry_policy:
      kind: "${task.retry_policy?.kind ?? 'none'}"
      attempts: ${getRetryAttempts(task.retry_policy)}

  source:
    type: "${task.source_kind ?? 'unknown'}"
    name: "${task.source_name ?? 'default'}"

  sink:
    type: "${emit?.kind ?? 'handler'}"
    name: "${emit?.name ?? 'default'}"`;
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

export async function loadTaskMetricWindows(task: Task) {
  if (!task.apiServiceName || !task.apiName || !task.environment) {
    return [];
  }

  const response = await request<TaskDetailResponse>(
    `/api/v1/services/${encodeURIComponent(task.apiServiceName)}/tasks/${encodeURIComponent(task.apiName)}`,
    {
      query: {
        environment: task.environment,
        lookback_minutes: DEFAULT_LOOKBACK_MINUTES,
        metric_window_limit: 24,
        event_limit: 1,
      },
    },
  );
  return response.recent_metric_windows;
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
  const selectedDashboardSummary = dashboard.service;

  const services = servicesResponse.items.map((service) =>
    serviceId(service) === serviceId(selectedSummary)
      ? mapService(selectedDashboardSummary, dashboard, taskResponse.items)
      : mapService(service),
  );

  return {
    services,
    selectedServiceId: serviceId(selectedDashboardSummary),
    tasks: taskResponse.items.map((task) => mapTask(selectedDashboardSummary, task, taskResponse.lookback_minutes)),
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
