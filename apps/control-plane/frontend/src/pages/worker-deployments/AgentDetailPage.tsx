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
import { VibeActionGroup } from "../../components/ui/VibeActionGroup";
import { VibeDataRow, VibeDataTable } from "../../components/ui/VibeDataTable";
import { VibeButton } from "../../components/ui/VibeButton";
import { VibeSummaryStrip } from "../../components/ui/VibeSummary";
import { VibeTag, VibeTagGroup } from "../../components/ui/VibeTag";
import { formatDateTime, formatRelativeTime, formatIdentifierPreview } from "../../lib/formatters";
import { SegmentedControl } from "../../components/ui/SegmentedControl";

type AgentDetailTab = "overview" | "deployments" | "system";

export function AgentDetailPage() {
  const { t } = useTranslation();
  const { agentId } = useParams<{ agentId: string }>();
  const [deployOpen, setDeployOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<AgentDetailTab>("overview");
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
  const labelEntries = Object.entries(agent.labels);
  const platformEntries = Object.entries(agent.platform)
    .filter(([, value]) => ["string", "number", "boolean"].includes(typeof value))
    .slice(0, 6);

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
          <VibeButton onClick={() => setDeployOpen(true)} variant="primary">
            {t("agentsDetail.deployAction")}
          </VibeButton>
        }
      />

      <DeployDialog open={deployOpen} onClose={() => setDeployOpen(false)} />

      <VibeSummaryStrip
        className="runtime-summary-strip"
        items={[
          { label: t("agentsList.tableHeaderStatus"), value: agent.status },
          {
            label: t("agentsDetail.summarySlots"),
            tone: "success",
            value: `${agent.used_slots}/${agent.max_concurrent_deployments}`,
          },
          { label: t("agentsDetail.tableHeaderDeployment"), value: deployments.length },
          {
            label: t("agentsDetail.summaryRunningFailed"),
            tone: failedCount > 0 ? "danger" : "default",
            value: `${runningCount}/${failedCount}`,
          },
        ]}
      />

      <div className="worker-tab-bar agent-detail-tab-bar">
        <SegmentedControl<AgentDetailTab>
          ariaLabel={t("agentsDetail.tabsLabel")}
          value={activeTab}
          onChange={setActiveTab}
          options={[
            { label: t("agentsDetail.tabOverview"), value: "overview" },
            { label: t("agentsDetail.tabDeployments"), value: "deployments" },
            { label: t("agentsDetail.tabSystem"), value: "system" },
          ]}
        />
      </div>

      <section
        className={activeTab === "overview" ? "agent-overview-panel worker-tab-panel is-active" : "agent-overview-panel worker-tab-panel"}
        hidden={activeTab !== "overview"}
        role="tabpanel"
      >
        <section className="worker-studio-surface agent-studio-surface">
          <header className="worker-studio-head agent-studio-head">
            <div>
              <span>{t("agentsDetail.overviewEyebrow")}</span>
              <h3>{t("agentsDetail.overviewTitle")}</h3>
              <p>{t("agentsDetail.overviewSubtitle")}</p>
            </div>
            <div className="worker-studio-status-strip agent-studio-status-strip">
              <span>{t(`status.${agent.status}`, { defaultValue: agent.status })}</span>
              <span>{t("agentsDetail.slots", { used: agent.used_slots, max: agent.max_concurrent_deployments })}</span>
            </div>
          </header>

          <div className="worker-overview-grid agent-overview-grid">
            <article className="worker-overview-card agent-overview-card">
              <span>{t("agentsList.tableHeaderStatus")}</span>
              <strong>
                <StatusBadge
                  label={t(`status.${agent.status}`, { defaultValue: agent.status })}
                  value={agent.status}
                />
              </strong>
              <p>{t("agentsDetail.statusHint", { time: formatRelativeTime(agent.last_seen_at) })}</p>
            </article>
            <article className="worker-overview-card agent-overview-card">
              <span>{t("agentsDetail.summarySlots")}</span>
              <strong>
                {agent.used_slots}/{agent.max_concurrent_deployments}
              </strong>
              <p>{t("agentsDetail.slotHint")}</p>
            </article>
            <article className="worker-overview-card agent-overview-card">
              <span>{t("agentsDetail.agentVersion")}</span>
              <strong>{agent.agent_version ?? t("common.notAvailable")}</strong>
              <p>{t("agentsDetail.versionHint", { version: agent.onestep_version ?? t("common.notAvailable") })}</p>
            </article>
            <article className="worker-overview-card agent-overview-card">
              <span>{t("agentsDetail.registeredAt")}</span>
              <strong>{formatDateTime(agent.registered_at)}</strong>
              <p>{t("agentsDetail.lastSeenHint", { time: formatDateTime(agent.last_seen_at) })}</p>
            </article>
          </div>
        </section>
      </section>

      <section
        className={activeTab === "deployments" ? "agent-deployments-panel worker-tab-panel is-active" : "agent-deployments-panel worker-tab-panel"}
        hidden={activeTab !== "deployments"}
        role="tabpanel"
      >
        <section className="worker-studio-surface agent-studio-surface">
          <header className="worker-studio-head agent-studio-head">
            <div>
              <span>{t("agentsDetail.deploymentsEyebrow")}</span>
              <h3>{t("agentsDetail.deploymentsTitle")}</h3>
              <p>{t("agentsDetail.deploymentsSubtitle")}</p>
            </div>
            <VibeButton icon="+" onClick={() => setDeployOpen(true)} variant="primary">
              {t("agentsDetail.deployAction")}
            </VibeButton>
          </header>

          <div className="agent-panel-body">
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
                action={
                  <VibeButton icon="+" onClick={() => setDeployOpen(true)} variant="primary">
                    {t("agentsDetail.deployAction")}
                  </VibeButton>
                }
              />
            ) : null}

            {deployments.length > 0 ? (
              <VibeDataTable
                className="runtime-table-card deployments-table-card"
                columns={[
                  { key: "deployment", label: t("agentsDetail.tableHeaderDeployment") },
                  { key: "desired", label: t("agentsDetail.tableHeaderDesired") },
                  { key: "observed", label: t("agentsDetail.tableHeaderObserved") },
                  { key: "started", label: t("agentsDetail.tableHeaderStarted") },
                  { key: "error", label: t("agentsDetail.tableHeaderError") },
                  { key: "actions", label: t("agentsDetail.tableHeaderActions") },
                ]}
                gridClassName="runtime-deployments-grid"
                headClassName="runtime-table-head"
              >
                {deployments.map((deployment) => {
                  const isRunning = deployment.observed_status === "running";
                  const desiredRunning = deployment.desired_status === "running";
                  return (
                    <VibeDataRow
                      className="runtime-table-row"
                      gridClassName="runtime-deployments-grid"
                      key={deployment.deployment_id}
                    >
                      <div className="ref-service-cell">
                        <Link
                          to={`/agents/${encodeURIComponent(agentId)}/deployments/${encodeURIComponent(deployment.deployment_id)}/events`}
                        >
                          <strong>{formatIdentifierPreview(deployment.deployment_id)}</strong>
                        </Link>
                        <VibeTagGroup>
                          <VibeTag>
                            pkg {formatIdentifierPreview(deployment.workflow_package_id)}
                          </VibeTag>
                        </VibeTagGroup>
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
                      <VibeActionGroup className="ref-meta-cell">
                        {desiredRunning && !isRunning ? (
                          <VibeButton
                            disabled={activeMutationPending}
                            onClick={() => void startMutation.mutateAsync(deployment.deployment_id)}
                            variant="secondary"
                          >
                            {t("agentsDetail.actionStart")}
                          </VibeButton>
                        ) : null}
                        {desiredRunning ? (
                          <VibeButton
                            disabled={activeMutationPending}
                            onClick={() => void stopMutation.mutateAsync(deployment.deployment_id)}
                            variant="secondary"
                          >
                            {t("agentsDetail.actionStop")}
                          </VibeButton>
                        ) : null}
                        <VibeButton
                          disabled={activeMutationPending}
                          onClick={() => void restartMutation.mutateAsync(deployment.deployment_id)}
                          variant="secondary"
                        >
                          {t("agentsDetail.actionRestart")}
                        </VibeButton>
                      </VibeActionGroup>
                    </VibeDataRow>
                  );
                })}
              </VibeDataTable>
            ) : null}
          </div>
        </section>
      </section>

      <section
        className={activeTab === "system" ? "agent-system-panel worker-tab-panel is-active" : "agent-system-panel worker-tab-panel"}
        hidden={activeTab !== "system"}
        role="tabpanel"
      >
        <section className="worker-studio-surface agent-studio-surface">
          <header className="worker-studio-head agent-studio-head">
            <div>
              <span>{t("agentsDetail.systemEyebrow")}</span>
              <h3>{t("agentsDetail.systemTitle")}</h3>
              <p>{t("agentsDetail.systemSubtitle")}</p>
            </div>
          </header>

          <div className="worker-overview-grid agent-system-grid">
            <article className="worker-overview-card agent-overview-card">
              <span>{t("agentsList.executionModeTitle")}</span>
              <strong>{agent.execution_mode}</strong>
              <p>{t("agentsDetail.pythonVersion", { version: agent.python_version ?? t("common.notAvailable") })}</p>
            </article>
            <article className="worker-overview-card agent-overview-card">
              <span>{t("agentsList.capabilitiesTitle")}</span>
              <strong>{agent.capabilities.length}</strong>
              <VibeTagGroup>
                {agent.capabilities.length > 0 ? (
                  agent.capabilities.map((capability) => (
                    <VibeTag key={capability}>{capability}</VibeTag>
                  ))
                ) : (
                  <VibeTag>{t("agentsList.noCapabilities")}</VibeTag>
                )}
              </VibeTagGroup>
            </article>
            <article className="worker-overview-card agent-overview-card">
              <span>{t("agentsDetail.labelsTitle")}</span>
              <strong>{labelEntries.length}</strong>
              <VibeTagGroup>
                {labelEntries.length > 0 ? (
                  labelEntries.map(([key, value]) => (
                    <VibeTag key={key}>
                      {key}={value}
                    </VibeTag>
                  ))
                ) : (
                  <VibeTag>{t("agentsList.noLabels")}</VibeTag>
                )}
              </VibeTagGroup>
            </article>
            <article className="worker-overview-card agent-overview-card">
              <span>{t("agentsDetail.platformTitle")}</span>
              <strong>{platformEntries.length > 0 ? t("agentsDetail.platformDetected") : t("common.notAvailable")}</strong>
              <div className="agent-platform-list">
                {platformEntries.length > 0 ? (
                  platformEntries.map(([key, value]) => (
                    <span key={key}>
                      {key}: {String(value)}
                    </span>
                  ))
                ) : (
                  <span>{t("agentsDetail.platformEmpty")}</span>
                )}
              </div>
            </article>
          </div>
        </section>
      </section>
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
