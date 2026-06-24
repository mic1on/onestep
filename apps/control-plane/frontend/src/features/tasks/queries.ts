import { useQuery } from "@tanstack/react-query";

import { getTaskDetail } from "../../lib/api/client";
import type { Environment } from "../../lib/api/types";

const LIVE_REFETCH_INTERVAL_MS = 5_000;

export function useTaskDetailQuery(
  serviceName: string,
  taskName: string,
  environment: Environment,
  lookbackMinutes: number,
) {
  return useQuery({
    queryKey: ["task-detail", serviceName, taskName, environment, lookbackMinutes],
    queryFn: () => getTaskDetail(serviceName, taskName, environment, lookbackMinutes),
    refetchInterval: LIVE_REFETCH_INTERVAL_MS,
  });
}
