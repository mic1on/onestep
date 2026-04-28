import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { EmptyState } from "../../components/ui/EmptyState";
import { Panel } from "../../components/ui/Panel";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { TaskEventFailureDetails } from "../../components/ui/TaskEventFailureDetails";
import { CommandFeed } from "../../features/commands/components/CommandFeed";
import { SessionList } from "../../features/commands/components/SessionList";
import { useServiceCommandsQuery, useServiceSessionsQuery } from "../../features/commands/queries";
import { ServiceInstancesList } from "../../features/services/components/ServiceInstancesList";
import { useServiceInstancesQuery, useServiceTasksQuery } from "../../features/services/queries";
import { TaskCommandControls } from "../../features/tasks/components/TaskCommandControls";
import { TaskMetricHistoryChart } from "../../features/tasks/components/TaskMetricHistoryChart";
import {
  formatRetryPolicySummary,
  TaskEmitValue,
  TaskSourceValue,
  TaskTopologyPreview,
} from "../../features/tasks/components/TaskTopologySummary";
import { useTaskDetailQuery } from "../../features/tasks/queries";
import {
  formatCompactJson,
  formatCount,
  formatDateTime,
  formatDurationMs,
  formatIdentifierPreview,
  formatRelativeTime,
} from "../../lib/formatters";
import { parseEnvironment, parseLookback } from "../../lib/params";
import { instancePath, servicePath, taskPath } from "../../lib/routes";
import type { AgentSessionSummary, InstanceSummary, TaskDashboardSummary } from "../../lib/api/types";

type ServiceView = "overview" | "instances" | "tasks" | "commands";

