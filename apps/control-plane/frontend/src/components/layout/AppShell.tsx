import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { useConsoleSessionQuery } from "../../features/auth/queries";
import { logoutConsole } from "../../lib/api/client";
import { getApiBaseUrl } from "../../lib/runtime-config";
import { SegmentedControl } from "../ui/SegmentedControl";

const API_BASE_URL = getApiBaseUrl();

export function AppShell() {
  const { i18n, t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const sessionQuery = useConsoleSessionQuery();
  const activeLanguage = i18n.resolvedLanguage?.startsWith("zh") ? "zh" : "en";

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

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-brand">
          <p className="eyebrow">{t("app.brand")}</p>
          <h1>{t("app.title")}</h1>
          <p className="app-subtitle">{t("app.subtitle")}</p>
        </div>
        <nav className="app-nav" aria-label={t("app.primaryNavAriaLabel")}>
          <NavLink
            to="/services?environment=prod"
            className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
          >
            {t("app.servicesNav")}
          </NavLink>
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
            <button className="button-secondary" onClick={() => void handleLogout()} type="button">
              {t("app.logout")}
            </button>
          ) : null}
          <span className="api-chip">{t("app.apiBase", { url: API_BASE_URL })}</span>
        </nav>
      </header>
      <main className="page-shell">
        <Outlet />
      </main>
    </div>
  );
}
