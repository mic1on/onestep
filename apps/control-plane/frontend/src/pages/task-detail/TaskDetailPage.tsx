import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { EmptyState } from "../../components/ui/EmptyState";
import { Panel } from "../../components/ui/Panel";
import { SegmentedControl } from "../../components/ui/SegmentedControl";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { TaskEventFailureDetails } from "../../components/ui/TaskEventFailureDetails";
import { VibeInlineButton } from "../../components/ui/VibeInlineButton";
import { VibeSummaryStrip } from "../../components/ui/VibeSummary";
import { useConsoleSessionQuery } from "../../features/auth/queries";
import { canViewCommandControls } from "../../features/auth/session";
import { CommandFeed } from "../../features/commands/components/CommandFeed";
import { ConnectionStatusBanner } from "../../features/commands/components/ConnectionStatusBanner";
import { SessionList } from "../../features/commands/components/SessionList";
import { isUiQueryDataStale, useServiceCommandsQuery, useServiceSessionsQuery } from "../../features/commands/queries";
import { useCommandStreamStatus } from "../../features/commands/useCommandStream";
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
import { ApiTimeoutError } from "../../lib/api/client";
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
type TaskActivityTab = "events" | "instances" | "commands" | "sessions";
type OperationIssue = {
  message: string;
  timeout: boolean;
};

