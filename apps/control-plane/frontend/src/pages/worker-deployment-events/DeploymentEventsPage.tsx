import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { EmptyState } from "../../components/ui/EmptyState";
import { SignalConsoleHeader } from "../../components/ui/SignalConsoleHeader";
import { StatusBadge } from "../../components/ui/StatusBadge";
import {
  useWorkerDeploymentEventsQuery,
  useWorkerDeploymentQuery,
} from "../../features/worker-agents/queries";
import { formatDateTime, formatIdentifierPreview } from "../../lib/formatters";

export function DeploymentEventsPage() {
  const { t } = useTranslation();
  const { agentId, deploymentId } = useParams<{ agentId: string; deploymentId: string }>();
  const deploymentQuery = useWorkerDeploymentQuery(deploymentId ?? "");
  const eventsQuery = useWorkerDeploymentEventsQuery(deploymentId ?? "");

  if (!agentId || !deploymentId) {
    return (
      <EmptyState title={t("deploymentEvents.missingTitle")} body={t("deploymentEvents.missingBody")} />
    );
  }
  if (deploymentQuery.isPending) {
    return <div className="loading-block">{t("deploymentEvents.loading")}</div>;
  }

  const deployment = deploymentQuery.data;
  const events = eventsQuery.data?.items ?? [];

  return (
    <div className="ref-console-page signal-console-runtime-page deployment-events-page">
      <SignalConsoleHeader
        kicker={t("deploymentEvents.eyebrow")}
        title={deployment ? formatIdentifierPreview(deployment.deployment_id) : deploymentId}
        description={
          deployment ? (
            <p className="signal-console-hero-note">
              <StatusBadge
                value={deployment.observed_status === "running" ? "active" : "cancelled"}
                label={deployment.observed_status}
              />{" "}
              · desired {deployment.desired_status}
            </p>
          ) : (
            <p className="signal-console-hero-note">{String(deploymentQuery.error ?? "")}</p>
          )
        }
        side={
          <Link to={`/agents/${encodeURIComponent(agentId)}`}>
            {t("deploymentEvents.backToAgent")}
          </Link>
        }
      />

      <section className="ref-summary-strip runtime-summary-strip">
        <article className="ref-summary-chip ref-summary-chip-default">
          <span>{t("deploymentEvents.summaryEvents")}</span>
          <strong>{events.length}</strong>
        </article>
        <article className="ref-summary-chip ref-summary-chip-default">
          <span>{t("agentsDetail.tableHeaderDesired")}</span>
          <strong>{deployment?.desired_status ?? "—"}</strong>
        </article>
        <article className="ref-summary-chip ref-summary-chip-success">
          <span>{t("agentsDetail.tableHeaderObserved")}</span>
          <strong>{deployment?.observed_status ?? "—"}</strong>
        </article>
      </section>

      {eventsQuery.error ? (
        <EmptyState title={t("deploymentEvents.loadErrorTitle")} body={String(eventsQuery.error)} />
      ) : null}
      {eventsQuery.isPending ? (
        <div className="loading-block">{t("deploymentEvents.loading")}</div>
      ) : null}
      {!eventsQuery.isPending && !eventsQuery.error && events.length === 0 ? (
        <EmptyState title={t("deploymentEvents.emptyTitle")} body={t("deploymentEvents.emptyBody")} />
      ) : null}

      {events.length > 0 ? (
        <section className="ref-table-card runtime-table-card deployment-events-table-card">
          <div className="ref-table-head runtime-table-head runtime-events-grid">
            <span>{t("deploymentEvents.tableHeaderTime")}</span>
            <span>{t("deploymentEvents.tableHeaderType")}</span>
            <span>{t("deploymentEvents.tableHeaderStatus")}</span>
            <span>{t("deploymentEvents.tableHeaderMessage")}</span>
          </div>
          <div className="ref-table-body">
            {events.map((event, index) => (
              <article className="ref-table-row runtime-table-row runtime-events-grid" key={`${event.created_at}-${index}`}>
                <div className="ref-meta-cell">
                  <strong>{formatDateTime(event.created_at)}</strong>
                </div>
                <div className="ref-meta-cell">
                  <strong>{event.event_type}</strong>
                </div>
                <div className="ref-meta-cell">
                  {event.observed_status ? <span>{event.observed_status}</span> : "—"}
                </div>
                <div className="ref-meta-cell">
                  <span>{event.message || "—"}</span>
                </div>
              </article>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
