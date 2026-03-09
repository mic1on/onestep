import { NavLink, Outlet } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { SegmentedControl } from "../ui/SegmentedControl";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

export function AppShell() {
  const { i18n, t } = useTranslation();
  const activeLanguage = i18n.resolvedLanguage?.startsWith("zh") ? "zh" : "en";

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
          <span className="api-chip">{t("app.apiBase", { url: API_BASE_URL })}</span>
        </nav>
      </header>
      <main className="page-shell">
        <Outlet />
      </main>
    </div>
  );
}
