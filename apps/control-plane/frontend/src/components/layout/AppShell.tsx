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
const ENVIRONMENT_OPTIONS = ["prod", "staging", "dev", "all"] as const;

type SidebarEnvironment = Environment | "all";
type ServiceSection = "overview" | "tasks" | "instances" | "events" | "commands";

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
  const environment = parseEnvironment(searchParams);
  const currentServiceName = params.serviceName;
  const selectedEnvironment: SidebarEnvironment =
    isServicesIndex && searchParams.get("environment") === "all" ? "all" : environment;
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
    const next = `${location.pathname}${location.search}${location.hash}` || "/services?environment=prod";
    navigate(`/login?next=${encodeURIComponent(next)}`, { replace: true });
  }

  function buildEnvironmentHref(option: SidebarEnvironment) {
    if (isServicesIndex) {
      return `/services${createSearch({
        environment: option,
        q: searchParams.get("q") ?? undefined,
      })}`;
    }

    if (!currentServiceName) {
      return `/services${createSearch({ environment: option === "all" ? environment : option })}`;
    }

    return servicePath(currentServiceName, {
      environment: option === "all" ? environment : option,
      lookback_minutes: lookbackMinutes,
      tab: activeSection === "overview" ? undefined : activeSection,
    });
  }

  function buildCatalogHref(service: ServiceSummary) {
    return servicePath(service.name, {
      environment: service.environment,
      lookback_minutes: lookbackMinutes,
      tab: activeSection === "overview" ? undefined : activeSection,
    });
  }

  return (
    <div className="app-shell">
      <aside className="app-sidebar">
        <div className="sidebar-frame">
          <div className="sidebar-brand">
            <p className="eyebrow">{t("app.brand")}</p>
            <h1>{t("app.title")}</h1>
            <p className="app-subtitle">{t("app.subtitle")}</p>
          </div>

          <nav className="sidebar-section" aria-label={t("app.primaryNavAriaLabel")}>
            <p className="sidebar-section-label">{t("app.navigationLabel")}</p>
            <NavLink
              className={({ isActive }) => (isActive ? "sidebar-nav-link active" : "sidebar-nav-link")}
              to={`/services${createSearch({ environment: selectedEnvironment })}`}
            >
              <span>{t("app.servicesNav")}</span>
              <span className="sidebar-nav-meta">{t("app.fleetSummaryLabel")}</span>
            </NavLink>
          </nav>

          <section className="sidebar-section">
            <div className="sidebar-section-head">
              <p className="sidebar-section-label">{t("app.environmentsLabel")}</p>
              <span className="sidebar-inline-meta">{t("app.viewScopeLabel")}</span>
            </div>
            <div className="sidebar-pill-grid">
              {ENVIRONMENT_OPTIONS.filter((option) => option !== "all" || isServicesIndex).map((option) => (
                <Link
                  key={option}
                  className={selectedEnvironment === option ? "sidebar-pill active" : "sidebar-pill"}
                  to={buildEnvironmentHref(option)}
                >
                  {t(`environment.${option}`)}
                </Link>
              ))}
            </div>
          </section>

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
                  <span className="sidebar-context-meta">{t("app.currentInstanceLabel", { name: params.instanceId })}</span>
                ) : null}
              </div>
              <div className="sidebar-link-stack">
                {(["overview", "tasks", "instances", "events", "commands"] as ServiceSection[]).map((section) => (
                  <Link
                    key={section}
                    className={activeSection === section ? "sidebar-subnav-link active" : "sidebar-subnav-link"}
                    to={servicePath(currentServiceName, {
                      environment,
                      lookback_minutes: lookbackMinutes,
                      tab: section === "overview" ? undefined : section,
                    })}
                  >
                    <span>{t(`tabs.${section}`)}</span>
                    <span className="sidebar-nav-meta">{t("app.sectionJumpLabel")}</span>
                  </Link>
                ))}
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
                      key={`${service.environment}:${service.name}`}
                      className={
                        currentServiceName === service.name
                          ? "sidebar-service-link active"
                          : "sidebar-service-link"
                      }
                      to={buildCatalogHref(service)}
                    >
                      <div className="sidebar-service-copy">
                        <strong>{service.name}</strong>
                        <span>
                          {t(`environment.${service.environment}`)} · {formatRelativeTime(service.last_seen_at)}
                        </span>
                      </div>
                      <StatusBadge
                        value={service.online_instance_count > 0 ? "online" : "offline"}
                        label={`${service.online_instance_count}/${service.instance_count}`}
                      />
                    </Link>
                  ))
                : null}
            </div>
          </section>

          <section className="sidebar-section sidebar-footer">
            <div className="sidebar-section-head">
              <p className="sidebar-section-label">{t("app.workspaceLabel")}</p>
              <span className="sidebar-inline-meta">{API_BASE_URL}</span>
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

  if (pathname.includes("/instances/")) {
    return "instances";
  }

  if (tab === "tasks" || tab === "instances" || tab === "events" || tab === "commands") {
    return tab;
  }

  return "overview";
}
