import { useQuery } from "@tanstack/react-query";

import { getConsoleSession } from "../../lib/api/client";

export const consoleSessionQueryKey = ["console-session"] as const;

export function useConsoleSessionQuery() {
  return useQuery({
    queryKey: consoleSessionQueryKey,
    queryFn: getConsoleSession,
    staleTime: 30_000,
  });
}
