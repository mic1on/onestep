import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createWorker,
  deleteWorker,
  deployWorker,
  downloadWorkerPackage,
  getWorker,
  listWorkers,
  updateWorker,
} from "../../lib/api/client";
import type {
  WorkerCreateRequest,
  WorkerDeployRequest,
  WorkerListResponse,
  WorkerUpdateRequest,
} from "../../lib/api/types";

export const WORKERS_QUERY_KEY = ["workers"];
export const workerQueryKey = (workerId: string) => [...WORKERS_QUERY_KEY, workerId] as const;

export function useWorkersQuery() {
  return useQuery({
    queryKey: WORKERS_QUERY_KEY,
    queryFn: () => listWorkers(),
  });
}

export function useWorkerQuery(workerId: string | undefined, enabled: boolean) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: workerId ? workerQueryKey(workerId) : [...WORKERS_QUERY_KEY, "detail"],
    queryFn: () => getWorker(workerId ?? ""),
    enabled: enabled && Boolean(workerId),
    initialData: () => {
      if (!workerId) return undefined;
      return queryClient
        .getQueryData<WorkerListResponse>(WORKERS_QUERY_KEY)
        ?.items.find((worker) => worker.id === workerId);
    },
  });
}

export function useCreateWorkerMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: WorkerCreateRequest) => createWorker(payload),
    onSuccess: (worker) => {
      queryClient.setQueryData(workerQueryKey(worker.id), worker);
      queryClient.invalidateQueries({ queryKey: WORKERS_QUERY_KEY });
    },
  });
}

export function useUpdateWorkerMutation(workerId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: WorkerUpdateRequest) => updateWorker(workerId, payload),
    onSuccess: (worker) => {
      queryClient.setQueryData(workerQueryKey(worker.id), worker);
      queryClient.invalidateQueries({ queryKey: WORKERS_QUERY_KEY });
    },
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

export function useDownloadWorkerPackageMutation(workerId: string) {
  return useMutation({
    mutationFn: () => downloadWorkerPackage(workerId),
  });
}
