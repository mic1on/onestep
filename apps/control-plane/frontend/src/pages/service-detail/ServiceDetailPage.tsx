import { Link, useParams, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";

import { CodeBlock } from "../../components/ui/CodeBlock";
import { EmptyState } from "../../components/ui/EmptyState";
import { PageHeader } from "../../components/ui/PageHeader";
import { Panel } from "../../components/ui/Panel";
import { SegmentedControl } from "../../components/ui/SegmentedControl";
import { StatCard } from "../../components/ui/StatCard";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { CommandFeed } from "../../features/commands/components/CommandFeed";
import { ServiceCommandFanout } from "../../features/commands/components/ServiceCommandFanout";
import { SessionList } from "../../features/commands/components/SessionList";
import {
  useServiceCommandsQuery,
  useServiceSessionsQuery,
} from "../../features/commands/queries";
import { ServiceEventsFeed } from "../../features/services/components/ServiceEventsFeed";
import { ServiceInstancesList } from "../../features/services/components/ServiceInstancesList";
import { ServiceTasksList } from "../../features/services/components/ServiceTasksList";
import {
  useServiceDashboardQuery,
  useServiceInstancesQuery,
  useServiceTasksQuery,
} from "../../features/services/queries";
import type {
  AgentCommandSummary,
  AgentSessionSummary,
  Environment,
  InstanceSummary,
  ServiceDashboardResponse,
  TaskDashboardSummary,
} from "../../lib/api/types";
import { formatCompactJson, formatCount, formatDateTime, formatIdentifierPreview } from "../../lib/formatters";
import { parseEnvironment, parseLookback } from "../../lib/params";
import { servicePath } from "../../lib/routes";

const LOOKBACK_OPTIONS = [15, 60, 360, 1440];
const TAB_OPTIONS = ["overview", "tasks", "instances", "events", "commands"] as const;
type ServiceDetailTab = (typeof TAB_OPTIONS)[number];

export function ServiceDetailPage() {
  const { t } = useTranslation();
  const { serviceName } = useParams<{ serviceName: string }>();
  const [searchParams, setSearchParams] = useSearchParams();

  if (!serviceName) {
    return <EmptyState title={t("serviceDetail.missingTitle")} body={t("serviceDetail.missingBody")} />;
  }

  const environment = parseEnvironment(searchParams);
  const lookbackMinutes = parseLookback(searchParams, 60);
  const currentTab = parseTab(searchParams.get("tab"));
  const shouldFetchTasks = currentTab === "overview" || currentTab === "tasks";
  const shouldFetchInstances =
    currentTab === "overview" || currentTab === "instances" || currentTab === "commands";
  const shouldFetchCommands = currentTab === "overview" || currentTab === "commands";

  const dashboardQuery = useServiceDashboardQuery(serviceName, environment, lookbackMinutes);
  const tasksQuery = useServiceTasksQuery(serviceName, environment, lookbackMinutes, {
    enabled: shouldFetchTasks,
  });
  const instancesQuery = useServiceInstancesQuery(serviceName, environment, {
    enabled: shouldFetchInstances,
  });
  const commandsQuery = useServiceCommandsQuery(serviceName, environment, {
    enabled: shouldFetchCommands,
  });
  const sessionsQuery = useServiceSessionsQuery(serviceName, environment, {
    enabled: shouldFetchCommands,
  });

  function updateParam(key: string, value: string) {
    const next = new URLSearchParams(searchParams);
    next.set(key, value);
    setSearchParams(next, { replace: true });
  }

  if (dashboardQuery.error) {
    return <EmptyState title={t("serviceDetail.loadErrorTitle")} body={String(dashboardQuery.error)} />;
  }

  const dashboard = dashboardQuery.data;
  const tasks = tasksQuery.data?.items ?? [];
  const instances = instancesQuery.data?.items ?? [];
  const commands = commandsQuery.data?.items ?? [];
  const sessions = sessionsQuery.data?.items ?? [];
  const commandFailureCount = dashboard
    ? dashboard.command_overview.statuses.failed +
      dashboard.command_overview.statuses.expired +
      dashboard.command_overview.statuses.rejected +
      dashboard.command_overview.statuses.timeout +
      dashboard.command_overview.statuses.cancelled
    : 0;

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow={t("serviceDetail.eyebrow", { environment: t(`environment.${environment}`) })}
        title={serviceName}
        subtitle={
          dashboard
            ? t("serviceDetail.subtitleLoaded", {
                version: dashboard.service.latest_deployment_version,
                time: formatDateTime(dashboard.service.latest_sync_at),
              })
            : t("serviceDetail.subtitleLoading")
        }
        actions={
          <div className="page-actions-stack service-header-actions">
            {dashboard ? (
              <div className="service-header-meta">
                <StatusBadge
                  value={dashboard.topology_consistent ? "consistent" : "drift"}
                  label={
                    dashboard.topology_consistent
                      ? t("serviceDetail.topologyAligned")
                      : t("serviceDetail.topologyMismatch")
                  }
                />
                <span
                  className="code-chip"
                  title={dashboard.service.latest_topology_hash ?? t("common.topologyUnavailable")}
                >
                  {dashboard.service.latest_topology_hash
                    ? formatIdentifierPreview(dashboard.service.latest_topology_hash)
                    : t("common.topologyUnavailable")}
                </span>
              </div>
            ) : null}
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
          <section className="stats-grid stats-grid-quad">
            <StatCard
              label={t("serviceDetail.onlineInstances")}
              value={String(dashboard.instance_connectivity.online)}
              hint={t("common.totalHint", { count: dashboard.instance_connectivity.total })}
              tone="success"
            />
            <StatCard
              label={t("serviceDetail.offlineInstances")}
              value={String(dashboard.instance_connectivity.offline)}
              hint={t("common.neverReportedHint", { count: dashboard.instance_connectivity.never_reported })}
              tone="warning"
            />
            <StatCard
              label={t("serviceDetail.failingTasks")}
              value={String(dashboard.failing_task_count)}
              hint={t("common.trackedTasksHint", { count: dashboard.task_count })}
              tone={dashboard.failing_task_count > 0 ? "danger" : "default"}
            />
            <StatCard
              label={t("serviceDetail.activeSessions")}
              value={String(dashboard.command_overview.active_session_count)}
              hint={t("common.totalHint", { count: dashboard.service.instance_count })}
              tone={dashboard.command_overview.active_session_count > 0 ? "success" : "default"}
            />
            <StatCard
              label={t("serviceDetail.commandsInFlight")}
              value={String(dashboard.command_overview.statuses.in_flight)}
              hint={t("common.totalHint", { count: dashboard.command_overview.statuses.total })}
              tone={dashboard.command_overview.statuses.in_flight > 0 ? "warning" : "default"}
            />
            <StatCard
              label={t("serviceDetail.commandFailures")}
              value={String(commandFailureCount)}
              hint={t("common.lastCommandInline", { time: formatDateTime(dashboard.command_overview.last_command_at) })}
              tone={commandFailureCount > 0 ? "danger" : "default"}
            />
            <StatCard
              label={t("serviceDetail.topologyHashes")}
              value={String(dashboard.topology_hashes.length)}
              hint={
                dashboard.topology_hashes.length === 0
                  ? t("common.noneReported")
                  : dashboard.topology_consistent
                    ? t("common.allReportingInstancesAligned")
                    : t("common.distinctHashesVisible", { count: dashboard.topology_hashes.length })
              }
              tone={dashboard.topology_consistent ? "success" : "danger"}
            />
          </section>
        </>
      ) : null}

      {renderTabContent({
        currentTab,
        dashboard,
        dashboardLoading: dashboardQuery.isPending,
        tasks,
        tasksError: tasksQuery.error,
        tasksLoading: tasksQuery.isPending,
        instances,
        instancesError: instancesQuery.error,
        instancesLoading: instancesQuery.isPending,
        commands,
        commandsError: commandsQuery.error,
        commandsLoading: commandsQuery.isPending,
        sessions,
        sessionsError: sessionsQuery.error,
        sessionsLoading: sessionsQuery.isPending,
        serviceName,
        environment,
        lookbackMinutes,
        t,
      })}
    </div>
  );
}

