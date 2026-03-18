import { startTransition, useDeferredValue, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";

import { EmptyState } from "../../components/ui/EmptyState";
import { PageHeader } from "../../components/ui/PageHeader";
import { Panel } from "../../components/ui/Panel";
import { StatCard } from "../../components/ui/StatCard";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { useServicesQuery } from "../../features/services/queries";
import { formatDateTime, formatIdentifierPreview, formatRelativeTime } from "../../lib/formatters";
import { servicePath } from "../../lib/routes";

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

      <section className="stats-grid stats-grid-compact">
        <StatCard
          label={t("servicesList.visibleServices")}
          value={String(filteredItems.length)}
          hint={t("common.afterFilters")}
          size="compact"
        />
        <StatCard
          label={t("servicesList.fullyOnline")}
          value={String(onlineHeavyCount)}
          hint={t("common.allInstancesReporting")}
          size="compact"
          tone="success"
        />
        <StatCard
          label={t("servicesList.filterScope")}
          value={t(`environment.${selectedEnvironment}`)}
          hint={t("common.environmentLens")}
          size="compact"
          tone={selectedEnvironment === "prod" ? "warning" : "default"}
        />
      </section>

      <Panel title={t("servicesList.panelTitle")} subtitle={t("servicesList.panelSubtitle")}>
        <div className="toolbar toolbar-wide">
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
          <div className="toolbar-note">
            <span>{t("servicesList.filterScope")}</span>
            <strong>{t(`environment.${selectedEnvironment}`)}</strong>
          </div>
        </div>

        {error ? <EmptyState title={t("servicesList.loadErrorTitle")} body={String(error)} /> : null}
        {isPending ? <div className="loading-block">{t("servicesList.loading")}</div> : null}
        {!isPending && !error && filteredItems.length === 0 ? (
          <EmptyState title={t("servicesList.emptyTitle")} body={t("servicesList.emptyBody")} />
        ) : null}

        {!isPending && !error && filteredItems.length > 0 ? (
          <div className="service-directory">
            {filteredItems.map((service) => (
              <Link
                key={`${service.environment}:${service.name}`}
                className="service-directory-row"
                to={servicePath(service.name, {
                  environment: service.environment,
                  lookback_minutes: 60,
                })}
              >
                <div className="service-directory-copy">
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
                  <p className="service-card-copy">
                    {t("servicesList.cardBody", {
                      lastSeen: formatRelativeTime(service.last_seen_at),
                      lastSync: formatDateTime(service.latest_sync_at),
                    })}
                  </p>
                </div>

                <div className="service-directory-meta">
                  <span className="service-directory-line">{t(`environment.${service.environment}`)}</span>
                  <span className="service-directory-line">{service.latest_deployment_version}</span>
                  <span className="service-directory-line" title={service.latest_topology_hash ?? t("common.unset")}>
                    {t("servicesList.topologyLine", {
                      value: service.latest_topology_hash
                        ? formatIdentifierPreview(service.latest_topology_hash)
                        : t("common.unset"),
                    })}
                  </span>
                </div>

                <div className="service-directory-metrics">
                  <StatusBadge
                    value={
                      service.instance_count > 0 && service.online_instance_count === service.instance_count
                        ? "consistent"
                        : "starting"
                    }
                    label={t("servicesList.onlineBadge", {
                      online: service.online_instance_count,
                      total: service.instance_count,
                    })}
                  />
                  <span className="service-directory-line">{t("common.totalHint", { count: service.instance_count })}</span>
                  <span className="service-directory-line">
                    {t("common.lastSyncInline", {
                      time: formatDateTime(service.latest_sync_at),
                    })}
                  </span>
                </div>
              </Link>
            ))}
          </div>
        ) : null}
      </Panel>
    </div>
  );
}
