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
      tab: activeSection === "tasks" ? undefined : activeSection,
    });
  }

  const primaryItems = [
    {
      id: "services",
      label: t("app.servicesNav"),
      meta: t("app.fleetSummaryLabel"),
      visual: "01",
      href: `/services${createSearch({ environment: selectedEnvironment, q: searchParams.get("q") ?? undefined })}`,
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
              {primaryItems.map((item) => (
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
              ))}
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

  if (tab === "overview") {
    return "overview";
  }

  if (pathname.startsWith("/services/")) {
    return "tasks";
  }

  return "overview";
}
