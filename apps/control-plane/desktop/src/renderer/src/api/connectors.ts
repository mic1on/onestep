import { apiGet } from "./client";
import type { ConnectorSummary, PaginatedResponse } from "./types";

export async function listConnectors() {
  const response = await apiGet<PaginatedResponse<ConnectorSummary>>("/api/v1/connectors");
  return response.items;
}
