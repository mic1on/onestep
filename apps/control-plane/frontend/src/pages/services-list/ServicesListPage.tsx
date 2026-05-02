import { startTransition, useDeferredValue, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";

import { EmptyState } from "../../components/ui/EmptyState";
import { PageHeader } from "../../components/ui/PageHeader";
import { useServicesQuery } from "../../features/services/queries";
import type { ServiceSummary } from "../../lib/api/types";
import { formatDateTime, formatRelativeTime } from "../../lib/formatters";
import { servicePath } from "../../lib/routes";

export function ServicesListPage() {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const environmentParam = searchParams.get("environment");
  const selectedEnvironment =
    environmentParam === "all" || environmentParam === "dev" || environmentParam === "staging" || environmentParam === "prod"
      ? environmentParam
      : "all";
  const [search, setSearch] = useState(searchParams.get("q") ?? "");
  const deferredSearch = useDeferredValue(search);

  const { data, isPending, error } = useServicesQuery(
    selectedEnvironment === "all" ? undefined : selectedEnvironment,
  );

  const query = deferredSearch.trim().toLowerCase();
  const filteredItems = !query
    ? (data?.items ?? [])
    : (data?.items ?? []).filter((service) => service.name.toLowerCase().includes(query));
  const sortedItems = [...filteredItems].sort(compareServicesForSurface);
  const totalInstances = sortedItems.reduce((sum, service) => sum + service.instance_count, 0);
  const onlineInstances = sortedItems.reduce((sum, service) => sum + service.online_instance_count, 0);
  const attentionCount = sortedItems.filter(serviceNeedsAttention).length;

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
    <div className="ref-console-page">
      <PageHeader
        title={t("servicesList.title")}
        titleMeta={
          <span className="service-title-chip">{sortedItems.length}</span>
        }
        subtitle={
          <p>
            {t("servicesList.subtitle")}
          </p>
        }
      />

      <div className="ref-page-actions">
        <label className="ref-inline-control ref-inline-control-select">
          <span>{t("servicesList.filterScope")}</span>
          <select
            onChange={(event) => updateSearchParam("environment", event.target.value)}
            value={selectedEnvironment}
          >
            <option value="all">{t("environment.all")}</option>
            <option value="prod">{t("environment.prod")}</option>
            <option value="staging">{t("environment.staging")}</option>
            <option value="dev">{t("environment.dev")}</option>
          </select>
        </label>

        <label className="ref-inline-control ref-inline-control-search">
          <span>{t("common.search")}</span>
          <input
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
        </label>
      </div>

      <section className="ref-summary-strip">
        <SummaryChip
          label={t("servicesList.summaryServices")}
          tone="default"
          value={String(sortedItems.length)}
        />
        <SummaryChip
          label={t("servicesList.summaryOnline")}
          tone="success"
          value={`${onlineInstances}/${totalInstances || 0}`}
        />
        <SummaryChip
          label={t("servicesList.summaryReady")}
          tone="accent"
          value={String(sortedItems.filter(isServiceFullyOnline).length)}
        />
        <SummaryChip
          label={t("servicesList.summaryAttention")}
          tone={attentionCount > 0 ? "danger" : "default"}
          value={String(attentionCount)}
        />
      </section>

      {error ? <EmptyState title={t("servicesList.loadErrorTitle")} body={String(error)} /> : null}
      {isPending ? <div className="loading-block">{t("servicesList.loading")}</div> : null}
      {!isPending && !error && sortedItems.length === 0 ? (
        <EmptyState title={t("servicesList.emptyTitle")} body={t("servicesList.emptyBody")} />
      ) : null}

      {!isPending && !error && sortedItems.length > 0 ? (
        <section className="ref-table-card">
          <div className="ref-table-head">
            <span>{t("servicesList.tableHeaderName")}</span>
            <span>{t("servicesList.tableHeaderCreated")}</span>
            <span>{t("servicesList.tableHeaderLastActive")}</span>
            <span>{t("servicesList.tableHeaderDeployment")}</span>
            <span>{t("servicesList.tableHeaderInstances")}</span>
          </div>

          <div className="ref-table-body">
            {sortedItems.map((service) => (
              <article className="ref-table-row" key={`${service.environment}:${service.name}`}>
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
              </article>
            ))}
          </div>
        </section>
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

function serviceNeedsAttention(service: ServiceSummary) {
  return getServiceAttentionScore(service) > 0;
}

function isServiceFullyOnline(service: ServiceSummary) {
  return service.instance_count > 0 && service.online_instance_count > 0 && !isServiceStale(service.latest_sync_at);
}

function getServiceAttentionScore(service: ServiceSummary) {
  let score = 0;

  if (service.instance_count === 0 || service.online_instance_count === 0) {
    score += 5;
  }

  if (service.latest_sync_at === null) {
    score += 4;
  } else if (isServiceStale(service.latest_sync_at)) {
    score += 2;
  }

  if (!service.latest_topology_hash) {
    score += 1;
  }

  return score;
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
  if (service.instance_count > 0 && service.online_instance_count > 0 && service.online_instance_count === service.instance_count && !isServiceStale(service.latest_sync_at)) {
    return "ref-usage-fill is-healthy";
  }
  if (service.online_instance_count > 0) {
    return "ref-usage-fill is-warning";
  }
  return "ref-usage-fill is-empty";
}

function getActivityHint(service: ServiceSummary, t: (key: string) => string) {
  if (service.last_seen_at === null) {
    return t("servicesList.noActivityHint");
  }
  return formatDateTime(service.last_seen_at);
}

function isServiceStale(value: string | null) {
  if (!value) {
    return true;
  }
  return Date.now() - Date.parse(value) > 15 * 60 * 1000;
}

function compareDateDesc(left: string | null, right: string | null) {
  const leftValue = left ? Date.parse(left) : 0;
  const rightValue = right ? Date.parse(right) : 0;
  return rightValue - leftValue;
}