export function TaskDetailPage() {
  const { i18n, t } = useTranslation();
  const { serviceName, taskName } = useParams<{ serviceName: string; taskName: string }>();
  const [searchParams] = useSearchParams();
  const isZh = Boolean(i18n.resolvedLanguage?.startsWith("zh"));
  const [activityTab, setActivityTab] = useState<TaskActivityTab>("events");
  const [visibleTaskCommandCount, setVisibleTaskCommandCount] = useState(4);
  const [visibleTaskCount, setVisibleTaskCount] = useState(100);
  const [commandIssue, setCommandIssue] = useState<OperationIssue | null>(null);
  const sessionQuery = useConsoleSessionQuery();
  const streamStatus = useCommandStreamStatus();

  if (!serviceName || !taskName) {
    return <EmptyState title={t("taskDetail.missingTitle")} body={t("taskDetail.missingBody")} />;
  }

  const resolvedServiceName = serviceName;
  const resolvedTaskName = taskName;
  const environment = parseEnvironment(searchParams);
  const lookbackMinutes = parseLookback(searchParams, 60);

  const taskQuery = useTaskDetailQuery(resolvedServiceName, resolvedTaskName, environment, lookbackMinutes);
  const tasksQuery = useServiceTasksQuery(resolvedServiceName, environment, lookbackMinutes, {
    limit: visibleTaskCount,
  });
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
  const controllableInstances = taskControlStates.filter(isControllableInstance);
  const commands = commandsQuery.data?.items ?? [];
  const taskCommands = commands.filter(
    (command) =>
      (
        command.kind === "pause_task" ||
        command.kind === "resume_task" ||
        command.kind === "discard_dead_letters" ||
        command.kind === "replay_dead_letters" ||
        command.kind === "run_task_once"
      ) &&
      command.args.task_name === resolvedTaskName,
  );
  const visibleTaskCommands = taskCommands.slice(0, visibleTaskCommandCount);
  const instanceOrder = new Map(taskControlStates.map((state, index) => [state.instance_id, index]));
  const boundInstanceIds = new Set(taskControlStates.map((state) => state.instance_id));
  const boundInstances = sortBoundInstances(instancesQuery.data?.items ?? [], boundInstanceIds, instanceOrder);
  const boundSessions = sortBoundSessions(sessionsQuery.data?.items ?? [], boundInstanceIds, instanceOrder);
  const taskStatus: "ok" | "warning" | "degraded" = summary ? deriveTaskStatus(summary) : "ok";
  const retrySummary = summary ? formatRetryPolicySummary(summary.retry_policy, isZh) : t("common.none");
  const taskHeroMetric = summary ? buildTaskHeroMetric(summary, isZh) : null;
  const canViewControls = canViewCommandControls(sessionQuery.data);
  const connectionNotice = buildTaskConnectionNotice(
    t,
    resolvedTaskName,
    streamStatus,
    taskQuery.dataUpdatedAt,
    Boolean(taskQuery.data),
    taskQuery.error,
    commandIssue,
  );
  const activityTabs: { label: string; value: TaskActivityTab }[] = [
    { label: t("taskDetail.recentEventsTitle"), value: "events" },
    { label: t("taskDetail.boundInstancesTitle"), value: "instances" },
    { label: t("taskDetail.recentCommandsTitle"), value: "commands" },
    { label: t("taskDetail.sessionsTitle"), value: "sessions" },
  ];

  function buildServiceViewHref(view: ServiceView) {
    return servicePath(resolvedServiceName, {
      environment,
      lookback_minutes: lookbackMinutes,
      tab: view === "overview" ? undefined : view,
    });
  }

  return (
    <div className="ref-console-page ref-detail-page signal-console-detail-page signal-console-task-detail">
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
              <span className="signal-console-kicker">
                {t("taskDetail.eyebrow", { environment: t(`environment.${environment}`) })}
              </span>
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

          <div className="ref-detail-header-actions">
            <div className="signal-console-detail-topbar-metric">
              <span>{taskHeroMetric?.label ?? t("taskDetail.failedDlq")}</span>
              <strong>{taskHeroMetric?.value ?? "--"}</strong>
              {taskHeroMetric?.note ? <p>{taskHeroMetric.note}</p> : null}
            </div>
          </div>
        </div>
      </section>

      {taskQuery.isPending ? <div className="loading-block hero-block">{t("taskDetail.loading")}</div> : null}
      {connectionNotice ? (
        <ConnectionStatusBanner
          body={connectionNotice.body}
          detail={connectionNotice.detail}
          title={connectionNotice.title}
          tone={connectionNotice.tone}
        />
      ) : null}

      {summary ? (
        <div className="ref-detail-layout">
          <aside className="ref-side-nav">
            <Link className="ref-side-nav-item" to={buildServiceViewHref("overview")}>
              <span>{isZh ? "概览" : "Overview"}</span>
            </Link>
            <Link className="ref-side-nav-item" to={buildServiceViewHref("instances")}>
              <span>{isZh ? "实例" : "Instances"}</span>
            </Link>
            <Link className="ref-side-nav-item is-active" to={buildServiceViewHref("tasks")}>
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
                  <h3>{isZh ? `任务列表 (${tasks.length})` : `Tasks (${tasks.length})`}</h3>
                </div>
                <div className="ref-task-browser-card">
                  {tasks.length ? (
                    tasks.map((task) => {
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
                    })
                  ) : (
                    <EmptyState
                      title={t("serviceTasksList.emptyTitle")}
                      body={t("serviceTasksList.emptyBody")}
                    />
                  )}
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
                      <VibeInlineButton
                        onClick={() => setVisibleTaskCount((count) => count + 100)}
                        variant="ghost"
                      >
                        {t("common.loadMore")}
                      </VibeInlineButton>
                    ) : null}
                  </div>
                ) : null}
              </section>

              <section className="ref-task-detail-pane">
                <div aria-hidden="true" className="ref-section-headline ref-section-headline-ghost">
                  <h3>{isZh ? "任务列表" : "Tasks"}</h3>
                </div>
                <VibeSummaryStrip
                  items={[
                    {
                      label: t("taskDetail.succeeded"),
                      tone: "success",
                      value: formatCount(summary.succeeded),
                    },
                    {
                      label: t("taskDetail.failedDlq"),
                      tone: summary.failed + summary.dead_lettered > 0 ? "danger" : "default",
                      value: formatCount(summary.failed + summary.dead_lettered),
                    },
                    { label: t("taskDetail.retried"), tone: "accent", value: formatCount(summary.retried) },
                    {
                      label: t("taskDetail.maxP95"),
                      value: formatDurationMs(summary.max_p95_duration_ms),
                    },
                  ]}
                />

                <Panel
                  className="ref-card-panel"
                  subtitle={summary.description ?? t("common.notAvailable")}
                  title={isZh ? "任务配置" : "Task configuration"}
                >
                  <div className="ref-info-grid">
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

                {canViewControls && controllableInstances.length > 0 ? (
                  <Panel
                    className="ref-card-panel"
                    subtitle={t("taskDetail.controlsSubtitle")}
                    title={t("taskDetail.controlsTitle")}
                  >
                    <TaskCommandControls
                      environment={environment}
                      onIssueChange={setCommandIssue}
                      serviceName={resolvedServiceName}
                      taskControl={payload.task_control}
                      taskName={resolvedTaskName}
                    />
                  </Panel>
                ) : null}

                <details className="ref-collapse-card">
                  <summary>
                    <strong>{isZh ? "任务活动" : "Task activity"}</strong>
                    <span>
                      {isZh
                        ? "整合最近事件、绑定实例、最近命令和关联会话，默认收起。"
                        : "Collapsed by default with recent events, bound instances, commands, and sessions."}
                    </span>
                  </summary>
                  <div className="ref-collapse-body ref-task-activity-stack">
                    <SegmentedControl
                      ariaLabel={isZh ? "任务活动视图" : "Task activity view"}
                      onChange={setActivityTab}
                      options={activityTabs}
                      value={activityTab}
                    />

                    <section className="ref-task-activity-panel">
                      <header className="ref-task-activity-header">
                        <div>
                          <h3>{getTaskActivityTitle(activityTab, t)}</h3>
                          <p>{getTaskActivitySubtitle(activityTab, t)}</p>
                        </div>
                      </header>

                      {activityTab === "events" ? (
                        payload.recent_events.length ? (
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
                        )
                      ) : null}

                      {activityTab === "instances" ? (
                        <ServiceInstancesList
                          environment={environment}
                          instances={boundInstances}
                          lookbackMinutes={lookbackMinutes}
                          serviceName={resolvedServiceName}
                        />
                      ) : null}

                      {activityTab === "commands" ? (
                        <>
                          <CommandFeed
                            commands={visibleTaskCommands}
                            emptyBody={t("taskDetail.noCommandsBody")}
                            emptyTitle={t("taskDetail.noCommandsTitle")}
                            environment={environment}
                            lookbackMinutes={lookbackMinutes}
                            serviceName={resolvedServiceName}
                          />
                          {taskCommands.length > 0 ? (
                            <div className="ref-inline-pagination">
                              <span>
                                {t("common.showingVisibleItems", {
                                  visible: visibleTaskCommands.length,
                                  total: taskCommands.length,
                                })}
                              </span>
                              {visibleTaskCommands.length < taskCommands.length ? (
                                <VibeInlineButton
                                  onClick={() => setVisibleTaskCommandCount((count) => count + 4)}
                                  variant="ghost"
                                >
                                  {t("common.loadMore")}
                                </VibeInlineButton>
                              ) : null}
                            </div>
                          ) : null}
                        </>
                      ) : null}

                      {activityTab === "sessions" ? (
                        <SessionList
                          emptyBody={t("taskDetail.noSessionsBody")}
                          emptyTitle={t("taskDetail.noSessionsTitle")}
                          sessions={boundSessions}
                        />
                      ) : null}
                    </section>
                  </div>
                </details>

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

