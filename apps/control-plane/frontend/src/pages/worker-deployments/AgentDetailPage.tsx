import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { DeployDialog } from "../../features/worker-agents/components/DeployDialog";
import {
  useRestartWorkerDeploymentMutation,
  useStartWorkerDeploymentMutation,
  useStopWorkerDeploymentMutation,
  useWorkerAgentQuery,
  useWorkerDeploymentsQuery,
} from "../../features/worker-agents/queries";
import { EmptyState } from "../../components/ui/EmptyState";
import { SignalConsoleHeader } from "../../components/ui/SignalConsoleHeader";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { formatDateTime, formatRelativeTime, formatIdentifierPreview } from "../../lib/formatters";

export function AgentDetailPage() {
  const { t } = useTranslation();
  const { agentId } = useParams<{ agentId: string }>();
  const [deployOpen, setDeployOpen] = useState(false);
  const agentQuery = useWorkerAgentQuery(agentId ?? "");
  const deploymentsQuery = useWorkerDeploymentsQuery(agentId);
  const startMutation = useStartWorkerDeploymentMutation();
  const stopMutation = useStopWorkerDeploymentMutation();
  const restartMutation = useRestartWorkerDeploymentMutation();

  if (!agentId) {
    return <EmptyState title={t("agentsDetail.missingTitle")} body={t("agentsDetail.missingBody")} />;
  }
  if (agentQuery.isPending) {
    return <div className="loading-block">{t("agentsDetail.loading")}</div>;
  }
  if (agentQuery.error || !agentQuery.data) {
    return (
      <EmptyState title={t("agentsDetail.loadErrorTitle")} body={String(agentQuery.error)} />
    );
  }

  const agent = agentQuery.data;
  const deployments = deploymentsQuery.data?.items ?? [];
  const runningCount = deployments.filter((deployment) => deployment.observed_status === "running").length;
  const failedCount = deployments.filter((deployment) => deployment.observed_status === "failed").length;
  const activeMutationPending =
    startMutation.isPending || stopMutation.isPending || restartMutation.isPending;

  return (
    <div className="ref-console-page signal-console-runtime-page agent-detail-page">
      <SignalConsoleHeader
        kicker={t("agentsDetail.eyebrow")}
        title={agent.display_name}
        description={
          <p className="signal-console-hero-note">
            <StatusBadge value={agent.status} label={t(`status.${agent.status}`)} /> ·{" "}
            {t("agentsDetail.slots", {
              used: agent.used_slots,
              max: agent.max_concurrent_deployments,
            })}
          </p>
        }
        secondary={
          <div className="ref-meta-row">
            <span>{t("agentsDetail.executionMode")}: {agent.execution_mode}</span>
            <span>{t("agentsDetail.agentVersion")}: {agent.agent_version ?? t("common.notAvailable")}</span>
            <span>{t("agentsDetail.onestepVersion")}: {agent.onestep_version ?? t("common.notAvailable")}</span>
            <span>{t("agentsDetail.registeredAt")}: {formatDateTime(agent.registered_at)}</span>
          </div>
        }
        side={
          <button type="button" onClick={() => setDeployOpen(true)}>
            {t("agentsDetail.deployAction")}
          </button>
        }
      />

      <DeployDialog open={deployOpen} onClose={() => setDeployOpen(false)} />

      <section className="ref-summary-strip runtime-summary-strip">
        <article className="ref-summary-chip ref-summary-chip-default">
          <span>{t("agentsList.tableHeaderStatus")}</span>
          <strong>{agent.status}</strong>
        </article>
        <article className="ref-summary-chip ref-summary-chip-success">
          <span>{t("agentsDetail.summarySlots")}</span>
          <strong>
            {agent.used_slots}/{agent.max_concurrent_deployments}
          </strong>
        </article>
        <article className="ref-summary-chip ref-summary-chip-default">
          <span>{t("agentsDetail.tableHeaderDeployment")}</span>
          <strong>{deployments.length}</strong>
        </article>
        <article
          className={
            failedCount > 0
              ? "ref-summary-chip ref-summary-chip-danger"
              : "ref-summary-chip ref-summary-chip-default"
          }
        >
          <span>{t("agentsDetail.summaryRunningFailed")}</span>
          <strong>
            {runningCount}/{failedCount}
          </strong>
        </article>
      </section>

      {deploymentsQuery.error ? (
        <EmptyState title={t("agentsDetail.deploymentsLoadError")} body={String(deploymentsQuery.error)} />
      ) : null}
      {deploymentsQuery.isPending ? (
        <div className="loading-block">{t("agentsDetail.deploymentsLoading")}</div>
      ) : null}
      {!deploymentsQuery.isPending && !deploymentsQuery.error && deployments.length === 0 ? (
        <EmptyState
          title={t("agentsDetail.deploymentsEmptyTitle")}
          body={t("agentsDetail.deploymentsEmptyBody")}
        />
      ) : null}

      {deployments.length > 0 ? (
        <section className="ref-table-card runtime-table-card deployments-table-card">
          <div className="ref-table-head runtime-table-head runtime-deployments-grid">
            <span>{t("agentsDetail.tableHeaderDeployment")}</span>
            <span>{t("agentsDetail.tableHeaderDesired")}</span>
            <span>{t("agentsDetail.tableHeaderObserved")}</span>
            <span>{t("agentsDetail.tableHeaderStarted")}</span>
            <span>{t("agentsDetail.tableHeaderError")}</span>
            <span>{t("agentsDetail.tableHeaderActions")}</span>
          </div>
          <div className="ref-table-body">
            {deployments.map((deployment) => {
              const isRunning = deployment.observed_status === "running";
              const desiredRunning = deployment.desired_status === "running";
              return (
                <article className="ref-table-row runtime-table-row runtime-deployments-grid" key={deployment.deployment_id}>
                  <div className="ref-service-cell">
                    <Link
                      to={`/agents/${encodeURIComponent(agentId)}/deployments/${encodeURIComponent(deployment.deployment_id)}/events`}
                    >
                      <strong>{formatIdentifierPreview(deployment.deployment_id)}</strong>
                    </Link>
                    <span className="ref-service-tags">
                      <span className="ref-mini-tag">
                        pkg {formatIdentifierPreview(deployment.workflow_package_id)}
                      </span>
                    </span>
                  </div>
                  <div className="ref-meta-cell">
                    <StatusBadge value={deployment.desired_status === "running" ? "active" : "cancelled"} />
                  </div>
                  <div className="ref-meta-cell">
                    <StatusBadge value={mapObservedStatus(deployment.observed_status)} label={deployment.observed_status} />
                  </div>
                  <div className="ref-meta-cell">
                    <strong title={formatDateTime(deployment.started_at)}>
                      {formatRelativeTime(deployment.started_at)}
                    </strong>
                  </div>
                  <div className="ref-meta-cell">
                    <strong>{deployment.last_error_code ?? "—"}</strong>
                    <span>{deployment.last_error_message ?? ""}</span>
                  </div>
                  <div className="ref-meta-cell ref-page-actions">
                    {desiredRunning && !isRunning ? (
                      <button
                        type="button"
                        disabled={activeMutationPending}
                        onClick={() => void startMutation.mutateAsync(deployment.deployment_id)}
                      >
                        {t("agentsDetail.actionStart")}
                      </button>
                    ) : null}
                    {desiredRunning ? (
                      <button
                        type="button"
                        disabled={activeMutationPending}
                        onClick={() => void stopMutation.mutateAsync(deployment.deployment_id)}
                      >
                        {t("agentsDetail.actionStop")}
                      </button>
                    ) : null}
                    <button
                      type="button"
                      disabled={activeMutationPending}
                      onClick={() => void restartMutation.mutateAsync(deployment.deployment_id)}
                    >
                      {t("agentsDetail.actionRestart")}
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        </section>
      ) : null}
    </div>
  );
}

function mapObservedStatus(status: string) {
  if (status === "running") return "active" as const;
  if (status === "failed") return "failed" as const;
  if (status === "stopped" || status === "cancelled") return "cancelled" as const;
  if (
    status === "pending" ||
    status === "assigned" ||
    status === "preparing" ||
    status === "installing" ||
    status === "checking"
  ) {
    return "pending" as const;
  }
  return "unknown" as const;
}
