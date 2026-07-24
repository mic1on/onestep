import { apiGet } from "./client";
import type { PaginatedResponse, WorkerAgentSummary } from "./types";

type ApiWorkerAgentSummary = {
  worker_agent_id: string;
  display_name: string;
  status?: string | null;
  last_seen_at?: string | null;
};

export async function listWorkerAgents() {
  const response = await apiGet<PaginatedResponse<ApiWorkerAgentSummary>>(
    "/api/v1/worker-agents?limit=100&offset=0",
  );
  return response.items.map(
    (agent): WorkerAgentSummary => ({
      id: agent.worker_agent_id,
      name: agent.display_name,
      status: agent.status ?? "unknown",
      last_seen_at: agent.last_seen_at ?? null,
    }),
  );
}
