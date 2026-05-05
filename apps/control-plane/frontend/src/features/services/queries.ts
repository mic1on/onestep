import { useQuery } from "@tanstack/react-query";

import {
  getServiceDashboard,
  listServiceInstances,
  listServiceTasks,
  listServices,
} from "../../lib/api/client";
import type { Environment } from "../../lib/api/types";

type QueryOptions = {
  enabled?: boolean;
};

type PaginatedQueryOptions = QueryOptions & {
  limit?: number;
};

const LIVE_REFETCH_INTERVAL_MS = 5_000;

export function useServicesQuery(environment?: Environment) {
  return useQuery({
    queryKey: ["services", environment ?? "all"],
    queryFn: () => listServices(environment),
  });
}

export function useServiceDashboardQuery(serviceName: string, environment: Environment, lookbackMinutes: number) {
  return useQuery({
    queryKey: ["service-dashboard", serviceName, environment, lookbackMinutes],
    queryFn: () => getServiceDashboard(serviceName, environment, lookbackMinutes),
    refetchInterval: LIVE_REFETCH_INTERVAL_MS,
  });
}

export function useServiceTasksQuery(
  serviceName: string,
  environment: Environment,
  lookbackMinutes: number,
  options: PaginatedQueryOptions = {},
) {
  return useQuery({
    queryKey: ["service-tasks", serviceName, environment, lookbackMinutes, options.limit ?? null],
    queryFn: () => listServiceTasks(serviceName, environment, lookbackMinutes, options.limit),
    enabled: options.enabled ?? true,
  });
}

export function useServiceInstancesQuery(
  serviceName: string,
  environment: Environment,
  options: PaginatedQueryOptions = {},
) {
  return useQuery({
    queryKey: ["service-instances", serviceName, environment, options.limit ?? null],
    queryFn: () => listServiceInstances(serviceName, environment, options.limit),
    enabled: options.enabled ?? true,
    refetchInterval: LIVE_REFETCH_INTERVAL_MS,
  });
}
