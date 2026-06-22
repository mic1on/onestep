import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { EmptyState } from "../../components/ui/EmptyState";
import { VibeField } from "../../components/ui/VibeField";
import { VibeSummaryStrip } from "../../components/ui/VibeSummary";
import { VibehubSelect } from "../../components/ui/VibehubSelect";
import {
  buildCommandCenterModel,
  type CommandCenterAttentionItem,
} from "../../features/command-center/attention";
import { useCommandStreamStatus } from "../../features/commands/useCommandStream";
import { useConnectorsQuery } from "../../features/connectors/queries";
import { useServicesQuery } from "../../features/services/queries";
import {
  useWorkerAgentsQuery,
  useWorkerDeploymentsQuery,
} from "../../features/worker-agents/queries";
import { useWorkersQuery } from "../../features/workers/queries";
import { formatRelativeTime } from "../../lib/formatters";

export function CommandCenterPage() {
  const { t } = useTranslation();
  const [search, setSearch] = useState("");
  const servicesQuery = useServicesQuery();
  const agentsQuery = useWorkerAgentsQuery();
  const deploymentsQuery = useWorkerDeploymentsQuery();
  const workersQuery = useWorkersQuery();
  const connectorsQuery = useConnectorsQuery();
  const commandStreamStatus = useCommandStreamStatus();

  const model = useMemo(
    () =>
      buildCommandCenterModel({
        services: servicesQuery.data,
        agents: agentsQuery.data,
        deployments: deploymentsQuery.data,
        workers: workersQuery.data,
        connectors: connectorsQuery.data?.items,
        commandStreamPhase: commandStreamStatus.phase,
      }),
    [
      servicesQuery.data,
      agentsQuery.data,
      deploymentsQuery.data,
      workersQuery.data,
      connectorsQuery.data,
      commandStreamStatus.phase,
    ],
  );

  const searchText = search.trim().toLowerCase();
  const filteredItems = model.items.filter((item) =>
    `${item.label} ${item.signal} ${item.kind}`.toLowerCase().includes(searchText),
  );
  const isPending =
    servicesQuery.isPending ||
    agentsQuery.isPending ||
    deploymentsQuery.isPending ||
    workersQuery.isPending ||
    connectorsQuery.isPending;
  const firstError =
    servicesQuery.error ||
    agentsQuery.error ||
    deploymentsQuery.error ||
    workersQuery.error ||
    connectorsQuery.error;

  return (
    <div className="ref-console-page command-center-page">
      <header className="command-center-header">
        <div className="command-center-header-copy">
          <span className="signal-console-kicker">{t("commandCenter.eyebrow")}</span>
          <h2>{t("commandCenter.title")}</h2>
          <p>{t("commandCenter.subtitle")}</p>
        </div>

        <div className="command-center-controls">
          <VibehubSelect
            disabled
            label={t("commandCenter.scopeLabel")}
            options={[{ value: "all", label: t("commandCenter.scopeAll") }]}
            value="all"
          />
          <VibeField
            label={t("commandCenter.searchLabel")}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={t("commandCenter.searchPlaceholder")}
            type="search"
            value={search}
          />
        </div>
      </header>

      <VibeSummaryStrip
        className="command-center-summary"
        items={[
          {
            label: t("commandCenter.summaryAttention"),
            tone: "danger",
            value: String(model.summary.attentionCount),
          },
          {
            label: t("commandCenter.summaryInstances"),
            tone: "success",
            value: model.summary.onlineInstancesLabel,
          },
          { label: t("commandCenter.summaryServices"), value: String(model.summary.activeServices) },
          { label: t("commandCenter.summaryDeployments"), value: String(model.summary.deploymentCount) },
          { label: t("commandCenter.summaryCapacity"), value: model.summary.agentCapacityLabel },
        ]}
      />

      {firstError ? (
        <EmptyState title={t("commandCenter.loadErrorTitle")} body={String(firstError)} />
      ) : null}
      {isPending ? <div className="loading-block">{t("commandCenter.loading")}</div> : null}

      {!isPending && !firstError ? (
        <div className="command-center-workbench">
          <section className="command-center-panel command-center-queue">
            <header className="panel-header">
              <div>
                <h3>{t("commandCenter.queueTitle")}</h3>
                <p>{t("commandCenter.queueSubtitle")}</p>
              </div>
            </header>
            {filteredItems.length === 0 ? (
              <EmptyState
                title={t("commandCenter.emptyTitle")}
                body={t("commandCenter.emptyBody")}
              />
            ) : (
              <div className="command-center-attention-list">
                {filteredItems.map((item) => (
                  <AttentionRow item={item} key={item.id} />
                ))}
              </div>
            )}
          </section>

          <section className="command-center-panel command-center-actions">
            <header className="panel-header">
              <div>
                <h3>{t("commandCenter.actionsTitle")}</h3>
                <p>{t("commandCenter.actionsSubtitle")}</p>
              </div>
            </header>
            <div className="command-center-action-list">
              {filteredItems.slice(0, 3).map((item) => (
                <CommandCenterAction item={item} key={`action:${item.id}`} />
              ))}
            </div>
          </section>
        </div>
      ) : null}

      <section className="command-center-lower">
        <SignalPanel
          title={t("commandCenter.lowerAgentsTitle")}
          value={model.summary.agentCapacityLabel}
        />
        <SignalPanel
          title={t("commandCenter.lowerBuildTitle")}
          value={String(
            workersQuery.data?.items.filter((worker) => worker.status === "ready").length ?? 0,
          )}
        />
        <SignalPanel
          title={t("commandCenter.lowerNotificationsTitle")}
          value={String(model.summary.connectorCount)}
        />
      </section>
    </div>
  );
}