function buildTaskConnectionNotice(
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

function getTaskActivityTitle(activityTab: TaskActivityTab, t: ReturnType<typeof useTranslation>["t"]) {
  if (activityTab === "instances") {
    return t("taskDetail.boundInstancesTitle");
  }
  if (activityTab === "commands") {
    return t("taskDetail.recentCommandsTitle");
  }
  if (activityTab === "sessions") {
    return t("taskDetail.sessionsTitle");
  }
  return t("taskDetail.recentEventsTitle");
}

function getTaskActivitySubtitle(activityTab: TaskActivityTab, t: ReturnType<typeof useTranslation>["t"]) {
  if (activityTab === "instances") {
    return t("taskDetail.boundInstancesSubtitle");
  }
  if (activityTab === "commands") {
    return t("taskDetail.recentCommandsSubtitle");
  }
  if (activityTab === "sessions") {
    return t("taskDetail.sessionsSubtitle");
  }
  return t("taskDetail.recentEventsSubtitle");
}

function buildTaskHeroMetric(summary: TaskDashboardSummary, isZh: boolean) {
  const issueCount = summary.failed + summary.dead_lettered;

  if (issueCount > 0) {
    return {
      label: isZh ? "失败 / 死信" : "Failed / DLQ",
      value: formatCount(issueCount),
      note: isZh ? "优先处理异常、死信和重试链路。" : "Prioritize failures, dead letters, and retry paths.",
    };
  }

  return {
    label: isZh ? "成功总数" : "Succeeded",
    value: formatCount(summary.succeeded),
    note: isZh ? "当前时间窗口内累计成功执行。" : "Successful runs accumulated in the current window.",
  };
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
