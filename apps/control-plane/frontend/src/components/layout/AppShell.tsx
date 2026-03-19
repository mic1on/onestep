import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, NavLink, Outlet, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { useConsoleSessionQuery } from "../../features/auth/queries";
import { useServicesQuery } from "../../features/services/queries";
import type { Environment, ServiceSummary } from "../../lib/api/types";
import { logoutConsole } from "../../lib/api/client";
import { formatRelativeTime } from "../../lib/formatters";
import { createSearch, parseEnvironment, parseLookback } from "../../lib/params";
import { servicePath } from "../../lib/routes";
import { getApiBaseUrl } from "../../lib/runtime-config";
import { SegmentedControl } from "../ui/SegmentedControl";
import { StatusBadge } from "../ui/StatusBadge";

const API_BASE_URL = getApiBaseUrl();
const SERVICE_CATALOG_LIMIT = 10;

type SidebarEnvironment = Environment | "all";
type ServiceSection = "overview" | "tasks";

export function AppShell() {
  const { i18n, t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const params = useParams<{ serviceName?: string; taskName?: string; instanceId?: string }>();
  const [searchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const sessionQuery = useConsoleSessionQuery();
  const activeLanguage = i18n.resolvedLanguage?.startsWith("zh") ? "zh" : "en";
  const isServicesIndex = location.pathname === "/services";
  const rawEnvironment = searchParams.get("environment");
  const environment = parseEnvironment(searchParams);
  const currentServiceName = params.serviceName;
  const selectedEnvironment: SidebarEnvironment =
    rawEnvironment === "all" || (isServicesIndex && rawEnvironment === null) ? "all" : environment;
  const lookbackMinutes = parseLookback(searchParams, 60);
  const activeSection = getActiveServiceSection(location.pathname, searchParams.get("tab"));
  const catalogQuery = useServicesQuery(selectedEnvironment === "all" ? undefined : selectedEnvironment);
  const visibleServices = prioritizeActiveService(catalogQuery.data?.items ?? [], currentServiceName);
  const catalogItems = catalogQuery.data?.items ?? [];
  const healthyServiceCount = catalogItems.filter(
    (service) => service.instance_count > 0 && service.online_instance_count === service.instance_count,
  ).length;
  const attentionServiceCount = catalogItems.filter(
    (service) =>
      service.online_instance_count < service.instance_count ||
      service.latest_sync_at === null ||
      isServiceStale(service.latest_sync_at),
  ).length;
  const searchQuery = searchParams.get("q")?.trim() ?? "";
  const authenticated = Boolean(sessionQuery.data?.authenticated);

  async function handleLogout() {
    await logoutConsole();
    queryClient.setQueryData(["console-session"], {
      auth_configured: true,
      authenticated: false,
      username: null,
    });
    const next = `${location.pathname}${location.search}${location.hash}` || "/services?environment=all";
    navigate(`/login?next=${encodeURIComponent(next)}`, { replace: true });
  }

  function buildCatalogHref(service: ServiceSummary) {
    return servicePath(service.name, {
      environment: service.environment,
      lookback_minutes: lookbackMinutes,
      tab: activeSection === "overview" ? undefined : activeSection,
    });
  }

  const primaryItems = [
    {
      id: "services",
      isStatic: false,
      label: t("app.servicesNav"),
      meta: t("app.fleetSummaryLabel"),
      visual: "01",
      href: `/services${createSearch({ environment: selectedEnvironment, q: searchParams.get("q") ?? undefined })}`,
    },
    {
      id: "audit",
      isStatic: true,
      label: activeLanguage === "zh" ? "审计日志" : "Audit log",
      meta: activeLanguage === "zh" ? "只读预留" : "reserved",
      visual: "02",
    },
    {
      id: "nodes",
      isStatic: true,
      label: activeLanguage === "zh" ? "拓扑图谱" : "Topology",
      meta: activeLanguage === "zh" ? "只读预留" : "reserved",
      visual: "03",
    },
  ] as const;

  const managementItems = [
    {
      id: "agents",
      label: activeLanguage === "zh" ? "Agent 会话" : "Agent sessions",
      visual: "AG",
    },
    {
      id: "settings",
      label: activeLanguage === "zh" ? "系统设置" : "System settings",
      visual: "CF",
    },
  ] as const;

  return (
    <div className="app-shell">
      <aside className="app-sidebar">
        <div className="sidebar-frame">
          <div className="sidebar-brand">
            <div className="sidebar-brand-mark">OS</div>
            <div className="sidebar-brand-copy">
              <p className="eyebrow">{t("app.brand")}</p>
              <h1>{t("app.title")}</h1>
              <p className="app-subtitle">{t("app.subtitle")}</p>
            </div>
          </div>

          <nav className="sidebar-section" aria-label={t("app.primaryNavAriaLabel")}>
            <p className="sidebar-section-label">{t("app.navigationLabel")}</p>
            <div className="sidebar-link-stack">
              {primaryItems.map((item) =>
                item.isStatic ? (
                  <button className="sidebar-nav-link sidebar-nav-link-static" disabled key={item.id} type="button">
                    <div className="sidebar-nav-copy">
                      <span>{item.label}</span>
                      <span className="sidebar-nav-meta">{item.meta}</span>
                    </div>
                    <span className="sidebar-nav-visual">{item.visual}</span>
                  </button>
                ) : (
                  <NavLink
                    className={({ isActive }) => (isActive ? "sidebar-nav-link active" : "sidebar-nav-link")}
                    key={item.id}
                    to={item.href}
                  >
                    <div className="sidebar-nav-copy">
                      <span>{item.label}</span>
                      <span className="sidebar-nav-meta">{item.meta}</span>
                    </div>
                    <span className="sidebar-nav-visual">{item.visual}</span>
                  </NavLink>
                ),
              )}
            </div>
          </nav>

          {currentServiceName ? (
            <section className="sidebar-section">
              <div className="sidebar-context-card">
                <p className="sidebar-section-label">{t("app.currentServiceLabel")}</p>
                <strong>{currentServiceName}</strong>
                <p>
                  {t("app.currentScope", {
                    environment: t(`environment.${environment}`),
                    scope: t(`tabs.${activeSection}`),
                  })}
                </p>
                {params.taskName ? (
                  <span className="sidebar-context-meta">{t("app.currentTaskLabel", { name: params.taskName })}</span>
                ) : null}
                {params.instanceId ? (
                  <span className="sidebar-context-meta">
                    {t("app.currentInstanceLabel", { name: params.instanceId })}
                  </span>
                ) : null}
              </div>
            </section>
          ) : null}

          <section className="sidebar-section">
            <p className="sidebar-section-label">{activeLanguage === "zh" ? "资源管理" : "Management"}</p>
            <div className="sidebar-link-stack">
              {managementItems.map((item) => (
                <button className="sidebar-nav-link sidebar-nav-link-static" disabled key={item.id} type="button">
                  <div className="sidebar-nav-copy">
                    <span>{item.label}</span>
                    <span className="sidebar-nav-meta">
                      {activeLanguage === "zh" ? "控制面预留" : "control-plane"}
                    </span>
                  </div>
                  <span className="sidebar-nav-visual">{item.visual}</span>
                </button>
              ))}
            </div>
          </section>

          <section className="sidebar-section sidebar-grow">
            <div className="sidebar-section-head">
              <p className="sidebar-section-label">{t("app.serviceCatalogLabel")}</p>
              {catalogQuery.data ? (
                <span className="sidebar-inline-meta">{t("app.catalogCount", { count: catalogQuery.data.total })}</span>
              ) : null}
            </div>
            <div className="sidebar-service-list">
              {catalogQuery.isPending ? <div className="sidebar-message">{t("app.catalogLoading")}</div> : null}
              {!catalogQuery.isPending && catalogQuery.error ? (
                <div className="sidebar-message">{t("app.catalogError")}</div>
              ) : null}
              {!catalogQuery.isPending && !catalogQuery.error && visibleServices.length === 0 ? (
                <div className="sidebar-message">{t("app.catalogEmpty")}</div>
              ) : null}
              {!catalogQuery.isPending && !catalogQuery.error
                ? visibleServices.slice(0, SERVICE_CATALOG_LIMIT).map((service) => (
                    <Link
                      className={
                        currentServiceName === service.name ? "sidebar-service-link active" : "sidebar-service-link"
                      }
                      key={`${service.environment}:${service.name}`}
                      to={buildCatalogHref(service)}
                    >
                      <div className="sidebar-service-copy">
                        <strong>{service.name}</strong>
                        <span>
                          {t(`environment.${service.environment}`)} · {formatRelativeTime(service.last_seen_at)}
                        </span>
                      </div>
                      <StatusBadge
                        label={`${service.online_instance_count}/${service.instance_count}`}
                        value={service.online_instance_count > 0 ? "online" : "offline"}
                      />
                    </Link>
                  ))
                : null}
            </div>
          </section>

          <section className="sidebar-section sidebar-footer">
            <div className="sidebar-health-card">
              <div className="sidebar-health-row">
                <div className="sidebar-brand-mark sidebar-brand-mark-small">CP</div>
                <div className="sidebar-health-copy">
                  <p>{activeLanguage === "zh" ? "控制面健康" : "Control plane health"}</p>
                  <strong>{activeLanguage === "zh" ? "稳定运行中" : "Stable"}</strong>
                </div>
              </div>
              <span className="sidebar-shell-meta">{API_BASE_URL}</span>
            </div>
            <SegmentedControl
              ariaLabel={t("app.languageAriaLabel")}
              onChange={(value) => {
                void i18n.changeLanguage(value);
              }}
              options={[
                { label: t("language.en"), value: "en" },
                { label: t("language.zh"), value: "zh" },
              ]}
              value={activeLanguage}
            />
            {sessionQuery.data?.auth_configured && sessionQuery.data.username ? (
              <span className="api-chip">{t("app.signedInAs", { username: sessionQuery.data.username })}</span>
            ) : null}
            {sessionQuery.data?.auth_configured ? (
              <button className="button-secondary sidebar-logout" onClick={() => void handleLogout()} type="button">
                {t("app.logout")}
              </button>
            ) : null}
          </section>
        </div>
      </aside>

      <main className="app-main">
        <div className="page-shell">
          <Outlet />
        </div>
      </main>

      <aside className="app-inspector">
        <div className="inspector-frame">
          <section className="inspector-card">
            <h3>{activeLanguage === "zh" ? "当前状态" : "Current status"}</h3>
            <div className="inspector-metric-list">
              <div className="inspector-metric-row">
                <span>{activeLanguage === "zh" ? "操作员" : "Operator"}</span>
                <strong>{authenticated ? (activeLanguage === "zh" ? "在线" : "Online") : activeLanguage === "zh" ? "未登录" : "Offline"}</strong>
              </div>
              <div className="inspector-metric-row">
                <span>{activeLanguage === "zh" ? "环境范围" : "Environment"}</span>
                <strong>{t(`environment.${selectedEnvironment}`)}</strong>
              </div>
              <div className="inspector-metric-row">
                <span>{activeLanguage === "zh" ? "当前区段" : "Section"}</span>
                <strong>{t(`tabs.${activeSection}`)}</strong>
              </div>
              <div className="inspector-metric-row">
                <span>{activeLanguage === "zh" ? "观察窗口" : "Lookback"}</span>
                <strong>{`${lookbackMinutes}m`}</strong>
              </div>
            </div>
          </section>

          <section className="inspector-card">
            <h3>{activeLanguage === "zh" ? "目录摘要" : "Catalog summary"}</h3>
            <div className="inspector-chip-grid">
              <span className="status-badge badge-accent">
                {activeLanguage === "zh" ? `服务 ${catalogItems.length}` : `${catalogItems.length} services`}
              </span>
              <span className="status-badge badge-success">
                {activeLanguage === "zh" ? `稳定 ${healthyServiceCount}` : `${healthyServiceCount} healthy`}
              </span>
              <span className={attentionServiceCount > 0 ? "status-badge badge-warning" : "status-badge badge-muted"}>
                {activeLanguage === "zh" ? `关注 ${attentionServiceCount}` : `${attentionServiceCount} attention`}
              </span>
            </div>
            <div className="inspector-metric-list">
              <div className="inspector-metric-row">
                <span>{activeLanguage === "zh" ? "已登录账号" : "Signed in as"}</span>
                <strong>{sessionQuery.data?.username ?? (activeLanguage === "zh" ? "匿名" : "Guest")}</strong>
              </div>
              <div className="inspector-metric-row">
                <span>{activeLanguage === "zh" ? "当前筛选" : "Search filter"}</span>
                <strong>{searchQuery || (activeLanguage === "zh" ? "无" : "None")}</strong>
              </div>
            </div>
          </section>

          <section className="inspector-card">
            <h3>{activeLanguage === "zh" ? "当前焦点" : "Current focus"}</h3>
            {currentServiceName ? (
              <div className="inspector-focus-card">
                <strong>{currentServiceName}</strong>
                <p>
                  {t(`environment.${environment}`)} · {t(`tabs.${activeSection}`)}
                </p>
                {params.taskName ? <span>{activeLanguage === "zh" ? `任务 ${params.taskName}` : `Task ${params.taskName}`}</span> : null}
                {params.instanceId ? (
                  <span>{activeLanguage === "zh" ? `实例 ${params.instanceId}` : `Instance ${params.instanceId}`}</span>
                ) : null}
              </div>
            ) : (
              <div className="empty-state">
                <h3>{activeLanguage === "zh" ? "尚未下钻服务" : "No service selected"}</h3>
                <p>
                  {activeLanguage === "zh"
                    ? "先从左侧目录进入一个服务，再查看更细的运行上下文。"
                    : "Open a service from the left catalog to inspect runtime context."}
                </p>
              </div>
            )}
          </section>
        </div>
      </aside>
    </div>
  );
}

function prioritizeActiveService(items: ServiceSummary[], activeServiceName?: string) {
  const sorted = [...items].sort((left, right) => left.name.localeCompare(right.name));
  if (!activeServiceName) {
    return sorted;
  }

  const active = sorted.find((service) => service.name === activeServiceName);
  if (!active) {
    return sorted;
  }

  return [active, ...sorted.filter((service) => service.name !== activeServiceName)];
}

function getActiveServiceSection(pathname: string, tab: string | null): ServiceSection {
  if (pathname.includes("/tasks/")) {
    return "tasks";
  }

  if (tab === "tasks") {
    return "tasks";
  }

  return "overview";
}

function isServiceStale(value: string | null) {
  if (!value) {
    return true;
  }
  return Date.now() - Date.parse(value) > 15 * 60 * 1000;
}
