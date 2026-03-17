import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createInstanceCommand,
  listInstanceCommands,
  listServiceCommands,
  listServiceSessions,
} from "../../lib/api/client";
import type { AgentCommandKind, Environment } from "../../lib/api/types";

type QueryOptions = {
  enabled?: boolean;
};

const COMMAND_REFETCH_INTERVAL_MS = 5_000;

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