function AttentionRow({ item }: { item: CommandCenterAttentionItem }) {
  const { t } = useTranslation();
  const actionLabel = translateActionLabel(item.nextActionLabel, t);

  return (
    <article className="command-center-attention-row">
      <div className="command-center-attention-main">
        <span className={`status-badge badge-${badgeTone(item.severity)}`}>
          {translateKindLabel(item.kind, t)}
        </span>
        <strong>{item.label}</strong>
        <p>{item.signal}</p>
      </div>
      <div className="command-center-attention-meta">
        <span>{item.updatedAt ? formatRelativeTime(item.updatedAt) : "--"}</span>
        <Link to={item.href}>{actionLabel}</Link>
      </div>
    </article>
  );
}

function CommandCenterAction({ item }: { item: CommandCenterAttentionItem }) {
  const { t } = useTranslation();
  const actionLabel = translateActionLabel(item.nextActionLabel, t);

  return (
    <Link className="command-center-action" to={item.href}>
      <span className={`command-center-dot is-${item.severity}`} />
      <span>
        <strong>{actionVerb(actionLabel)}</strong>
        <small>{item.label}</small>
      </span>
    </Link>
  );
}

function SignalPanel({ title, value }: { title: string; value: string }) {
  return (
    <article className="command-center-signal-panel">
      <span>{title}</span>
      <strong>{value}</strong>
    </article>
  );
}

function badgeTone(severity: CommandCenterAttentionItem["severity"]) {
  if (severity === "critical") return "danger";
  if (severity === "warning") return "warning";
  if (severity === "ok") return "success";
  return "accent";
}

function actionVerb(label: string) {
  return label.split(" ")[0] ?? label;
}

const actionLabelKeys: Record<string, string> = {
  "Open service": "commandCenter.actionOpenService",
  "Inspect agent": "commandCenter.actionInspectAgent",
  "Watch events": "commandCenter.actionWatchEvents",
  "Open step": "commandCenter.actionOpenWorker",
  "Review live updates": "commandCenter.actionReviewLiveUpdates",
};

function translateActionLabel(label: string, t: (key: string) => unknown) {
  const key = actionLabelKeys[label];
  return key ? String(t(key)) : label;
}

function translateKindLabel(
  kind: CommandCenterAttentionItem["kind"],
  t: (key: string) => unknown,
) {
  if (kind === "worker") return String(t("commandCenter.kindWorker"));
  if (kind === "worker_agent") return String(t("commandCenter.kindWorkerAgent"));
  return kind.replace("_", " ");
}
