import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { EmptyState } from "../../components/ui/EmptyState";
import { Panel } from "../../components/ui/Panel";
import { SegmentedControl } from "../../components/ui/SegmentedControl";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { TaskEventFailureDetails } from "../../components/ui/TaskEventFailureDetails";
import { useToast } from "../../components/ui/ToastProvider";
import { useConsoleSessionQuery } from "../../features/auth/queries";
import { canViewCommandControls, canViewDestructiveControls } from "../../features/auth/session";
import { CommandFeed } from "../../features/commands/components/CommandFeed";
import { CommandQuickActions } from "../../features/commands/components/CommandQuickActions";
import { ConnectionStatusBanner } from "../../features/commands/components/ConnectionStatusBanner";
import { DestructiveCommandReviewDialog } from "../../features/commands/components/DestructiveCommandReviewDialog";
import {
  getCommandRiskLevel,
  isDestructiveCommand,
  commandSupportsQueueing,
  getCommandCapability,
  hasCommandCapability,
} from "../../features/commands/capabilities";
import { isUiQueryDataStale, useCreateInstanceCommandMutation, useInstanceCommandsQuery } from "../../features/commands/queries";
import { useCommandStreamStatus } from "../../features/commands/useCommandStream";
import { useInstanceDetailQuery } from "../../features/instances/queries";
import { useServiceInstancesQuery } from "../../features/services/queries";
import { ApiTimeoutError } from "../../lib/api/client";
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
type InstanceActivityTab = "commands" | "metrics" | "events";
type OperationIssue = {
  message: string;
  timeout: boolean;
};

