import { type QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createServiceCommandFanout,
  createTaskCommandFanout,
  createInstanceCommand,
  listInstanceCommands,
  listServiceCommands,
  listServiceSessions,
} from "../../lib/api/client";
import type {
  AgentCommandDeliveryMode,
  AgentCommandKind,
  Environment,
  ServiceCommandOfflineBehavior,
  ServiceCommandTargetMode,
  TaskCommandKind,
} from "../../lib/api/types";

type QueryOptions = {
  enabled?: boolean;
};

const COMMAND_REFETCH_INTERVAL_MS = 5_000;

export async function invalidateCommandStreamQueries(queryClient: QueryClient) {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ["service-dashboard"] }),
    queryClient.invalidateQueries({ queryKey: ["service-commands"] }),
    queryClient.invalidateQueries({ queryKey: ["instance-commands"] }),
    queryClient.invalidateQueries({ queryKey: ["instance-detail"] }),
  ]);
}

export async function invalidateSessionStreamQueries(queryClient: QueryClient) {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ["service-dashboard"] }),
    queryClient.invalidateQueries({ queryKey: ["service-sessions"] }),
    queryClient.invalidateQueries({ queryKey: ["service-instances"] }),
    queryClient.invalidateQueries({ queryKey: ["instance-detail"] }),
  ]);
}

export function useServiceCommandsQuery(
  serviceName: string,
  environment: Environment,
  options: QueryOptions = {},
) {
  return useQuery({
    queryKey: ["service-commands", serviceName, environment],
    queryFn: () => listServiceCommands(serviceName, environment),
    enabled: options.enabled ?? true,
    refetchInterval: COMMAND_REFETCH_INTERVAL_MS,
  });
}

export function useServiceSessionsQuery(
  serviceName: string,
  environment: Environment,
  options: QueryOptions = {},
) {
  return useQuery({
    queryKey: ["service-sessions", serviceName, environment],
    queryFn: () => listServiceSessions(serviceName, environment),
    enabled: options.enabled ?? true,
    refetchInterval: COMMAND_REFETCH_INTERVAL_MS,
  });
}

export function useInstanceCommandsQuery(instanceId: string, options: QueryOptions = {}) {
  return useQuery({
    queryKey: ["instance-commands", instanceId],
    queryFn: () => listInstanceCommands(instanceId),
    enabled: options.enabled ?? true,
    refetchInterval: COMMAND_REFETCH_INTERVAL_MS,
  });
}

export function useCreateInstanceCommandMutation(
  serviceName: string,
  environment: Environment,
  instanceId: string,
) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: {
      kind: AgentCommandKind;
      args?: Record<string, unknown>;
      timeout_s?: number;
      delivery_mode?: AgentCommandDeliveryMode;
      reason?: string;
    }) => createInstanceCommand(instanceId, payload),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["instance-commands", instanceId] }),
        queryClient.invalidateQueries({
          queryKey: ["instance-detail", serviceName, instanceId, environment],
        }),
        queryClient.invalidateQueries({ queryKey: ["service-dashboard", serviceName, environment] }),
        queryClient.invalidateQueries({ queryKey: ["service-commands", serviceName, environment] }),
        queryClient.invalidateQueries({ queryKey: ["service-sessions", serviceName, environment] }),
        queryClient.invalidateQueries({ queryKey: ["service-instances", serviceName, environment] }),
      ]);
    },
  });
}

export function useCreateServiceCommandFanoutMutation(
  serviceName: string,
  environment: Environment,
) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: {
      kind: Exclude<AgentCommandKind, "shutdown">;
      args?: Record<string, unknown>;
      timeout_s?: number;
      reason: string;
      target_mode: ServiceCommandTargetMode;
      target_instance_ids?: string[];
      offline_behavior: ServiceCommandOfflineBehavior;
    }) => createServiceCommandFanout(serviceName, environment, payload),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["service-dashboard", serviceName, environment] }),
        queryClient.invalidateQueries({ queryKey: ["service-commands", serviceName, environment] }),
        queryClient.invalidateQueries({ queryKey: ["service-sessions", serviceName, environment] }),
        queryClient.invalidateQueries({ queryKey: ["service-instances", serviceName, environment] }),
      ]);
    },
  });
}

export function useCreateTaskCommandFanoutMutation(
  serviceName: string,
  taskName: string,
  environment: Environment,
) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: {
      kind: TaskCommandKind;
      args?: Record<string, unknown>;
      timeout_s?: number;
      reason: string;
      target_mode?: ServiceCommandTargetMode;
      target_instance_ids?: string[];
      offline_behavior?: ServiceCommandOfflineBehavior;
    }) => createTaskCommandFanout(serviceName, taskName, environment, payload),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["service-dashboard", serviceName, environment] }),
        queryClient.invalidateQueries({ queryKey: ["service-commands", serviceName, environment] }),
        queryClient.invalidateQueries({ queryKey: ["service-sessions", serviceName, environment] }),
        queryClient.invalidateQueries({ queryKey: ["service-instances", serviceName, environment] }),
        queryClient.invalidateQueries({ queryKey: ["service-tasks", serviceName, environment] }),
        queryClient.invalidateQueries({ queryKey: ["task-detail", serviceName, taskName, environment] }),
      ]);
    },
  });
}
