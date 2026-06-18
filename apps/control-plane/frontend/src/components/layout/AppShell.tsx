import { useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { canManageNotificationSettings } from "../../features/auth/session";
import { useConsoleSessionQuery } from "../../features/auth/queries";
import { logoutAllConsole, logoutConsole } from "../../lib/api/client";
import { changeLanguage, getCurrentLanguage } from "../../lib/i18n";

export function AppShell() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const sessionQuery = useConsoleSessionQuery();

  const username = sessionQuery.data?.username;
  const authConfigured = sessionQuery.data?.auth_configured;
  const authenticated = sessionQuery.data?.authenticated;
  const canManageNotifications = canManageNotificationSettings(sessionQuery.data);

  async function handleLogoutAll() {
    try {
      await logoutAllConsole();
    } catch {
      // ignore
    }
    queryClient.setQueryData(["console-session"], null);
    queryClient.invalidateQueries({ queryKey: ["console-session"] });
    navigate("/login", { replace: true });
  }

  async function handleLogout() {
    try {
      await logoutConsole();
    } catch {
      // ignore
    }
    queryClient.setQueryData(["console-session"], null);
    queryClient.invalidateQueries({ queryKey: ["console-session"] });
    navigate("/login", { replace: true });
  }

  return (
    <div className="app-shell">
      <header className="shell-topbar">
        <div className="shell-topbar-main">
          <NavLink className="shell-brand" to="/">
            <span className="shell-brand-mark">01</span>
            <span className="shell-brand-copy">
              <strong className="shell-brand-title">{t("app.brand")}</strong>
              <span className="shell-brand-subtitle">{t("app.title")}</span>
            </span>
          </NavLink>

          <nav className="shell-nav" aria-label={t("app.primaryNavAriaLabel")}>
            <NavGroup label={t("app.navGroupNow")}>
              <ShellNavLink to="/">{t("app.commandCenterNav")}</ShellNavLink>
              <ShellNavLink to="/services?environment=all">{t("app.servicesNav")}</ShellNavLink>
            </NavGroup>
            <NavGroup label={t("app.navGroupDeploy")}>
              <ShellNavLink to="/agents">{t("app.agentsNav")}</ShellNavLink>
            </NavGroup>
            <NavGroup label={t("app.navGroupBuild")}>
              <ShellNavLink to="/workers">{t("app.workersNav")}</ShellNavLink>
              <ShellNavLink to="/connectors">{t("app.connectorsNav")}</ShellNavLink>
            </NavGroup>
            {canManageNotifications ? (
              <NavGroup label={t("app.navGroupAdmin")}>
                <ShellNavLink to="/settings/notifications">{t("app.notificationsNav")}</ShellNavLink>
              </NavGroup>
            ) : null}
          </nav>
        </div>

        <div className="shell-topbar-side">
          <button
            className="shell-lang-btn"
            type="button"
            aria-label={t("app.languageAriaLabel")}
            onClick={() => void changeLanguage(getCurrentLanguage() === "zh" ? "en" : "zh")}
          >
            {getCurrentLanguage() === "zh" ? t("language.en") : t("language.zh")}
          </button>
          {authConfigured && authenticated && username && (
            <>
              <span className="shell-username">{username}</span>
              <button className="shell-logout-btn" onClick={() => void handleLogoutAll()} type="button">
                {t("app.logoutAll")}
              </button>
              <button className="shell-logout-btn" onClick={() => void handleLogout()} type="button">
                {t("app.logout")}
              </button>
            </>
          )}
        </div>
      </header>

      <main className="app-main">
        <div className="page-shell signal-console-frame">
          <Outlet />
        </div>
      </main>
    </div>
  );
}

function NavGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="shell-nav-group">
      <span className="shell-nav-group-label">{label}</span>
      {children}
    </div>
  );
}

function ShellNavLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <NavLink
      className={({ isActive }) => (isActive ? "shell-nav-link active" : "shell-nav-link")}
      end={to === "/"}
      to={to}
    >
      {children}
    </NavLink>
  );
}
