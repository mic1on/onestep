import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createPipeline,
  deletePipeline,
  getPipeline,
  listPipelines,
  updatePipeline,
  validatePipeline,
} from "../../lib/api/client";
import type { Pipeline, PipelineGraph } from "../../lib/api/types";

export const PIPELINES_QUERY_KEY = ["pipelines"];

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
