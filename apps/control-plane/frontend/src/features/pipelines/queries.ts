import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createPipelineCredential,
  createPipeline,
  deletePipelineCredential,
  deletePipeline,
  exportPipeline,
  getPipeline,
  listPipelineConnectors,
  listPipelineCredentials,
  listPipelines,
  updatePipelineCredential,
  updatePipeline,
  validatePipeline,
} from "../../lib/api/client";
import type { Pipeline, PipelineGraph } from "../../lib/api/types";

export const PIPELINES_QUERY_KEY = ["pipelines"];
export const PIPELINE_CONNECTORS_QUERY_KEY = ["pipeline-connectors"];
export const PIPELINE_CREDENTIALS_QUERY_KEY = ["pipeline-credentials"];

export function pipelineQueryKey(pipelineId: string) {
  return ["pipeline", pipelineId];
}

export function usePipelinesQuery() {
  return useQuery({
    queryKey: PIPELINES_QUERY_KEY,
    queryFn: () => listPipelines(),
  });
}

export function usePipelineQuery(pipelineId: string) {
  return useQuery({
    queryKey: pipelineQueryKey(pipelineId),
    queryFn: () => getPipeline(pipelineId),
    enabled: Boolean(pipelineId),
  });
}

export function usePipelineConnectorsQuery() {
  return useQuery({
    queryKey: PIPELINE_CONNECTORS_QUERY_KEY,
    queryFn: () => listPipelineConnectors(),
  });
}

export function usePipelineCredentialsQuery() {
  return useQuery({
    queryKey: PIPELINE_CREDENTIALS_QUERY_KEY,
    queryFn: () => listPipelineCredentials(),
  });
}

export function useCreatePipelineMutation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: { name: string; description: string; graph: PipelineGraph }) =>
      createPipeline(payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: PIPELINES_QUERY_KEY });
    },
  });
}

export function useUpdatePipelineMutation(pipelineId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: Partial<Pick<Pipeline, "name" | "description" | "graph">>) =>
      updatePipeline(pipelineId, payload),
    onSuccess: async (pipeline) => {
      queryClient.setQueryData(pipelineQueryKey(pipeline.id), pipeline);
      await queryClient.invalidateQueries({ queryKey: PIPELINES_QUERY_KEY });
    },
  });
}

export function useDeletePipelineMutation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (pipelineId: string) => deletePipeline(pipelineId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: PIPELINES_QUERY_KEY });
    },
  });
}

export function useValidatePipelineMutation(pipelineId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () => validatePipeline(pipelineId),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: PIPELINES_QUERY_KEY }),
        queryClient.invalidateQueries({ queryKey: pipelineQueryKey(pipelineId) }),
      ]);
    },
  });
}

export function useExportPipelineMutation(pipelineId: string) {
  return useMutation({
    mutationFn: () => exportPipeline(pipelineId),
  });
}

export function useCreatePipelineCredentialMutation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: {
      name: string;
      connector_type: string;
      config: Record<string, unknown>;
      env_vars: Record<string, string>;
    }) => createPipelineCredential(payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: PIPELINE_CREDENTIALS_QUERY_KEY });
    },
  });
}

export function useUpdatePipelineCredentialMutation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      credentialId,
      payload,
    }: {
      credentialId: string;
      payload: Parameters<typeof updatePipelineCredential>[1];
    }) => updatePipelineCredential(credentialId, payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: PIPELINE_CREDENTIALS_QUERY_KEY });
    },
  });
}

export function useDeletePipelineCredentialMutation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (credentialId: string) => deletePipelineCredential(credentialId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: PIPELINE_CREDENTIALS_QUERY_KEY });
    },
  });
}
