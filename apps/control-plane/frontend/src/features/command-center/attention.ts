import type {
  ConnectorSummary,
  ServiceListResponse,
  UiStreamConnectionPhase,
  WorkerAgentListResponse,
  WorkerDeploymentListResponse,
  WorkerDeploymentObservedStatus,
  WorkerListResponse,
} from "../../lib/api/types";

export type CommandCenterSeverity = "critical" | "warning" | "info" | "ok";

export type CommandCenterItemKind =
  | "service"
  | "worker_agent"
  | "deployment"
  | "worker"
  | "connector"
  | "stream";

export type CommandCenterAttentionItem = {
  id: string;
  kind: CommandCenterItemKind;
  severity: CommandCenterSeverity;
  label: string;
  signal: string;
  nextActionLabel: string;
  href: string;
  updatedAt: string | null;
};

export type CommandCenterSummary = {
  attentionCount: number;
  activeServices: number;
  onlineInstancesLabel: string;
  deploymentCount: number;
  agentCapacityLabel: string;
  connectorCount: number;
};

export type CommandCenterModel = {
  summary: CommandCenterSummary;
  items: CommandCenterAttentionItem[];
};

export type BuildCommandCenterInput = {
  services?: ServiceListResponse;
  agents?: WorkerAgentListResponse;
  deployments?: WorkerDeploymentListResponse;
  workers?: WorkerListResponse;
  connectors?: ConnectorSummary[];
  commandStreamPhase?: UiStreamConnectionPhase;
};

const DEPLOYMENT_ATTENTION_STATUSES = new Set<WorkerDeploymentObservedStatus>([
  "failed",
  "cancelled",
  "stopping",
]);
const STREAM_ATTENTION_PHASES = new Set<UiStreamConnectionPhase>([
  "stale",
  "error",
  "reconnecting",
]);

export function buildCommandCenterModel(input: BuildCommandCenterInput): CommandCenterModel {
  const items: CommandCenterAttentionItem[] = [
    ...buildServiceItems(input.services),
    ...buildAgentItems(input.agents),
    ...buildDeploymentItems(input.deployments),
    ...buildWorkerItems(input.workers),
    ...buildStreamItems(input.commandStreamPhase),
  ];

  return {
    summary: {
      attentionCount: items.length,
      activeServices: input.services?.summary.total_services ?? input.services?.items.length ?? 0,
      onlineInstancesLabel: `${input.services?.summary.online_instances ?? 0}/${input.services?.summary.total_instances ?? 0}`,
      deploymentCount: input.deployments?.total ?? input.deployments?.items.length ?? 0,
      agentCapacityLabel: buildAgentCapacityLabel(input.agents),
      connectorCount: input.connectors?.length ?? 0,
    },
    items,
  };
}

function buildServiceItems(services?: ServiceListResponse): CommandCenterAttentionItem[] {
  if (!services) {
    return [];
  }

  return services.items
    .filter(
      (service) =>
        service.service_status === "attention" ||
        service.online_instance_count < service.instance_count,
    )
    .map((service) => ({
      id: `service:${service.environment}:${service.name}`,
      kind: "service",
      severity: service.online_instance_count === 0 ? "critical" : "warning",
      label: service.name,
      signal: `${service.online_instance_count}/${service.instance_count} instances online`,
      nextActionLabel: "Open service",
      href: `/services/${encodeURIComponent(service.name)}?environment=${service.environment}&lookback_minutes=60`,
      updatedAt: service.last_seen_at,
    }));
}

function buildAgentItems(agents?: WorkerAgentListResponse): CommandCenterAttentionItem[] {
  if (!agents) {
    return [];
  }

  return agents.items
    .filter((agent) => agent.status !== "online")
    .map((agent) => ({
      id: `agent:${agent.worker_agent_id}`,
      kind: "worker_agent",
      severity: "warning",
      label: agent.display_name,
      signal: `agent is ${agent.status}`,
      nextActionLabel: "Inspect agent",
      href: `/agents/${encodeURIComponent(agent.worker_agent_id)}`,
      updatedAt: agent.last_seen_at,
    }));
}

function buildDeploymentItems(deployments?: WorkerDeploymentListResponse): CommandCenterAttentionItem[] {
  if (!deployments) {
    return [];
  }

  return deployments.items
    .filter((deployment) => DEPLOYMENT_ATTENTION_STATUSES.has(deployment.observed_status))
    .map((deployment) => ({
      id: `deployment:${deployment.deployment_id}`,
      kind: "deployment",
      severity: deployment.observed_status === "failed" ? "critical" : "warning",
      label: deployment.workflow_package_id,
      signal: deployment.last_error_message ?? `deployment is ${deployment.observed_status}`,
      nextActionLabel: "Watch events",
      href: `/agents/${encodeURIComponent(deployment.worker_agent_id)}/deployments/${encodeURIComponent(deployment.deployment_id)}/events`,
      updatedAt: deployment.updated_at,
    }));
}

function buildWorkerItems(workers?: WorkerListResponse): CommandCenterAttentionItem[] {
  if (!workers) {
    return [];
  }

  return workers.items
    .filter((worker) => worker.status !== "ready")
    .map((worker) => ({
      id: `worker:${worker.id}`,
      kind: "worker",
      severity: "info",
      label: worker.name,
      signal: `worker is ${worker.status}`,
      nextActionLabel: "Open worker",
      href: `/workers/${encodeURIComponent(worker.id)}`,
      updatedAt: worker.updated_at,
    }));
}

function buildStreamItems(phase?: UiStreamConnectionPhase): CommandCenterAttentionItem[] {
  if (!phase || !STREAM_ATTENTION_PHASES.has(phase)) {
    return [];
  }

  return [
    {
      id: "stream:commands",
      kind: "stream",
      severity: phase === "error" ? "critical" : "warning",
      label: "Command stream",
      signal: `stream is ${phase}`,
      nextActionLabel: "Review live updates",
      href: "/services?environment=all",
      updatedAt: null,
    },
  ];
}

function buildAgentCapacityLabel(agents?: WorkerAgentListResponse) {
  if (!agents) {
    return "0/0";
  }

  const used = agents.items.reduce((total, agent) => total + agent.used_slots, 0);
  const total = agents.items.reduce(
    (sum, agent) => sum + agent.max_concurrent_deployments,
    0,
  );
  return `${used}/${total}`;
}
