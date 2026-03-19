import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { EmptyState } from "../../components/ui/EmptyState";
import { PageHeader } from "../../components/ui/PageHeader";
import { Panel } from "../../components/ui/Panel";
import { StatCard } from "../../components/ui/StatCard";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { TaskEventFailureDetails } from "../../components/ui/TaskEventFailureDetails";
import { CommandFeed } from "../../features/commands/components/CommandFeed";
import { CommandReasonDialog } from "../../features/commands/components/CommandReasonDialog";
import { CommandQuickActions } from "../../features/commands/components/CommandQuickActions";
import {
  commandRequiresReason,
  commandSupportsQueueing,
  getCommandCapability,
  hasCommandCapability,
} from "../../features/commands/capabilities";
import { useCreateInstanceCommandMutation, useInstanceCommandsQuery } from "../../features/commands/queries";
import { useInstanceDetailQuery } from "../../features/instances/queries";
import type { AgentCommandDeliveryMode, AgentCommandKind } from "../../lib/api/types";
import {
  formatCompactJson,
  formatCount,
  formatDateTime,
  formatDurationMs,
  formatIdentifierPreview,
  formatRelativeTime,
} from "../../lib/formatters";
import { createSearch, parseEnvironment, parseLookback } from "../../lib/params";

export function InstanceDetailPage() {
  const { t } = useTranslation();
  const { serviceName, instanceId } = useParams<{ serviceName: string; instanceId: string }>();
  const [searchParams] = useSearchParams();
  const [submitMessage, setSubmitMessage] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [reasonDialogKind, setReasonDialogKind] = useState<AgentCommandKind | null>(null);
  const [deliveryModeOverride, setDeliveryModeOverride] = useState<AgentCommandDeliveryMode | null>(null);

  if (!serviceName || !instanceId) {
    return <EmptyState title={t("instanceDetail.missingTitle")} body={t("instanceDetail.missingBody")} />;
  }

  const environment = parseEnvironment(searchParams);
  const lookbackMinutes = parseLookback(searchParams, 60);
  const query = useInstanceDetailQuery(serviceName, instanceId, environment, lookbackMinutes);
  const commandsQuery = useInstanceCommandsQuery(instanceId);
  const createCommandMutation = useCreateInstanceCommandMutation(serviceName, environment, instanceId);

  if (query.error) {
    return <EmptyState title={t("instanceDetail.loadErrorTitle")} body={String(query.error)} />;
  }

  const payload = query.data;
  const instance = payload?.instance;
  const activeSession = instance?.active_session;
  const latestSession = payload?.latest_session;
  const deliveryMode =
    deliveryModeOverride ?? (activeSession ? "dispatch_now_only" : "queue_until_reconnect");

  async function dispatchCommand(kind: AgentCommandKind, reason?: string) {
    setSubmitError(null);
    setSubmitMessage(null);
    const capabilitySource = deliveryMode === "queue_until_reconnect" ? latestSession : activeSession;

    if (deliveryMode === "dispatch_now_only" && !activeSession) {
      const message = t("commands.disabledReason.noSession");
      setSubmitError(message);
      throw new Error(message);
    }

    if (deliveryMode === "queue_until_reconnect" && !commandSupportsQueueing(kind)) {
      const message = t("commands.disabledReason.queueUnavailable", {
        kind: t(`commandKind.${kind}`, { defaultValue: kind }),
      });
      setSubmitError(message);
      throw new Error(message);
    }

    if (deliveryMode === "queue_until_reconnect" && !latestSession) {
      const message = t("commands.disabledReason.noKnownSession");
      setSubmitError(message);
      throw new Error(message);
    }

    if (!capabilitySource || !hasCommandCapability(kind, capabilitySource.accepted_capabilities)) {
      const message = t("commands.disabledReason.missingCapability", {
        capability: getCommandCapability(kind),
      });
      setSubmitError(message);
      throw new Error(message);
    }

    try {
      const response = await createCommandMutation.mutateAsync({
        kind,
        args: kind === "ping" ? { nonce: Date.now() } : {},
        timeout_s: resolveCommandTimeoutSeconds(kind),
        delivery_mode: deliveryMode,
        reason,
      });
      setSubmitMessage(
        response.status === "pending" && response.session_id === null
          ? t("instanceDetail.commandQueuedOk", {
              kind: t(`commandKind.${kind}`, { defaultValue: kind }),
            })
          : t("instanceDetail.commandDispatchOk", {
              kind: t(`commandKind.${kind}`, { defaultValue: kind }),
            }),
      );
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : String(error));
      throw error;
    }
  }

  async function handleCommandSubmit(kind: AgentCommandKind) {
    if (commandRequiresReason(kind)) {
      setReasonDialogKind(kind);
      return;
    }
    await dispatchCommand(kind);
  }

  return (
    <div className="page-stack">
      <PageHeader
        title={instance?.node_name ?? instanceId}
      />

      {query.isPending ? <div className="loading-block hero-block">{t("instanceDetail.loading")}</div> : null}

      {instance ? (
        <>
          <section className="stats-grid">
            <StatCard
              label={t("instanceDetail.connectivity")}
              value={t(`status.${instance.connectivity}`)}
              tone={instance.connectivity === "online" ? "success" : "warning"}
            />
            <StatCard
              label={t("instanceDetail.health")}
              value={t(`status.${instance.status}`, { defaultValue: instance.status })}
              tone={instance.status === "ok" ? "success" : "warning"}
            />
            <StatCard label={t("instanceDetail.lastSeen")} value={formatRelativeTime(instance.last_seen_at)} />
            <StatCard label={t("instanceDetail.lastSync")} value={formatDateTime(instance.last_sync_at)} />
          </section>

          <div className="two-column-grid">
            <Panel title={t("instanceDetail.runtimeSnapshotTitle")} subtitle={t("instanceDetail.runtimeSnapshotSubtitle")}>
              <div className="badge-row">
                <StatusBadge value={instance.connectivity} />
                <StatusBadge value={instance.status} />
              </div>
              <dl className="definition-grid">
                <div>
                  <dt>{t("instanceDetail.instanceId")}</dt>
                  <dd>{instance.instance_id}</dd>
                </div>
                <div>
                  <dt>{t("instanceDetail.host")}</dt>
                  <dd>{instance.hostname ?? t("common.notAvailable")}</dd>
                </div>
                <div>
                  <dt>{t("instanceDetail.pid")}</dt>
                  <dd>{instance.pid ?? t("common.notAvailable")}</dd>
                </div>
                <div>
                  <dt>{t("instanceDetail.deployment")}</dt>
                  <dd>{instance.deployment_version}</dd>
                </div>
                <div>
                  <dt>{t("instanceDetail.onestep")}</dt>
                  <dd>{instance.onestep_version ?? t("common.notAvailable")}</dd>
                </div>
                <div>
                  <dt>{t("instanceDetail.python")}</dt>
                  <dd>{instance.python_version ?? t("common.notAvailable")}</dd>
                </div>
                <div>
                  <dt>{t("instanceDetail.startedAt")}</dt>
                  <dd>{formatDateTime(instance.started_at)}</dd>
                </div>
                <div>
                  <dt>{t("instanceDetail.topologyHash")}</dt>
                  <dd title={instance.last_topology_hash ?? t("common.notAvailable")}>
                    {formatIdentifierPreview(instance.last_topology_hash)}
                  </dd>
                </div>
              </dl>
            </Panel>

            <Panel title={t("instanceDetail.appSnapshotTitle")} subtitle={t("instanceDetail.appSnapshotSubtitle")}>
              <pre className="json-block">{formatCompactJson(payload?.app_snapshot)}</pre>
            </Panel>
          </div>

          <div className="two-column-grid">
            <Panel title={t("instanceDetail.controlPlaneTitle")} subtitle={t("instanceDetail.controlPlaneSubtitle")}>
              {activeSession ? (
                <>
                  <div className="badge-row">
                    <StatusBadge value={activeSession.status} />
                    <span className="code-chip">{formatIdentifierPreview(activeSession.session_id)}</span>
                  </div>
                  <dl className="definition-grid">
                    <div>
                      <dt>{t("instanceDetail.controlConnection")}</dt>
                      <dd>{formatDateTime(activeSession.connected_at)}</dd>
                    </div>
                    <div>
                      <dt>{t("instanceDetail.lastSeen")}</dt>
                      <dd>{formatRelativeTime(activeSession.last_message_at)}</dd>
                    </div>
                    <div>
                      <dt>{t("instanceDetail.host")}</dt>
                      <dd>{activeSession.hostname ?? t("common.notAvailable")}</dd>
                    </div>
                    <div>
                      <dt>{t("instanceDetail.controlCapabilities")}</dt>
                      <dd>{formatCount(activeSession.accepted_capabilities.length)}</dd>
                    </div>
                  </dl>
                  {activeSession.accepted_capabilities.length ? (
                    <div className="command-chip-grid">
                      {activeSession.accepted_capabilities.map((capability) => (
                        <span className="code-chip" key={capability}>
                          {capability}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </>
              ) : latestSession ? (
                <>
                  <div className="badge-row">
                    <StatusBadge value={latestSession.status} />
                    <span className="code-chip">{formatIdentifierPreview(latestSession.session_id)}</span>
                  </div>
                  <p className="command-inline-text">{t("instanceDetail.latestControlSessionHint")}</p>
                  <dl className="definition-grid">
                    <div>
                      <dt>{t("instanceDetail.controlConnection")}</dt>
                      <dd>{formatDateTime(latestSession.connected_at)}</dd>
                    </div>
                    <div>
                      <dt>{t("instanceDetail.lastSeen")}</dt>
                      <dd>{formatRelativeTime(latestSession.last_message_at)}</dd>
                    </div>
                    <div>
                      <dt>{t("instanceDetail.latestControlDisconnect")}</dt>
                      <dd>{formatDateTime(latestSession.disconnected_at)}</dd>
                    </div>
                    <div>
                      <dt>{t("instanceDetail.controlCapabilities")}</dt>
                      <dd>{formatCount(latestSession.accepted_capabilities.length)}</dd>
                    </div>
                  </dl>
                  {latestSession.accepted_capabilities.length ? (
                    <div className="command-chip-grid">
                      {latestSession.accepted_capabilities.map((capability) => (
                        <span className="code-chip" key={capability}>
                          {capability}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </>
              ) : (
                <EmptyState
                  title={t("instanceDetail.controlIdleTitle")}
                  body={t("instanceDetail.controlIdleBody")}
                />
              )}

              <div className="command-panel-actions">
                <CommandQuickActions
                  activeAcceptedCapabilities={activeSession?.accepted_capabilities ?? []}
                  deliveryMode={deliveryMode}
                  hasActiveSession={activeSession !== undefined && activeSession !== null}
                  hasKnownSession={latestSession !== undefined && latestSession !== null}
                  isSubmitting={createCommandMutation.isPending}
                  latestAcceptedCapabilities={latestSession?.accepted_capabilities ?? []}
                  onDeliveryModeChange={setDeliveryModeOverride}
                  onSubmit={handleCommandSubmit}
                />
              </div>

              {submitMessage ? <div className="inline-feedback inline-feedback-success">{submitMessage}</div> : null}
              {submitError ? <div className="inline-feedback inline-feedback-error">{submitError}</div> : null}
            </Panel>

            <Panel title={t("instanceDetail.commandsTitle")} subtitle={t("instanceDetail.commandsSubtitle")}>
              {commandsQuery.isPending ? <div className="loading-block">{t("serviceDetail.loadingCommandFeed")}</div> : null}
              {!commandsQuery.isPending && commandsQuery.error ? (
                <EmptyState title={t("instanceDetail.loadErrorTitle")} body={String(commandsQuery.error)} />
              ) : null}
              {!commandsQuery.isPending && !commandsQuery.error ? (
                <CommandFeed
                  commands={commandsQuery.data?.items ?? []}
                  emptyBody={t("instanceDetail.noCommandsBody")}
                  emptyTitle={t("instanceDetail.noCommandsTitle")}
                />
              ) : null}
            </Panel>
          </div>

          <div className="two-column-grid">
            <Panel title={t("instanceDetail.recentWindowsTitle")} subtitle={t("instanceDetail.recentWindowsSubtitle")}>
              {payload?.recent_metric_windows.length ? (
                <div className="stack-list">
                  {payload.recent_metric_windows.map((window) => (
                    <article className="event-row" key={window.window_id}>
                      <div>
                        <strong>{window.task_name}</strong>
                        <p>{window.window_id}</p>
                      </div>
                      <div className="row-metrics">
                        <span>{t("metrics.ok", { value: formatCount(window.succeeded) })}</span>
                        <span>{t("metrics.fail", { value: formatCount(window.failed) })}</span>
                        <span>{t("metrics.p95", { value: formatDurationMs(window.p95_duration_ms) })}</span>
                      </div>
                    </article>
                  ))}
                </div>
              ) : (
                <EmptyState title={t("instanceDetail.noWindowsTitle")} body={t("instanceDetail.noWindowsBody")} />
              )}
            </Panel>

            <Panel title={t("instanceDetail.recentEventsTitle")} subtitle={t("instanceDetail.recentEventsSubtitle")}>
              {payload?.recent_events.length ? (
                <div className="stack-list">
                  {payload.recent_events.map((event) => (
                    <article className="event-row" key={event.event_id}>
                      <div>
                        <strong>{event.task_name}</strong>
                        <p>
                          {t(`eventKind.${event.kind}`, { defaultValue: event.kind })} · {formatDateTime(event.occurred_at)}
                        </p>
                      </div>
                      <TaskEventFailureDetails event={event} />
                    </article>
                  ))}
                </div>
              ) : (
                <EmptyState title={t("instanceDetail.noEventsTitle")} body={t("instanceDetail.noEventsBody")} />
              )}
            </Panel>
          </div>
        </>
      ) : null}

      <CommandReasonDialog
        description={
          reasonDialogKind
            ? t(`commandReasonDialog.instanceBody.${reasonDialogKind}`)
            : ""
        }
        isSubmitting={createCommandMutation.isPending}
        onCancel={() => setReasonDialogKind(null)}
        onConfirm={async (reason) => {
          if (!reasonDialogKind) {
            return;
          }
          await dispatchCommand(reasonDialogKind, reason);
          setReasonDialogKind(null);
        }}
        open={reasonDialogKind !== null}
        title={
          reasonDialogKind
            ? t(`commandReasonDialog.instanceTitle.${reasonDialogKind}`)
            : ""
        }
      />
    </div>
  );
}

function resolveCommandTimeoutSeconds(kind: AgentCommandKind) {
  if (kind === "shutdown" || kind === "restart") {
    return 30;
  }
  if (kind === "drain") {
    return 120;
  }
  return 10;
}
