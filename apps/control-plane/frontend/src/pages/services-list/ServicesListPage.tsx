import { startTransition, useDeferredValue, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";

import { EmptyState } from "../../components/ui/EmptyState";
import { PageHeader } from "../../components/ui/PageHeader";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { useServicesQuery } from "../../features/services/queries";
import type { ServiceSummary } from "../../lib/api/types";
import { formatDateTime, formatIdentifierPreview, formatRelativeTime } from "../../lib/formatters";
import { servicePath } from "../../lib/routes";

export function ServicesListPage() {
  const { i18n, t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const environmentParam = searchParams.get("environment");
  const selectedEnvironment =
    environmentParam === "all" || environmentParam === "dev" || environmentParam === "staging" || environmentParam === "prod"
      ? environmentParam
      : "all";
  const [search, setSearch] = useState(searchParams.get("q") ?? "");
  const deferredSearch = useDeferredValue(search);
  const isZh = i18n.resolvedLanguage?.startsWith("zh");

  const { data, isPending, error, refetch } = useServicesQuery(
    selectedEnvironment === "all" ? undefined : selectedEnvironment,
  );

  const query = deferredSearch.trim().toLowerCase();
  const filteredItems = !query
    ? (data?.items ?? [])
    : (data?.items ?? []).filter((service) => service.name.toLowerCase().includes(query));

  const fullyOnlineCount = filteredItems.filter(
    (service) => service.instance_count > 0 && service.online_instance_count === service.instance_count,
  ).length;
  const totalInstances = filteredItems.reduce((sum, service) => sum + service.instance_count, 0);
  const onlineInstances = filteredItems.reduce((sum, service) => sum + service.online_instance_count, 0);
  const attentionCount = filteredItems.filter(
    (service) =>
      service.online_instance_count < service.instance_count ||
      service.latest_sync_at === null ||
      isServiceStale(service.latest_sync_at),
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
    <div className="page-stack fleet-page">
      <PageHeader
        title={t("servicesList.title")}
        actions={
          <>
            <label className="fleet-filter-select fleet-filter-select-header">
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
            <button className="button-secondary" onClick={() => void refetch()} type="button">
              {t("common.refresh")}
            </button>
          </>
        }
      />

      <section className="fleet-metric-grid">
        <FleetMetricCard
          hint={isZh ? "当前筛选窗口" : "current filter scope"}
          label={t("servicesList.visibleServices")}
          tone="accent"
          value={String(filteredItems.length)}
        />
        <FleetMetricCard
          hint={isZh ? "实例在线总量" : "live instance footprint"}
          label={isZh ? "在线实例" : "Online instances"}
          tone="success"
          value={`${onlineInstances}/${totalInstances || 0}`}
        />
        <FleetMetricCard
          hint={isZh ? "实例全部在线" : "all instances online"}
          label={t("servicesList.fullyOnline")}
          tone="default"
          value={String(fullyOnlineCount)}
        />
        <FleetMetricCard
          hint={isZh ? "需重点排查" : "needs review"}
          label={isZh ? "关注项" : "Attention"}
          tone={attentionCount > 0 ? "danger" : "success"}
          value={String(attentionCount)}
        />
      </section>

      <section className="panel fleet-table-panel">
        <header className="panel-header fleet-table-header">
          <div>
            <h3>{isZh ? "运行时服务目录" : "Runtime service directory"}</h3>
            <p>{t("servicesList.panelSubtitle")}</p>
          </div>
          <div className="fleet-table-controls">
            <label className="search-field fleet-search-field">
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
        </header>

        {error ? <EmptyState title={t("servicesList.loadErrorTitle")} body={String(error)} /> : null}
        {isPending ? <div className="loading-block">{t("servicesList.loading")}</div> : null}
        {!isPending && !error && filteredItems.length === 0 ? (
          <EmptyState title={t("servicesList.emptyTitle")} body={t("servicesList.emptyBody")} />
        ) : null}

        {!isPending && !error && filteredItems.length > 0 ? (
          <div className="fleet-table">
            <div className="fleet-table-head">
              <span>{isZh ? "服务 / 环境" : "Service / environment"}</span>
              <span>{isZh ? "实例健康度" : "Fleet health"}</span>
              <span>{isZh ? "部署版本" : "Deployment"}</span>
              <span>{isZh ? "拓扑摘要" : "Topology"}</span>
              <span>{isZh ? "最近同步" : "Last sync"}</span>
              <span>{isZh ? "动作" : "Action"}</span>
            </div>
            {filteredItems.map((service) => (
              <Link
                className="fleet-table-row"
                key={`${service.environment}:${service.name}`}
                to={servicePath(service.name, {
                  environment: service.environment,
                  lookback_minutes: 60,
                })}
              >
                <div className="fleet-service-summary">
                  <div className="fleet-service-title-row">
                    <strong>{service.name}</strong>
                    <StatusBadge
                      label={t(`environment.${service.environment}`)}
                      value={service.online_instance_count > 0 ? "active" : "offline"}
                    />
                  </div>
                  <p>
                    {isZh ? "最近可见" : "last seen"} {formatRelativeTime(service.last_seen_at)}
                  </p>
                </div>

                <div className="fleet-health-cell">
                  <div className="fleet-health-bar">
                    <span
                      className={getHealthBarClass(service)}
                      style={{
                        width: `${service.instance_count > 0 ? (service.online_instance_count / service.instance_count) * 100 : 0}%`,
                      }}
                    />
                  </div>
                  <div className="fleet-inline-meta">
                    <span>{`${service.online_instance_count}/${service.instance_count}`}</span>
                    <span>
                      {service.instance_count > 0 && service.online_instance_count === service.instance_count
                        ? isZh
                          ? "稳定"
                          : "healthy"
                        : isZh
                          ? "降级"
                          : "partial"}
                    </span>
                  </div>
                </div>

                <div className="fleet-inline-stack">
                  <strong>{service.latest_deployment_version}</strong>
                  <span>{t(`environment.${service.environment}`)}</span>
                </div>

                <div className="fleet-inline-stack fleet-inline-stack-singleline">
                  <strong title={service.latest_topology_hash ?? t("common.topologyUnavailable")}>
                    {formatIdentifierPreview(service.latest_topology_hash, 12, 8)}
                  </strong>
                </div>

                <div className="fleet-inline-stack fleet-inline-stack-singleline">
                  <strong title={formatDateTime(service.latest_sync_at)}>{formatRelativeTime(service.latest_sync_at)}</strong>
                </div>

                <div className="fleet-row-action">
                  <span>{isZh ? "打开" : "Open"}</span>
                </div>
              </Link>
            ))}
          </div>
        ) : null}
      </section>
    </div>
  );
}

function FleetMetricCard({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint: string;
  tone: "default" | "accent" | "success" | "danger";
}) {
  return (
    <article className={`fleet-metric-card fleet-metric-card-${tone}`}>
      <p>{label}</p>
      <strong>{value}</strong>
      <span>{hint}</span>
    </article>
  );
}

function isServiceStale(value: string | null) {
  if (!value) {
    return true;
  }
  return Date.now() - Date.parse(value) > 15 * 60 * 1000;
}

function getHealthBarClass(service: ServiceSummary) {
  if (service.instance_count > 0 && service.online_instance_count === service.instance_count) {
    return "fleet-health-bar-fill is-healthy";
  }
  if (service.online_instance_count > 0) {
    return "fleet-health-bar-fill is-partial";
  }
  return "fleet-health-bar-fill is-empty";
}
