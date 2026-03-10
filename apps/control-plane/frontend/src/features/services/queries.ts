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
  });
}

export function useServiceTasksQuery(
  serviceName: string,
  environment: Environment,
  lookbackMinutes: number,
  options: QueryOptions = {},
) {
  return useQuery({
    queryKey: ["service-tasks", serviceName, environment, lookbackMinutes],
    queryFn: () => listServiceTasks(serviceName, environment, lookbackMinutes),
    enabled: options.enabled ?? true,
  });
}

export function useServiceInstancesQuery(
  serviceName: string,
  environment: Environment,
  options: QueryOptions = {},
) {
  return useQuery({
    queryKey: ["service-instances", serviceName, environment],
    queryFn: () => listServiceInstances(serviceName, environment),
    enabled: options.enabled ?? true,
  });
}