export function InstanceDetailPage() {
  const { i18n, t } = useTranslation();
  const { pushToast } = useToast();
  const { serviceName, instanceId } = useParams<{ serviceName: string; instanceId: string }>();
  const [searchParams] = useSearchParams();
  const [submitIssue, setSubmitIssue] = useState<OperationIssue | null>(null);
  const [reasonDialogKind, setReasonDialogKind] = useState<AgentCommandKind | null>(null);
  const [deliveryModeOverride, setDeliveryModeOverride] = useState<AgentCommandDeliveryMode | null>(null);
  const [activityTab, setActivityTab] = useState<InstanceActivityTab>("commands");
  const [visibleInstanceCount, setVisibleInstanceCount] = useState(100);
  const [visibleCommandCount, setVisibleCommandCount] = useState(50);
  const isZh = Boolean(i18n.resolvedLanguage?.startsWith("zh"));
  const sessionQuery = useConsoleSessionQuery();
  const streamStatus = useCommandStreamStatus();

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
    limit: visibleInstanceCount,
  });
  const commandsQuery = useInstanceCommandsQuery(resolvedInstanceId, {
    enabled: !detailQuery.error,
    limit: visibleCommandCount,
  });
  const createCommandMutation = useCreateInstanceCommandMutation(resolvedServiceName, environment, resolvedInstanceId);

  if (detailQuery.error) {
    return <EmptyState title={t("instanceDetail.loadErrorTitle")} body={String(detailQuery.error)} />;
  }

  const payload = detailQuery.data;
  const instance = payload?.instance;
  const instances = instancesQuery.data?.items ?? [];
  const activeSession = instance?.active_session;
  const latestSession = payload?.latest_session;
  const deliveryMode =
    deliveryModeOverride ?? (activeSession ? "dispatch_now_only" : "queue_until_reconnect");
  const runtimeSignals = payload && instance ? buildInstanceRuntimeSignals(payload, instance, isZh) : [];
  const instanceStatus = instance ? deriveInstanceStatus(instance) : "offline";
  const instanceHeroMetric = payload && instance ? buildInstanceHeroMetric(instance, payload, isZh) : null;
  const canViewControls = canViewCommandControls(sessionQuery.data);
  const canViewDestructiveActions = canViewDestructiveControls(sessionQuery.data);
  const connectionNotice = buildInstanceConnectionNotice(
    t,
    instance?.node_name ?? resolvedInstanceId,
    streamStatus,
    detailQuery.dataUpdatedAt,
    Boolean(detailQuery.data),
    detailQuery.error,
    submitIssue,
  );
  const activityTabs: Array<{ label: string; value: InstanceActivityTab }> = [
    { label: t("instanceDetail.activityTabs.commands"), value: "commands" },
    { label: t("instanceDetail.activityTabs.metrics"), value: "metrics" },
    { label: t("instanceDetail.activityTabs.events"), value: "events" },
  ];

  function buildServiceViewHref(view: ServiceView) {
    return servicePath(resolvedServiceName, {
      environment,
      lookback_minutes: lookbackMinutes,
      tab: view === "overview" ? undefined : view,
    });
  }

  async function dispatchCommand(kind: AgentCommandKind, reason?: string) {
    setSubmitIssue(null);
    const capabilitySource = deliveryMode === "queue_until_reconnect" ? latestSession : activeSession;

    if (deliveryMode === "dispatch_now_only" && !activeSession) {
      const message = t("commands.disabledReason.noSession");
      setSubmitIssue({ message, timeout: false });
      throw new Error(message);
    }

    if (deliveryMode === "queue_until_reconnect" && !commandSupportsQueueing(kind)) {
      const message = t("commands.disabledReason.queueUnavailable", {
        kind: t(`commandKind.${kind}`, { defaultValue: kind }),
      });
      setSubmitIssue({ message, timeout: false });
      throw new Error(message);
    }

    if (deliveryMode === "queue_until_reconnect" && !latestSession) {
      const message = t("commands.disabledReason.noKnownSession");
      setSubmitIssue({ message, timeout: false });
      throw new Error(message);
    }

    if (!capabilitySource || !hasCommandCapability(kind, capabilitySource.accepted_capabilities)) {
      const message = t("commands.disabledReason.missingCapability", {
        capability: getCommandCapability(kind),
      });
      setSubmitIssue({ message, timeout: false });
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
      const message =
        response.status === "pending" && response.session_id === null
          ? t("instanceDetail.commandQueuedOk", {
              kind: t(`commandKind.${kind}`, { defaultValue: kind }),
            })
          : t("instanceDetail.commandDispatchOk", {
              kind: t(`commandKind.${kind}`, { defaultValue: kind }),
            });
      setSubmitIssue(null);
      pushToast({ tone: "success", message });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setSubmitIssue({
        message,
        timeout: error instanceof ApiTimeoutError,
      });
      pushToast({ tone: "error", message });
      throw error;
    }
  }

  async function handleCommandSubmit(kind: AgentCommandKind) {
    if (isDestructiveCommand(kind)) {
      setReasonDialogKind(kind);
      return;
    }
    await dispatchCommand(kind);
  }

  return (
    <div className="ref-console-page ref-detail-page signal-console-detail-page signal-console-instance-detail">
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
              <span className="signal-console-kicker">
                {t("instanceDetail.eyebrow", { environment: t(`environment.${environment}`) })}
              </span>
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

          <div className="ref-detail-header-actions">
            <div className="signal-console-detail-topbar-metric">
              <span>{instanceHeroMetric?.label ?? t("instanceDetail.connectivity")}</span>
              <strong>{instanceHeroMetric?.value ?? "--"}</strong>
              {instanceHeroMetric?.note ? <p>{instanceHeroMetric.note}</p> : null}
            </div>
          </div>
        </div>
      </section>

      {detailQuery.isPending ? <div className="loading-block hero-block">{t("instanceDetail.loading")}</div> : null}
      {connectionNotice ? (
        <ConnectionStatusBanner
          body={connectionNotice.body}
          detail={connectionNotice.detail}
          title={connectionNotice.title}
          tone={connectionNotice.tone}
        />
      ) : null}

      {instance ? (
        <div className="ref-detail-layout">
          <aside className="ref-side-nav">
            <Link className="ref-side-nav-item" to={buildServiceViewHref("overview")}>
              <span>{isZh ? "概览" : "Overview"}</span>
            </Link>
            <Link className="ref-side-nav-item is-active" to={buildServiceViewHref("instances")}>
              <span>{isZh ? "实例" : "Instances"}</span>
            </Link>
            <Link className="ref-side-nav-item" to={buildServiceViewHref("tasks")}>
              <span>{isZh ? "任务" : "Tasks"}</span>
            </Link>
            <Link className="ref-side-nav-item" to={buildServiceViewHref("commands")}>
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
                {instancesQuery.data && instancesQuery.data.total > 0 ? (
                  <div className="ref-inline-pagination">
                    <span>
                      {t("common.showingVisibleItems", {
                        visible: instances.length,
                        total: instancesQuery.data.total,
                      })}
                    </span>
                    {instances.length < instancesQuery.data.total ? (
                      <button
                        className="ref-ghost-button"
                        onClick={() => setVisibleInstanceCount((count) => count + 100)}
                        type="button"
                      >
                        {t("common.loadMore")}
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </section>

              <section className="ref-task-detail-pane">
                <div aria-hidden="true" className="ref-section-headline ref-section-headline-ghost">
                  <h3>{isZh ? "实例列表" : "Instances"}</h3>
                </div>
                <section className="ref-summary-strip">
                  <SummaryChip label={t("instanceDetail.connectivity")} tone={instance.connectivity === "online" ? "success" : "danger"} value={t(`status.${instance.connectivity}`)} />
                  <SummaryChip label={t("instanceDetail.health")} tone={instance.status === "ok" ? "success" : "accent"} value={t(`status.${instance.status}`, { defaultValue: instance.status })} />
                  <SummaryChip label={t("instanceDetail.lastSeen")} tone="default" value={formatRelativeTime(instance.last_seen_at)} />
                  <SummaryChip label={t("instanceDetail.lastSync")} tone="default" value={formatDateTime(instance.last_sync_at)} />
                </section>

                <Panel
                  className="ref-card-panel"
                  subtitle={t("instanceDetail.runtimeSnapshotSubtitle")}
                  title={t("instanceDetail.runtimeSnapshotTitle")}
                >
                  <div className="ref-compact-kpi-grid ref-compact-kpi-grid-instance">
                    {runtimeSignals.map((signal) => (
                      <article className="ref-compact-kpi" key={signal.label}>
                        <span>{signal.label}</span>
                        <strong>{signal.value}</strong>
                      </article>
                    ))}
                  </div>
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

                <details className="ref-collapse-card">
                  <summary>
                    <strong>{isZh ? "实例活动" : "Instance activity"}</strong>
                    <span>
                      {isZh
                        ? "整合最近命令、指标窗口和任务事件，默认收起。"
                        : "Collapsed by default with recent commands, metric windows, and task events."}
                    </span>
                  </summary>
                  <div className="ref-collapse-body ref-task-activity-stack">
                    <SegmentedControl
                      ariaLabel={t("instanceDetail.activityAriaLabel")}
                      onChange={(value) => setActivityTab(value as InstanceActivityTab)}
                      options={activityTabs}
                      value={activityTab}
                    />

                    <section className="ref-task-activity-panel">
                      <header className="ref-task-activity-header">
                        <div>
                          <h3>{getInstanceActivityTitle(activityTab, t)}</h3>
                          <p>{getInstanceActivitySubtitle(activityTab, t)}</p>
                        </div>
                      </header>

                      {activityTab === "commands" ? (
                        <>
                          {canViewControls ? (
                            <CommandQuickActions
                              activeAcceptedCapabilities={
                                canViewDestructiveActions
                                  ? activeSession?.accepted_capabilities ?? []
                                  : (activeSession?.accepted_capabilities ?? []).filter((capability) =>
                                      capability !== "command.shutdown" &&
                                      capability !== "command.restart" &&
                                      capability !== "command.drain"
                                    )
                              }
                              deliveryMode={deliveryMode}
                              hasActiveSession={activeSession !== undefined && activeSession !== null}
                              hasKnownSession={latestSession !== undefined && latestSession !== null}
                              isSubmitting={createCommandMutation.isPending}
                              latestAcceptedCapabilities={
                                canViewDestructiveActions
                                  ? latestSession?.accepted_capabilities ?? []
                                  : (latestSession?.accepted_capabilities ?? []).filter((capability) =>
                                      capability !== "command.shutdown" &&
                                      capability !== "command.restart" &&
                                      capability !== "command.drain"
                                    )
                              }
                              onDeliveryModeChange={setDeliveryModeOverride}
                              onSubmit={handleCommandSubmit}
                            />
                          ) : null}
                          <CommandFeed
                            commands={(commandsQuery.data?.items ?? []).slice(0, visibleCommandCount)}
                            emptyBody={t("instanceDetail.noCommandsBody")}
                            emptyTitle={t("instanceDetail.noCommandsTitle")}
                          />
                          {(commandsQuery.data?.items?.length ?? 0) > 0 ? (
                            <div className="ref-inline-pagination">
                              <span>
                                {t("common.showingVisibleItems", {
                                  visible: commandsQuery.data?.items.length ?? 0,
                                  total: commandsQuery.data?.total ?? 0,
                                })}
                              </span>
                              {(commandsQuery.data?.items.length ?? 0) < (commandsQuery.data?.total ?? 0) ? (
                                <button
                                  className="ref-ghost-button"
                                  onClick={() => setVisibleCommandCount((count) => count + 50)}
                                  type="button"
                                >
                                  {t("common.loadMore")}
                                </button>
                              ) : null}
                            </div>
                          ) : null}
                        </>
                      ) : null}

                      {activityTab === "metrics" ? (
                        payload.recent_metric_windows.length ? (
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
                        )
                      ) : null}

                      {activityTab === "events" ? (
                        payload.recent_events.length ? (
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
                        )
                      ) : null}
                    </section>
                  </div>
                </details>

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

      <DestructiveCommandReviewDialog
        commandLabel={reasonDialogKind ? t(`commandKind.${reasonDialogKind}`, { defaultValue: reasonDialogKind }) : ""}
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
        riskLevel={reasonDialogKind ? getCommandRiskLevel(reasonDialogKind) : "critical"}
        targetSummary={instance?.node_name ?? resolvedInstanceId}
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

function buildInstanceHeroMetric(
  instance: InstanceSummary,
  payload: NonNullable<ReturnType<typeof useInstanceDetailQuery>["data"]>,
  isZh: boolean,
) {
  if (instance.connectivity !== "online") {
    return {
      label: isZh ? "连通状态" : "Connectivity",
      value: isZh ? "离线" : "Offline",
      note: isZh ? "当前实例没有活跃连接，命令只能等待重连后再处理。" : "No active connection is attached. Commands can only proceed after reconnect when supported.",
    };
  }

  return {
    label: isZh ? "最近事件" : "Recent events",
    value: formatCount(payload.recent_events.length),
    note: isZh ? "当前时间窗口内的离散事件数。" : "Discrete events visible inside the current lookback window.",
  };
}

function getInstanceActivityTitle(tab: InstanceActivityTab, t: ReturnType<typeof useTranslation>["t"]) {
  if (tab === "commands") {
    return t("instanceDetail.commandsTitle");
  }
  if (tab === "metrics") {
    return t("instanceDetail.recentWindowsTitle");
  }
  return t("instanceDetail.recentEventsTitle");
}

function getInstanceActivitySubtitle(tab: InstanceActivityTab, t: ReturnType<typeof useTranslation>["t"]) {
  if (tab === "commands") {
    return t("instanceDetail.commandsSubtitle");
  }
  if (tab === "metrics") {
    return t("instanceDetail.recentWindowsSubtitle");
  }
  return t("instanceDetail.recentEventsSubtitle");
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

function buildInstanceConnectionNotice(
  t: ReturnType<typeof useTranslation>["t"],
  label: string,
  streamStatus: ReturnType<typeof useCommandStreamStatus>,
  dataUpdatedAt: number,
  hasData: boolean,
  queryError: unknown,
  issue: OperationIssue | null,
) {
  if (issue) {
    if (issue.timeout) {
      return {
        tone: "error" as const,
        title: t("controlPlaneStatus.banner.timeoutTitle"),
        body: t("controlPlaneStatus.banner.timeoutBody", { label }),
        detail: issue.message,
      };
    }
    return {
      tone: "error" as const,
      title: t("controlPlaneStatus.banner.commandFailedTitle"),
      body: t("controlPlaneStatus.banner.commandFailedBody"),
      detail: issue.message,
    };
  }

  if (queryError instanceof ApiTimeoutError && hasData) {
    return {
      tone: "error" as const,
      title: t("controlPlaneStatus.banner.timeoutTitle"),
      body: t("controlPlaneStatus.banner.timeoutBody", { label }),
      detail: queryError.message,
    };
  }

  if (streamStatus.phase === "error") {
    return {
      tone: "error" as const,
      title: t("controlPlaneStatus.banner.streamErrorTitle"),
      body: t("controlPlaneStatus.banner.streamErrorBody"),
    };
  }

  if (streamStatus.phase === "stale") {
    return {
      tone: "warning" as const,
      title: t("controlPlaneStatus.banner.streamStaleTitle"),
      body: t("controlPlaneStatus.banner.streamStaleBody"),
    };
  }

  if (streamStatus.phase === "reconnecting") {
    return {
      tone: "warning" as const,
      title: t("controlPlaneStatus.banner.streamReconnectingTitle"),
      body: t("controlPlaneStatus.banner.streamReconnectingBody"),
    };
  }

  if (hasData && isUiQueryDataStale(dataUpdatedAt)) {
    return {
      tone: "warning" as const,
      title: t("controlPlaneStatus.banner.staleDataTitle", { label }),
      body: t("controlPlaneStatus.banner.staleDataBody", {
        age: formatRelativeTime(new Date(dataUpdatedAt).toISOString()),
      }),
    };
  }

  return null;
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
