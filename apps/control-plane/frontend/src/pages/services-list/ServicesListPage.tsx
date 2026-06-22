import { startTransition, useDeferredValue, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";

import { EmptyState } from "../../components/ui/EmptyState";
import { SignalConsoleHeader } from "../../components/ui/SignalConsoleHeader";
import { VibeActionGroup } from "../../components/ui/VibeActionGroup";
import { VibeDataRow, VibeDataTable } from "../../components/ui/VibeDataTable";
import { VibeField } from "../../components/ui/VibeField";
import { VibeSummaryStrip } from "../../components/ui/VibeSummary";
import { VibeFilterChip, VibeTag, VibeTagGroup } from "../../components/ui/VibeTag";
import { VibehubSelect } from "../../components/ui/VibehubSelect";
import { useServicesQuery } from "../../features/services/queries";
import { formatConnectorKind } from "../../features/tasks/components/TaskTopologySummary";
import type { ServiceSummary } from "../../lib/api/types";
import { formatDateTime, formatRelativeTime } from "../../lib/formatters";
import { servicePath } from "../../lib/routes";
import { getInactiveServiceDays } from "../../lib/runtime-config";

const DAY_IN_MS = 24 * 60 * 60 * 1000;
type Translate = (key: string, options?: Record<string, unknown>) => string;

export function ServicesListPage() {
  const { i18n, t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const isZh = Boolean(i18n.resolvedLanguage?.startsWith("zh"));
  const environmentParam = searchParams.get("environment");
  const selectedEnvironment =
    environmentParam === "all" || environmentParam === "dev" || environmentParam === "staging" || environmentParam === "prod"
      ? environmentParam
      : "all";
  const sourceKindParam = searchParams.get("source_kind") ?? undefined;
  const [search, setSearch] = useState(searchParams.get("q") ?? "");
  const deferredSearch = useDeferredValue(search);

  const { data, isPending, error } = useServicesQuery(
    selectedEnvironment === "all" ? undefined : selectedEnvironment,
    sourceKindParam,
    deferredSearch.trim() || undefined,
  );
  const inactiveServiceDays = getInactiveServiceDays();
  const inactiveThresholdMs = inactiveServiceDays * DAY_IN_MS;
  const filteredItems = data?.items ?? [];
  const { activeItems, inactiveItems } = partitionServices(filteredItems, inactiveThresholdMs);
  const sortedActiveItems = [...activeItems].sort(compareServicesForSurface);
  const sortedInactiveItems = [...inactiveItems].sort(compareServicesForSurface);
  const sortedItems = [...sortedActiveItems, ...sortedInactiveItems];
  const summary = data?.summary;
  const sourceKindCounts = data?.source_kind_counts ?? {};

  function updateSearchParam(key: string, value: string | undefined) {
    const next = new URLSearchParams(searchParams);
    if (!value) {
      next.delete(key);
    } else {
      next.set(key, value);
    }
    setSearchParams(next, { replace: true });
  }

  return (
    <div className="ref-console-page signal-console-services-page">
      <SignalConsoleHeader
        className="signal-console-services-hero"
        description={<p className="signal-console-hero-note">{t("servicesList.subtitle")}</p>}
        kicker={t("servicesList.eyebrow")}
        side={
          <>
            <div className="signal-console-hero-actions signal-console-services-hero-actions">
              <VibeActionGroup>
                <VibehubSelect
                  label={t("servicesList.filterScope")}
                  onChange={(nextValue) => updateSearchParam("environment", nextValue)}
                  options={[
                    { value: "all", label: t("environment.all") },
                    { value: "prod", label: t("environment.prod") },
                    { value: "staging", label: t("environment.staging") },
                    { value: "dev", label: t("environment.dev") },
                  ]}
                  value={selectedEnvironment}
                />

                <VibeField
                  className="ref-inline-control-search"
                  label={t("common.search")}
                  name="service-search"
                  onChange={(event) => {
                    const nextValue = event.target.value;
                    setSearch(nextValue);
                    startTransition(() => {
                      updateSearchParam("q", nextValue || undefined);
                    });
                  }}
                  placeholder={t("servicesList.searchPlaceholder")}
                  type="search"
                  value={search}
                />
              </VibeActionGroup>
            </div>

            <div className="signal-console-metric signal-console-services-metric">
              <span>{t("servicesList.visibleServices")}</span>
              <strong>{sortedItems.length}</strong>
              <p className="signal-console-hero-note">{t("common.afterFilters")}</p>
            </div>
          </>
        }
        title={t("servicesList.title")}
      />

      {/* Tag filter bar */}
      {Object.keys(sourceKindCounts).length > 0 ? (
        <nav className="ref-tag-bar">
          {Object.entries(sourceKindCounts)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([kind, count]) => {
              const isActive = sourceKindParam === kind;
              return (
                <VibeFilterChip
                  active={isActive}
                  count={count}
                  key={kind}
                  label={formatConnectorKind(kind, isZh)}
                  onClick={() =>
                    updateSearchParam("source_kind", isActive ? undefined : kind)
                  }
                />
              );
            })}
          {sourceKindParam ? (
            <VibeFilterChip
              clear
              label="clear filter"
              onClick={() => updateSearchParam("source_kind", undefined)}
            />
          ) : null}
        </nav>
      ) : null}

      <VibeSummaryStrip
        items={[
          { label: t("servicesList.summaryServices"), value: String(sortedItems.length) },
          {
            label: t("servicesList.summaryOnline"),
            tone: "success",
            value: `${summary?.online_instances ?? 0}/${summary?.total_instances ?? 0}`,
          },
          { label: t("servicesList.summaryReady"), tone: "accent", value: String(summary?.ready_services ?? 0) },
          {
            label: t("servicesList.summaryAttention"),
            tone: (summary?.attention_services ?? 0) > 0 ? "danger" : "default",
            value: String(summary?.attention_services ?? 0),
          },
        ]}
      />

      {error ? <EmptyState title={t("servicesList.loadErrorTitle")} body={String(error)} /> : null}
      {isPending ? <div className="loading-block">{t("servicesList.loading")}</div> : null}
      {!isPending && !error && sortedItems.length === 0 ? (
        <EmptyState title={t("servicesList.emptyTitle")} body={t("servicesList.emptyBody")} />
      ) : null}

      {!isPending && !error && sortedActiveItems.length > 0 ? (
        <ServicesTable isZh={isZh} items={sortedActiveItems} t={t} />
      ) : null}

      {!isPending && !error && sortedInactiveItems.length > 0 ? (
        <details className="ref-collapse-card">
          <summary>
            <strong>{t("servicesList.inactiveSectionTitle", { count: sortedInactiveItems.length })}</strong>
            <span>{t("servicesList.inactiveSectionDescription", { days: inactiveServiceDays })}</span>
          </summary>
          <div className="ref-collapse-body">
            <ServicesTable isZh={isZh} items={sortedInactiveItems} t={t} />
          </div>
        </details>
      ) : null}
    </div>
  );
}

function ServicesTable({ isZh, items, t }: { isZh: boolean; items: ServiceSummary[]; t: Translate }) {
  return (
    <VibeDataTable
      columns={[
        { key: "name", label: t("servicesList.tableHeaderName") },
        { key: "created", label: t("servicesList.tableHeaderCreated") },
        { key: "lastActive", label: t("servicesList.tableHeaderLastActive") },
        { key: "deployment", label: t("servicesList.tableHeaderDeployment") },
        { key: "instances", label: t("servicesList.tableHeaderInstances") },
      ]}
    >
      {items.map((service) => (
        <ServiceRow key={`${service.environment}:${service.name}`} isZh={isZh} service={service} t={t} />
      ))}
    </VibeDataTable>
  );
}

function ServiceRow({ isZh, service, t }: { isZh: boolean; service: ServiceSummary; t: Translate }) {
  return (
    <VibeDataRow>
      <div className="ref-service-cell">
        <Link
          className="ref-service-link"
          to={servicePath(service.name, {
            environment: service.environment,
            lookback_minutes: 60,
          })}
        >
          <strong>{service.name}</strong>
          <span>{t(`environment.${service.environment}`)}</span>
          {service.task_count > 0 || service.source_kinds.length > 0 ? (
            <VibeTagGroup>
              {service.task_count > 0 ? (
                <VibeTag variant="tasks">
                  {service.task_count} {service.task_count === 1 ? "task" : "tasks"}
                </VibeTag>
              ) : null}
              {service.source_kinds.map((kind) => (
                <VibeTag key={kind}>{formatConnectorKind(kind, isZh)}</VibeTag>
              ))}
            </VibeTagGroup>
          ) : null}
        </Link>
      </div>

      <div className="ref-meta-cell">
        <strong>{formatDateTime(service.created_at)}</strong>
      </div>

      <div className="ref-meta-cell">
        <strong title={formatDateTime(service.last_seen_at)}>
          {formatRelativeTime(service.last_seen_at)}
        </strong>
        <span>{getActivityHint(service, t)}</span>
      </div>

      <div className="ref-meta-cell">
        <strong>{service.latest_deployment_version}</strong>
      </div>

      <div className="ref-coverage-cell">
        <div className="ref-usage-bar">
          <span
            className={getCoverageBarClass(service)}
            style={{
              width: `${service.instance_count > 0 ? (service.online_instance_count / service.instance_count) * 100 : 0}%`,
            }}
          />
        </div>
        <strong>
          {t("servicesList.instanceLive")}: {service.online_instance_count}/{service.instance_count}
        </strong>
      </div>
    </VibeDataRow>
  );
}

function getServiceAttentionScore(service: ServiceSummary) {
  if (service.service_status === "offline") {
    return 2;
  }
  if (service.service_status === "attention") {
    return 1;
  }
  return 0;
}

function compareServicesForSurface(left: ServiceSummary, right: ServiceSummary) {
  const attentionDelta = getServiceAttentionScore(right) - getServiceAttentionScore(left);
  if (attentionDelta !== 0) {
    return attentionDelta;
  }

  const lastActivityDelta = compareDateDesc(left.last_seen_at, right.last_seen_at);
  if (lastActivityDelta !== 0) {
    return lastActivityDelta;
  }

  return left.name.localeCompare(right.name);
}

function getCoverageBarClass(service: ServiceSummary) {
  if (service.instance_count > 0 && service.online_instance_count === service.instance_count) {
    return "ref-usage-fill is-healthy";
  }
  if (service.online_instance_count > 0) {
    return "ref-usage-fill is-warning";
  }
  return "ref-usage-fill is-empty";
}

function getActivityHint(service: ServiceSummary, t: Translate) {
  if (service.last_seen_at === null) {
    return t("servicesList.noActivityHint");
  }
  return formatDateTime(service.last_seen_at);
}

function partitionServices(services: ServiceSummary[], inactiveThresholdMs: number) {
  return services.reduce(
    (groups, service) => {
      if (isServiceInactive(service, inactiveThresholdMs)) {
        groups.inactiveItems.push(service);
      } else {
        groups.activeItems.push(service);
      }

      return groups;
    },
    { activeItems: [] as ServiceSummary[], inactiveItems: [] as ServiceSummary[] },
  );
}

function isServiceInactive(service: ServiceSummary, inactiveThresholdMs: number) {
  if (service.last_seen_at === null) {
    return true;
  }

  return Date.now() - Date.parse(service.last_seen_at) > inactiveThresholdMs;
}

function compareDateDesc(left: string | null, right: string | null) {
  const leftValue = left ? Date.parse(left) : 0;
  const rightValue = right ? Date.parse(right) : 0;
  return rightValue - leftValue;
}
