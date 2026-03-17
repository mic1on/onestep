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
import { CommandQuickActions } from "../../features/commands/components/CommandQuickActions";
import { useCreateInstanceCommandMutation, useInstanceCommandsQuery } from "../../features/commands/queries";
import { useInstanceDetailQuery } from "../../features/instances/queries";
import type { AgentCommandKind } from "../../lib/api/types";
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

  async function handleCommandSubmit(kind: AgentCommandKind) {
    setSubmitError(null);
    setSubmitMessage(null);
    try {
      await createCommandMutation.mutateAsync({
        kind,
        args: kind === "ping" ? { nonce: Date.now() } : {},
        timeout_s: kind === "shutdown" ? 30 : 10,
      });
      setSubmitMessage(
        t("instanceDetail.commandDispatchOk", {
          kind: t(`commandKind.${kind}`, { defaultValue: kind }),
        }),
      );
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : String(error));
    }
  }

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow={t("instanceDetail.eyebrow", { environment: t(`environment.${environment}`) })}
        title={instance?.node_name ?? instanceId}
        subtitle={
          <span>
            <Link
              className="inline-link"
              to={`/services/${serviceName}${createSearch({
                environment,
                lookback_minutes: lookbackMinutes,
              })}`}
            >
              {serviceName}
            </Link>{" "}
            / {t("instanceDetail.subtitleSuffix", { lookbackMinutes })}
          </span>
        }
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
              ) : (
                <EmptyState
                  title={t("instanceDetail.controlIdleTitle")}
                  body={t("instanceDetail.controlIdleBody")}
                />
              )}

              <div className="command-panel-actions">
                <CommandQuickActions
                  disabled={!activeSession}
                  isSubmitting={createCommandMutation.isPending}
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
    </div>
  );
}
