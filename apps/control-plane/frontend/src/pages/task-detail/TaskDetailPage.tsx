import { useTranslation } from "react-i18next";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { EmptyState } from "../../components/ui/EmptyState";
import { PageHeader } from "../../components/ui/PageHeader";
import { Panel } from "../../components/ui/Panel";
import { StatCard } from "../../components/ui/StatCard";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { TaskEventFailureDetails } from "../../components/ui/TaskEventFailureDetails";
import { CommandFeed } from "../../features/commands/components/CommandFeed";
import { SessionList } from "../../features/commands/components/SessionList";
import { useServiceCommandsQuery, useServiceSessionsQuery } from "../../features/commands/queries";
import { ServiceInstancesList } from "../../features/services/components/ServiceInstancesList";
import { useServiceInstancesQuery } from "../../features/services/queries";
import { TaskCommandControls } from "../../features/tasks/components/TaskCommandControls";
import { TaskMetricHistoryChart } from "../../features/tasks/components/TaskMetricHistoryChart";
import { useTaskDetailQuery } from "../../features/tasks/queries";
import {
  formatCompactJson,
  formatCount,
  formatDateTime,
  formatDurationMs,
  formatIdentifierPreview,
} from "../../lib/formatters";
import { createSearch, parseEnvironment, parseLookback } from "../../lib/params";
import { servicePath, taskPath } from "../../lib/routes";

const TAB_OPTIONS = ["control", "events", "details"] as const;
const READ_ONLY_TAB_OPTIONS = ["events", "details"] as const;
type TaskDetailTab = (typeof TAB_OPTIONS)[number];

