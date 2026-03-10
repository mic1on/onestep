import type {
  ConsoleSessionResponse,
  Environment,
  InstanceDetailResponse,
  InstanceListResponse,
  ServiceDashboardResponse,
  ServiceListResponse,
  TaskDashboardListResponse,
  TaskDetailResponse,
} from "./types";
import { getApiBaseUrl } from "../runtime-config";

const API_BASE_URL = getApiBaseUrl();

type QueryValue = string | number | undefined | null;
type RequestOptions = {
  method?: "GET" | "POST";
  query?: Record<string, QueryValue>;
  body?: unknown;
  redirectOnUnauthorized?: boolean;
};

function buildUrl(path: string, query: Record<string, QueryValue> = {}) {
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
  const response = await fetch(buildUrl(path, options.query), {
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

  return (await response.json()) as T;
}

export function listServices(environment?: Environment) {
  return request<ServiceListResponse>("/api/v1/services", { query: { environment } });
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
