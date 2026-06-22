import { StatusBadge } from "../../../components/ui/StatusBadge";
import type { WorkerAgentSummary } from "../../../lib/api/types";

type WorkerAgentDeployCardProps = {
  agent: WorkerAgentSummary;
  disabled?: boolean;
  deploying?: boolean;
  deployingLabel: string;
  fallbackVersionLabel: string;
  onDeploy: (agentId: string) => void;
};

export function WorkerAgentDeployCard({
  agent,
  disabled = false,
  deploying = false,
  deployingLabel,
  fallbackVersionLabel,
  onDeploy,
}: WorkerAgentDeployCardProps) {
  return (
    <button
      className="worker-agent-deploy-card"
      disabled={disabled}
      onClick={() => onDeploy(agent.worker_agent_id)}
      type="button"
    >
      <span className="worker-agent-deploy-main">
        <strong>{agent.display_name}</strong>
        <span>{agent.worker_agent_id}</span>
      </span>
      <span className="worker-agent-deploy-meta">
        <StatusBadge value={agent.status} />
        <span>
          {agent.used_slots}/{agent.max_concurrent_deployments}
        </span>
        {deploying ? <span>{deployingLabel}</span> : null}
        <span>{agent.agent_version ?? fallbackVersionLabel}</span>
      </span>
    </button>
  );
}
