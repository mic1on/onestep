import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useEffect, useMemo, useState } from "react";

import { EmptyState } from "../../components/ui/EmptyState";
import { OverflowDialog } from "../../components/ui/OverflowDialog";
import { SignalConsoleHeader } from "../../components/ui/SignalConsoleHeader";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { VibeActionGroup } from "../../components/ui/VibeActionGroup";
import { VibeButton } from "../../components/ui/VibeButton";
import { VibeDialogField } from "../../components/ui/VibeDialogField";
import { VibeInlineButton } from "../../components/ui/VibeInlineButton";
import { VibeSummaryStrip } from "../../components/ui/VibeSummary";
import { VibeTag, VibeTagGroup } from "../../components/ui/VibeTag";
import { useWorkerAgentsQuery } from "../../features/worker-agents/queries";
import { formatDateTime, formatRelativeTime } from "../../lib/formatters";

function defaultPlaneUrl() {
  if (typeof window === "undefined") {
    return "http://localhost:8000";
  }
  return window.location.origin;
}

function shellQuote(value: string) {
  if (value === "$(hostname)") {
    return '"$(hostname)"';
  }
  return `'${value.replace(/'/g, "'\\''")}'`;
}

function buildInstallCommand({
  planeUrl,
  registrationToken,
  agentName,
  maxConcurrency,
}: {
  planeUrl: string;
  registrationToken: string;
  agentName: string;
  maxConcurrency: string;
}) {
  const normalizedPlaneUrl = (planeUrl.trim() || "http://localhost:8000").replace(
    /\/+$/,
    "",
  );
  const normalizedToken = registrationToken.trim() || "<registration-token>";
  const normalizedAgentName = agentName.trim() || "$(hostname)";
  const normalizedMaxConcurrency = maxConcurrency.trim() || "1";
  const maxConcurrencyArg = /^\d+$/.test(normalizedMaxConcurrency)
    ? normalizedMaxConcurrency
    : shellQuote(normalizedMaxConcurrency);
  return [
    "curl -fsSL",
    `${normalizedPlaneUrl}/agent-install.sh`,
    "| bash -s --",
    "--token",
    shellQuote(normalizedToken),
    "--name",
    shellQuote(normalizedAgentName),
    "--max-concurrency",
    maxConcurrencyArg,
  ].join(" ");
}