function parseTab(value: string | null): ServiceDetailTab {
  if (value && TAB_OPTIONS.includes(value as ServiceDetailTab)) {
    return value as ServiceDetailTab;
  }
  return "overview";
}

type RenderTabContentProps = {
  currentTab: ServiceDetailTab;
  dashboard: ServiceDashboardResponse | undefined;
  dashboardLoading: boolean;
  tasks: TaskDashboardSummary[];
  tasksError: Error | null;
  tasksLoading: boolean;
  instances: InstanceSummary[];
  instancesError: Error | null;
  instancesLoading: boolean;
  commands: AgentCommandSummary[];
  commandsError: Error | null;
  commandsLoading: boolean;
  sessions: AgentSessionSummary[];
  sessionsError: Error | null;
  sessionsLoading: boolean;
  serviceName: string;
  environment: Environment;
  lookbackMinutes: number;
  t: TFunction;
};

function renderTabContent({
  currentTab,
  dashboard,
  dashboardLoading,
  tasks,
  tasksError,
  tasksLoading,
  instances,
  instancesError,
  instancesLoading,
  commands,
  commandsError,
  commandsLoading,
  sessions,
  sessionsError,
  sessionsLoading,
  serviceName,
  environment,
  lookbackMinutes,
  t,
}: RenderTabContentProps) {
  const showTopologyDriftPanel = Boolean(dashboard && dashboard.topology_hashes.length > 1);

  if (currentTab === "tasks") {
    return (
      <div className="two-column-grid">
        <Panel title={t("serviceDetail.allTasksTitle")} subtitle={t("serviceDetail.allTasksSubtitle")}>
          {tasksLoading ? <div className="loading-block">{t("serviceDetail.loadingTaskSummaries")}</div> : null}
          {!tasksLoading && tasksError ? (
            <EmptyState
              title={t("serviceDetail.taskLoadErrorTitle")}
              body={String(tasksError)}
            />
          ) : null}
          {!tasksLoading && !tasksError ? (
            <ServiceTasksList
              environment={environment}
              lookbackMinutes={lookbackMinutes}
              serviceName={serviceName}
              tasks={tasks}
            />
          ) : null}
        </Panel>
        <Panel title={t("serviceDetail.serviceContextTitle")} subtitle={t("serviceDetail.serviceContextSubtitle")}>
          {dashboard ? <ServiceContextPanel dashboard={dashboard} /> : <div className="loading-block">{t("serviceDetail.loadingContext")}</div>}
        </Panel>
      </div>
    );
  }

  if (currentTab === "instances") {
    return (
      <div className="two-column-grid">
        <Panel title={t("serviceDetail.allInstancesTitle")} subtitle={t("serviceDetail.allInstancesSubtitle")}>
          {instancesLoading ? <div className="loading-block">{t("serviceDetail.loadingInstanceSnapshots")}</div> : null}
          {!instancesLoading && instancesError ? (
            <EmptyState
              title={t("serviceDetail.instanceLoadErrorTitle")}
              body={String(instancesError)}
            />
          ) : null}
          {!instancesLoading && !instancesError ? (
            <ServiceInstancesList
              environment={environment}
              instances={instances}
              lookbackMinutes={lookbackMinutes}
              serviceName={serviceName}
            />
          ) : null}
        </Panel>
        <Panel title={t("serviceDetail.healthDistributionTitle")} subtitle={t("serviceDetail.healthDistributionSubtitle")}>
          {dashboard ? <ServiceHealthPanel dashboard={dashboard} /> : <div className="loading-block">{t("serviceDetail.loadingHealthSummary")}</div>}
        </Panel>
      </div>
    );
  }

  if (currentTab === "events") {
    return (
      showTopologyDriftPanel && dashboard ? (
        <div className="two-column-grid">
          <Panel title={t("serviceDetail.recentEventsTitle")} subtitle={t("serviceDetail.recentEventsSubtitle", { lookbackMinutes })}>
            {dashboardLoading ? <div className="loading-block">{t("serviceDetail.loadingEventStream")}</div> : null}
            {!dashboardLoading && dashboard ? (
              <ServiceEventsFeed
                emptyBody={t("serviceDetail.quietServiceBody")}
                emptyTitle={t("serviceDetail.quietServiceTitle")}
                events={dashboard.recent_events}
              />
            ) : null}
          </Panel>
          <Panel
            title={t("serviceDetail.topologyDriftTitle")}
            subtitle={t("serviceDetail.topologyDriftSubtitle", { count: dashboard.topology_hashes.length })}
          >
            <CodeBlock>{formatCompactJson(dashboard.topology_hashes)}</CodeBlock>
          </Panel>
        </div>
      ) : (
        <Panel title={t("serviceDetail.recentEventsTitle")} subtitle={t("serviceDetail.recentEventsSubtitle", { lookbackMinutes })}>
          {dashboardLoading ? <div className="loading-block">{t("serviceDetail.loadingEventStream")}</div> : null}
          {!dashboardLoading && dashboard ? (
            <ServiceEventsFeed
              emptyBody={t("serviceDetail.quietServiceBody")}
              emptyTitle={t("serviceDetail.quietServiceTitle")}
              events={dashboard.recent_events}
            />
          ) : null}
        </Panel>
      )
    );
  }

  if (currentTab === "commands") {
    return (
      <div className="page-stack">
        <Panel title={t("serviceDetail.serviceControlsTitle")} subtitle={t("serviceDetail.serviceControlsSubtitle")}>
          {instancesLoading ? <div className="loading-block">{t("serviceDetail.loadingInstanceSnapshots")}</div> : null}
          {!instancesLoading && instancesError ? (
            <EmptyState
              title={t("serviceDetail.instanceLoadErrorTitle")}
              body={String(instancesError)}
            />
          ) : null}
          {!instancesLoading && !instancesError ? (
            <ServiceCommandFanout
              environment={environment}
              instances={instances}
              serviceName={serviceName}
            />
          ) : null}
        </Panel>

        <div className="two-column-grid">
          <Panel title={t("serviceDetail.recentCommandsTitle")} subtitle={t("serviceDetail.recentCommandsSubtitle")}>
            {commandsLoading ? <div className="loading-block">{t("serviceDetail.loadingCommandFeed")}</div> : null}
            {!commandsLoading && commandsError ? (
              <EmptyState
                title={t("serviceDetail.loadErrorTitle")}
                body={String(commandsError)}
              />
            ) : null}
            {!commandsLoading && !commandsError ? (
              <CommandFeed
                commands={commands}
                emptyBody={t("serviceDetail.noCommandsBody")}
                emptyTitle={t("serviceDetail.noCommandsTitle")}
                environment={environment}
                lookbackMinutes={lookbackMinutes}
                serviceName={serviceName}
              />
            ) : null}
          </Panel>
          <Panel title={t("serviceDetail.sessionsTitle")} subtitle={t("serviceDetail.sessionsSubtitle")}>
            {sessionsLoading ? <div className="loading-block">{t("serviceDetail.loadingSessionFeed")}</div> : null}
            {!sessionsLoading && sessionsError ? (
              <EmptyState
                title={t("serviceDetail.loadErrorTitle")}
                body={String(sessionsError)}
              />
            ) : null}
            {!sessionsLoading && !sessionsError ? (
              <SessionList
                emptyBody={t("serviceDetail.noSessionsBody")}
                emptyTitle={t("serviceDetail.noSessionsTitle")}
                sessions={sessions}
              />
            ) : null}
          </Panel>
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="two-column-grid">
        <Panel title={t("serviceDetail.tasksTitle")} subtitle={t("serviceDetail.tasksSubtitle")}>
          {tasksLoading ? <div className="loading-block">{t("serviceDetail.loadingTaskSummaries")}</div> : null}
          {!tasksLoading && tasksError ? (
            <EmptyState
              title={t("serviceDetail.taskLoadErrorTitle")}
              body={String(tasksError)}
            />
          ) : null}
          {!tasksLoading && !tasksError ? (
            <ServiceTasksList
              environment={environment}
              limit={6}
              lookbackMinutes={lookbackMinutes}
              serviceName={serviceName}
              tasks={tasks}
            />
          ) : null}
          <div className="panel-footer">
            <Link
              className="button-link"
              to={servicePath(serviceName, {
                environment,
                lookback_minutes: lookbackMinutes,
                tab: "tasks",
              })}
            >
              {t("common.viewAllTasks")}
            </Link>
          </div>
        </Panel>

        <Panel title={t("serviceDetail.instancesTitle")} subtitle={t("serviceDetail.instancesSubtitle")}>
          {instancesLoading ? <div className="loading-block">{t("serviceDetail.loadingInstanceSnapshots")}</div> : null}
          {!instancesLoading && instancesError ? (
            <EmptyState
              title={t("serviceDetail.instanceLoadErrorTitle")}
              body={String(instancesError)}
            />
          ) : null}
          {!instancesLoading && !instancesError ? (
            <ServiceInstancesList
              environment={environment}
              instances={instances}
              limit={6}
              lookbackMinutes={lookbackMinutes}
              serviceName={serviceName}
            />
          ) : null}
          <div className="panel-footer">
            <Link
              className="button-link"
              to={servicePath(serviceName, {
                environment,
                lookback_minutes: lookbackMinutes,
                tab: "instances",
              })}
            >
              {t("common.viewAllInstances")}
            </Link>
          </div>
        </Panel>
      </div>

      {showTopologyDriftPanel && dashboard ? (
        <div className="two-column-grid">
          <Panel title={t("serviceDetail.recentEventsTitle")} subtitle={t("serviceDetail.recentEventsSubtitle", { lookbackMinutes })}>
            {dashboardLoading ? <div className="loading-block">{t("serviceDetail.loadingEventStream")}</div> : null}
            {!dashboardLoading && dashboard ? (
              <ServiceEventsFeed
                emptyBody={t("serviceDetail.quietServiceBody")}
                emptyTitle={t("serviceDetail.quietServiceTitle")}
                events={dashboard.recent_events}
              />
            ) : null}
            <div className="panel-footer">
              <Link
                className="button-link"
                to={servicePath(serviceName, {
                  environment,
                  lookback_minutes: lookbackMinutes,
                  tab: "events",
                })}
              >
                {t("common.viewEventFocus")}
              </Link>
            </div>
          </Panel>

          <Panel
            title={t("serviceDetail.topologyDriftTitle")}
            subtitle={t("serviceDetail.topologyDriftSubtitle", { count: dashboard.topology_hashes.length })}
          >
            <CodeBlock>{formatCompactJson(dashboard.topology_hashes)}</CodeBlock>
          </Panel>
        </div>
      ) : (
        <Panel title={t("serviceDetail.recentEventsTitle")} subtitle={t("serviceDetail.recentEventsSubtitle", { lookbackMinutes })}>
          {dashboardLoading ? <div className="loading-block">{t("serviceDetail.loadingEventStream")}</div> : null}
          {!dashboardLoading && dashboard ? (
            <ServiceEventsFeed
              emptyBody={t("serviceDetail.quietServiceBody")}
              emptyTitle={t("serviceDetail.quietServiceTitle")}
              events={dashboard.recent_events}
            />
          ) : null}
          <div className="panel-footer">
            <Link
              className="button-link"
              to={servicePath(serviceName, {
                environment,
                lookback_minutes: lookbackMinutes,
                tab: "events",
              })}
            >
              {t("common.viewEventFocus")}
            </Link>
          </div>
        </Panel>
      )}

      <div className="two-column-grid">
        <Panel title={t("serviceDetail.recentCommandsTitle")} subtitle={t("serviceDetail.recentCommandsSubtitle")}>
          {commandsLoading ? <div className="loading-block">{t("serviceDetail.loadingCommandFeed")}</div> : null}
          {!commandsLoading && commandsError ? (
            <EmptyState
              title={t("serviceDetail.loadErrorTitle")}
              body={String(commandsError)}
            />
          ) : null}
          {!commandsLoading && !commandsError ? (
            <CommandFeed
              commands={commands.slice(0, 6)}
              emptyBody={t("serviceDetail.noCommandsBody")}
              emptyTitle={t("serviceDetail.noCommandsTitle")}
              environment={environment}
              lookbackMinutes={lookbackMinutes}
              serviceName={serviceName}
            />
          ) : null}
          <div className="panel-footer">
            <Link
              className="button-link"
              to={servicePath(serviceName, {
                environment,
                lookback_minutes: lookbackMinutes,
                tab: "commands",
              })}
            >
              {t("common.viewCommandFocus")}
            </Link>
          </div>
        </Panel>

        <Panel title={t("serviceDetail.sessionsTitle")} subtitle={t("serviceDetail.sessionsSubtitle")}>
          {sessionsLoading ? <div className="loading-block">{t("serviceDetail.loadingSessionFeed")}</div> : null}
          {!sessionsLoading && sessionsError ? (
            <EmptyState
              title={t("serviceDetail.loadErrorTitle")}
              body={String(sessionsError)}
            />
          ) : null}
          {!sessionsLoading && !sessionsError ? (
            <SessionList
              emptyBody={t("serviceDetail.noSessionsBody")}
              emptyTitle={t("serviceDetail.noSessionsTitle")}
              sessions={sessions.slice(0, 6)}
            />
          ) : null}
          <div className="panel-footer">
            <Link
              className="button-link"
              to={servicePath(serviceName, {
                environment,
                lookback_minutes: lookbackMinutes,
                tab: "commands",
              })}
            >
              {t("common.viewCommandFocus")}
            </Link>
          </div>
        </Panel>
      </div>
    </>
  );
}

function ServiceContextPanel({
  dashboard,
}: {
  dashboard: ServiceDashboardResponse;
}) {
  const { t } = useTranslation();

  return (
    <dl className="definition-grid">
      <div>
        <dt>{t("serviceDetail.latestDeployment")}</dt>
        <dd>{dashboard.service.latest_deployment_version}</dd>
      </div>
      <div>
        <dt>{t("serviceDetail.latestTopologyHash")}</dt>
        <dd title={dashboard.service.latest_topology_hash ?? t("common.notAvailable")}>
          {formatIdentifierPreview(dashboard.service.latest_topology_hash)}
        </dd>
      </div>
      <div>
        <dt>{t("serviceDetail.taskCount")}</dt>
        <dd>{dashboard.task_count}</dd>
      </div>
      <div>
        <dt>{t("serviceDetail.failingTasks")}</dt>
        <dd>{formatCount(dashboard.failing_task_count)}</dd>
      </div>
      <div>
        <dt>{t("serviceDetail.onlineInstancesLabel")}</dt>
        <dd>{dashboard.instance_connectivity.online}</dd>
      </div>
      <div>
        <dt>{t("serviceDetail.offlineInstancesLabel")}</dt>
        <dd>{dashboard.instance_connectivity.offline}</dd>
      </div>
    </dl>
  );
}

function ServiceHealthPanel({
  dashboard,
}: {
  dashboard: ServiceDashboardResponse;
}) {
  const { t } = useTranslation();

  return (
    <dl className="definition-grid">
      <div>
        <dt>{t("serviceDetail.ok")}</dt>
        <dd>{dashboard.instance_statuses.ok}</dd>
      </div>
      <div>
        <dt>{t("serviceDetail.degraded")}</dt>
        <dd>{dashboard.instance_statuses.degraded}</dd>
      </div>
      <div>
        <dt>{t("serviceDetail.error")}</dt>
        <dd>{dashboard.instance_statuses.error}</dd>
      </div>
      <div>
        <dt>{t("serviceDetail.starting")}</dt>
        <dd>{dashboard.instance_statuses.starting}</dd>
      </div>
      <div>
        <dt>{t("serviceDetail.unknown")}</dt>
        <dd>{dashboard.instance_statuses.unknown}</dd>
      </div>
      <div>
        <dt>{t("serviceDetail.topology")}</dt>
        <dd>{dashboard.topology_consistent ? t("serviceDetail.topologyAlignedShort") : t("serviceDetail.topologyDriftingShort")}</dd>
      </div>
    </dl>
  );
}
