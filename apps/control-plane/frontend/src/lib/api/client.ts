import type {
  AgentCommandDeliveryMode,
  AgentCommandKind,
  AgentCommandListResponse,
  AgentCommandSummary,
  AgentSessionListResponse,
  ConsoleRole,
  ConsoleSessionResponse,
  Environment,
  InstanceDetailResponse,
  InstanceListResponse,
  NotificationChannel,
  NotificationChannelListResponse,
  NotificationChannelPatchRequest,
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
const SERVICES_PAGE_SIZE = 100;
const SERVICE_TASKS_PAGE_SIZE = 100;
const SERVICE_INSTANCES_PAGE_SIZE = 100;
const SERVICE_COMMANDS_PAGE_SIZE = 50;
const SERVICE_SESSIONS_PAGE_SIZE = 50;
const INSTANCE_COMMANDS_PAGE_SIZE = 50;

type QueryValue = string | number | undefined | null;
type RequestOptions = {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  query?: Record<string, QueryValue>;
  body?: unknown;
  redirectOnUnauthorized?: boolean;
  timeoutMs?: number;
};

type SessionExpiredListener = () => void;

const sessionExpiredListeners = new Set<SessionExpiredListener>();
const DEFAULT_REQUEST_TIMEOUT_MS = 15_000;

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

async function requestPaginatedItems<TItem>(
  path: string,
  options: Omit<RequestOptions, "query"> & {
    query?: Record<string, QueryValue>;
    pageSize: number;
    targetCount?: number;
  },
) {
  const items: TItem[] = [];
  let offset = 0;
  let total = 0;

  while (true) {
    const remainingTarget =
      options.targetCount === undefined ? options.pageSize : Math.max(options.targetCount - items.length, 1);
    const response = await request<{
      items: TItem[];
      total: number;
      limit: number;
      offset: number;
    }>(path, {
      ...options,
      query: {
        ...options.query,
        limit: Math.min(options.pageSize, remainingTarget),
        offset,
      },
    });

    items.push(...response.items);
    total = response.total;
    offset += response.items.length;

    if (
      response.items.length === 0 ||
      items.length >= total ||
      (options.targetCount !== undefined && items.length >= options.targetCount)
    ) {
      return {
        items,
        total,
        limit: options.targetCount ?? response.limit,
        offset: 0,
      };
    }
  }
}

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export class SessionExpiredError extends ApiError {
  constructor(message = "Session expired") {
    super(message, 401);
    this.name = "SessionExpiredError";
  }
}

export class ApiTimeoutError extends ApiError {
  timeoutMs: number;

  constructor(timeoutMs: number) {
    super(`Request timed out after ${Math.round(timeoutMs / 1_000)}s`, 408);
    this.name = "ApiTimeoutError";
    this.timeoutMs = timeoutMs;
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

function notifySessionExpired() {
  for (const listener of sessionExpiredListeners) {
    listener();
  }
}

export function subscribeToSessionExpired(listener: SessionExpiredListener) {
  sessionExpiredListeners.add(listener);
  return () => {
    sessionExpiredListeners.delete(listener);
  };
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const timeoutMs = options.timeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS;
  const controller = new AbortController();
  const timeoutId = globalThis.setTimeout(() => controller.abort(), timeoutMs);
  let response: Response;

  try {
    response = await fetch(buildApiUrl(path, options.query), {
      method: options.method ?? "GET",
      credentials: "include",
      headers: {
        Accept: "application/json",
        ...(options.body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: controller.signal,
    });
  } catch (error) {
    if (
      typeof error === "object" &&
      error !== null &&
      "name" in error &&
      error.name === "AbortError"
    ) {
      throw new ApiTimeoutError(timeoutMs);
    }
    throw error;
  } finally {
    globalThis.clearTimeout(timeoutId);
  }

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
    if (response.status === 401) {
      notifySessionExpired();
      throw new SessionExpiredError(detail || "Session expired");
    }
    throw new ApiError(detail, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

type ConsoleSessionPayload = {
  auth_configured: boolean;
  bootstrap_required?: boolean;
  authenticated: boolean;
  username?: string | null;
  role?: string | null;
  primary_role?: string | null;
  roles?: string[] | null;
};

function normalizeConsoleRole(value: string | null | undefined): ConsoleRole | null {
  if (value === "viewer" || value === "operator" || value === "admin") {
    return value;
  }
  return null;
}

function normalizeConsoleRoles(values: string[] | null | undefined, primaryRole: ConsoleRole | null): ConsoleRole[] {
  const roles = (values ?? [])
    .map((value) => normalizeConsoleRole(value))
    .filter((value): value is ConsoleRole => value !== null);
  if (primaryRole && !roles.includes(primaryRole)) {
    roles.unshift(primaryRole);
  }
  return roles;
}

function normalizeConsoleSession(payload: ConsoleSessionPayload): ConsoleSessionResponse {
  const role = normalizeConsoleRole(payload.role ?? payload.primary_role ?? null);
  return {
    auth_configured: payload.auth_configured,
    bootstrap_required: payload.bootstrap_required ?? false,
    authenticated: payload.authenticated,
    username: payload.username ?? null,
    role,
    roles: normalizeConsoleRoles(payload.roles, role),
  };
}

function normalizeNotificationChannel(channel: {
  id: string;
  name: string;
  provider: NotificationProvider;
  webhook_url_masked?: string;
  webhookUrlMasked?: string;
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
    webhook_url_masked:
      channel.webhook_url_masked ??
      channel.webhookUrlMasked ??
      channel.webhook_url ??
      channel.webhookUrl ??
      "",
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

export function listServices(environment?: Environment, sourceKind?: string) {
  return request<ServiceListResponse>("/api/v1/services", {
    query: { environment, source_kind: sourceKind, limit: SERVICES_PAGE_SIZE, offset: 0 },
  });
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

export async function updateNotificationChannel(channelId: string, payload: NotificationChannelPatchRequest) {
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

export function listServiceTasks(
  serviceName: string,
  environment: Environment,
  lookbackMinutes: number,
  limit?: number,
) {
  return requestPaginatedItems<TaskDashboardListResponse["items"][number]>(
    `/api/v1/services/${encodeURIComponent(serviceName)}/tasks`,
    {
      query: {
        environment,
        lookback_minutes: lookbackMinutes,
      },
      pageSize: SERVICE_TASKS_PAGE_SIZE,
      targetCount: limit,
    },
  ) as Promise<TaskDashboardListResponse>;
}

export function listServiceInstances(serviceName: string, environment: Environment, limit?: number) {
  return requestPaginatedItems<InstanceListResponse["items"][number]>(
    `/api/v1/services/${encodeURIComponent(serviceName)}/instances`,
    {
      query: {
        environment,
      },
      pageSize: SERVICE_INSTANCES_PAGE_SIZE,
      targetCount: limit,
    },
  ) as Promise<InstanceListResponse>;
}

export function listServiceCommands(serviceName: string, environment: Environment, limit?: number) {
  return requestPaginatedItems<AgentCommandListResponse["items"][number]>(
    `/api/v1/services/${encodeURIComponent(serviceName)}/commands`,
    {
      query: {
        environment,
      },
      pageSize: SERVICE_COMMANDS_PAGE_SIZE,
      targetCount: limit,
    },
  ) as Promise<AgentCommandListResponse>;
}

export function listServiceSessions(serviceName: string, environment: Environment, limit?: number) {
  return requestPaginatedItems<AgentSessionListResponse["items"][number]>(
    `/api/v1/services/${encodeURIComponent(serviceName)}/sessions`,
    {
      query: {
        environment,
      },
      pageSize: SERVICE_SESSIONS_PAGE_SIZE,
      targetCount: limit,
    },
  ) as Promise<AgentSessionListResponse>;
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

export function listInstanceCommands(instanceId: string, limit?: number) {
  return requestPaginatedItems<AgentCommandListResponse["items"][number]>(
    `/api/v1/instances/${encodeURIComponent(instanceId)}/commands`,
    {
      pageSize: INSTANCE_COMMANDS_PAGE_SIZE,
      targetCount: limit,
    },
  ) as Promise<AgentCommandListResponse>;
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
  return request<ConsoleSessionPayload>("/api/v1/auth/session", {
    redirectOnUnauthorized: false,
  }).then(normalizeConsoleSession);
}

export function loginConsole(username: string, password: string) {
  return request<ConsoleSessionPayload>("/api/v1/auth/login", {
    method: "POST",
    body: { username, password },
    redirectOnUnauthorized: false,
  }).then(normalizeConsoleSession);
}

export function logoutConsole() {
  return request<ConsoleSessionPayload>("/api/v1/auth/logout", {
    method: "POST",
    redirectOnUnauthorized: false,
  }).then(normalizeConsoleSession);
}

export function logoutAllConsole() {
  return request<ConsoleSessionPayload>("/api/v1/auth/logout-all", {
    method: "POST",
    redirectOnUnauthorized: false,
  }).then(normalizeConsoleSession);
}
