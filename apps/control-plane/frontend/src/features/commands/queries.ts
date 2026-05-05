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
  ServiceCommandFanoutResponse,
  ServiceCommandTargetMode,
  TaskCommandKind,
  TaskDetailResponse,
  TaskInstanceControlState,
} from "../../lib/api/types";

type QueryOptions = {
  enabled?: boolean;
  limit?: number;
};

const COMMAND_REFETCH_INTERVAL_MS = 5_000;
const TASK_CONTROL_REVALIDATE_DELAY_MS = 1_500;

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
    queryKey: ["service-commands", serviceName, environment, options.limit ?? null],
    queryFn: () => listServiceCommands(serviceName, environment, options.limit),
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
    queryKey: ["service-sessions", serviceName, environment, options.limit ?? null],
    queryFn: () => listServiceSessions(serviceName, environment, options.limit),
    enabled: options.enabled ?? true,
    refetchInterval: COMMAND_REFETCH_INTERVAL_MS,
  });
}

export function useInstanceCommandsQuery(instanceId: string, options: QueryOptions = {}) {
  return useQuery({
    queryKey: ["instance-commands", instanceId, options.limit ?? null],
    queryFn: () => listInstanceCommands(instanceId, options.limit),
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
    onSuccess: async (response, payload) => {
      patchTaskDetailQueries(queryClient, serviceName, taskName, environment, response, payload.kind);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["service-dashboard", serviceName, environment] }),
        queryClient.invalidateQueries({ queryKey: ["service-commands", serviceName, environment] }),
        queryClient.invalidateQueries({ queryKey: ["service-sessions", serviceName, environment] }),
        queryClient.invalidateQueries({ queryKey: ["service-instances", serviceName, environment] }),
        queryClient.invalidateQueries({ queryKey: ["service-tasks", serviceName, environment] }),
      ]);
      globalThis.setTimeout(() => {
        void queryClient.invalidateQueries({ queryKey: ["task-detail", serviceName, taskName, environment] });
      }, TASK_CONTROL_REVALIDATE_DELAY_MS);
    },
  });
}

function patchTaskDetailQueries(
  queryClient: QueryClient,
  serviceName: string,
  taskName: string,
  environment: Environment,
  response: ServiceCommandFanoutResponse,
  kind: TaskCommandKind,
) {
  if (kind !== "pause_task" && kind !== "resume_task") {
    return;
  }

  const targetInstanceIds = new Set(
    [...response.dispatched, ...response.queued].map((target) => target.instance_id),
  );

  if (targetInstanceIds.size === 0) {
    return;
  }

  const queries = queryClient.getQueriesData<TaskDetailResponse>({
    queryKey: ["task-detail", serviceName, taskName, environment],
  });

  for (const [queryKey, cached] of queries) {
    if (!cached) {
      continue;
    }

    queryClient.setQueryData<TaskDetailResponse>(queryKey, {
      ...cached,
      task_control: {
        ...cached.task_control,
        instances: cached.task_control.instances.map((state) =>
          targetInstanceIds.has(state.instance_id) ? applyTaskControlStateTransition(state, kind) : state,
        ),
      },
    });
  }
}

function applyTaskControlStateTransition(
  state: TaskInstanceControlState,
  kind: Extract<TaskCommandKind, "pause_task" | "resume_task">,
): TaskInstanceControlState {
  if (kind === "pause_task") {
    return {
      ...state,
      state_known: true,
      pause_requested: false,
      paused: true,
      accepting_new_work: false,
    };
  }

  return {
    ...state,
    state_known: true,
    pause_requested: false,
    paused: false,
    accepting_new_work: true,
  };
}
