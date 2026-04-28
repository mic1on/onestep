import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { CodeBlock } from "../../components/ui/CodeBlock";
import { EmptyState } from "../../components/ui/EmptyState";
import { Panel } from "../../components/ui/Panel";
import { SegmentedControl } from "../../components/ui/SegmentedControl";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { CommandReasonDialog } from "../../features/commands/components/CommandReasonDialog";
import { useCreateServiceCommandFanoutMutation, useServiceCommandsQuery, useServiceSessionsQuery } from "../../features/commands/queries";
import { ServiceCommandFanout } from "../../features/commands/components/ServiceCommandFanout";
import { useServiceDashboardQuery, useServiceInstancesQuery, useServiceTasksQuery } from "../../features/services/queries";
import type {
  AgentCommandKind,
  AgentSessionSummary,
  Environment,
  InstanceSummary,
  TaskDashboardSummary,
} from "../../lib/api/types";
import {
  formatDateTime,
  formatDurationMs,
  formatIdentifierPreview,
  formatRelativeTime,
} from "../../lib/formatters";
import { parseEnvironment, parseLookback } from "../../lib/params";
import { instancePath, servicePath, taskPath } from "../../lib/routes";

const LOOKBACK_OPTIONS = [15, 60, 360, 1440];
const DETAIL_VIEWS = ["overview", "instances", "tasks", "commands"] as const;
const QUICK_ACTIONS: Array<Exclude<AgentCommandKind, "shutdown" | "ping" | "pause_task" | "resume_task" | "discard_dead_letters" | "replay_dead_letters">> = [
  "sync_now",
  "flush_metrics",
  "flush_events",
  "restart",
];
type DetailView = (typeof DETAIL_VIEWS)[number];

