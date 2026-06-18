import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createWorker,
  deleteWorker,
  deployWorker,
  listWorkers,
  updateWorker,
} from "../../lib/api/client";
import type {
  WorkerCreateRequest,
  WorkerDeployRequest,
  WorkerUpdateRequest,
} from "../../lib/api/types";

export const WORKERS_QUERY_KEY = ["workers"];

export function useWorkersQuery() {
  return useQuery({
    queryKey: WORKERS_QUERY_KEY,
    queryFn: () => listWorkers(),
  });
}

export function useCreateWorkerMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: WorkerCreateRequest) => createWorker(payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: WORKERS_QUERY_KEY }),
  });
}

export function useUpdateWorkerMutation(workerId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: WorkerUpdateRequest) => updateWorker(workerId, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: WORKERS_QUERY_KEY }),
  });
}

export function useDeleteWorkerMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (workerId: string) => deleteWorker(workerId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: WORKERS_QUERY_KEY }),
  });
}

export function useDeployWorkerMutation(workerId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: WorkerDeployRequest) => deployWorker(workerId, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: WORKERS_QUERY_KEY }),
  });
}
