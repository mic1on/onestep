import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { CodeBlock } from "../../components/ui/CodeBlock";
import { EmptyState } from "../../components/ui/EmptyState";
import { Panel } from "../../components/ui/Panel";
import { SegmentedControl } from "../../components/ui/SegmentedControl";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { useToast } from "../../components/ui/ToastProvider";
import { useConsoleSessionQuery } from "../../features/auth/queries";
import { canViewCommandControls, canViewDestructiveControls } from "../../features/auth/session";
import { CommandReasonDialog } from "../../features/commands/components/CommandReasonDialog";
import { ConnectionStatusBanner } from "../../features/commands/components/ConnectionStatusBanner";
import { DestructiveCommandReviewDialog } from "../../features/commands/components/DestructiveCommandReviewDialog";
import {
  getCommandRiskLevel,
} from "../../features/commands/capabilities";
import {
  isUiQueryDataStale,
  useCreateServiceCommandFanoutMutation,
  useServiceCommandsQuery,
  useServiceSessionsQuery,
} from "../../features/commands/queries";
import { ServiceCommandFanout } from "../../features/commands/components/ServiceCommandFanout";
import { useCommandStreamStatus } from "../../features/commands/useCommandStream";
import { useServiceDashboardQuery, useServiceInstancesQuery, useServiceTasksQuery } from "../../features/services/queries";
import { TaskTopologyPreview } from "../../features/tasks/components/TaskTopologySummary";
import { ApiTimeoutError } from "../../lib/api/client";
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
const SYNC_ACTIONS = ["sync_now", "flush_metrics", "flush_events"] as const;
const QUICK_ACTIONS: Array<
  Exclude<
    AgentCommandKind,
    "shutdown" | "ping" | "pause_task" | "resume_task" | "discard_dead_letters" | "replay_dead_letters"
  > | "sync_all"
> = [...SYNC_ACTIONS, "sync_all", "restart"];
type DetailView = (typeof DETAIL_VIEWS)[number];
type QuickActionKind = (typeof QUICK_ACTIONS)[number];
type OperationIssue = {
  message: string;
  timeout: boolean;
};