export function TaskDetailPage() {
  const { i18n, t } = useTranslation();
  const { serviceName, taskName } = useParams<{ serviceName: string; taskName: string }>();
  const [searchParams] = useSearchParams();
  const isZh = i18n.resolvedLanguage?.startsWith("zh");

  if (!serviceName || !taskName) {
    return <EmptyState title={t("taskDetail.missingTitle")} body={t("taskDetail.missingBody")} />;
  }

  const environment = parseEnvironment(searchParams);
  const lookbackMinutes = parseLookback(searchParams, 60);
  const requestedTab = parseTab(searchParams.get("tab"));
  const query = useTaskDetailQuery(serviceName, taskName, environment, lookbackMinutes);
  const hasTaskDetailError = Boolean(query.error);
  const payload = query.data;
  const summary = payload?.summary;
  const taskControlStates = payload?.task_control.instances ?? [];
  const controllableInstances = taskControlStates.filter(isControllableInstance);
  const hasControllableInstances = controllableInstances.length > 0;
  const visibleTabs = hasControllableInstances ? TAB_OPTIONS : READ_ONLY_TAB_OPTIONS;
  const currentTab = resolveCurrentTab(requestedTab, hasControllableInstances);
  const commandsQuery = useServiceCommandsQuery(serviceName, environment, {
    enabled: !hasTaskDetailError && currentTab === "control" && hasControllableInstances,
  });
  const instancesQuery = useServiceInstancesQuery(serviceName, environment, {
    enabled: !hasTaskDetailError && currentTab === "details",
  });
  const sessionsQuery = useServiceSessionsQuery(serviceName, environment, {
    enabled: !hasTaskDetailError && currentTab === "details",
  });
  const instanceOrder = new Map(taskControlStates.map((state, index) => [state.instance_id, index]));
  const boundInstanceIds = new Set(taskControlStates.map((state) => state.instance_id));
  const taskCommands =
    commandsQuery.data?.items.filter(
      (command) =>
        (command.kind === "pause_task" || command.kind === "resume_task") &&
        command.args.task_name === taskName,
    ) ?? [];
  const boundInstances = (instancesQuery.data?.items ?? [])
    .filter((instance) => boundInstanceIds.has(instance.instance_id))
    .sort((left, right) => {
      const leftIndex = instanceOrder.get(left.instance_id) ?? Number.MAX_SAFE_INTEGER;
      const rightIndex = instanceOrder.get(right.instance_id) ?? Number.MAX_SAFE_INTEGER;
      return leftIndex - rightIndex;
    });
  const boundSessions = (sessionsQuery.data?.items ?? [])
    .filter((session) => boundInstanceIds.has(session.instance_id))
    .sort((left, right) => {
      const leftIndex = instanceOrder.get(left.instance_id) ?? Number.MAX_SAFE_INTEGER;
      const rightIndex = instanceOrder.get(right.instance_id) ?? Number.MAX_SAFE_INTEGER;
      return leftIndex - rightIndex;
    });

  if (query.error) {
    return <EmptyState title={t("taskDetail.loadErrorTitle")} body={String(query.error)} />;
  }

  return (
    <div className="page-stack">
      <PageHeader
        actions={
          <div className="page-actions-stack">
            <Link
              className="button-link"
              to={servicePath(serviceName, {
                environment,
                lookback_minutes: lookbackMinutes,
              })}
            >
              {isZh ? "返回任务列表" : "Back to task list"}
            </Link>
          </div>
        }
        title={taskName}
      />

      {query.isPending ? <div className="loading-block hero-block">{t("taskDetail.loading")}</div> : null}

      {summary ? (
        <>
          <section className="stats-grid stats-grid-quad stats-grid-compact task-detail-summary-grid">
            <StatCard
              label={t("taskDetail.succeeded")}
              size="compact"
              tone="success"
              value={formatCount(summary.succeeded)}
            />
            <StatCard
              label={t("taskDetail.failedDlq")}
              size="compact"
              tone={summary.failed + summary.dead_lettered > 0 ? "danger" : "default"}
              value={formatCount(summary.failed + summary.dead_lettered)}
            />
            <StatCard
              hint={t("taskDetail.retriedHint")}
              label={t("taskDetail.retried")}
              size="compact"
              value={formatCount(summary.retried)}
            />
            <StatCard
              label={t("taskDetail.maxP95")}
              size="compact"
              value={formatDurationMs(summary.max_p95_duration_ms)}
            />
          </section>

          <nav className="service-section-nav" aria-label={t("taskDetail.sectionAriaLabel")}>
            {visibleTabs.map((section) => (
              <Link
                key={section}
                className={currentTab === section ? "service-section-link active" : "service-section-link"}
                to={taskPath(serviceName, taskName, {
                  environment,
                  lookback_minutes: lookbackMinutes,
                  tab: section === "control" ? undefined : section,
                })}
              >
                <div className="service-section-link-copy">
                  <span className="service-section-link-title">{t(`tabs.${section}`)}</span>
                  <span className="service-section-link-label">
                    {section === "control"
                      ? t("taskDetail.controlsTitle")
                      : section === "events"
                        ? t("taskDetail.recentEventsTitle")
                        : t("taskDetail.boundInstancesTitle")}
                  </span>
                </div>
                <div className="service-section-link-metric tone-default">
                  <strong>
                    {section === "control"
                      ? controllableInstances.length
                      : section === "events"
                        ? payload.recent_events.length
                        : taskControlStates.length}
                  </strong>
                </div>
              </Link>
            ))}
          </nav>

          {currentTab === "control" ? (
            <div className="page-stack">
              <Panel className="panel-flat" title={t("taskDetail.controlsTitle")} subtitle={t("taskDetail.controlsSubtitle")}>
                <TaskCommandControls
                  environment={environment}
                  serviceName={serviceName}
                  taskName={taskName}
                  taskControl={payload.task_control}
                />
              </Panel>

              <Panel className="panel-flat panel-flat-compact" title={t("taskDetail.recentCommandsTitle")} subtitle={t("taskDetail.recentCommandsSubtitle")}>
                {commandsQuery.error ? (
                  <EmptyState title={t("taskDetail.recentCommandsTitle")} body={String(commandsQuery.error)} />
                ) : (
                  <CommandFeed
                    commands={taskCommands.slice(0, 5)}
                    emptyBody={t("taskDetail.noCommandsBody")}
                    emptyTitle={t("taskDetail.noCommandsTitle")}
                    environment={environment}
                    lookbackMinutes={lookbackMinutes}
                    serviceName={serviceName}
                  />
                )}
              </Panel>
            </div>
          ) : null}

          {currentTab === "events" ? (
            <div className="page-stack">
              <Panel className="panel-flat panel-flat-compact" title={t("taskDetail.eventProfileTitle")} subtitle={t("taskDetail.eventProfileSubtitle")}>
                <div className="badge-row">
                  <StatusBadge
                    value={summary.failed > 0 ? "drift" : "consistent"}
                    label={t("taskDetail.failedBadge", { count: summary.event_counts.failed })}
                  />
                  <StatusBadge value="starting" label={t("taskDetail.retriedBadge", { count: summary.event_counts.retried })} />
                  <StatusBadge
                    value="consistent"
                    label={t("taskDetail.succeededBadge", { count: summary.event_counts.succeeded })}
                  />
                </div>
                <dl className="definition-grid">
                  <div>
                    <dt>{t("taskDetail.cancelled")}</dt>
                    <dd>{summary.event_counts.cancelled}</dd>
                  </div>
                  <div>
                    <dt>{t("taskDetail.succeededEvents")}</dt>
                    <dd>{summary.event_counts.succeeded}</dd>
                  </div>
                  <div>
                    <dt>{t("taskDetail.windowCount")}</dt>
                    <dd>{summary.metric_window_count}</dd>
                  </div>
                  <div>
                    <dt>{t("taskDetail.weightedAvg")}</dt>
                    <dd>{formatDurationMs(summary.weighted_avg_duration_ms)}</dd>
                  </div>
                </dl>
              </Panel>

              <div className="two-column-grid">
                <Panel className="panel-flat" title={t("taskDetail.recentEventsTitle")} subtitle={t("taskDetail.recentEventsSubtitle")}>
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

                <Panel className="panel-flat" title={t("taskDetail.recentWindowsTitle")} subtitle={t("taskDetail.recentWindowsSubtitle")}>
                  {payload.recent_metric_windows.length ? (
                    <div className="task-history-stack">
                      <TaskMetricHistoryChart windows={payload.recent_metric_windows} />
                      <div className="stack-list task-window-list">
                        {payload.recent_metric_windows.map((window) => (
                          <article className="event-row" key={window.window_id}>
                            <div>
                              <strong>{window.window_id}</strong>
                              <p>
                                {t("taskDetail.windowRange", {
                                  start: formatDateTime(window.window_started_at),
                                  end: formatDateTime(window.window_ended_at),
                                })}
                              </p>
                            </div>
                            <div className="row-metrics">
                              <span>{t("metrics.ok", { value: formatCount(window.succeeded) })}</span>
                              <span>{t("metrics.fail", { value: formatCount(window.failed + window.dead_lettered) })}</span>
                              <span>{t("metrics.p95", { value: formatDurationMs(window.p95_duration_ms) })}</span>
                            </div>
                          </article>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <EmptyState title={t("taskDetail.noWindowsTitle")} body={t("taskDetail.noWindowsBody")} />
                  )}
                </Panel>
              </div>
            </div>
          ) : null}

          {currentTab === "details" ? (
            <div className="page-stack">
              <Panel className="panel-flat" title={t("taskDetail.configurationTitle")} subtitle={t("taskDetail.configurationSubtitle")}>
                <dl className="definition-grid">
                  <div>
                    <dt>{t("taskDetail.description")}</dt>
                    <dd>{summary.description ?? t("common.notAvailable")}</dd>
                  </div>
                  <div>
                    <dt>{t("taskDetail.source")}</dt>
                    <dd>{summary.source_name ?? t("common.notAvailable")}</dd>
                  </div>
                  <div>
                    <dt>{t("taskDetail.sourceKind")}</dt>
                    <dd>{summary.source_kind ?? t("common.notAvailable")}</dd>
                  </div>
                  <div>
                    <dt>{t("taskDetail.concurrency")}</dt>
                    <dd>{summary.concurrency ?? t("common.notAvailable")}</dd>
                  </div>
                  <div>
                    <dt>{t("taskDetail.timeout")}</dt>
                    <dd>{summary.timeout_s ? `${summary.timeout_s}s` : t("common.none")}</dd>
                  </div>
                  <div>
                    <dt>{t("taskDetail.topologyHash")}</dt>
                    <dd title={summary.topology_hash ?? t("common.notAvailable")}>
                      {formatIdentifierPreview(summary.topology_hash)}
                    </dd>
                  </div>
                  <div>
                    <dt>{t("taskDetail.lastEvent")}</dt>
                    <dd>{formatDateTime(summary.last_event_at)}</dd>
                  </div>
                </dl>
                <div className="json-stack">
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
              </Panel>

              <Panel className="panel-flat" title={t("taskDetail.boundInstancesTitle")} subtitle={t("taskDetail.boundInstancesSubtitle")}>
                {instancesQuery.isPending ? <div className="loading-block">{t("taskDetail.loadingBoundInstances")}</div> : null}
                {!instancesQuery.isPending && instancesQuery.error ? (
                  <EmptyState title={t("taskDetail.boundInstancesTitle")} body={String(instancesQuery.error)} />
                ) : null}
                {!instancesQuery.isPending && !instancesQuery.error ? (
                  <ServiceInstancesList
                    environment={environment}
                    instances={boundInstances}
                    lookbackMinutes={lookbackMinutes}
                    serviceName={serviceName}
                  />
                ) : null}
              </Panel>

              <Panel className="panel-flat panel-flat-compact" title={t("taskDetail.sessionsTitle")} subtitle={t("taskDetail.sessionsSubtitle")}>
                {sessionsQuery.isPending ? <div className="loading-block">{t("taskDetail.loadingSessions")}</div> : null}
                {!sessionsQuery.isPending && sessionsQuery.error ? (
                  <EmptyState title={t("taskDetail.sessionsTitle")} body={String(sessionsQuery.error)} />
                ) : null}
                {!sessionsQuery.isPending && !sessionsQuery.error ? (
                  <SessionList
                    emptyBody={t("taskDetail.noSessionsBody")}
                    emptyTitle={t("taskDetail.noSessionsTitle")}
                    sessions={boundSessions}
                  />
                ) : null}
              </Panel>
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

function parseTab(value: string | null): TaskDetailTab | null {
  if (value && TAB_OPTIONS.includes(value as TaskDetailTab)) {
    return value as TaskDetailTab;
  }
  return null;
}

function resolveCurrentTab(requestedTab: TaskDetailTab | null, hasControllableInstances: boolean): TaskDetailTab {
  if (requestedTab === "events" || requestedTab === "details") {
    return requestedTab;
  }
  if (requestedTab === "control" && hasControllableInstances) {
    return requestedTab;
  }
  return hasControllableInstances ? "control" : "events";
}

function isControllableInstance(state: { connectivity: string; supported_commands: string[] }) {
  return state.connectivity === "online" && state.supported_commands.length > 0;
}