export function TaskDetailPage() {
  const { i18n, t } = useTranslation();
  const { serviceName, taskName } = useParams<{ serviceName: string; taskName: string }>();
  const [searchParams] = useSearchParams();
  const isZh = Boolean(i18n.resolvedLanguage?.startsWith("zh"));

  if (!serviceName || !taskName) {
    return <EmptyState title={t("taskDetail.missingTitle")} body={t("taskDetail.missingBody")} />;
  }

  const resolvedServiceName = serviceName;
  const resolvedTaskName = taskName;
  const environment = parseEnvironment(searchParams);
  const lookbackMinutes = parseLookback(searchParams, 60);

  const taskQuery = useTaskDetailQuery(resolvedServiceName, resolvedTaskName, environment, lookbackMinutes);
  const tasksQuery = useServiceTasksQuery(resolvedServiceName, environment, lookbackMinutes);
  const commandsQuery = useServiceCommandsQuery(resolvedServiceName, environment, {
    enabled: !taskQuery.error,
  });
  const instancesQuery = useServiceInstancesQuery(resolvedServiceName, environment, {
    enabled: !taskQuery.error,
  });
  const sessionsQuery = useServiceSessionsQuery(resolvedServiceName, environment, {
    enabled: !taskQuery.error,
  });

  if (taskQuery.error) {
    return <EmptyState title={t("taskDetail.loadErrorTitle")} body={String(taskQuery.error)} />;
  }

  const payload = taskQuery.data;
  const summary = payload?.summary;
  const tasks = prioritizeTasks(tasksQuery.data?.items ?? []);
  const taskControlStates = payload?.task_control.instances ?? [];
  const eventCountTotal = summary
    ? summary.event_counts.failed +
      summary.event_counts.retried +
      summary.event_counts.dead_lettered +
      summary.event_counts.cancelled +
      summary.event_counts.succeeded
    : 0;
  const controllableInstances = taskControlStates.filter(isControllableInstance);
  const commands = commandsQuery.data?.items ?? [];
  const taskCommands = commands.filter(
    (command) =>
      (command.kind === "pause_task" || command.kind === "resume_task") &&
      command.args.task_name === resolvedTaskName,
  );
  const instanceOrder = new Map(taskControlStates.map((state, index) => [state.instance_id, index]));
  const boundInstanceIds = new Set(taskControlStates.map((state) => state.instance_id));
  const boundInstances = sortBoundInstances(instancesQuery.data?.items ?? [], boundInstanceIds, instanceOrder);
  const boundSessions = sortBoundSessions(sessionsQuery.data?.items ?? [], boundInstanceIds, instanceOrder);
  const taskStatus: "ok" | "warning" | "degraded" = summary ? deriveTaskStatus(summary) : "ok";
  const retrySummary = summary ? formatRetryPolicySummary(summary.retry_policy, isZh) : t("common.none");

  function buildServiceViewHref(view: ServiceView) {
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
          <Link className="ref-back-button" to={buildServiceViewHref("tasks")}>
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
                <h2>{resolvedTaskName}</h2>
                <StatusBadge {...getTaskStatusBadge(taskStatus, isZh)} />
              </div>
              <p>
                {isZh
                  ? `${resolvedServiceName} · 最近事件 ${formatRelativeTime(summary?.last_event_at ?? null)}`
                  : `${resolvedServiceName} · last event ${formatRelativeTime(summary?.last_event_at ?? null)}`}
              </p>
            </div>
          </div>
        </div>
      </section>

      {taskQuery.isPending ? <div className="loading-block hero-block">{t("taskDetail.loading")}</div> : null}

      {summary ? (
        <div className="ref-detail-layout">
          <aside className="ref-side-nav">
            <Link className="ref-side-nav-item" to={buildServiceViewHref("overview")}>
              <span className="ref-side-nav-icon">◫</span>
              <span>{isZh ? "概览" : "Overview"}</span>
            </Link>
            <Link className="ref-side-nav-item" to={buildServiceViewHref("instances")}>
              <span className="ref-side-nav-icon">≣</span>
              <span>{isZh ? "实例" : "Instances"}</span>
            </Link>
            <Link className="ref-side-nav-item is-active" to={buildServiceViewHref("tasks")}>
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
                  <h3>{isZh ? `任务列表 (${tasks.length})` : `Tasks (${tasks.length})`}</h3>
                </div>
                <div className="ref-task-browser-card">
                  {tasks.map((task) => {
                    const issueCount = task.failed + task.dead_lettered;
                    const isActive = task.task_name === resolvedTaskName;

                    return (
                      <Link
                        className={isActive ? "ref-task-browser-row is-active" : "ref-task-browser-row"}
                        key={task.task_name}
                        to={taskPath(resolvedServiceName, task.task_name, {
                          environment,
                          lookback_minutes: lookbackMinutes,
                        })}
                      >
                        <div className="ref-task-browser-copy">
                          <strong>{task.task_name}</strong>
                          <span>{task.description ?? t("common.notAvailable")}</span>
                          <TaskTopologyPreview isZh={isZh} task={task} />
                        </div>
                        <div className="ref-task-browser-metrics">
                          <span>{t("taskDetail.failedDlq")}: {formatCount(issueCount)}</span>
                          <span>{t("taskDetail.maxP95")}: {formatDurationMs(task.max_p95_duration_ms)}</span>
                        </div>
                      </Link>
                    );
                  })}
                </div>
              </section>

              <section className="ref-task-detail-pane">
                <section className="ref-summary-strip">
                  <SummaryChip label={t("taskDetail.succeeded")} tone="success" value={formatCount(summary.succeeded)} />
                  <SummaryChip
                    label={t("taskDetail.failedDlq")}
                    tone={summary.failed + summary.dead_lettered > 0 ? "danger" : "default"}
                    value={formatCount(summary.failed + summary.dead_lettered)}
                  />
                  <SummaryChip label={t("taskDetail.retried")} tone="accent" value={formatCount(summary.retried)} />
                  <SummaryChip label={t("taskDetail.maxP95")} tone="default" value={formatDurationMs(summary.max_p95_duration_ms)} />
                </section>

                <section className="ref-overview-grid">
                  <Panel
                    className="ref-card-panel"
                    subtitle={isZh ? "当前任务的定义、输入来源和运行约束。" : "Task definition, source, and current execution constraints."}
                    title={isZh ? "任务配置" : "Task configuration"}
                  >
                    <div className="ref-info-grid">
                      <InfoPair label={t("taskDetail.description")} value={summary.description ?? t("common.notAvailable")} />
                      <InfoPair
                        label={t("taskDetail.source")}
                        value={<TaskSourceValue emptyLabel={t("common.notAvailable")} isZh={isZh} task={summary} />}
                      />
                      <InfoPair
                        label={t("taskDetail.emit")}
                        value={<TaskEmitValue emit={summary.emit} emptyLabel={t("common.none")} isZh={isZh} />}
                      />
                      <InfoPair label={t("taskDetail.concurrency")} value={String(summary.concurrency ?? t("common.notAvailable"))} />
                      <InfoPair
                        label={t("taskDetail.timeout")}
                        value={summary.timeout_s ? `${summary.timeout_s}s` : t("common.none")}
                      />
                      <InfoPair label={t("taskDetail.retryPolicy")} value={retrySummary} />
                      <InfoPair label={t("taskDetail.lastEvent")} value={formatDateTime(summary.last_event_at)} />
                    </div>
                  </Panel>

                  <Panel
                    className="ref-card-panel"
                    subtitle={isZh ? "仅展示有真实来源的任务运行信号。" : "Only signals backed by real task data are shown here."}
                    title={isZh ? "任务运行摘要" : "Task runtime signals"}
                  >
                    <div className="ref-signal-grid">
                      <SignalCard
                        label={t("taskDetail.eventTotal")}
                        note={isZh ? "事件总量" : "Current event volume"}
                        value={formatCount(eventCountTotal)}
                      />
                      <SignalCard
                        label={t("taskDetail.windowCount")}
                        note={isZh ? "指标窗口数量" : "Metric windows in scope"}
                        value={formatCount(summary.metric_window_count)}
                      />
                      <SignalCard
                        label={t("taskDetail.avgDuration")}
                        note={isZh ? "基于窗口聚合" : "Weighted from metric windows"}
                        value={formatDurationMs(summary.weighted_avg_duration_ms)}
                      />
                      <SignalCard
                        label={t("taskDetail.boundInstancesTitle")}
                        note={isZh ? "当前绑定实例" : "Instances currently reporting this task"}
                        value={String(taskControlStates.length)}
                      />
                    </div>
                  </Panel>
                </section>

                {controllableInstances.length > 0 ? (
                  <Panel
                    className="ref-card-panel"
                    subtitle={t("taskDetail.controlsSubtitle")}
                    title={t("taskDetail.controlsTitle")}
                  >
                    <TaskCommandControls
                      environment={environment}
                      serviceName={resolvedServiceName}
                      taskControl={payload.task_control}
                      taskName={resolvedTaskName}
                    />
                  </Panel>
                ) : null}

                <section className="ref-access-grid">
                  <Panel
                    className="ref-card-panel"
                    subtitle={t("taskDetail.recentCommandsSubtitle")}
                    title={t("taskDetail.recentCommandsTitle")}
                  >
                    <CommandFeed
                      commands={taskCommands.slice(0, 4)}
                      emptyBody={t("taskDetail.noCommandsBody")}
                      emptyTitle={t("taskDetail.noCommandsTitle")}
                      environment={environment}
                      lookbackMinutes={lookbackMinutes}
                      serviceName={resolvedServiceName}
                    />
                  </Panel>

                  <Panel
                    className="ref-card-panel"
                    subtitle={t("taskDetail.sessionsSubtitle")}
                    title={t("taskDetail.sessionsTitle")}
                  >
                    <SessionList
                      emptyBody={t("taskDetail.noSessionsBody")}
                      emptyTitle={t("taskDetail.noSessionsTitle")}
                      sessions={boundSessions}
                    />
                  </Panel>
                </section>

                <section className="ref-access-grid">
                  <Panel
                    className="ref-card-panel"
                    subtitle={t("taskDetail.recentEventsSubtitle")}
                    title={t("taskDetail.recentEventsTitle")}
                  >
                    {payload.recent_events.length ? (
                      <div className="stack-list">
                        {payload.recent_events.map((event) => (
                          <article className="event-row" key={event.event_id}>
                            <div>
                              <strong>{t(`eventKind.${event.kind}`, { defaultValue: event.kind })}</strong>
                              <p>{formatDateTime(event.occurred_at)}</p>
                            </div>
                            <TaskEventFailureDetails event={event} />
                          </article>
                        ))}
                      </div>
                    ) : (
                      <EmptyState title={t("taskDetail.noEventsTitle")} body={t("taskDetail.noEventsBody")} />
                    )}
                  </Panel>

                  <Panel
                    className="ref-card-panel"
                    subtitle={t("taskDetail.boundInstancesSubtitle")}
                    title={t("taskDetail.boundInstancesTitle")}
                  >
                    <ServiceInstancesList
                      environment={environment}
                      instances={boundInstances}
                      lookbackMinutes={lookbackMinutes}
                      serviceName={resolvedServiceName}
                    />
                  </Panel>
                </section>

                <Panel
                  className="ref-card-panel"
                  subtitle={t("taskDetail.recentWindowsSubtitle")}
                  title={t("taskDetail.recentWindowsTitle")}
                >
                  {payload.recent_metric_windows.length ? (
                    <div className="task-history-stack">
                      <TaskMetricHistoryChart windows={payload.recent_metric_windows} />
                    </div>
                  ) : (
                    <EmptyState title={t("taskDetail.noWindowsTitle")} body={t("taskDetail.noWindowsBody")} />
                  )}
                </Panel>

                <details className="ref-collapse-card">
                  <summary>
                    <strong>{isZh ? "原始配置" : "Raw configuration"}</strong>
                    <span>{isZh ? "展开查看 emit、retry 和 source 配置。" : "Expand to inspect emit, retry, and source configuration."}</span>
                  </summary>
                  <div className="ref-collapse-body ref-json-stack">
                    <div>
                      <p className="json-label">{t("taskDetail.emit")}</p>
                      <pre className="json-block">{formatCompactJson(summary.emit)}</pre>
                    </div>
                    <div>
                      <p className="json-label">{t("taskDetail.retryPolicy")}</p>
                      <pre className="json-block">{formatCompactJson(summary.retry_policy)}</pre>
                    </div>
                    <div>
                      <p className="json-label">{t("taskDetail.sourceConfig")}</p>
                      <pre className="json-block">{formatCompactJson(summary.source_config)}</pre>
                    </div>
                  </div>
                </details>
              </section>
            </div>
          </div>
        </div>
      ) : null}
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

function deriveTaskStatus(summary: TaskDashboardSummary) {
  if (summary.failed + summary.dead_lettered > 0) {
    return "degraded" as const;
  }
  if (summary.retried > 0) {
    return "warning" as const;
  }
  return "ok" as const;
}

function getTaskStatusBadge(status: "ok" | "warning" | "degraded", isZh: boolean) {
  if (status === "ok") {
    return { value: "online" as const, label: isZh ? "稳定" : "Stable" };
  }
  if (status === "warning") {
    return { value: "degraded" as const, label: isZh ? "重试中" : "Retrying" };
  }
  return { value: "failed" as const, label: isZh ? "异常" : "Issues" };
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
  if (instance.connectivity === "online") {
    if (instance.status === "error" || instance.status === "degraded") {
      return 3;
    }
    if (instance.active_session === null) {
      return 2;
    }
    return 1;
  }
  if (instance.connectivity === "offline") {
    return 0;
  }
  return -1;
}

function sortBoundInstances(
  instances: InstanceSummary[],
  boundInstanceIds: Set<string>,
  instanceOrder: Map<string, number>,
) {
  return instances
    .filter((instance) => boundInstanceIds.has(instance.instance_id))
    .sort((left, right) => {
      const leftIndex = instanceOrder.get(left.instance_id) ?? Number.MAX_SAFE_INTEGER;
      const rightIndex = instanceOrder.get(right.instance_id) ?? Number.MAX_SAFE_INTEGER;
      return leftIndex - rightIndex;
    });
}

function sortBoundSessions(
  sessions: AgentSessionSummary[],
  boundInstanceIds: Set<string>,
  instanceOrder: Map<string, number>,
) {
  return sessions
    .filter((session) => boundInstanceIds.has(session.instance_id))
    .sort((left, right) => {
      const leftIndex = instanceOrder.get(left.instance_id) ?? Number.MAX_SAFE_INTEGER;
      const rightIndex = instanceOrder.get(right.instance_id) ?? Number.MAX_SAFE_INTEGER;
      return leftIndex - rightIndex;
    });
}

function compareDateDesc(left: string | null, right: string | null) {
  const leftValue = left ? Date.parse(left) : 0;
  const rightValue = right ? Date.parse(right) : 0;
  return rightValue - leftValue;
}

function isControllableInstance(state: { connectivity: string; supported_commands: string[] }) {
  return state.connectivity === "online" && state.supported_commands.length > 0;
}
