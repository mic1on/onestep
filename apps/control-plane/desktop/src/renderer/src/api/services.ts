import { apiGet } from "./client";
import type {
  Environment,
  HealthStatus,
  InstanceSummary,
  PaginatedResponse,
  ServiceSummary,
  TaskSummary,
} from "./types";

type ApiServiceSummary = Omit<ServiceSummary, "health"> & {
  service_status: "online" | "attention" | "offline";
};

type ApiTaskSummary = {
  task_name: string;
  succeeded?: number;
  failed?: number;
  last_event_at?: string | null;
};

function serviceHealth(status: ApiServiceSummary["service_status"] | undefined): HealthStatus {
  if (status === "online") return "healthy";
  if (status === "attention") return "degraded";
  if (status === "offline") return "offline";
  return "unknown";
}

function normalizeService(service: ApiServiceSummary): ServiceSummary {
  return {
    ...service,
    health: serviceHealth(service.service_status),
  };
}

function normalizeTask(task: ApiTaskSummary): TaskSummary {
  const failures = task.failed ?? 0;
  return {
    name: task.task_name,
    status: failures > 0 ? "failed" : "healthy",
    success_count: task.succeeded ?? 0,
    failure_count: failures,
    last_event_at: task.last_event_at ?? null,
  };
}

export async function listServices(environment: Environment = "dev") {
  const response = await apiGet<PaginatedResponse<ApiServiceSummary>>(
    `/api/v1/services?environment=${encodeURIComponent(environment)}&limit=100&offset=0`,
  );
  return response.items.map(normalizeService);
}

export async function getService(serviceName: string, environment: Environment = "dev") {
  const service = await apiGet<ApiServiceSummary>(
    `/api/v1/services/${encodeURIComponent(serviceName)}?environment=${encodeURIComponent(environment)}`,
  );
  return normalizeService(service);
}

export async function listServiceTasks(serviceName: string, environment: Environment = "dev") {
  const response = await apiGet<PaginatedResponse<ApiTaskSummary>>(
    `/api/v1/services/${encodeURIComponent(serviceName)}/tasks?environment=${encodeURIComponent(
      environment,
    )}&lookback_minutes=60&limit=100&offset=0`,
  );
  return response.items.map(normalizeTask);
}

export async function listServiceInstances(serviceName: string, environment: Environment = "dev") {
  const response = await apiGet<PaginatedResponse<InstanceSummary>>(
    `/api/v1/services/${encodeURIComponent(serviceName)}/instances?environment=${encodeURIComponent(
      environment,
    )}&limit=100&offset=0`,
  );
  return response.items;
}
