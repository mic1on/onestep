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
import { ServiceTasksList } from "../../features/services/components/ServiceTasksList";
import { useServiceDashboardQuery, useServiceTasksQuery } from "../../features/services/queries";
import type { Environment, ServiceDashboardResponse, TaskDashboardSummary } from "../../lib/api/types";
import { formatCompactJson, formatCount, formatDateTime, formatIdentifierPreview } from "../../lib/formatters";
import { parseEnvironment, parseLookback } from "../../lib/params";
import { servicePath } from "../../lib/routes";

const LOOKBACK_OPTIONS = [15, 60, 360, 1440];
const TAB_OPTIONS = ["overview", "tasks"] as const;
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

  function updateParam(key: string, value: string) {
    const next = new URLSearchParams(searchParams);
    next.set(key, value);
    setSearchParams(next, { replace: true });
  }

  if (dashboardQuery.error) {
    return <EmptyState title={t("serviceDetail.loadErrorTitle")} body={String(dashboardQuery.error)} />;
  }

  const dashboard = dashboardQuery.data;
  const tasks = prioritizeTasksForOverview(tasksQuery.data?.items ?? []);

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
          <ServiceSummaryCards dashboard={dashboard} t={t} />
          <ServiceSectionNav
            currentTab={currentTab}
            dashboard={dashboard}
            environment={environment}
            lookbackMinutes={lookbackMinutes}
            serviceName={serviceName}
          />
        </>
      ) : null}

      {renderTabContent({
        currentTab,
        dashboard,
        tasks,
        tasksError: tasksQuery.error,
        tasksLoading: tasksQuery.isPending,
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
  tasks: TaskDashboardSummary[];
  tasksError: Error | null;
  tasksLoading: boolean;
  serviceName: string;
  environment: Environment;
  lookbackMinutes: number;
  t: TFunction;
};

function renderTabContent({
  currentTab,
  dashboard,
  tasks,
  tasksError,
  tasksLoading,
  serviceName,
  environment,
  lookbackMinutes,
  t,
}: RenderTabContentProps) {
  const showTopologyDriftPanel = Boolean(dashboard && dashboard.topology_hashes.length > 1);

  if (currentTab === "tasks") {
    return (
      <div className="page-stack">
        <Panel className="panel-flat" title={t("serviceDetail.allTasksTitle")} subtitle={t("serviceDetail.allTasksSubtitle")}>
          {tasksLoading ? <div className="loading-block">{t("serviceDetail.loadingTaskSummaries")}</div> : null}
          {!tasksLoading && tasksError ? (
            <EmptyState title={t("serviceDetail.taskLoadErrorTitle")} body={String(tasksError)} />
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

        <Panel className="panel-flat panel-flat-compact" title={t("serviceDetail.serviceContextTitle")} subtitle={t("serviceDetail.serviceContextSubtitle")}>
          {dashboard ? <ServiceContextPanel dashboard={dashboard} /> : <div className="loading-block">{t("serviceDetail.loadingContext")}</div>}
        </Panel>
      </div>
    );
  }

  return (
    <div className="page-stack">
      <Panel className="panel-flat" title={t("serviceDetail.tasksTitle")} subtitle={t("serviceDetail.tasksSubtitle")}>
        {tasksLoading ? <div className="loading-block">{t("serviceDetail.loadingTaskSummaries")}</div> : null}
        {!tasksLoading && tasksError ? (
          <EmptyState title={t("serviceDetail.taskLoadErrorTitle")} body={String(tasksError)} />
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

      <Panel className="panel-flat panel-flat-compact" title={t("serviceDetail.attentionTitle")} subtitle={t("serviceDetail.attentionSubtitle")}>
        {dashboard ? (
          <ServiceAttentionPanel
            dashboard={dashboard}
            environment={environment}
            lookbackMinutes={lookbackMinutes}
            serviceName={serviceName}
          />
        ) : (
          <div className="loading-block">{t("serviceDetail.loadingContext")}</div>
        )}
      </Panel>

      <Panel className="panel-flat panel-flat-compact" title={t("serviceDetail.serviceContextTitle")} subtitle={t("serviceDetail.serviceContextSubtitle")}>
        {dashboard ? <ServiceContextPanel dashboard={dashboard} /> : <div className="loading-block">{t("serviceDetail.loadingContext")}</div>}
      </Panel>

      <Panel className="panel-flat panel-flat-compact" title={t("serviceDetail.healthDistributionTitle")} subtitle={t("serviceDetail.healthDistributionSubtitle")}>
        {dashboard ? <ServiceHealthPanel dashboard={dashboard} /> : <div className="loading-block">{t("serviceDetail.loadingHealthSummary")}</div>}
      </Panel>

      {showTopologyDriftPanel && dashboard ? (
        <Panel
          className="panel-flat panel-flat-compact"
          title={t("serviceDetail.topologyDriftTitle")}
          subtitle={t("serviceDetail.topologyDriftSubtitle", { count: dashboard.topology_hashes.length })}
        >
          <CodeBlock>{formatCompactJson(dashboard.topology_hashes)}</CodeBlock>
        </Panel>
      ) : null}
    </div>
  );
}

function ServiceSummaryCards({
  dashboard,
  t,
}: {
  dashboard: ServiceDashboardResponse;
  t: TFunction;
}) {
  const rows = [
    {
      hint: t("common.totalHint", { count: dashboard.instance_connectivity.total }),
      label: t("serviceDetail.onlineInstances"),
      tone: "success" as const,
      value: String(dashboard.instance_connectivity.online),
    },
    {
      hint: t("common.neverReportedHint", { count: dashboard.instance_connectivity.never_reported }),
      label: t("serviceDetail.offlineInstances"),
      tone: dashboard.instance_connectivity.offline > 0 ? ("warning" as const) : ("default" as const),
      value: String(dashboard.instance_connectivity.offline),
    },
    {
      hint: t("common.trackedTasksHint", { count: dashboard.task_count }),
      label: t("serviceDetail.taskCount"),
      tone: "default" as const,
      value: String(dashboard.task_count),
    },
    {
      hint: t("common.trackedTasksHint", { count: dashboard.task_count }),
      label: t("serviceDetail.failingTasks"),
      tone: dashboard.failing_task_count > 0 ? ("danger" as const) : ("default" as const),
      value: String(dashboard.failing_task_count),
    },
  ];

  return (
    <section className="stats-grid stats-grid-quad stats-grid-compact">
      {rows.map((row) => (
        <StatCard
          hint={row.hint}
          key={row.label}
          label={row.label}
          size="compact"
          tone={row.tone}
          value={row.value}
        />
      ))}
    </section>
  );
}

function ServiceSectionNav({
  currentTab,
  dashboard,
  environment,
  lookbackMinutes,
  serviceName,
}: {
  currentTab: ServiceDetailTab;
  dashboard: ServiceDashboardResponse;
  environment: Environment;
  lookbackMinutes: number;
  serviceName: string;
}) {
  const { t } = useTranslation();
  const sections = TAB_OPTIONS.map((section) => ({
    section,
    metric: getServiceSectionMetric({
      dashboard,
      section,
      t,
    }),
  }));

  return (
    <nav className="service-section-nav" aria-label={t("serviceDetail.sectionAriaLabel")}>
      {sections.map(({ section, metric }) => (
        <Link
          key={section}
          className={currentTab === section ? "service-section-link active" : "service-section-link"}
          to={servicePath(serviceName, {
            environment,
            lookback_minutes: lookbackMinutes,
            tab: section === "overview" ? undefined : section,
          })}
        >
          <div className="service-section-link-copy">
            <span className="service-section-link-title">{t(`tabs.${section}`)}</span>
            <span className="service-section-link-label">{metric.label}</span>
          </div>
          <div className={`service-section-link-metric tone-${metric.tone}`}>
            <strong>{metric.value}</strong>
          </div>
        </Link>
      ))}
    </nav>
  );
}

function ServiceAttentionPanel({
  dashboard,
  environment,
  lookbackMinutes,
  serviceName,
}: {
  dashboard: ServiceDashboardResponse;
  environment: Environment;
  lookbackMinutes: number;
  serviceName: string;
}) {
  const { t } = useTranslation();
  const instanceHealthAttentionCount =
    dashboard.instance_statuses.degraded +
    dashboard.instance_statuses.error +
    dashboard.instance_statuses.starting +
    dashboard.instance_statuses.unknown;
  const items = [
    dashboard.failing_task_count > 0
      ? {
          label: t("serviceDetail.failingTasks"),
          section: "tasks" as const,
          tone: "danger",
          value: String(dashboard.failing_task_count),
        }
      : null,
    dashboard.instance_connectivity.offline > 0
      ? {
          label: t("serviceDetail.offlineInstances"),
          section: "overview" as const,
          tone: "warning",
          value: String(dashboard.instance_connectivity.offline),
        }
      : instanceHealthAttentionCount > 0
        ? {
            label: t("serviceDetail.instanceReviewTitle"),
            section: "overview" as const,
            tone: "warning",
            value: String(instanceHealthAttentionCount),
          }
        : null,
    dashboard.topology_hashes.length > 1
      ? {
          label: t("serviceDetail.topologyDriftTitle"),
          section: "overview" as const,
          tone: "danger",
          value: String(dashboard.topology_hashes.length),
        }
      : null,
  ].filter((item): item is NonNullable<typeof item> => item !== null);

  if (items.length === 0) {
    return <EmptyState title={t("serviceDetail.attentionEmptyTitle")} body={t("serviceDetail.attentionEmptyBody")} />;
  }

  return (
    <div className="service-attention-list">
      {items.map((item) => (
        <Link
          key={`${item.section}:${item.label}`}
          className="service-attention-row"
          to={servicePath(serviceName, {
            environment,
            lookback_minutes: lookbackMinutes,
            tab: item.section === "overview" ? undefined : item.section,
          })}
        >
          <div className="service-attention-copy">
            <strong>{item.label}</strong>
            <p>{t(`tabs.${item.section}`)}</p>
          </div>
          <div className={`service-attention-count tone-${item.tone}`}>
            <strong>{item.value}</strong>
          </div>
        </Link>
      ))}
    </div>
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

function getServiceSectionMetric({
  dashboard,
  section,
  t,
}: {
  dashboard: ServiceDashboardResponse;
  section: ServiceDetailTab;
  t: TFunction;
}) {
  if (section === "overview") {
    return {
      label: t("serviceDetail.topology"),
      tone: dashboard.topology_consistent ? "success" : "danger",
      value: dashboard.topology_consistent
        ? t("serviceDetail.topologyAlignedShort")
        : t("serviceDetail.topologyDriftingShort"),
    };
  }

  return dashboard.failing_task_count > 0
    ? {
        label: t("serviceDetail.failingTasks"),
        tone: "danger",
        value: String(dashboard.failing_task_count),
      }
    : {
        label: t("serviceDetail.taskCount"),
        tone: "default",
        value: String(dashboard.task_count),
      };
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
