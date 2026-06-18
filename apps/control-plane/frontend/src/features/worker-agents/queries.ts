import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createWorkerDeployment,
  createWorkflowPackage,
  getWorkerAgent,
  getWorkerDeployment,
  listWorkerAgents,
  listWorkerDeploymentEvents,
  listWorkerDeployments,
  restartWorkerDeployment,
  startWorkerDeployment,
  stopWorkerDeployment,
} from "../../lib/api/client";
import type { WorkerDeploymentDesiredStatus } from "../../lib/api/types";

export const WORKER_AGENTS_QUERY_KEY = ["worker-agents"];
export const WORKER_DEPLOYMENTS_QUERY_KEY = ["worker-deployments"];

export function workerAgentQueryKey(workerAgentId: string) {
  return ["worker-agent", workerAgentId];
}

export function workerDeploymentQueryKey(deploymentId: string) {
  return ["worker-deployment", deploymentId];
}

export function workerDeploymentEventsQueryKey(deploymentId: string) {
  return ["worker-deployment-events", deploymentId];
}

export function useWorkerAgentsQuery() {
  return useQuery({
    queryKey: WORKER_AGENTS_QUERY_KEY,
    queryFn: () => listWorkerAgents(),
  });
}

export function useWorkerAgentQuery(workerAgentId: string) {
  return useQuery({
    queryKey: workerAgentQueryKey(workerAgentId),
    queryFn: () => getWorkerAgent(workerAgentId),
    enabled: Boolean(workerAgentId),
  });
}

export function useWorkerDeploymentsQuery(workerAgentId?: string) {
  return useQuery({
    queryKey: workerAgentId ? ["worker-deployments", workerAgentId] : WORKER_DEPLOYMENTS_QUERY_KEY,
    queryFn: () => listWorkerDeployments({ workerAgentId }),
  });
}

export function useWorkerDeploymentQuery(deploymentId: string) {
  return useQuery({
    queryKey: workerDeploymentQueryKey(deploymentId),
    queryFn: () => getWorkerDeployment(deploymentId),
    enabled: Boolean(deploymentId),
  });
}

export function useWorkerDeploymentEventsQuery(deploymentId: string) {
  return useQuery({
    queryKey: workerDeploymentEventsQueryKey(deploymentId),
    queryFn: () => listWorkerDeploymentEvents(deploymentId),
    enabled: Boolean(deploymentId),
  });
}

function invalidateAgentDeployments(queryClient: ReturnType<typeof useQueryClient>) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: WORKER_AGENTS_QUERY_KEY }),
    queryClient.invalidateQueries({ queryKey: WORKER_DEPLOYMENTS_QUERY_KEY }),
    queryClient.invalidateQueries({ queryKey: ["worker-deployments"] }),
  ]);
}

export function useStartWorkerDeploymentMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (deploymentId: string) => startWorkerDeployment(deploymentId),
    onSuccess: async (deployment) => {
      queryClient.setQueryData(workerDeploymentQueryKey(deployment.deployment_id), deployment);
      await invalidateAgentDeployments(queryClient);
    },
  });
}

export function useStopWorkerDeploymentMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (deploymentId: string) => stopWorkerDeployment(deploymentId),
    onSuccess: async (deployment) => {
      queryClient.setQueryData(workerDeploymentQueryKey(deployment.deployment_id), deployment);
      await invalidateAgentDeployments(queryClient);
    },
  });
}

export function useRestartWorkerDeploymentMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (deploymentId: string) => restartWorkerDeployment(deploymentId),
    onSuccess: async (deployment) => {
      queryClient.setQueryData(workerDeploymentQueryKey(deployment.deployment_id), deployment);
      await invalidateAgentDeployments(queryClient);
    },
  });
}

export type CreateDeploymentInput = {
  workflowPackageId: string;
  workerAgentId: string;
  desiredStatus: WorkerDeploymentDesiredStatus;
};

export function useCreateWorkerDeploymentMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateDeploymentInput) =>
      createWorkerDeployment({
        workflow_package_id: input.workflowPackageId,
        worker_agent_id: input.workerAgentId,
        desired_status: input.desiredStatus,
      }),
    onSuccess: async (deployment) => {
      queryClient.setQueryData(workerDeploymentQueryKey(deployment.deployment_id), deployment);
      await invalidateAgentDeployments(queryClient);
    },
  });
}

export type UploadPackageInput = {
  file: Blob;
  workflowId: string;
  version: string;
  filename?: string;
  entrypoint?: string;
};

export function useCreateWorkflowPackageMutation() {
  return useMutation({
    mutationFn: (input: UploadPackageInput) => createWorkflowPackage(input),
  });
}
