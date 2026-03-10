import { useQuery } from "@tanstack/react-query";

import { getConsoleSession } from "../../lib/api/client";

export function useConsoleSessionQuery() {
  return useQuery({
    queryKey: ["console-session"],
    queryFn: getConsoleSession,
    staleTime: 30_000,
  });
}
