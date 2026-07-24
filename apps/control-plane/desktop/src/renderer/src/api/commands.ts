import { apiGet } from "./client";
import { listServices } from "./services";
import type { CommandSummary, Environment, PaginatedResponse } from "./types";

type ApiCommandSummary = {
  command_id: string;
  kind?: string | null;
  status: string;
  created_at?: string | null;
  updated_at?: string | null;
};

function normalizeCommand(command: ApiCommandSummary): CommandSummary {
  return {
    id: command.command_id,
    status: command.status,
    command_type: command.kind ?? "command",
    created_at: command.created_at ?? null,
    updated_at: command.updated_at ?? null,
  };
}

async function listServiceCommands(serviceName: string, environment: Environment) {
  const response = await apiGet<PaginatedResponse<ApiCommandSummary>>(
    `/api/v1/services/${encodeURIComponent(serviceName)}/commands?environment=${encodeURIComponent(
      environment,
    )}&limit=25&offset=0`,
  );
  return response.items.map(normalizeCommand);
}

export async function listRecentCommands() {
  const services = await listServices();
  const results = await Promise.allSettled(
    services.slice(0, 20).map((service) => listServiceCommands(service.name, service.environment)),
  );
  return results
    .flatMap((result) => (result.status === "fulfilled" ? result.value : []))
    .sort((a, b) => (b.updated_at ?? b.created_at ?? "").localeCompare(a.updated_at ?? a.created_at ?? ""))
    .slice(0, 25);
}

export async function cancelCommand(_commandId: string): Promise<CommandSummary> {
  throw new Error("Command cancellation is not exposed by the current control-plane API");
}
