import { useQuery } from "@tanstack/react-query";

import { getInstanceDetail } from "../../lib/api/client";
import type { Environment } from "../../lib/api/types";

export function useInstanceDetailQuery(
  serviceName: string,
  instanceId: string,
  environment: Environment,
  lookbackMinutes: number,
) {
  return useQuery({
    queryKey: ["instance-detail", serviceName, instanceId, environment, lookbackMinutes],
    queryFn: () => getInstanceDetail(serviceName, instanceId, environment, lookbackMinutes),
    refetchInterval: 5_000,
  });
}
