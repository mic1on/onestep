import { apiGet } from "./client";
import type { PaginatedResponse, WorkerSummary } from "./types";

export async function listWorkers() {
  const response = await apiGet<PaginatedResponse<WorkerSummary>>("/api/v1/workers");
  return response.items;
}
