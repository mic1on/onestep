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
import { ServiceEventsFeed } from "../../features/services/components/ServiceEventsFeed";
import { ServiceInstancesList } from "../../features/services/components/ServiceInstancesList";
import { ServiceTasksList } from "../../features/services/components/ServiceTasksList";
import {
  useServiceDashboardQuery,
  useServiceInstancesQuery,
  useServiceTasksQuery,
} from "../../features/services/queries";
import type {
  Environment,
  InstanceSummary,
  ServiceDashboardResponse,
  TaskDashboardSummary,
} from "../../lib/api/types";
import { formatCompactJson, formatCount, formatDateTime, formatIdentifierPreview } from "../../lib/formatters";
import { parseEnvironment, parseLookback } from "../../lib/params";
import { servicePath } from "../../lib/routes";

const LOOKBACK_OPTIONS = [15, 60, 360, 1440];
const TAB_OPTIONS = ["overview", "tasks", "instances", "events"] as const;
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

  const dashboardQuery = useServiceDashboardQuery(serviceName, environment, lookbackMinutes);
  const tasksQuery = useServiceTasksQuery(serviceName, environment, lookbackMinutes);
  const instancesQuery = useServiceInstancesQuery(serviceName, environment);

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
          <div className="page-actions-stack">
            <SegmentedControl
              ariaLabel={t("serviceDetail.sectionAriaLabel")}
              onChange={(value) => updateParam("tab", value)}
              options={TAB_OPTIONS.map((option) => ({ label: t(`tabs.${option}`), value: option }))}
              value={currentTab}
            />
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
          <div className="hero-card">
            <div>
              <p className="eyebrow">{t("serviceDetail.currentLens")}</p>
              <h3>
                {dashboard.service.name} <span className="slash">/</span> {t(`environment.${dashboard.service.environment}`)}
              </h3>
              <p className="hero-copy">
                {dashboard.topology_consistent
                  ? t("serviceDetail.heroAligned")
                  : t("serviceDetail.heroDrift")}
              </p>
            </div>
            <div className="hero-tags">
              <StatusBadge
                value={dashboard.topology_consistent ? "consistent" : "drift"}
                label={dashboard.topology_consistent ? t("serviceDetail.topologyAligned") : t("serviceDetail.topologyMismatch")}
              />
              <span className="code-chip" title={dashboard.service.latest_topology_hash ?? t("common.topologyUnavailable")}>
                {dashboard.service.latest_topology_hash
                  ? formatIdentifierPreview(dashboard.service.latest_topology_hash)
                  : t("common.topologyUnavailable")}
              </span>
            </div>
          </div>

          <section className="stats-grid">
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
        tasksLoading: tasksQuery.isPending,
        instances,
        instancesLoading: instancesQuery.isPending,
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
  tasksLoading: boolean;
  instances: InstanceSummary[];
  instancesLoading: boolean;
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
  tasksLoading,
  instances,
  instancesLoading,
  serviceName,
  environment,
  lookbackMinutes,
  t,
}: RenderTabContentProps) {
  if (currentTab === "tasks") {
    return (
      <div className="two-column-grid">
        <Panel title={t("serviceDetail.allTasksTitle")} subtitle={t("serviceDetail.allTasksSubtitle")}>
          {tasksLoading ? <div className="loading-block">{t("serviceDetail.loadingTaskSummaries")}</div> : null}
          {!tasksLoading ? (
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
          {!instancesLoading ? (
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
        <Panel title={t("serviceDetail.topologyDriftTitle")} subtitle={t("serviceDetail.topologyDriftSubtitle")}>
          {dashboard ? <CodeBlock>{formatCompactJson(dashboard.topology_hashes)}</CodeBlock> : <div className="loading-block">{t("serviceDetail.loadingTopologyView")}</div>}
        </Panel>
      </div>
    );
  }

  return (
    <>
      <div className="two-column-grid">
        <Panel title={t("serviceDetail.tasksTitle")} subtitle={t("serviceDetail.tasksSubtitle")}>
          {tasksLoading ? <div className="loading-block">{t("serviceDetail.loadingTaskSummaries")}</div> : null}
          {!tasksLoading ? (
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
          {!instancesLoading ? (
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

        <Panel title={t("serviceDetail.topologyDriftTitle")} subtitle={t("serviceDetail.topologyDriftSubtitle")}>
          {dashboard ? <CodeBlock>{formatCompactJson(dashboard.topology_hashes)}</CodeBlock> : <div className="loading-block">{t("serviceDetail.loadingTopologyView")}</div>}
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
