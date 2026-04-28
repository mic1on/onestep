import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { EmptyState } from "../../components/ui/EmptyState";
import { Panel } from "../../components/ui/Panel";
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
import { useServiceInstancesQuery } from "../../features/services/queries";
import type { AgentCommandDeliveryMode, AgentCommandKind, InstanceSummary } from "../../lib/api/types";
import {
  formatCompactJson,
  formatCount,
  formatDateTime,
  formatDurationMs,
  formatIdentifierPreview,
  formatRelativeTime,
} from "../../lib/formatters";
import { parseEnvironment, parseLookback } from "../../lib/params";
import { instancePath, servicePath } from "../../lib/routes";

type ServiceView = "overview" | "instances" | "tasks" | "commands";

export function InstanceDetailPage() {
  const { i18n, t } = useTranslation();
  const { serviceName, instanceId } = useParams<{ serviceName: string; instanceId: string }>();
  const [searchParams] = useSearchParams();
  const [submitMessage, setSubmitMessage] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [reasonDialogKind, setReasonDialogKind] = useState<AgentCommandKind | null>(null);
  const [deliveryModeOverride, setDeliveryModeOverride] = useState<AgentCommandDeliveryMode | null>(null);
  const isZh = Boolean(i18n.resolvedLanguage?.startsWith("zh"));

  if (!serviceName || !instanceId) {
    return <EmptyState title={t("instanceDetail.missingTitle")} body={t("instanceDetail.missingBody")} />;
  }

  const resolvedServiceName = serviceName;
  const resolvedInstanceId = instanceId;
  const environment = parseEnvironment(searchParams);
  const lookbackMinutes = parseLookback(searchParams, 60);

  const detailQuery = useInstanceDetailQuery(resolvedServiceName, resolvedInstanceId, environment, lookbackMinutes);
  const instancesQuery = useServiceInstancesQuery(resolvedServiceName, environment, {
    enabled: !detailQuery.error,
  });
  const commandsQuery = useInstanceCommandsQuery(resolvedInstanceId, {
    enabled: !detailQuery.error,
  });
  const createCommandMutation = useCreateInstanceCommandMutation(resolvedServiceName, environment, resolvedInstanceId);

  if (detailQuery.error) {
    return <EmptyState title={t("instanceDetail.loadErrorTitle")} body={String(detailQuery.error)} />;
  }

  const payload = detailQuery.data;
  const instance = payload?.instance;
  const instances = prioritizeInstances(instancesQuery.data?.items ?? []);
  const activeSession = instance?.active_session;
  const latestSession = payload?.latest_session;
  const deliveryMode =
    deliveryModeOverride ?? (activeSession ? "dispatch_now_only" : "queue_until_reconnect");
  const runtimeSignals = payload && instance ? buildInstanceRuntimeSignals(payload, instance, isZh) : [];
  const instanceStatus = instance ? deriveInstanceStatus(instance) : "offline";

  function buildServiceViewHref(view: ServiceView) {
    return servicePath(resolvedServiceName, {
      environment,
      lookback_minutes: lookbackMinutes,
      tab: view === "overview" ? undefined : view,
    });
  }

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
    <div className="ref-console-page ref-detail-page">
      <section className="ref-detail-topbar">
        <div className="ref-detail-topbar-rail">
          <Link className="ref-back-button" to={buildServiceViewHref("instances")}>
            <span aria-hidden="true" className="ref-back-button-icon">
              ←
            </span>
            <span className="ref-back-button-label">{isZh ? "返回" : "Back"}</span>
          </Link>
        </div>

        <div className="ref-detail-topbar-main">
          <div className="ref-detail-title">
            <div className="ref-detail-title-copy">
              <div className="ref-detail-title-row">
                <h2>{instance?.node_name ?? resolvedInstanceId}</h2>
                <StatusBadge {...getInstanceStatusBadge(instanceStatus, isZh)} />
              </div>
              <p>
                {isZh
                  ? `${resolvedServiceName} · 最近同步 ${formatRelativeTime(instance?.last_sync_at ?? null)}`
                  : `${resolvedServiceName} · last sync ${formatRelativeTime(instance?.last_sync_at ?? null)}`}
              </p>
            </div>
          </div>
        </div>
      </section>

      {submitMessage ? <div className="inline-feedback inline-feedback-success">{submitMessage}</div> : null}
      {submitError ? <div className="inline-feedback inline-feedback-error">{submitError}</div> : null}
      {detailQuery.isPending ? <div className="loading-block hero-block">{t("instanceDetail.loading")}</div> : null}

      {instance ? (
        <div className="ref-detail-layout">
          <aside className="ref-side-nav">
            <Link className="ref-side-nav-item" to={buildServiceViewHref("overview")}>
              <span className="ref-side-nav-icon">◫</span>
              <span>{isZh ? "概览" : "Overview"}</span>
            </Link>
            <Link className="ref-side-nav-item is-active" to={buildServiceViewHref("instances")}>
              <span className="ref-side-nav-icon">≣</span>
              <span>{isZh ? "实例" : "Instances"}</span>
            </Link>
            <Link className="ref-side-nav-item" to={buildServiceViewHref("tasks")}>
              <span className="ref-side-nav-icon">⌘</span>
              <span>{isZh ? "任务" : "Tasks"}</span>
            </Link>
            <Link className="ref-side-nav-item" to={buildServiceViewHref("commands")}>
              <span className="ref-side-nav-icon">↻</span>
              <span>{isZh ? "命令" : "Commands"}</span>
            </Link>
          </aside>

          <div className="ref-detail-content">
            <div className="ref-task-workbench">
              <section className="ref-task-browser">
                <div className="ref-section-headline">
                  <h3>{isZh ? `实例列表 (${instances.length})` : `Instances (${instances.length})`}</h3>
                </div>
                <div className="ref-task-browser-card">
                  {instances.map((item) => (
                    <Link
                      className={item.instance_id === resolvedInstanceId ? "ref-task-browser-row is-active" : "ref-task-browser-row"}
                      key={item.instance_id}
                      to={instancePath(resolvedServiceName, item.instance_id, {
                        environment,
                        lookback_minutes: lookbackMinutes,
                      })}
                    >
                      <div className="ref-task-browser-copy">
                        <strong>{item.node_name}</strong>
                        <span>{formatIdentifierPreview(item.instance_id)}</span>
                      </div>
                      <div className="ref-task-browser-metrics">
                        <span>{t(`status.${item.connectivity}`)}</span>
                        <span>{formatRelativeTime(item.last_seen_at)}</span>
                      </div>
                    </Link>
                  ))}
                </div>
              </section>

              <section className="ref-task-detail-pane">
                <section className="ref-summary-strip">
                  <SummaryChip label={t("instanceDetail.connectivity")} tone={instance.connectivity === "online" ? "success" : "danger"} value={t(`status.${instance.connectivity}`)} />
                  <SummaryChip label={t("instanceDetail.health")} tone={instance.status === "ok" ? "success" : "accent"} value={t(`status.${instance.status}`, { defaultValue: instance.status })} />
                  <SummaryChip label={t("instanceDetail.lastSeen")} tone="default" value={formatRelativeTime(instance.last_seen_at)} />
                  <SummaryChip label={t("instanceDetail.lastSync")} tone="default" value={formatDateTime(instance.last_sync_at)} />
                </section>

                <section className="ref-overview-grid">
                  <Panel
                    className="ref-card-panel"
                    subtitle={t("instanceDetail.runtimeSnapshotSubtitle")}
                    title={t("instanceDetail.runtimeSnapshotTitle")}
                  >
                    <div className="ref-info-grid">
                      <InfoPair label={t("instanceDetail.instanceId")} value={instance.instance_id} />
                      <InfoPair label={t("instanceDetail.host")} value={instance.hostname ?? t("common.notAvailable")} />
                      <InfoPair label={t("instanceDetail.pid")} value={String(instance.pid ?? t("common.notAvailable"))} />
                      <InfoPair label={t("instanceDetail.deployment")} value={instance.deployment_version} />
                      <InfoPair label={t("instanceDetail.onestep")} value={instance.onestep_version ?? t("common.notAvailable")} />
                      <InfoPair label={t("instanceDetail.python")} value={instance.python_version ?? t("common.notAvailable")} />
                      <InfoPair label={t("instanceDetail.startedAt")} value={formatDateTime(instance.started_at)} />
                      <InfoPair label={t("instanceDetail.topologyHash")} value={formatIdentifierPreview(instance.last_topology_hash)} />
                    </div>
                  </Panel>

                  <Panel
                    className="ref-card-panel"
                    subtitle={isZh ? "仅展示实例当前真实存在的运行数据。" : "Only instance signals backed by current real data are shown."}
                    title={isZh ? "实例运行摘要" : "Instance runtime signals"}
                  >
                    <div className="ref-signal-grid">
                      {runtimeSignals.map((signal) => (
                        <SignalCard key={signal.label} label={signal.label} note={signal.note} value={signal.value} />
                      ))}
                    </div>
                  </Panel>
                </section>

                <section className="ref-access-grid">
                  <Panel
                    className="ref-card-panel"
                    subtitle={t("instanceDetail.controlPlaneSubtitle")}
                    title={t("instanceDetail.controlPlaneTitle")}
                  >
                    {activeSession ? (
                      <>
                        <div className="ref-session-list">
                          <div className="ref-session-item">
                            <div>
                              <strong>{formatIdentifierPreview(activeSession.session_id)}</strong>
                              <span>{activeSession.hostname ?? t("common.notAvailable")}</span>
                            </div>
                            <StatusBadge value={activeSession.status} />
                          </div>
                        </div>
                        <div className="ref-info-grid">
                          <InfoPair label={t("instanceDetail.controlConnection")} value={formatDateTime(activeSession.connected_at)} />
                          <InfoPair label={t("instanceDetail.lastSeen")} value={formatRelativeTime(activeSession.last_message_at)} />
                          <InfoPair label={t("instanceDetail.controlCapabilities")} value={formatCount(activeSession.accepted_capabilities.length)} />
                          <InfoPair label="Protocol" value={activeSession.protocol_version} />
                        </div>
                      </>
                    ) : latestSession ? (
                      <>
                        <div className="ref-session-list">
                          <div className="ref-session-item">
                            <div>
                              <strong>{formatIdentifierPreview(latestSession.session_id)}</strong>
                              <span>{latestSession.hostname ?? t("common.notAvailable")}</span>
                            </div>
                            <StatusBadge value={latestSession.status} />
                          </div>
                        </div>
                        <div className="ref-info-grid">
                          <InfoPair label={t("instanceDetail.controlConnection")} value={formatDateTime(latestSession.connected_at)} />
                          <InfoPair label={t("instanceDetail.lastSeen")} value={formatRelativeTime(latestSession.last_message_at)} />
                          <InfoPair label={t("instanceDetail.latestControlDisconnect")} value={formatDateTime(latestSession.disconnected_at)} />
                          <InfoPair label={t("instanceDetail.controlCapabilities")} value={formatCount(latestSession.accepted_capabilities.length)} />
                        </div>
                      </>
                    ) : (
                      <EmptyState title={t("instanceDetail.controlIdleTitle")} body={t("instanceDetail.controlIdleBody")} />
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
                  </Panel>

                  <Panel
                    className="ref-card-panel"
                    subtitle={t("instanceDetail.commandsSubtitle")}
                    title={t("instanceDetail.commandsTitle")}
                  >
                    <CommandFeed
                      commands={commandsQuery.data?.items ?? []}
                      emptyBody={t("instanceDetail.noCommandsBody")}
                      emptyTitle={t("instanceDetail.noCommandsTitle")}
                    />
                  </Panel>
                </section>

                <section className="ref-access-grid">
                  <Panel
                    className="ref-card-panel"
                    subtitle={t("instanceDetail.recentWindowsSubtitle")}
                    title={t("instanceDetail.recentWindowsTitle")}
                  >
                    {payload.recent_metric_windows.length ? (
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

                  <Panel
                    className="ref-card-panel"
                    subtitle={t("instanceDetail.recentEventsSubtitle")}
                    title={t("instanceDetail.recentEventsTitle")}
                  >
                    {payload.recent_events.length ? (
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
                </section>

                <details className="ref-collapse-card">
                  <summary>
                    <strong>{t("instanceDetail.appSnapshotTitle")}</strong>
                    <span>{t("instanceDetail.appSnapshotSubtitle")}</span>
                  </summary>
                  <div className="ref-collapse-body">
                    <pre className="json-block">{formatCompactJson(payload.app_snapshot)}</pre>
                  </div>
                </details>
              </section>
            </div>
          </div>
        </div>
      ) : null}

      <CommandReasonDialog
        description={reasonDialogKind ? t(`commandReasonDialog.instanceBody.${reasonDialogKind}`) : ""}
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
        title={reasonDialogKind ? t(`commandReasonDialog.instanceTitle.${reasonDialogKind}`) : ""}
      />
    </div>
  );
}

function SummaryChip({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "default" | "accent" | "success" | "danger";
}) {
  return (
    <article className={`ref-summary-chip ref-summary-chip-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function InfoPair({ label, value }: { label: string; value: string }) {
  return (
    <div className="ref-info-pair">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function SignalCard({
  label,
  value,
  note,
}: {
  label: string;
  value: string;
  note?: string;
}) {
  return (
    <div className="ref-signal-card">
      <span>{label}</span>
      <strong>{value}</strong>
      {note ? <p>{note}</p> : null}
    </div>
  );
}

function buildInstanceRuntimeSignals(
  payload: NonNullable<ReturnType<typeof useInstanceDetailQuery>["data"]>,
  instance: InstanceSummary,
  isZh: boolean,
) {
  const totalSucceeded = payload.recent_metric_windows.reduce((sum, window) => sum + window.succeeded, 0);
  const totalFailed = payload.recent_metric_windows.reduce((sum, window) => sum + window.failed + window.dead_lettered, 0);

  return [
    {
      label: isZh ? "指标窗口" : "Metric windows",
      value: String(payload.recent_metric_windows.length),
      note: isZh ? "当前时间窗口内的聚合数" : "Current window aggregates",
    },
    {
      label: isZh ? "最近事件" : "Recent events",
      value: String(payload.recent_events.length),
      note: isZh ? "当前时间窗口内的离散事件" : "Discrete events in scope",
    },
    {
      label: isZh ? "成功总数" : "Succeeded",
      value: formatCount(totalSucceeded),
      note: isZh ? "基于最近指标窗口" : "Summed from recent metric windows",
    },
    {
      label: isZh ? "失败 / DLQ" : "Failed / DLQ",
      value: formatCount(totalFailed),
      note: isZh ? "基于最近指标窗口" : "Summed from recent metric windows",
    },
    {
      label: isZh ? "最近同步" : "Last sync",
      value: formatRelativeTime(instance.last_sync_at),
      note: isZh ? "来自实例状态" : "Reported by instance state",
    },
    {
      label: isZh ? "活跃会话" : "Active session",
      value: instance.active_session ? (isZh ? "已连接" : "Connected") : isZh ? "无" : "None",
      note: isZh ? "来自控制会话状态" : "Control session state",
    },
  ];
}

function prioritizeInstances(instances: InstanceSummary[]) {
  return [...instances].sort((left, right) => {
    const priorityDelta = getInstancePriority(right) - getInstancePriority(left);
    if (priorityDelta !== 0) {
      return priorityDelta;
    }
    return compareDateDesc(left.last_seen_at, right.last_seen_at);
  });
}

function getInstancePriority(instance: InstanceSummary) {
  if (instance.instance_id) {
    if (instance.connectivity !== "online") {
      return 3;
    }
    if (instance.status === "error" || instance.status === "degraded") {
      return 2;
    }
    if (instance.active_session === null) {
      return 1;
    }
  }
  return 0;
}

function deriveInstanceStatus(instance: InstanceSummary): "online" | "offline" | "degraded" {
  if (instance.connectivity !== "online") {
    return "offline";
  }
  if (instance.status === "error" || instance.status === "degraded") {
    return "degraded";
  }
  return "online";
}

function getInstanceStatusBadge(status: "online" | "offline" | "degraded", isZh: boolean) {
  if (status === "online") {
    return { value: "online" as const, label: isZh ? "运行中" : "Running" };
  }
  if (status === "degraded") {
    return { value: "degraded" as const, label: isZh ? "需关注" : "Review" };
  }
  return { value: "offline" as const, label: isZh ? "离线" : "Offline" };
}

function compareDateDesc(left: string | null, right: string | null) {
  const leftValue = left ? Date.parse(left) : 0;
  const rightValue = right ? Date.parse(right) : 0;
  return rightValue - leftValue;
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
