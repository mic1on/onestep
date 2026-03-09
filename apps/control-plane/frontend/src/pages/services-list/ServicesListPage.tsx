import { startTransition, useDeferredValue, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";

import { EmptyState } from "../../components/ui/EmptyState";
import { PageHeader } from "../../components/ui/PageHeader";
import { Panel } from "../../components/ui/Panel";
import { SegmentedControl } from "../../components/ui/SegmentedControl";
import { StatCard } from "../../components/ui/StatCard";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { useServicesQuery } from "../../features/services/queries";
import { formatDateTime, formatIdentifierPreview, formatRelativeTime } from "../../lib/formatters";
import { servicePath } from "../../lib/routes";
import type { Environment } from "../../lib/api/types";

type EnvironmentFilter = Environment | "all";

const ENVIRONMENT_OPTIONS: EnvironmentFilter[] = ["prod", "staging", "dev", "all"];

export function ServicesListPage() {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const environmentParam = searchParams.get("environment");
  const selectedEnvironment =
    environmentParam === "all" || environmentParam === "dev" || environmentParam === "staging" || environmentParam === "prod"
      ? environmentParam
      : "prod";
  const [search, setSearch] = useState(searchParams.get("q") ?? "");
  const deferredSearch = useDeferredValue(search);

  const { data, isPending, error, refetch } = useServicesQuery(
    selectedEnvironment === "all" ? undefined : selectedEnvironment,
  );

  const query = deferredSearch.trim().toLowerCase();
  const filteredItems = !query
    ? (data?.items ?? [])
    : (data?.items ?? []).filter((service) => service.name.toLowerCase().includes(query));

  const onlineHeavyCount = filteredItems.filter(
    (service) => service.instance_count > 0 && service.online_instance_count === service.instance_count,
  ).length;

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
    <div className="page-stack">
      <PageHeader
        eyebrow={t("servicesList.eyebrow")}
        title={t("servicesList.title")}
        subtitle={t("servicesList.subtitle")}
        actions={
          <button className="button-secondary" onClick={() => void refetch()} type="button">
            {t("common.refresh")}
          </button>
        }
      />

      <section className="stats-grid">
        <StatCard
          label={t("servicesList.visibleServices")}
          value={String(filteredItems.length)}
          hint={t("common.afterFilters")}
        />
        <StatCard
          label={t("servicesList.fullyOnline")}
          value={String(onlineHeavyCount)}
          hint={t("common.allInstancesReporting")}
          tone="success"
        />
        <StatCard
          label={t("servicesList.filterScope")}
          value={t(`environment.${selectedEnvironment}`)}
          hint={t("common.environmentLens")}
          tone={selectedEnvironment === "prod" ? "warning" : "default"}
        />
      </section>

      <Panel title={t("servicesList.panelTitle")} subtitle={t("servicesList.panelSubtitle")}>
        <div className="toolbar">
          <SegmentedControl
            ariaLabel={t("servicesList.filterScope")}
            onChange={(value) => updateSearchParam("environment", value)}
            options={ENVIRONMENT_OPTIONS.map((option) => ({
              label: t(`environment.${option}`),
              value: option,
            }))}
            value={selectedEnvironment}
          />
          <label className="search-field">
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

        {error ? <EmptyState title={t("servicesList.loadErrorTitle")} body={String(error)} /> : null}
        {isPending ? <div className="loading-block">{t("servicesList.loading")}</div> : null}
        {!isPending && !error && filteredItems.length === 0 ? (
          <EmptyState title={t("servicesList.emptyTitle")} body={t("servicesList.emptyBody")} />
        ) : null}

        {!isPending && !error && filteredItems.length > 0 ? (
          <div className="card-grid">
            {filteredItems.map((service) => {
              return (
                <Link
                  key={`${service.environment}:${service.name}`}
                  className="service-card"
                  to={servicePath(service.name, {
                    environment: service.environment,
                    lookback_minutes: 60,
                  })}
                >
                  <div className="service-card-topline">
                    <span className="service-name">{service.name}</span>
                    <StatusBadge
                      value={service.online_instance_count > 0 ? "online" : "offline"}
                      label={t("servicesList.onlineBadge", {
                        online: service.online_instance_count,
                        total: service.instance_count,
                      })}
                    />
                  </div>
                  <div className="service-card-meta">
                    <span>{t(`environment.${service.environment}`)}</span>
                    <span>{service.latest_deployment_version}</span>
                  </div>
                  <p className="service-card-copy">
                    {t("servicesList.cardBody", {
                      lastSeen: formatRelativeTime(service.last_seen_at),
                      lastSync: formatDateTime(service.latest_sync_at),
                    })}
                  </p>
                  <div className="service-card-footer">
                    <span title={service.latest_topology_hash ?? t("common.unset")}>
                      {t("servicesList.topologyLine", {
                        value: service.latest_topology_hash
                          ? formatIdentifierPreview(service.latest_topology_hash)
                          : t("common.unset"),
                      })}
                    </span>
                  </div>
                </Link>
              );
            })}
          </div>
        ) : null}
      </Panel>
    </div>
  );
}
