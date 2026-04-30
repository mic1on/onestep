import type {
  AgentCommandDeliveryMode,
  AgentCommandKind,
  AgentCommandListResponse,
  AgentCommandSummary,
  AgentSessionListResponse,
  ConsoleSessionResponse,
  Environment,
  InstanceDetailResponse,
  InstanceListResponse,
  NotificationChannel,
  NotificationChannelListResponse,
  NotificationChannelTestResponse,
  NotificationChannelUpsertRequest,
  NotificationEventType,
  NotificationProvider,
  NotificationServiceListResponse,
  NotificationServiceScope,
  ServiceCommandFanoutResponse,
  ServiceCommandOfflineBehavior,
  ServiceCommandTargetMode,
  ServiceDashboardResponse,
  ServiceListResponse,
  TaskCommandKind,
  TaskDashboardListResponse,
  TaskDetailResponse,
} from "./types";
import { getApiBaseUrl } from "../runtime-config";

const API_BASE_URL = getApiBaseUrl();

type QueryValue = string | number | undefined | null;
type RequestOptions = {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  query?: Record<string, QueryValue>;
  body?: unknown;
  redirectOnUnauthorized?: boolean;
};

export function buildApiUrl(path: string, query: Record<string, QueryValue> = {}) {
  const url = /^https?:\/\//.test(API_BASE_URL)
    ? new URL(`${API_BASE_URL}${path}`)
    : new URL(`${API_BASE_URL}${path}`, window.location.origin);
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") {
      continue;
    }
    url.searchParams.set(key, String(value));
  }
  return url;
}

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function redirectToLogin() {
  if (typeof window === "undefined" || window.location.pathname === "/login") {
    return;
  }
  const next = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  const loginUrl = new URL("/login", window.location.origin);
  loginUrl.searchParams.set("next", next || "/services?environment=prod");
  window.location.assign(`${loginUrl.pathname}${loginUrl.search}`);
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await fetch(buildApiUrl(path, options.query), {
    method: options.method ?? "GET",
    credentials: "include",
    headers: {
      Accept: "application/json",
      ...(options.body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = (await response.json()) as { detail?: string };
      detail = payload.detail ?? detail;
    } catch {
      // ignore json parse failures on error bodies
    }
    if (response.status === 401 && options.redirectOnUnauthorized !== false) {
      redirectToLogin();
    }
    throw new ApiError(detail, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

function normalizeNotificationChannel(channel: {
  id: string;
  name: string;
  provider: NotificationProvider;
  webhook_url?: string;
  webhookUrl?: string;
  enabled: boolean;
  service_scopes?: NotificationServiceScope[];
  service_scopes_json?: NotificationServiceScope[];
  serviceScopes?: NotificationServiceScope[];
  event_types?: NotificationEventType[];
  event_type_filters?: NotificationEventType[];
  event_types_json?: NotificationEventType[];
  eventTypes?: NotificationEventType[];
  missed_start_grace_seconds?: number | null;
  missedStartGraceSeconds?: number | null;
  created_at: string;
  updated_at: string;
}): NotificationChannel {
  return {
    id: channel.id,
    name: channel.name,
    provider: channel.provider,
    webhook_url: channel.webhook_url ?? channel.webhookUrl ?? "",
    enabled: channel.enabled,
    service_scopes: channel.service_scopes ?? channel.service_scopes_json ?? channel.serviceScopes ?? [],
    event_types:
      channel.event_types ?? channel.event_type_filters ?? channel.event_types_json ?? channel.eventTypes ?? [],
    missed_start_grace_seconds:
      channel.missed_start_grace_seconds ?? channel.missedStartGraceSeconds ?? null,
    created_at: channel.created_at,
    updated_at: channel.updated_at,
  };
}

export function listServices(environment?: Environment) {
  return request<ServiceListResponse>("/api/v1/services", { query: { environment } });
}

export async function listNotificationChannels() {
  const response = await request<
    NotificationChannelListResponse | { items: Array<Parameters<typeof normalizeNotificationChannel>[0]> }
  >("/api/v1/settings/notifications/channels");
  return {
    items: response.items.map(normalizeNotificationChannel),
  } satisfies NotificationChannelListResponse;
}

export async function createNotificationChannel(payload: NotificationChannelUpsertRequest) {
  const response = await request<Parameters<typeof normalizeNotificationChannel>[0]>(
    "/api/v1/settings/notifications/channels",
    {
      method: "POST",
      body: payload,
    },
  );
  return normalizeNotificationChannel(response);
}

export async function updateNotificationChannel(channelId: string, payload: NotificationChannelUpsertRequest) {
  const response = await request<Parameters<typeof normalizeNotificationChannel>[0]>(
    `/api/v1/settings/notifications/channels/${encodeURIComponent(channelId)}`,
    {
      method: "PATCH",
      body: payload,
    },
  );
  return normalizeNotificationChannel(response);
}

export function deleteNotificationChannel(channelId: string) {
  return request<void>(`/api/v1/settings/notifications/channels/${encodeURIComponent(channelId)}`, {
    method: "DELETE",
  });
}

export function testNotificationChannel(channelId: string) {
  return request<NotificationChannelTestResponse>(
    `/api/v1/settings/notifications/channels/${encodeURIComponent(channelId)}/test`,
    {
      method: "POST",
    },
  );
}

export async function listNotificationServices() {
  const response = await request<
    NotificationServiceListResponse | ServiceListResponse | { items: NotificationServiceScope[] }
  >("/api/v1/settings/notifications/services");

  if ("total" in response) {
    return {
      items: response.items.map((service) => ({
        name: service.name,
        environment: service.environment,
      })),
    } satisfies NotificationServiceListResponse;
  }

  return {
    items: response.items,
  } satisfies NotificationServiceListResponse;
}

export function getServiceDashboard(serviceName: string, environment: Environment, lookbackMinutes: number) {
  return request<ServiceDashboardResponse>(`/api/v1/services/${encodeURIComponent(serviceName)}/dashboard`, {
    query: {
      environment,
      lookback_minutes: lookbackMinutes,
    },
  });
}

export function listServiceTasks(serviceName: string, environment: Environment, lookbackMinutes: number) {
  return request<TaskDashboardListResponse>(`/api/v1/services/${encodeURIComponent(serviceName)}/tasks`, {
    query: {
      environment,
      lookback_minutes: lookbackMinutes,
      limit: 100,
    },
  });
}

export function listServiceInstances(serviceName: string, environment: Environment) {
  return request<InstanceListResponse>(`/api/v1/services/${encodeURIComponent(serviceName)}/instances`, {
    query: {
      environment,
      limit: 100,
    },
  });
}

export function listServiceCommands(serviceName: string, environment: Environment) {
  return request<AgentCommandListResponse>(`/api/v1/services/${encodeURIComponent(serviceName)}/commands`, {
    query: {
      environment,
      limit: 20,
    },
  });
}

export function listServiceSessions(serviceName: string, environment: Environment) {
  return request<AgentSessionListResponse>(`/api/v1/services/${encodeURIComponent(serviceName)}/sessions`, {
    query: {
      environment,
      limit: 20,
    },
  });
}

export function getTaskDetail(
  serviceName: string,
  taskName: string,
  environment: Environment,
  lookbackMinutes: number,
) {
  return request<TaskDetailResponse>(
    `/api/v1/services/${encodeURIComponent(serviceName)}/tasks/${encodeURIComponent(taskName)}`,
    {
      query: {
        environment,
        lookback_minutes: lookbackMinutes,
        metric_window_limit: 20,
        event_limit: 20,
      },
    },
  );
}

export function getInstanceDetail(
  serviceName: string,
  instanceId: string,
  environment: Environment,
  lookbackMinutes: number,
) {
  return request<InstanceDetailResponse>(
    `/api/v1/services/${encodeURIComponent(serviceName)}/instances/${encodeURIComponent(instanceId)}`,
    {
      query: {
        environment,
        lookback_minutes: lookbackMinutes,
        metric_window_limit: 20,
        event_limit: 20,
      },
    },
  );
}

export function listInstanceCommands(instanceId: string) {
  return request<AgentCommandListResponse>(`/api/v1/instances/${encodeURIComponent(instanceId)}/commands`, {
    query: {
      limit: 20,
    },
  });
}

export function createInstanceCommand(
  instanceId: string,
  payload: {
    kind: AgentCommandKind;
    args?: Record<string, unknown>;
    timeout_s?: number;
    delivery_mode?: AgentCommandDeliveryMode;
    reason?: string;
  },
) {
  return request<AgentCommandSummary>(`/api/v1/instances/${encodeURIComponent(instanceId)}/commands`, {
    method: "POST",
    body: {
      kind: payload.kind,
      args: payload.args ?? {},
      timeout_s: payload.timeout_s ?? 10,
      delivery_mode: payload.delivery_mode ?? "dispatch_now_only",
      reason: payload.reason ?? null,
    },
  });
}

export function createServiceCommandFanout(
  serviceName: string,
  environment: Environment,
  payload: {
    kind: Exclude<AgentCommandKind, "shutdown">;
    args?: Record<string, unknown>;
    timeout_s?: number;
    reason: string;
    target_mode: ServiceCommandTargetMode;
    target_instance_ids?: string[];
    offline_behavior: ServiceCommandOfflineBehavior;
  },
) {
  return request<ServiceCommandFanoutResponse>(
    `/api/v1/services/${encodeURIComponent(serviceName)}/commands`,
    {
      method: "POST",
      query: { environment },
      body: {
        kind: payload.kind,
        args: payload.args ?? {},
        timeout_s: payload.timeout_s ?? 10,
        reason: payload.reason,
        target_mode: payload.target_mode,
        target_instance_ids: payload.target_instance_ids ?? [],
        offline_behavior: payload.offline_behavior,
      },
    },
  );
}

export function createTaskCommandFanout(
  serviceName: string,
  taskName: string,
  environment: Environment,
  payload: {
    kind: TaskCommandKind;
    args?: Record<string, unknown>;
    timeout_s?: number;
    reason: string;
    target_mode?: ServiceCommandTargetMode;
    target_instance_ids?: string[];
    offline_behavior?: ServiceCommandOfflineBehavior;
  },
) {
  return request<ServiceCommandFanoutResponse>(
    `/api/v1/services/${encodeURIComponent(serviceName)}/tasks/${encodeURIComponent(taskName)}/commands`,
    {
      method: "POST",
      query: { environment },
      body: {
        kind: payload.kind,
        args: payload.args ?? {},
        timeout_s: payload.timeout_s ?? 10,
        reason: payload.reason,
        target_mode: payload.target_mode ?? "all_online",
        target_instance_ids: payload.target_instance_ids ?? [],
        offline_behavior: payload.offline_behavior ?? "skip",
      },
    },
  );
}

export function getConsoleSession() {
  return request<ConsoleSessionResponse>("/api/v1/auth/session", {
    redirectOnUnauthorized: false,
  });
}

export function loginConsole(username: string, password: string) {
  return request<ConsoleSessionResponse>("/api/v1/auth/login", {
    method: "POST",
    body: { username, password },
    redirectOnUnauthorized: false,
  });
}

export function logoutConsole() {
  return request<ConsoleSessionResponse>("/api/v1/auth/logout", {
    method: "POST",
    redirectOnUnauthorized: false,
  });
}
