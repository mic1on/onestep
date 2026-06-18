import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { EmptyState } from "../../components/ui/EmptyState";
import { SignalConsoleHeader } from "../../components/ui/SignalConsoleHeader";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { useWorkerAgentsQuery } from "../../features/worker-agents/queries";
import { formatDateTime, formatRelativeTime } from "../../lib/formatters";

export function WorkerAgentsPage() {
  const { t } = useTranslation();
  const agentsQuery = useWorkerAgentsQuery();
  const agents = agentsQuery.data?.items ?? [];
  const total = agentsQuery.data?.total ?? 0;
  const onlineCount = agents.filter((agent) => agent.status === "online").length;

  return (
    <div className="ref-console-page signal-console-runtime-page worker-agents-page">
      <SignalConsoleHeader
        kicker={t("agentsList.eyebrow")}
        title={t("agentsList.title")}
        description={<p className="signal-console-hero-note">{t("agentsList.subtitle")}</p>}
        side={
          <div className="signal-console-hero-actions">
            <div className="signal-console-metric">
              <span>{t("agentsList.summaryAgents")}</span>
              <strong>{total}</strong>
            </div>
          </div>
        }
      />

      <section className="ref-summary-strip runtime-summary-strip">
        <article className="ref-summary-chip ref-summary-chip-default">
          <span>{t("agentsList.summaryAgents")}</span>
          <strong>{total}</strong>
        </article>
        <article className="ref-summary-chip ref-summary-chip-success">
          <span>{t("agentsList.summaryOnline")}</span>
          <strong>{onlineCount}/{total}</strong>
        </article>
        <article
          className={
            total - onlineCount > 0
              ? "ref-summary-chip ref-summary-chip-danger"
              : "ref-summary-chip ref-summary-chip-default"
          }
        >
          <span>{t("agentsList.summaryOffline")}</span>
          <strong>{total - onlineCount}</strong>
        </article>
      </section>

      {agentsQuery.error ? (
        <EmptyState title={t("agentsList.loadErrorTitle")} body={String(agentsQuery.error)} />
      ) : null}
      {agentsQuery.isPending ? (
        <div className="loading-block">{t("agentsList.loading")}</div>
      ) : null}
      {!agentsQuery.isPending && !agentsQuery.error && agents.length === 0 ? (
        <EmptyState title={t("agentsList.emptyTitle")} body={t("agentsList.emptyBody")} />
      ) : null}

      {agents.length > 0 ? (
        <section className="ref-table-card runtime-table-card agents-table-card">
          <div className="ref-table-head runtime-table-head runtime-agents-grid">
            <span>{t("agentsList.tableHeaderName")}</span>
            <span>{t("agentsList.tableHeaderStatus")}</span>
            <span>{t("agentsList.tableHeaderSlots")}</span>
            <span>{t("agentsList.tableHeaderMode")}</span>
            <span>{t("agentsList.tableHeaderVersion")}</span>
            <span>{t("agentsList.tableHeaderLastSeen")}</span>
          </div>
          <div className="ref-table-body">
            {agents.map((agent) => (
              <article className="ref-table-row runtime-table-row runtime-agents-grid" key={agent.worker_agent_id}>
                <div className="ref-service-cell">
                  <Link
                    className="ref-service-link"
                    to={`/agents/${encodeURIComponent(agent.worker_agent_id)}`}
                  >
                    <strong>{agent.display_name}</strong>
                    <span className="ref-service-tags">
                      {Object.entries(agent.labels).map(([key, value]) => (
                        <span key={key} className="ref-mini-tag">
                          {key}={value}
                        </span>
                      ))}
                    </span>
                  </Link>
                </div>
                <div className="ref-meta-cell">
                  <StatusBadge value={agent.status} />
                </div>
                <div className="ref-meta-cell">
                  <strong>
                    {agent.used_slots}/{agent.max_concurrent_deployments}
                  </strong>
                </div>
                <div className="ref-meta-cell">
                  <strong>{agent.execution_mode}</strong>
                </div>
                <div className="ref-meta-cell">
                  <strong>{agent.agent_version ?? t("common.notAvailable")}</strong>
                  <span>{agent.onestep_version ?? ""}</span>
                </div>
                <div className="ref-meta-cell">
                  <strong title={formatDateTime(agent.last_seen_at)}>
                    {formatRelativeTime(agent.last_seen_at)}
                  </strong>
                  <span>{formatDateTime(agent.registered_at)}</span>
                </div>
              </article>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