export function WorkerAgentsPage() {
  const { t } = useTranslation();
  const agentsQuery = useWorkerAgentsQuery();
  const agents = agentsQuery.data?.items ?? [];
  const total = agentsQuery.data?.total ?? 0;
  const onlineCount = agents.filter((agent) => agent.status === "online").length;
  const [installDialogOpen, setInstallDialogOpen] = useState(false);
  const [planeUrl, setPlaneUrl] = useState(defaultPlaneUrl);
  const [registrationToken, setRegistrationToken] = useState("");
  const [agentName, setAgentName] = useState("$(hostname)");
  const [maxConcurrency, setMaxConcurrency] = useState("1");
  const [copied, setCopied] = useState(false);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const installCommand = useMemo(
    () =>
      buildInstallCommand({
        planeUrl,
        registrationToken,
        agentName,
        maxConcurrency,
      }),
    [agentName, maxConcurrency, planeUrl, registrationToken],
  );
  const selectedAgent = useMemo(() => {
    if (agents.length === 0) return null;
    return (
      agents.find((agent) => agent.worker_agent_id === selectedAgentId) ??
      agents[0]
    );
  }, [agents, selectedAgentId]);

  useEffect(() => {
    if (agents.length === 0) {
      setSelectedAgentId(null);
      return;
    }
    if (!selectedAgentId || !agents.some((agent) => agent.worker_agent_id === selectedAgentId)) {
      setSelectedAgentId(agents[0].worker_agent_id);
    }
  }, [agents, selectedAgentId]);

  async function copyInstallCommand() {
    await navigator.clipboard.writeText(installCommand);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  }

  return (
    <div className="ref-console-page signal-console-runtime-page worker-agents-page">
      <SignalConsoleHeader
        kicker={t("agentsList.eyebrow")}
        title={t("agentsList.title")}
        description={<p className="signal-console-hero-note">{t("agentsList.subtitle")}</p>}
        side={
          <div className="signal-console-hero-actions signal-console-header-actions worker-agents-hero-actions">
            <VibeActionGroup>
              <VibeButton
                className="agent-install-open-button"
                icon="+"
                onClick={() => setInstallDialogOpen(true)}
                variant="primary"
              >
                {t("agentsList.addAgent")}
              </VibeButton>
            </VibeActionGroup>
          </div>
        }
      />

      <VibeSummaryStrip
        className="runtime-summary-strip"
        items={[
          { label: t("agentsList.summaryAgents"), value: total },
          { label: t("agentsList.summaryOnline"), tone: "success", value: `${onlineCount}/${total}` },
          {
            label: t("agentsList.summaryOffline"),
            tone: total - onlineCount > 0 ? "danger" : "default",
            value: total - onlineCount,
          },
        ]}
      />

      {agentsQuery.error ? (
        <EmptyState title={t("agentsList.loadErrorTitle")} body={String(agentsQuery.error)} />
      ) : null}
      {agentsQuery.isPending ? (
        <div className="loading-block">{t("agentsList.loading")}</div>
      ) : null}
      {!agentsQuery.isPending && !agentsQuery.error && agents.length === 0 ? (
        <EmptyState
          title={t("agentsList.emptyTitle")}
          body={t("agentsList.emptyBody")}
          action={
            <VibeButton
              className="agent-install-open-button"
              icon="+"
              onClick={() => setInstallDialogOpen(true)}
              variant="primary"
            >
              {t("agentsList.addAgent")}
            </VibeButton>
          }
        />
      ) : null}

      {agents.length > 0 && selectedAgent ? (
        <section className="workers-asset-workbench agents-asset-workbench" aria-label={t("agentsList.workbenchLabel")}>
          <aside className="workers-asset-rail agents-asset-rail" aria-label={t("agentsList.libraryTitle")}>
            <div className="workers-asset-rail-head agents-asset-rail-head">
              <span>{t("agentsList.libraryEyebrow")}</span>
              <h3>{t("agentsList.libraryTitle")}</h3>
            </div>
            <div className="workers-asset-list agents-asset-list">
              {agents.map((agent) => {
                const selected = agent.worker_agent_id === selectedAgent.worker_agent_id;
                const freeSlots = Math.max(
                  agent.max_concurrent_deployments - agent.used_slots,
                  0,
                );
                return (
                  <button
                    className={selected ? "workers-asset-card agents-asset-card is-selected" : "workers-asset-card agents-asset-card"}
                    key={agent.worker_agent_id}
                    onClick={() => setSelectedAgentId(agent.worker_agent_id)}
                    type="button"
                  >
                    <span className="workers-asset-glyph agents-asset-glyph" aria-hidden="true">AG</span>
                    <span className="workers-asset-copy agents-asset-copy">
                      <strong>{agent.display_name}</strong>
                      <span>
                        {t("agentsList.agentCardMeta", {
                          mode: agent.execution_mode,
                          slots: freeSlots,
                        })}
                      </span>
                    </span>
                    <span className={agent.status === "online" ? "workers-asset-status agents-asset-status is-ready" : "workers-asset-status agents-asset-status"}>
                      {t(`status.${agent.status}`, { defaultValue: agent.status })}
                    </span>
                  </button>
                );
              })}
            </div>
          </aside>

          <section className="workers-detail-surface agents-detail-surface">
            <header className="workers-detail-head agents-detail-head">
              <div>
                <span>{t("agentsList.selectedAgentLabel")}</span>
                <h3>{selectedAgent.display_name}</h3>
                <p>
                  {t("agentsList.selectedAgentSubtitle", {
                    mode: selectedAgent.execution_mode,
                    time: formatRelativeTime(selectedAgent.last_seen_at),
                  })}
                </p>
              </div>
              <Link
                className="vibe-button vibe-button-primary agents-open-detail"
                to={`/agents/${encodeURIComponent(selectedAgent.worker_agent_id)}`}
              >
                <span className="vibe-button-label">{t("agentsList.openDetail")}</span>
              </Link>
            </header>

            <div className="workers-detail-grid agents-detail-grid">
              <section className="workers-route-panel agents-health-panel">
                <div className="workers-panel-heading agents-panel-heading">
                  <span>{t("agentsList.healthTitle")}</span>
                  <strong>
                    <StatusBadge
                      label={t(`status.${selectedAgent.status}`, { defaultValue: selectedAgent.status })}
                      value={selectedAgent.status}
                    />
                  </strong>
                </div>
                <div className="workers-route-facts agents-route-facts">
                  <article>
                    <span>{t("agentsList.tableHeaderSlots")}</span>
                    <strong>
                      {selectedAgent.used_slots}/{selectedAgent.max_concurrent_deployments}
                    </strong>
                  </article>
                  <article>
                    <span>{t("agentsList.tableHeaderLastSeen")}</span>
                    <strong title={formatDateTime(selectedAgent.last_seen_at)}>
                      {formatRelativeTime(selectedAgent.last_seen_at)}
                    </strong>
                  </article>
                  <article>
                    <span>{t("agentsList.tableHeaderVersion")}</span>
                    <strong>{selectedAgent.agent_version ?? t("common.notAvailable")}</strong>
                  </article>
                </div>
                <VibeTagGroup className="agents-label-tags">
                  {Object.entries(selectedAgent.labels).length > 0 ? (
                    Object.entries(selectedAgent.labels).map(([key, value]) => (
                      <VibeTag key={key}>
                        {key}={value}
                      </VibeTag>
                    ))
                  ) : (
                    <VibeTag>{t("agentsList.noLabels")}</VibeTag>
                  )}
                </VibeTagGroup>
              </section>

              <aside className="workers-runbook-panel agents-runbook-panel">
                <span>{t("agentsList.runbookEyebrow")}</span>
                <h4>{t("agentsList.runbookTitle")}</h4>
                <div className="workers-runbook-facts agents-runbook-facts">
                  <div>
                    <span>{t("agentsList.executionModeTitle")}</span>
                    <strong>{selectedAgent.execution_mode}</strong>
                  </div>
                  <div>
                    <span>{t("agentsList.capabilitiesTitle")}</span>
                    <strong>
                      {selectedAgent.capabilities.length > 0
                        ? selectedAgent.capabilities.join(", ")
                        : t("agentsList.noCapabilities")}
                    </strong>
                  </div>
                  <div>
                    <span>{t("agentsDetail.onestepVersion")}</span>
                    <strong>{selectedAgent.onestep_version ?? t("common.notAvailable")}</strong>
                  </div>
                </div>
              </aside>
            </div>
          </section>
        </section>
      ) : null}

      <OverflowDialog
        className="agent-install-dialog"
        description={t("agentsList.installDialogDescription")}
        onClose={() => setInstallDialogOpen(false)}
        open={installDialogOpen}
        title={t("agentsList.installDialogTitle")}
      >
        <div className="agent-install-dialog-body">
          <section className="agent-install-field-grid">
            <VibeDialogField
              aria-label={t("agentsList.installPlaneUrlLabel")}
              className="agent-install-field"
              label={t("agentsList.installPlaneUrlLabel")}
              onChange={(event) => setPlaneUrl(event.target.value)}
              value={planeUrl}
            />
            <VibeDialogField
              aria-label={t("agentsList.installTokenLabel")}
              className="agent-install-field"
              label={t("agentsList.installTokenLabel")}
              onChange={(event) => setRegistrationToken(event.target.value)}
              placeholder={t("agentsList.installTokenPlaceholder")}
              type="password"
              value={registrationToken}
            />
            <VibeDialogField
              aria-label={t("agentsList.installNameLabel")}
              className="agent-install-field"
              label={t("agentsList.installNameLabel")}
              onChange={(event) => setAgentName(event.target.value)}
              value={agentName}
            />
            <VibeDialogField
              aria-label={t("agentsList.installConcurrencyLabel")}
              className="agent-install-field"
              label={t("agentsList.installConcurrencyLabel")}
              min="1"
              onChange={(event) => setMaxConcurrency(event.target.value)}
              type="number"
              value={maxConcurrency}
            />
          </section>

          <section className="agent-install-command-section">
            <div className="agent-install-command-heading">
              <span>{t("agentsList.installScriptTitle")}</span>
              <VibeInlineButton onClick={copyInstallCommand}>
                {copied ? t("agentsList.installCopied") : t("agentsList.installCopy")}
              </VibeInlineButton>
            </div>
            <pre className="json-block agent-install-command">{installCommand}</pre>
          </section>

          <p className="agent-install-hint">{t("agentsList.installHint")}</p>
        </div>
      </OverflowDialog>
    </div>
  );
}
