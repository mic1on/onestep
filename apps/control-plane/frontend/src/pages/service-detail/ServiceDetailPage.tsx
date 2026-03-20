import { Link, useParams, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { CodeBlock } from "../../components/ui/CodeBlock";
import { EmptyState } from "../../components/ui/EmptyState";
import { PageHeader } from "../../components/ui/PageHeader";
import { Panel } from "../../components/ui/Panel";
import { SegmentedControl } from "../../components/ui/SegmentedControl";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { CommandFeed } from "../../features/commands/components/CommandFeed";
import { ServiceCommandFanout } from "../../features/commands/components/ServiceCommandFanout";
import { useServiceCommandsQuery, useServiceSessionsQuery } from "../../features/commands/queries";
import { ServiceTasksList } from "../../features/services/components/ServiceTasksList";
import { useServiceDashboardQuery, useServiceInstancesQuery, useServiceTasksQuery } from "../../features/services/queries";
import type { AgentSessionSummary, Environment, TaskDashboardSummary } from "../../lib/api/types";
import { formatDateTime, formatDurationMs, formatRelativeTime } from "../../lib/formatters";
import { parseEnvironment, parseLookback } from "../../lib/params";
import { servicePath } from "../../lib/routes";

const LOOKBACK_OPTIONS = [15, 60, 360, 1440];
const DEFAULT_SERVICE_TAB = "tasks" as const;
const TAB_OPTIONS = [DEFAULT_SERVICE_TAB, "overview"] as const;
type ServiceDetailTab = (typeof TAB_OPTIONS)[number];

export function ServiceDetailPage() {
  const { i18n, t } = useTranslation();
  const { serviceName } = useParams<{ serviceName: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const isZh = i18n.resolvedLanguage?.startsWith("zh");

  if (!serviceName) {
    return <EmptyState title={t("serviceDetail.missingTitle")} body={t("serviceDetail.missingBody")} />;
  }

  const environment = parseEnvironment(searchParams);
  const lookbackMinutes = parseLookback(searchParams, 60);
  const currentTab = parseTab(searchParams.get("tab"));

  const dashboardQuery = useServiceDashboardQuery(serviceName, environment, lookbackMinutes);
  const tasksQuery = useServiceTasksQuery(serviceName, environment, lookbackMinutes);
  const commandsQuery = useServiceCommandsQuery(serviceName, environment, {
    enabled: currentTab === "overview",
  });
  const sessionsQuery = useServiceSessionsQuery(serviceName, environment, {
    enabled: currentTab === "overview",
  });
  const instancesQuery = useServiceInstancesQuery(serviceName, environment, {
    enabled: currentTab === "overview",
  });

  function updateParam(key: string, value: string) {
    const next = new URLSearchParams(searchParams);
    if (value) {
      next.set(key, value);
    } else {
      next.delete(key);
    }
    setSearchParams(next, { replace: true });
  }

  if (dashboardQuery.error) {
    return <EmptyState title={t("serviceDetail.loadErrorTitle")} body={String(dashboardQuery.error)} />;
  }

  const dashboard = dashboardQuery.data;
  const tasks = prioritizeTasksForOverview(tasksQuery.data?.items ?? []);
  const commands = commandsQuery.data?.items ?? [];
  const sessions = sessionsQuery.data?.items ?? [];
  const instances = instancesQuery.data?.items ?? [];
  const capabilitySummary = summarizeCapabilities(sessions);

  return (
    <div className="page-stack service-console-page">
      <PageHeader
        title={serviceName}
        titleMeta={
          dashboard ? (
            <>
              <span className="service-title-chip service-title-chip-environment">
                {t(`environment.${environment}`)}
              </span>
              <span
                className={dashboard.topology_consistent ? "service-title-chip" : "service-title-chip is-danger"}
                title={dashboard.service.latest_topology_hash ?? t("common.topologyUnavailable")}
              >
                {dashboard.topology_consistent ? t("serviceDetail.topologyAligned") : t("serviceDetail.topologyMismatch")}
              </span>
              <span className="service-title-chip service-title-chip-meta">
                {isZh ? "部署版本" : "Deployment"} {dashboard.service.latest_deployment_version}
              </span>
              <span className="service-title-chip service-title-chip-meta">
                {isZh ? "最近同步" : "Last sync"} {formatRelativeTime(dashboard.service.latest_sync_at)}
              </span>
            </>
          ) : null
        }
        actions={
          <div className="page-actions-stack service-header-actions">
            <Link
              className="button-link"
              to={`/services?environment=${environment}`}
            >
              {isZh ? "返回服务目录" : "Back to services"}
            </Link>
            <SegmentedControl
              ariaLabel={t("serviceDetail.lookbackAriaLabel")}
              onChange={(value) => updateParam("lookback_minutes", String(value))}
              options={LOOKBACK_OPTIONS.map((value) => ({
                label: value >= 1440 ? "1d" : `${value}m`,
                value: String(value),
              }))}
              value={String(lookbackMinutes)}
            />
          </div>
        }
      />

      {dashboardQuery.isPending ? <div className="loading-block hero-block">{t("serviceDetail.loading")}</div> : null}

      {dashboard ? (
        <>
          <nav className="service-command-tabs" aria-label={t("serviceDetail.sectionAriaLabel")}>
            {TAB_OPTIONS.map((section) => (
              <Link
                className={currentTab === section ? "service-command-tab active" : "service-command-tab"}
                key={section}
                to={servicePath(serviceName, {
                  environment,
                  lookback_minutes: lookbackMinutes,
                  tab: toServiceTabParam(section),
                })}
              >
                <div className="service-command-tab-copy">
                  <strong>{t(`tabs.${section}`)}</strong>
                  <span>
                    {section === "tasks"
                      ? isZh
                        ? "任务与失败焦点"
                        : "tasks and failures"
                      : isZh
                        ? "控制与诊断"
                        : "controls and diagnostics"}
                  </span>
                </div>
                {section === "tasks" ? (
                  <div className="service-command-tab-stats" aria-label={isZh ? "任务摘要" : "Task summary"}>
                    <div className="service-command-tab-stat">
                      <span>{isZh ? "总数" : "Total"}</span>
                      <strong>{dashboard.task_count}</strong>
                    </div>
                    <div className="service-command-tab-stat">
                      <span>{isZh ? "正常" : "Normal"}</span>
                      <strong>{Math.max(dashboard.task_count - dashboard.failing_task_count, 0)}</strong>
                    </div>
                    <div className="service-command-tab-stat is-danger">
                      <span>{isZh ? "异常" : "Abnormal"}</span>
                      <strong>{dashboard.failing_task_count}</strong>
                    </div>
                  </div>
                ) : (
                  <span className="service-command-tab-metric">
                    {dashboard.command_overview.statuses.total}
                  </span>
                )}
              </Link>
            ))}
          </nav>

          {currentTab === "overview" ? (
            <div className="page-stack">
              <div className="service-console-grid">
                <div className="service-console-main">
                  <Panel
                    actions={
                      <Link
                        className="button-link"
                        to={servicePath(serviceName, {
                          environment,
                          lookback_minutes: lookbackMinutes,
                          tab: toServiceTabParam("tasks"),
                        })}
                      >
                        {t("common.viewAllTasks")}
                      </Link>
                    }
                    title={isZh ? "活跃任务列表" : "Active tasks"}
                    subtitle={t("serviceDetail.tasksSubtitle")}
                  >
                    {tasksQuery.isPending ? <div className="loading-block">{t("serviceDetail.loadingTaskSummaries")}</div> : null}
                    {!tasksQuery.isPending && tasksQuery.error ? (
                      <EmptyState title={t("serviceDetail.taskLoadErrorTitle")} body={String(tasksQuery.error)} />
                    ) : null}
                    {!tasksQuery.isPending && !tasksQuery.error ? (
                      <ServiceTasksList
                        environment={environment}
                        limit={6}
                        lookbackMinutes={lookbackMinutes}
                        serviceName={serviceName}
                        tasks={tasks}
                      />
                    ) : null}
                  </Panel>

                  <Panel
                    title={isZh ? "最近控制命令" : "Recent control commands"}
                    subtitle={t("serviceDetail.recentCommandsSubtitle")}
                  >
                    {commandsQuery.isPending ? <div className="loading-block">{t("serviceDetail.loadingCommandFeed")}</div> : null}
                    {!commandsQuery.isPending && commandsQuery.error ? (
                      <EmptyState title={t("serviceDetail.recentCommandsTitle")} body={String(commandsQuery.error)} />
                    ) : null}
                    {!commandsQuery.isPending && !commandsQuery.error ? (
                      <CommandFeed
                        commands={commands.slice(0, 4)}
                        emptyBody={t("serviceDetail.noCommandsBody")}
                        emptyTitle={t("serviceDetail.noCommandsTitle")}
                        environment={environment}
                        lookbackMinutes={lookbackMinutes}
                        serviceName={serviceName}
                      />
                    ) : null}
                  </Panel>
                </div>

                <div className="service-console-side">
                  <Panel
                    title={t("serviceDetail.serviceControlsTitle")}
                    subtitle={t("serviceDetail.serviceControlsSubtitle")}
                  >
                    {instancesQuery.isPending ? (
                      <div className="loading-block">{t("serviceDetail.loadingInstanceSnapshots")}</div>
                    ) : instancesQuery.error ? (
                      <EmptyState title={t("serviceDetail.instanceLoadErrorTitle")} body={String(instancesQuery.error)} />
                    ) : (
                      <ServiceCommandFanout
                        environment={environment}
                        instances={instances}
                        serviceName={serviceName}
                      />
                    )}
                  </Panel>

                  <Panel
                    title={isZh ? "能力与会话" : "Capabilities and sessions"}
                    subtitle={t("serviceDetail.sessionsSubtitle")}
                  >
                    <div className="capability-summary-grid">
                      <article className="capability-summary-card">
                        <span>{isZh ? "活跃会话" : "Active sessions"}</span>
                        <strong>{dashboard.command_overview.active_session_count}</strong>
                      </article>
                      <article className="capability-summary-card">
                        <span>{isZh ? "已知能力" : "Known capabilities"}</span>
                        <strong>{capabilitySummary.length}</strong>
                      </article>
                    </div>
                    {capabilitySummary.length ? (
                      <div className="capability-chip-grid">
                        {capabilitySummary.slice(0, 8).map((item) => (
                          <span className="code-chip capability-chip" key={item.name}>
                            {item.name} · {item.count}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <EmptyState title={t("serviceDetail.noSessionsTitle")} body={t("serviceDetail.noSessionsBody")} />
                    )}
                  </Panel>

                  <Panel
                    title={t("serviceDetail.recentEventsTitle")}
                    subtitle={t("serviceDetail.recentEventsSubtitle", { lookbackMinutes })}
                  >
                    {dashboard.recent_events.length ? (
                      <div className="service-event-list">
                        {dashboard.recent_events.slice(0, 5).map((event) => (
                          <article className="service-event-row" key={event.event_id}>
                            <div>
                              <strong>{event.task_name}</strong>
                              <p>{formatDateTime(event.occurred_at)}</p>
                            </div>
                            <div className="service-event-metrics">
                              <StatusBadge value={mapEventKindToBadge(event.kind)} />
                              <span>{formatDurationMs(event.duration_ms)}</span>
                            </div>
                          </article>
                        ))}
                      </div>
                    ) : (
                      <EmptyState title={t("serviceDetail.quietServiceTitle")} body={t("serviceDetail.quietServiceBody")} />
                    )}
                  </Panel>
                </div>
              </div>

              {dashboard.topology_hashes.length > 1 ? (
                <Panel
                  title={t("serviceDetail.topologyDriftTitle")}
                  subtitle={t("serviceDetail.topologyDriftSubtitle", { count: dashboard.topology_hashes.length })}
                >
                  <CodeBlock>{JSON.stringify(dashboard.topology_hashes, null, 2)}</CodeBlock>
                </Panel>
              ) : null}
            </div>
          ) : (
            <div className="page-stack">
              <Panel
                actions={
                  <Link
                    className="button-link"
                    to={servicePath(serviceName, {
                      environment,
                      lookback_minutes: lookbackMinutes,
                      tab: toServiceTabParam("overview"),
                    })}
                  >
                    {isZh ? "查看概览" : "Open overview"}
                  </Link>
                }
                title={t("serviceDetail.allTasksTitle")}
                subtitle={t("serviceDetail.allTasksSubtitle")}
              >
                {tasksQuery.isPending ? <div className="loading-block">{t("serviceDetail.loadingTaskSummaries")}</div> : null}
                {!tasksQuery.isPending && tasksQuery.error ? (
                  <EmptyState title={t("serviceDetail.taskLoadErrorTitle")} body={String(tasksQuery.error)} />
                ) : null}
                {!tasksQuery.isPending && !tasksQuery.error ? (
                  <ServiceTasksList
                    environment={environment}
                    lookbackMinutes={lookbackMinutes}
                    serviceName={serviceName}
                    tasks={tasks}
                  />
                ) : null}
              </Panel>
            </div>
          )}
        </>
      ) : null}
    </div>
  );
}

function parseTab(value: string | null): ServiceDetailTab {
  if (value && TAB_OPTIONS.includes(value as ServiceDetailTab)) {
    return value as ServiceDetailTab;
  }
  return DEFAULT_SERVICE_TAB;
}

function toServiceTabParam(value: ServiceDetailTab) {
  return value === DEFAULT_SERVICE_TAB ? undefined : value;
}

function prioritizeTasksForOverview(tasks: TaskDashboardSummary[]) {
  return [...tasks].sort((left, right) => {
    const priorityDelta = getTaskPriority(right) - getTaskPriority(left);
    if (priorityDelta !== 0) {
      return priorityDelta;
    }

    const lastEventDelta = compareDateDesc(left.last_event_at, right.last_event_at);
    if (lastEventDelta !== 0) {
      return lastEventDelta;
    }

    return left.task_name.localeCompare(right.task_name);
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

function compareDateDesc(left: string | null, right: string | null) {
  const leftValue = left ? Date.parse(left) : 0;
  const rightValue = right ? Date.parse(right) : 0;
  return rightValue - leftValue;
}

function summarizeCapabilities(sessions: AgentSessionSummary[]) {
  const counts = new Map<string, number>();
  for (const session of sessions) {
    for (const capability of session.accepted_capabilities) {
      counts.set(capability, (counts.get(capability) ?? 0) + 1);
    }
  }

  return [...counts.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((left, right) => {
      if (right.count !== left.count) {
        return right.count - left.count;
      }
      return left.name.localeCompare(right.name);
    });
}

function mapEventKindToBadge(kind: string) {
  if (kind === "failed" || kind === "dead_lettered") {
    return "failed" as const;
  }
  if (kind === "retried" || kind === "cancelled") {
    return "degraded" as const;
  }
  return "succeeded" as const;
}