export function ServiceDetailPage() {
  const { i18n, t } = useTranslation();
  const { serviceName } = useParams<{ serviceName: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const [pendingQuickAction, setPendingQuickAction] =
    useState<(typeof QUICK_ACTIONS)[number] | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const isZh = Boolean(i18n.resolvedLanguage?.startsWith("zh"));

  if (!serviceName) {
    return <EmptyState title={t("serviceDetail.missingTitle")} body={t("serviceDetail.missingBody")} />;
  }

  const resolvedServiceName = serviceName;

  const environment = parseEnvironment(searchParams);
  const lookbackMinutes = parseLookback(searchParams, 60);
  const currentView = parseDetailView(searchParams.get("tab"));

  const dashboardQuery = useServiceDashboardQuery(serviceName, environment, lookbackMinutes);
  const tasksQuery = useServiceTasksQuery(serviceName, environment, lookbackMinutes);
  const commandsQuery = useServiceCommandsQuery(serviceName, environment);
  const sessionsQuery = useServiceSessionsQuery(serviceName, environment);
  const instancesQuery = useServiceInstancesQuery(serviceName, environment);
  const quickActionMutation = useCreateServiceCommandFanoutMutation(serviceName, environment);

  function updateParam(key: string, value: string) {
    const next = new URLSearchParams(searchParams);
    if (value) {
      next.set(key, value);
    } else {
      next.delete(key);
    }
    setSearchParams(next, { replace: true });
  }

  async function handleQuickActionConfirm(reason: string) {
    if (!pendingQuickAction) {
      return;
    }

    setActionError(null);
    setActionMessage(null);

    try {
      const result = await quickActionMutation.mutateAsync({
        kind: pendingQuickAction,
        timeout_s: resolveQuickActionTimeoutSeconds(pendingQuickAction),
        reason,
        target_mode: "all_online",
        offline_behavior: "skip",
      });

      setActionMessage(
        isZh
          ? `${t(`commandKind.${pendingQuickAction}`, { defaultValue: pendingQuickAction })} 已下发，目标 ${result.counts.dispatched + result.counts.queued} 个实例。`
          : `${t(`commandKind.${pendingQuickAction}`, { defaultValue: pendingQuickAction })} dispatched to ${result.counts.dispatched + result.counts.queued} instances.`,
      );
      setPendingQuickAction(null);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
      throw error;
    }
  }

  if (dashboardQuery.error) {
    return <EmptyState title={t("serviceDetail.loadErrorTitle")} body={String(dashboardQuery.error)} />;
  }

  const dashboard = dashboardQuery.data;
  const tasks = prioritizeTasks(tasksQuery.data?.items ?? []);
  const instances = prioritizeInstances(instancesQuery.data?.items ?? []);
  const commands = commandsQuery.data?.items ?? [];
  const sessions = sessionsQuery.data?.items ?? [];
  const topSessions = sessions.slice(0, 3);
  const serviceStatus: "online" | "offline" | "degraded" = dashboard ? deriveServiceStatus(dashboard) : "offline";
  const runtimeSignals = dashboard ? buildRuntimeSignals(dashboard, isZh) : [];

  function buildViewHref(view: DetailView) {
    return servicePath(resolvedServiceName, {
      environment,
      lookback_minutes: lookbackMinutes,
      tab: view === "overview" ? undefined : view,
    });
  }

  return (
    <div className="ref-console-page ref-detail-page">
      <section className="ref-detail-topbar">
        <div className="ref-detail-topbar-rail">
          <Link className="ref-back-button" to={`/services?environment=${environment}`}>
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
                <h2>{serviceName}</h2>
                <StatusBadge {...getServiceStatusBadge(serviceStatus, isZh)} />
              </div>
              <p>
                {isZh
                  ? `${t(`environment.${environment}`)} · 最近同步 ${formatRelativeTime(dashboard?.service.latest_sync_at ?? null)}`
                  : `${t(`environment.${environment}`)} · last sync ${formatRelativeTime(dashboard?.service.latest_sync_at ?? null)}`}
              </p>
            </div>
          </div>

          <div className="ref-detail-header-actions">
            <SegmentedControl
              ariaLabel={t("serviceDetail.lookbackAriaLabel")}
              onChange={(value) => updateParam("lookback_minutes", String(value))}
              options={LOOKBACK_OPTIONS.map((value) => ({
                label: value >= 1440 ? "1d" : `${value}m`,
                value: String(value),
              }))}
              value={String(lookbackMinutes)}
            />

            {QUICK_ACTIONS.map((kind) => (
              <button
                className={kind === "restart" ? "ref-ghost-button is-danger" : "ref-ghost-button"}
                disabled={quickActionMutation.isPending}
                key={kind}
                onClick={() => setPendingQuickAction(kind)}
                type="button"
              >
                {t(`commandKind.${kind}`, { defaultValue: kind })}
              </button>
            ))}
          </div>
        </div>
      </section>

      {actionMessage ? <div className="inline-feedback inline-feedback-success">{actionMessage}</div> : null}
      {actionError ? <div className="inline-feedback inline-feedback-error">{actionError}</div> : null}
      {dashboardQuery.isPending ? <div className="loading-block hero-block">{t("serviceDetail.loading")}</div> : null}

      {dashboard ? (
        <div className="ref-detail-layout">
          <aside className="ref-side-nav">
            <Link className={currentView === "overview" ? "ref-side-nav-item is-active" : "ref-side-nav-item"} to={buildViewHref("overview")}>
              <span className="ref-side-nav-icon">◫</span>
              <span>{isZh ? "概览" : "Overview"}</span>
            </Link>
            <Link className={currentView === "instances" ? "ref-side-nav-item is-active" : "ref-side-nav-item"} to={buildViewHref("instances")}>
              <span className="ref-side-nav-icon">≣</span>
              <span>{isZh ? "实例" : "Instances"}</span>
            </Link>
            <Link className={currentView === "tasks" ? "ref-side-nav-item is-active" : "ref-side-nav-item"} to={buildViewHref("tasks")}>
              <span className="ref-side-nav-icon">⌘</span>
              <span>{isZh ? "任务" : "Tasks"}</span>
            </Link>
            <Link className={currentView === "commands" ? "ref-side-nav-item is-active" : "ref-side-nav-item"} to={buildViewHref("commands")}>
              <span className="ref-side-nav-icon">↻</span>
              <span>{isZh ? "命令" : "Commands"}</span>
            </Link>
          </aside>

          <div className="ref-detail-content">
            {currentView === "overview" ? (
              <>
                <section className="ref-overview-grid ref-overview-grid-compact">
                  <Panel
                    className="ref-card-panel ref-card-panel-compact"
                    subtitle={isZh ? "关键基础信息" : "Key service metadata"}
                    title={isZh ? "基础信息" : "Basic information"}
                  >
                    <div className="ref-info-grid ref-info-grid-compact">
                      <InfoPair
                        label={isZh ? "时间" : "Timeline"}
                        value={
                          <div className="ref-info-value-stack">
                            <strong>
                              {isZh ? "创建" : "Created"} {formatDateTime(dashboard.service.created_at)}
                            </strong>
                            <span className="ref-info-value-meta">
                              {isZh ? "同步" : "Synced"} {formatDateTime(dashboard.service.latest_sync_at)}
                            </span>
                          </div>
                        }
                      />
                      <InfoPair
                        label={isZh ? "部署" : "Deployment"}
                        value={
                          <div className="ref-info-value-stack">
                            <strong>{dashboard.service.latest_deployment_version}</strong>
                            <span className="ref-info-value-meta">{t(`environment.${environment}`)}</span>
                          </div>
                        }
                      />
                      <InfoPair
                        label={isZh ? "拓扑" : "Topology"}
                        value={
                          <div className="ref-info-value-stack">
                            <strong>{formatIdentifierPreview(dashboard.service.latest_topology_hash)}</strong>
                            <div className="ref-info-value-tag">
                              <StatusBadge
                                label={dashboard.topology_consistent ? (isZh ? "一致" : "Aligned") : isZh ? "漂移" : "Drift"}
                                value={dashboard.topology_consistent ? "consistent" : "drift"}
                              />
                            </div>
                          </div>
                        }
                      />
                      <InfoPair
                        label={isZh ? "可见性" : "Visibility"}
                        value={
                          <div className="ref-info-value-stack">
                            <strong>{formatRelativeTime(dashboard.service.last_seen_at)}</strong>
                            <span className="ref-info-value-meta">
                              {isZh ? "覆盖" : "Coverage"} {dashboard.instance_connectivity.online}/{dashboard.instance_connectivity.total || 0}
                            </span>
                          </div>
                        }
                      />
                    </div>
                  </Panel>

                  <Panel
                    className="ref-card-panel ref-card-panel-compact"
                    subtitle={
                      isZh
                        ? `真实数据聚合，更新时间 ${formatDateTime(dashboard.service.latest_sync_at)}`
                        : `Aggregated from real control-plane data, updated ${formatDateTime(dashboard.service.latest_sync_at)}`
                    }
                    title={isZh ? "运行摘要" : "Runtime signals"}
                  >
                    <div className="ref-compact-kpi-grid">
                      {runtimeSignals.map((item) => (
                        <CompactKpi key={item.label} label={item.label} value={item.value} />
                      ))}
                    </div>
                  </Panel>
                </section>

                <section className="ref-access-grid">
                  <Panel
                    className="ref-card-panel"
                    subtitle={isZh ? "当前服务的接入状态、会话覆盖和命令概况。" : "Current access state, session coverage, and command status."}
                    title={isZh ? "接入与控制" : "Access and control"}
                  >
                    <div className="ref-access-card">
                      <div className="ref-access-row">
                        <span>{isZh ? "在线实例" : "Online instances"}</span>
                        <strong>{`${dashboard.instance_connectivity.online}/${dashboard.instance_connectivity.total || 0}`}</strong>
                      </div>
                      <div className="ref-access-row">
                        <span>{isZh ? "执行中命令" : "In-flight commands"}</span>
                        <strong>{dashboard.command_overview.statuses.in_flight}</strong>
                      </div>
                      <div className="ref-access-row">
                        <span>{isZh ? "最近命令" : "Last command"}</span>
                        <strong>{formatDateTime(dashboard.command_overview.last_command_at)}</strong>
                      </div>
                      <div className="ref-access-row">
                        <span>{isZh ? "最近完成" : "Last completed"}</span>
                        <strong>{formatDateTime(dashboard.command_overview.last_completed_at)}</strong>
                      </div>
                    </div>
                  </Panel>

                  <Panel
                    className="ref-card-panel"
                    subtitle={isZh ? "优先展示最近的控制会话，帮助判断是否可以安全发命令。" : "Recent control sessions help you quickly judge whether commands are safe to dispatch."}
                    title={isZh ? "控制会话" : "Control sessions"}
                  >
                    {topSessions.length ? (
                      <div className="ref-session-list">
                        {topSessions.map((session) => (
                          <div className="ref-session-item" key={session.session_id}>
                            <div>
                              <strong>{session.node_name ?? formatIdentifierPreview(session.instance_id)}</strong>
                              <span>{session.hostname ?? formatIdentifierPreview(session.session_id)}</span>
                            </div>
                            <StatusBadge value={session.status} />
                          </div>
                        ))}
                      </div>
                    ) : (
                      <EmptyState title={t("serviceDetail.noSessionsTitle")} body={t("serviceDetail.noSessionsBody")} />
                    )}
                  </Panel>
                </section>

                <section className="ref-summary-band">
                  <BandStat label={isZh ? "异常任务" : "Failing tasks"} value={String(dashboard.failing_task_count)} />
                  <BandStat label={isZh ? "拓扑哈希" : "Topology hashes"} value={String(dashboard.topology_hashes.length)} />
                  <BandStat label={isZh ? "命令总数" : "Commands"} value={String(dashboard.command_overview.statuses.total)} />
                  <BandStat label={isZh ? "能力数" : "Capabilities"} value={String(summarizeCapabilities(sessions).length)} />
                </section>
              </>
            ) : null}

            {currentView === "instances" ? (
              <section className="ref-table-section">
              <div className="ref-section-headline">
                <h3>{isZh ? `实例列表 (${instances.length})` : `Instances (${instances.length})`}</h3>
              </div>
              <div className="ref-detail-table-card">
                <div className="ref-detail-table-head ref-instance-grid">
                  <span>{isZh ? "实例" : "Instance"}</span>
                  <span>{isZh ? "会话" : "Session"}</span>
                  <span>{isZh ? "状态" : "Health"}</span>
                  <span>{isZh ? "最近同步" : "Last sync"}</span>
                  <span>{isZh ? "最近可见" : "Last seen"}</span>
                  <span>{isZh ? "操作" : "Action"}</span>
                </div>
                <div className="ref-detail-table-body">
                  {instances.map((instance) => (
                    <div className="ref-detail-table-row ref-instance-grid" key={instance.instance_id}>
                      <div className="ref-table-primary">
                        <strong>{instance.node_name}</strong>
                        <span>{formatIdentifierPreview(instance.instance_id)}</span>
                      </div>
                      <div className="ref-table-primary">
                        <strong>{instance.active_session ? t(`status.${instance.active_session.status}`) : t("status.offline")}</strong>
                        <span>{instance.hostname ?? t("common.notAvailable")}</span>
                      </div>
                      <div className="ref-table-status-group">
                        <StatusBadge value={instance.connectivity} />
                        <StatusBadge value={instance.status} />
                      </div>
                      <div className="ref-table-primary">
                        <strong>{formatDateTime(instance.last_sync_at)}</strong>
                      </div>
                      <div className="ref-table-primary">
                        <strong>{formatRelativeTime(instance.last_seen_at)}</strong>
                      </div>
                      <div className="ref-table-actions">
                        <Link
                          className="ref-icon-action"
                          to={instancePath(serviceName, instance.instance_id, {
                            environment,
                            lookback_minutes: lookbackMinutes,
                          })}
                        >
                          {isZh ? "详情" : "Detail"}
                        </Link>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
              </section>
            ) : null}

            {currentView === "tasks" ? (
              <section className="ref-table-section">
              <div className="ref-section-headline">
                <h3>{isZh ? `任务列表 (${tasks.length})` : `Tasks (${tasks.length})`}</h3>
              </div>
              <div className="ref-detail-table-card">
                <div className="ref-detail-table-head ref-task-grid">
                  <span>{isZh ? "任务" : "Task"}</span>
                  <span>{isZh ? "成功" : "Succeeded"}</span>
                  <span>{isZh ? "异常 / DLQ" : "Failed / DLQ"}</span>
                  <span>{isZh ? "P95" : "P95"}</span>
                  <span>{isZh ? "最近事件" : "Last event"}</span>
                  <span>{isZh ? "操作" : "Action"}</span>
                </div>
                <div className="ref-detail-table-body">
                  {tasks.map((task) => (
                    <div className="ref-detail-table-row ref-task-grid" key={task.task_name}>
                      <div className="ref-table-primary">
                        <strong>{task.task_name}</strong>
                        <span>{task.description ?? t("common.notAvailable")}</span>
                      </div>
                      <div className="ref-table-primary">
                        <strong>{task.succeeded}</strong>
                      </div>
                      <div className="ref-table-primary">
                        <strong>{task.failed + task.dead_lettered}</strong>
                      </div>
                      <div className="ref-table-primary">
                        <strong>{formatDurationMs(task.max_p95_duration_ms)}</strong>
                      </div>
                      <div className="ref-table-primary">
                        <strong>{formatDateTime(task.last_event_at)}</strong>
                      </div>
                      <div className="ref-table-actions">
                        <Link
                          className="ref-icon-action"
                          to={taskPath(serviceName, task.task_name, {
                            environment,
                            lookback_minutes: lookbackMinutes,
                          })}
                        >
                          {isZh ? "详情" : "Detail"}
                        </Link>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
              </section>
            ) : null}

            {currentView === "commands" ? (
              <>
                <section className="ref-table-section">
              <div className="ref-section-headline">
                <h3>{isZh ? "最近命令" : "Recent commands"}</h3>
              </div>
              <div className="ref-detail-table-card">
                <div className="ref-detail-table-head ref-command-grid">
                  <span>{isZh ? "命令" : "Command"}</span>
                  <span>{isZh ? "目标实例" : "Instance"}</span>
                  <span>{isZh ? "状态" : "Status"}</span>
                  <span>{isZh ? "创建时间" : "Created"}</span>
                  <span>{isZh ? "耗时" : "Duration"}</span>
                </div>
                <div className="ref-detail-table-body">
                  {commands.slice(0, 6).length ? (
                    commands.slice(0, 6).map((command) => (
                      <div className="ref-detail-table-row ref-command-grid" key={command.command_id}>
                        <div className="ref-table-primary">
                          <strong>{t(`commandKind.${command.kind}`, { defaultValue: command.kind })}</strong>
                          <span>{command.reason ?? t("common.notAvailable")}</span>
                        </div>
                        <div className="ref-table-primary">
                          <strong>{command.node_name ?? formatIdentifierPreview(command.instance_id)}</strong>
                        </div>
                        <div className="ref-table-status-group">
                          <StatusBadge value={command.status} />
                        </div>
                        <div className="ref-table-primary">
                          <strong>{formatDateTime(command.created_at)}</strong>
                        </div>
                        <div className="ref-table-primary">
                          <strong>{formatDurationMs(command.duration_ms)}</strong>
                        </div>
                      </div>
                    ))
                  ) : (
                    <div className="ref-empty-inline">{t("serviceDetail.noCommandsBody")}</div>
                  )}
                </div>
              </div>
                </section>

                <details className="ref-collapse-card">
                  <summary>
                    <strong>{isZh ? "高级控制" : "Advanced controls"}</strong>
                    <span>
                      {isZh
                        ? "需要更细粒度的目标选择和离线策略时展开。"
                        : "Expand for target selection and offline delivery strategy."}
                    </span>
                  </summary>
                  <div className="ref-collapse-body">
                    {instancesQuery.isPending ? (
                      <div className="loading-block">{t("serviceDetail.loadingInstanceSnapshots")}</div>
                    ) : instancesQuery.error ? (
                      <EmptyState title={t("serviceDetail.instanceLoadErrorTitle")} body={String(instancesQuery.error)} />
                    ) : (
                      <ServiceCommandFanout environment={environment} instances={instances} serviceName={serviceName} />
                    )}
                  </div>
                </details>

                {dashboard.topology_hashes.length > 1 ? (
                  <details className="ref-collapse-card">
                    <summary>
                      <strong>{t("serviceDetail.topologyDriftTitle")}</strong>
                      <span>{t("serviceDetail.topologyDriftSubtitle", { count: dashboard.topology_hashes.length })}</span>
                    </summary>
                    <div className="ref-collapse-body">
                      <CodeBlock>{JSON.stringify(dashboard.topology_hashes, null, 2)}</CodeBlock>
                    </div>
                  </details>
                ) : null}
              </>
            ) : null}
          </div>
        </div>
      ) : null}

      <CommandReasonDialog
        description={
          pendingQuickAction
            ? isZh
              ? `为 ${serviceName} 执行 ${t(`commandKind.${pendingQuickAction}`, { defaultValue: pendingQuickAction })}，请填写本次操作原因。`
              : `Provide a reason for ${t(`commandKind.${pendingQuickAction}`, { defaultValue: pendingQuickAction })} on ${serviceName}.`
            : ""
        }
        isSubmitting={quickActionMutation.isPending}
        onCancel={() => {
          if (!quickActionMutation.isPending) {
            setPendingQuickAction(null);
          }
        }}
        onConfirm={handleQuickActionConfirm}
        open={pendingQuickAction !== null}
        title={
          pendingQuickAction
            ? isZh
              ? `确认 ${t(`commandKind.${pendingQuickAction}`, { defaultValue: pendingQuickAction })}`
              : `Confirm ${t(`commandKind.${pendingQuickAction}`, { defaultValue: pendingQuickAction })}`
            : ""
        }
      />
    </div>
  );
}

function InfoPair({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="ref-info-pair">
      <span>{label}</span>
      {typeof value === "string" ? <strong>{value}</strong> : value}
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

function CompactKpi({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="ref-compact-kpi">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function BandStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="ref-band-stat">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function buildRuntimeSignals(
  dashboard: NonNullable<ReturnType<typeof useServiceDashboardQuery>["data"]>,
  isZh: boolean,
) {
  return [
    {
      label: isZh ? "在线实例" : "Online instances",
      value: `${dashboard.instance_connectivity.online}/${dashboard.instance_connectivity.total}`,
      note: isZh ? "来自实例连接状态" : "Derived from instance connectivity",
    },
    {
      label: isZh ? "活跃会话" : "Active sessions",
      value: `${dashboard.command_overview.active_session_count}`,
      note: isZh ? "来自已建立控制会话" : "Current control sessions",
    },
    {
      label: isZh ? "异常任务" : "Failing tasks",
      value: `${dashboard.failing_task_count}`,
      note: isZh ? "来自任务事件与 DLQ" : "Backed by task event and DLQ state",
    },
    {
      label: isZh ? "执行中命令" : "In-flight commands",
      value: `${dashboard.command_overview.statuses.in_flight}`,
      note: isZh ? "来自命令生命周期状态" : "Backed by command lifecycle state",
    },
    {
      label: isZh ? "最近事件" : "Recent events",
      value: `${dashboard.recent_events.length}`,
      note: isZh ? "来自当前时间窗口" : "Current window event count",
    },
  ];
}

function parseDetailView(value: string | null): DetailView {
  if (value && DETAIL_VIEWS.includes(value as DetailView)) {
    return value as DetailView;
  }
  return "overview";
}

function deriveServiceStatus(
  dashboard: NonNullable<ReturnType<typeof useServiceDashboardQuery>["data"]>,
): "online" | "offline" | "degraded" {
  if (dashboard.instance_connectivity.online === 0) {
    return "offline";
  }
  if (
    dashboard.failing_task_count > 0 ||
    !dashboard.topology_consistent ||
    dashboard.instance_connectivity.online < dashboard.instance_connectivity.total
  ) {
    return "degraded";
  }
  return "online";
}

function getServiceStatusBadge(status: "online" | "offline" | "degraded", isZh: boolean) {
  if (status === "online") {
    return { value: "online" as const, label: isZh ? "运行中" : "Running" };
  }
  if (status === "degraded") {
    return { value: "degraded" as const, label: isZh ? "需关注" : "Review" };
  }
  return { value: "offline" as const, label: isZh ? "离线" : "Offline" };
}

function prioritizeTasks(tasks: TaskDashboardSummary[]) {
  return [...tasks].sort((left, right) => {
    const priorityDelta = getTaskPriority(right) - getTaskPriority(left);
    if (priorityDelta !== 0) {
      return priorityDelta;
    }
    return compareDateDesc(left.last_event_at, right.last_event_at);
  });
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

function getTaskPriority(task: TaskDashboardSummary) {
  if (task.failed + task.dead_lettered > 0) {
    return 3;
  }
  if (task.retried > 0) {
    return 2;
  }
  if (task.max_p95_duration_ms !== null) {
    return 1;
  }
  return 0;
}

function getInstancePriority(instance: InstanceSummary) {
  if (instance.connectivity !== "online") {
    return 3;
  }
  if (instance.status === "error" || instance.status === "degraded") {
    return 2;
  }
  if (instance.active_session === null) {
    return 1;
  }
  return 0;
}

function summarizeCapabilities(sessions: AgentSessionSummary[]) {
  const counts = new Map<string, number>();
  for (const session of sessions) {
    for (const capability of session.accepted_capabilities) {
      counts.set(capability, (counts.get(capability) ?? 0) + 1);
    }
  }
  return [...counts.entries()].map(([name, count]) => ({ name, count }));
}

function compareDateDesc(left: string | null, right: string | null) {
  const leftValue = left ? Date.parse(left) : 0;
  const rightValue = right ? Date.parse(right) : 0;
  return rightValue - leftValue;
}

function resolveQuickActionTimeoutSeconds(kind: (typeof QUICK_ACTIONS)[number]) {
  if (kind === "restart") {
    return 30;
  }
  return 20;
}
