import { useQuery } from "@tanstack/react-query";

import {
  getServiceDashboard,
  listServiceInstances,
  listServiceTasks,
  listServices,
} from "../../lib/api/client";
import type { Environment } from "../../lib/api/types";

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

export function useServiceTasksQuery(serviceName: string, environment: Environment, lookbackMinutes: number) {
  return useQuery({
    queryKey: ["service-tasks", serviceName, environment, lookbackMinutes],
    queryFn: () => listServiceTasks(serviceName, environment, lookbackMinutes),
  });
}

export function useServiceInstancesQuery(serviceName: string, environment: Environment) {
  return useQuery({
    queryKey: ["service-instances", serviceName, environment],
    queryFn: () => listServiceInstances(serviceName, environment),
  });
}