export function ServiceDetailPage() {
  const { i18n, t } = useTranslation();
  const { pushToast } = useToast();
  const { serviceName } = useParams<{ serviceName: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const [pendingQuickAction, setPendingQuickAction] =
    useState<QuickActionKind | null>(null);
  const [visibleInstanceCount, setVisibleInstanceCount] = useState(100);
  const [visibleTaskCount, setVisibleTaskCount] = useState(100);
  const [visibleCommandCount, setVisibleCommandCount] = useState(50);
  const [actionIssue, setActionIssue] = useState<OperationIssue | null>(null);
  const isZh = Boolean(i18n.resolvedLanguage?.startsWith("zh"));
  const sessionQuery = useConsoleSessionQuery();
  const streamStatus = useCommandStreamStatus();

  if (!serviceName) {
    return <EmptyState title={t("serviceDetail.missingTitle")} body={t("serviceDetail.missingBody")} />;
  }

  const resolvedServiceName = serviceName;

  const environment = parseEnvironment(searchParams);
  const lookbackMinutes = parseLookback(searchParams, 60);
  const currentView = parseDetailView(searchParams.get("tab"));

  const dashboardQuery = useServiceDashboardQuery(serviceName, environment, lookbackMinutes);
  const tasksQuery = useServiceTasksQuery(serviceName, environment, lookbackMinutes, {
    limit: visibleTaskCount,
  });
  const commandsQuery = useServiceCommandsQuery(serviceName, environment, {
    limit: visibleCommandCount,
  });
  const sessionsQuery = useServiceSessionsQuery(serviceName, environment);
  const instancesQuery = useServiceInstancesQuery(serviceName, environment, {
    limit: visibleInstanceCount,
  });
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

    setActionIssue(null);

    try {
      if (pendingQuickAction === "sync_all") {
        let targetCount = 0;
        for (const kind of SYNC_ACTIONS) {
          const result = await quickActionMutation.mutateAsync({
            kind,
            timeout_s: resolveQuickActionTimeoutSeconds(kind),
            reason,
            target_mode: "all_online",
            offline_behavior: "skip",
          });
          targetCount = Math.max(targetCount, result.counts.dispatched + result.counts.queued);
        }

        const message = isZh
          ? `${t("serviceDetail.syncMenu.syncAll")} 已下发，目标 ${targetCount} 个实例。`
          : `${t("serviceDetail.syncMenu.syncAll")} dispatched to ${targetCount} instances.`;
        setActionIssue(null);
        pushToast({ tone: "success", message });
      } else {
        const result = await quickActionMutation.mutateAsync({
          kind: pendingQuickAction,
          timeout_s: resolveQuickActionTimeoutSeconds(pendingQuickAction),
          reason,
          target_mode: "all_online",
          offline_behavior: "skip",
        });

        const message = isZh
          ? `${getQuickActionLabel(pendingQuickAction, t)} 已下发，目标 ${result.counts.dispatched + result.counts.queued} 个实例。`
          : `${getQuickActionLabel(pendingQuickAction, t)} dispatched to ${result.counts.dispatched + result.counts.queued} instances.`;
        setActionIssue(null);
        pushToast({ tone: "success", message });
      }

      setPendingQuickAction(null);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setActionIssue({
        message,
        timeout: error instanceof ApiTimeoutError,
      });
      pushToast({ tone: "error", message });
      throw error;
    }
  }

  if (dashboardQuery.error) {
    return <EmptyState title={t("serviceDetail.loadErrorTitle")} body={String(dashboardQuery.error)} />;
  }

  const dashboard = dashboardQuery.data;
  const tasks = prioritizeTasks(tasksQuery.data?.items ?? []);
  const instances = instancesQuery.data?.items ?? [];
  const commands = commandsQuery.data?.items ?? [];
  const sessions = sessionsQuery.data?.items ?? [];
  const serviceStatus: "online" | "offline" | "degraded" = dashboard ? deriveServiceStatus(dashboard) : "offline";
  const runtimeSignals = dashboard ? buildRuntimeSignals(dashboard, isZh) : [];
  const primaryRuntimeSignal = runtimeSignals[0] ?? null;
  const canViewControls = canViewCommandControls(sessionQuery.data);
  const canViewDestructiveActions = canViewDestructiveControls(sessionQuery.data);
  const isDestructiveQuickAction = pendingQuickAction === "restart";
  const connectionNotice = buildServiceConnectionNotice(
    t,
    resolvedServiceName,
    streamStatus,
    dashboardQuery.dataUpdatedAt,
    Boolean(dashboardQuery.data),
    dashboardQuery.error,
    actionIssue,
  );

  function buildViewHref(view: DetailView) {
    return servicePath(resolvedServiceName, {
      environment,
      lookback_minutes: lookbackMinutes,
      tab: view === "overview" ? undefined : view,
    });
  }

  return (
    <div className="ref-console-page ref-detail-page signal-console-detail-page signal-console-service-detail">
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
              <span className="signal-console-kicker">
                {t("serviceDetail.eyebrow", { environment: t(`environment.${environment}`) })}
              </span>
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

          <div className="ref-detail-header-actions signal-console-service-header-actions">
            <div className="signal-console-detail-actions-row">
              <SegmentedControl
                ariaLabel={t("serviceDetail.lookbackAriaLabel")}
                onChange={(value) => updateParam("lookback_minutes", String(value))}
                options={LOOKBACK_OPTIONS.map((value) => ({
                  label: value >= 1440 ? "1d" : `${value}m`,
                  value: String(value),
                }))}
                value={String(lookbackMinutes)}
              />
              {canViewControls ? (
                <>
                  <div className="ref-action-menu" role="group">
                    <button className="ref-ghost-button ref-action-menu-trigger" disabled={quickActionMutation.isPending} type="button">
                      <span>{t("serviceDetail.syncMenu.trigger")}</span>
                      <span aria-hidden="true" className="ref-action-menu-caret">
                        ▾
                      </span>
                    </button>
                    <div className="ref-action-menu-panel">
                      {SYNC_ACTIONS.map((kind) => (
                        <button
                          className="ref-action-menu-item"
                          disabled={quickActionMutation.isPending}
                          key={kind}
                          onClick={() => setPendingQuickAction(kind)}
                          type="button"
                        >
                          {getQuickActionLabel(kind, t)}
                        </button>
                      ))}
                      <button
                        className="ref-action-menu-item ref-action-menu-item-strong"
                        disabled={quickActionMutation.isPending}
                        onClick={() => setPendingQuickAction("sync_all")}
                        type="button"
                      >
                        {t("serviceDetail.syncMenu.syncAll")}
                      </button>
                    </div>
                  </div>
                  {canViewDestructiveActions ? (
                    <button
                      className="ref-ghost-button is-danger"
                      disabled={quickActionMutation.isPending}
                      onClick={() => setPendingQuickAction("restart")}
                      type="button"
                    >
                      {t("commandKind.restart", { defaultValue: "restart" })}
                    </button>
                  ) : null}
                </>
              ) : null}
            </div>

            <div
              className="signal-console-detail-topbar-metric signal-console-service-topbar-metric-inline"
              title={primaryRuntimeSignal?.note ?? undefined}
            >
              <span>{primaryRuntimeSignal?.label ?? (isZh ? "运行信号" : "Runtime signal")}</span>
              <strong>{primaryRuntimeSignal?.value ?? "--"}</strong>
            </div>
          </div>
        </div>
      </section>

      {dashboardQuery.isPending ? <div className="loading-block hero-block">{t("serviceDetail.loading")}</div> : null}
      {connectionNotice ? (
        <ConnectionStatusBanner
          body={connectionNotice.body}
          detail={connectionNotice.detail}
          title={connectionNotice.title}
          tone={connectionNotice.tone}
        />
      ) : null}

      {dashboard ? (
        <div className="ref-detail-layout">
          <aside className="ref-side-nav">
            <Link className={currentView === "overview" ? "ref-side-nav-item is-active" : "ref-side-nav-item"} to={buildViewHref("overview")}>
              <span>{isZh ? "概览" : "Overview"}</span>
            </Link>
            <Link className={currentView === "instances" ? "ref-side-nav-item is-active" : "ref-side-nav-item"} to={buildViewHref("instances")}>
              <span>{isZh ? "实例" : "Instances"}</span>
            </Link>
            <Link className={currentView === "tasks" ? "ref-side-nav-item is-active" : "ref-side-nav-item"} to={buildViewHref("tasks")}>
              <span>{isZh ? "任务" : "Tasks"}</span>
            </Link>
            <Link className={currentView === "commands" ? "ref-side-nav-item is-active" : "ref-side-nav-item"} to={buildViewHref("commands")}>
              <span>{isZh ? "命令" : "Commands"}</span>
            </Link>
          </aside>

          <div className="ref-detail-content">
            {currentView === "overview" ? (
              <>
                <section className="ref-summary-band signal-console-summary-band">
                  <BandStat label={isZh ? "异常任务" : "Failing tasks"} value={String(dashboard.failing_task_count)} />
                  <BandStat label={isZh ? "拓扑哈希" : "Topology hashes"} value={String(dashboard.topology_hashes.length)} />
                  <BandStat label={isZh ? "命令总数" : "Commands"} value={String(dashboard.command_overview.statuses.total)} />
                  <BandStat label={isZh ? "能力数" : "Capabilities"} value={String(summarizeCapabilities(sessions).length)} />
                </section>

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
                </div>
                <div className="ref-detail-table-body">
                  {instances.map((instance) => (
                    <Link
                      className="ref-detail-table-row ref-instance-grid"
                      key={instance.instance_id}
                      to={instancePath(serviceName, instance.instance_id, {
                        environment,
                        lookback_minutes: lookbackMinutes,
                      })}
                    >
                      <div className="ref-table-primary">
                        <strong>{instance.node_name}</strong>
                        <span>{formatIdentifierPreview(instance.instance_id)}</span>
                      </div>
                      <div className="ref-table-primary" data-label={isZh ? "会话" : "Session"}>
                        <strong>{instance.active_session ? t(`status.${instance.active_session.status}`) : t("status.offline")}</strong>
                        <span>{instance.hostname ?? t("common.notAvailable")}</span>
                      </div>
                      <div className="ref-table-status-group" data-label={isZh ? "状态" : "Health"}>
                        <StatusBadge value={instance.connectivity} />
                        <StatusBadge value={instance.status} />
                      </div>
                      <div className="ref-table-primary" data-label={isZh ? "最近同步" : "Last sync"}>
                        <strong>{formatDateTime(instance.last_sync_at)}</strong>
                      </div>
                      <div className="ref-table-primary" data-label={isZh ? "最近可见" : "Last seen"}>
                        <strong>{formatRelativeTime(instance.last_seen_at)}</strong>
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
                </div>
                <div className="ref-detail-table-body">
                  {tasks.map((task) => (
                    <Link
                      className="ref-detail-table-row ref-task-grid"
                      key={task.task_name}
                      to={taskPath(serviceName, task.task_name, {
                        environment,
                        lookback_minutes: lookbackMinutes,
                      })}
                    >
                      <div className="ref-table-primary">
                        <strong>{task.task_name}</strong>
                        <span>{task.description ?? t("common.notAvailable")}</span>
                        <TaskTopologyPreview isZh={isZh} task={task} />
                      </div>
                      <div className="ref-table-primary" data-label={isZh ? "成功" : "Succeeded"}>
                        <strong>{task.succeeded}</strong>
                      </div>
                      <div className="ref-table-primary" data-label={isZh ? "异常 / DLQ" : "Failed / DLQ"}>
                        <strong>{task.failed + task.dead_lettered}</strong>
                      </div>
                      <div className="ref-table-primary" data-label="P95">
                        <strong>{formatDurationMs(task.max_p95_duration_ms)}</strong>
                      </div>
                      <div className="ref-table-primary" data-label={isZh ? "最近事件" : "Last event"}>
                        <strong>{formatDateTime(task.last_event_at)}</strong>
                      </div>
                    </Link>
                  ))}
                </div>
                {tasksQuery.data && tasksQuery.data.total > 0 ? (
                  <div className="ref-inline-pagination">
                    <span>
                      {t("common.showingVisibleItems", {
                        visible: tasks.length,
                        total: tasksQuery.data.total,
                      })}
                    </span>
                    {tasks.length < tasksQuery.data.total ? (
                      <button
                        className="ref-ghost-button"
                        onClick={() => setVisibleTaskCount((count) => count + 100)}
                        type="button"
                      >
                        {t("common.loadMore")}
                      </button>
                    ) : null}
                  </div>
                ) : null}
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
                  {commands.length ? (
                    commands.map((command) => (
                      <div className="ref-detail-table-row ref-command-grid" key={command.command_id}>
                        <div className="ref-table-primary">
                          <strong>{t(`commandKind.${command.kind}`, { defaultValue: command.kind })}</strong>
                          <span>{command.reason ?? t("common.notAvailable")}</span>
                        </div>
                        <div className="ref-table-primary" data-label={isZh ? "目标实例" : "Instance"}>
                          <strong>{command.node_name ?? formatIdentifierPreview(command.instance_id)}</strong>
                        </div>
                        <div className="ref-table-status-group" data-label={isZh ? "状态" : "Status"}>
                          <StatusBadge value={command.status} />
                        </div>
                        <div className="ref-table-primary" data-label={isZh ? "创建时间" : "Created"}>
                          <strong>{formatDateTime(command.created_at)}</strong>
                        </div>
                        <div className="ref-table-primary" data-label={isZh ? "耗时" : "Duration"}>
                          <strong>{formatDurationMs(command.duration_ms)}</strong>
                        </div>
                      </div>
                    ))
                  ) : (
                    <div className="ref-empty-inline">{t("serviceDetail.noCommandsBody")}</div>
                  )}
                </div>
                {commands.length > 0 ? (
                  <div className="ref-inline-pagination">
                    <span>
                      {t("common.showingVisibleItems", {
                        visible: commands.length,
                        total: commandsQuery.data?.total ?? commands.length,
                      })}
                    </span>
                    {commands.length < (commandsQuery.data?.total ?? commands.length) ? (
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
              </div>
                </section>

                {canViewDestructiveActions ? (
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
                        <ServiceCommandFanout
                          environment={environment}
                          instances={instances}
                          onIssueChange={setActionIssue}
                          serviceName={serviceName}
                        />
                      )}
                    </div>
                  </details>
                ) : null}

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
              ? `为 ${serviceName} 执行 ${getQuickActionLabel(pendingQuickAction, t)}，请填写本次操作原因。`
              : `Provide a reason for ${getQuickActionLabel(pendingQuickAction, t)} on ${serviceName}.`
            : ""
        }
        isSubmitting={quickActionMutation.isPending}
        onCancel={() => {
          if (!quickActionMutation.isPending) {
            setPendingQuickAction(null);
          }
        }}
        onConfirm={handleQuickActionConfirm}
        open={pendingQuickAction !== null && !isDestructiveQuickAction}
        title={
          pendingQuickAction
            ? isZh
              ? `确认 ${getQuickActionLabel(pendingQuickAction, t)}`
              : `Confirm ${getQuickActionLabel(pendingQuickAction, t)}`
            : ""
        }
      />
      <DestructiveCommandReviewDialog
        commandLabel={
          pendingQuickAction ? t(`commandKind.${pendingQuickAction}`, { defaultValue: pendingQuickAction }) : ""
        }
        description={
          pendingQuickAction
            ? isZh
              ? `为 ${serviceName} 执行 ${getQuickActionLabel(pendingQuickAction, t)}，请填写本次操作原因。`
              : `Provide a reason for ${getQuickActionLabel(pendingQuickAction, t)} on ${serviceName}.`
            : ""
        }
        isSubmitting={quickActionMutation.isPending}
        onCancel={() => {
          if (!quickActionMutation.isPending) {
            setPendingQuickAction(null);
          }
        }}
        onConfirm={handleQuickActionConfirm}
        open={pendingQuickAction !== null && isDestructiveQuickAction}
        riskLevel={pendingQuickAction === "restart" ? getCommandRiskLevel(pendingQuickAction) : "critical"}
        targetSummary={isZh ? `${serviceName} 的全部在线实例` : `All online instances for ${serviceName}`}
        title={
          pendingQuickAction
            ? isZh
              ? `确认 ${getQuickActionLabel(pendingQuickAction, t)}`
              : `Confirm ${getQuickActionLabel(pendingQuickAction, t)}`
            : ""
        }
      />
    </div>
  );
}

function buildServiceConnectionNotice(
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

function getQuickActionLabel(
  kind: QuickActionKind,
  t: ReturnType<typeof useTranslation>["t"],
) {
  if (kind === "sync_now") {
    return t("serviceDetail.syncMenu.syncConfig");
  }
  if (kind === "flush_metrics") {
    return t("serviceDetail.syncMenu.syncMetrics");
  }
  if (kind === "flush_events") {
    return t("serviceDetail.syncMenu.syncEvents");
  }
  if (kind === "sync_all") {
    return t("serviceDetail.syncMenu.syncAll");
  }
  return t(`commandKind.${kind}`, { defaultValue: kind });
}

function resolveQuickActionTimeoutSeconds(kind: Exclude<QuickActionKind, "sync_all">) {
  if (kind === "restart") {
    return 30;
  }
  return 20;
}
