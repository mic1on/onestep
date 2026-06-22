import { useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { canManageNotificationSettings } from "../../features/auth/session";
import { useConsoleSessionQuery } from "../../features/auth/queries";
import { logoutConsole } from "../../lib/api/client";
import { changeLanguage, getCurrentLanguage } from "../../lib/i18n";
import onestepPlaneLogo from "../../assets/onestep-plane-logo.svg";

export function AppShell() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const sessionQuery = useConsoleSessionQuery();

  const username = sessionQuery.data?.username;
  const authConfigured = sessionQuery.data?.auth_configured;
  const authenticated = sessionQuery.data?.authenticated;
  const canManageNotifications = canManageNotificationSettings(sessionQuery.data);

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

  const breadcrumbs = buildShellBreadcrumbs(location.pathname, t);

  return (
    <div className="app-shell cloudflare-console-shell">
      <aside className="shell-topbar shell-sidebar">
        <div className="shell-topbar-main">
          <NavLink className="shell-brand" to="/">
            <span className="shell-brand-mark">
              <img className="shell-brand-logo" src={onestepPlaneLogo} alt="" />
            </span>
            <span className="shell-brand-copy">
              <strong className="shell-brand-title">{t("app.brand")}</strong>
              <span className="shell-brand-subtitle">{t("app.title")}</span>
            </span>
          </NavLink>

          <nav className="shell-nav" aria-label={t("app.primaryNavAriaLabel")}>
            <NavGroup label={t("app.navGroupNow")}>
              <ShellNavLink icon="home" to="/">{t("app.commandCenterNav")}</ShellNavLink>
              <ShellNavLink icon="services" to="/services?environment=all">{t("app.servicesNav")}</ShellNavLink>
            </NavGroup>
            <NavGroup label={t("app.navGroupDeploy")}>
              <ShellNavLink icon="agents" to="/agents">{t("app.agentsNav")}</ShellNavLink>
            </NavGroup>
            <NavGroup label={t("app.navGroupBuild")}>
              <ShellNavLink icon="workers" to="/workers">{t("app.workersNav")}</ShellNavLink>
              <ShellNavLink icon="connectors" to="/connectors">{t("app.connectorsNav")}</ShellNavLink>
            </NavGroup>
            {canManageNotifications ? (
              <NavGroup label={t("app.navGroupAdmin")}>
                <ShellNavLink icon="notifications" to="/settings/notifications">{t("app.notificationsNav")}</ShellNavLink>
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
              <button className="shell-logout-btn" onClick={() => void handleLogout()} type="button">
                {t("app.logout")}
              </button>
            </>
          )}
        </div>
      </aside>

      <div className="shell-workspace">
        <header className="shell-workspace-topbar">
          <nav className="shell-breadcrumbs" aria-label={t("app.breadcrumbAriaLabel")}>
            {breadcrumbs.map((item, index) => (
              <span className="shell-breadcrumb" key={`${item.label}:${index}`}>
                {item.to ? <Link to={item.to}>{item.label}</Link> : <strong>{item.label}</strong>}
              </span>
            ))}
          </nav>
        </header>

        <main className="app-main">
          <div className="page-shell signal-console-frame">
            <Outlet />
          </div>
        </main>
      </div>
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

function ShellNavLink({ icon, to, children }: { icon: string; to: string; children: ReactNode }) {
  return (
    <NavLink
      className={({ isActive }) => (isActive ? "shell-nav-link active" : "shell-nav-link")}
      end={to === "/"}
      to={to}
    >
      <span className={`shell-nav-icon shell-nav-icon-${icon}`} aria-hidden="true" />
      {children}
    </NavLink>
  );
}

function buildShellBreadcrumbs(pathname: string, t: (key: string) => string) {
  const segments = pathname.split("/").filter(Boolean);

  if (segments.length === 0) {
    return [{ label: t("app.commandCenterNav") }];
  }

  if (segments[0] === "services") {
    const items: Array<{ label: string; to?: string }> = [
      { label: t("app.servicesNav"), to: "/services?environment=all" },
    ];
    if (segments[1]) {
      items.push({ label: decodeURIComponent(segments[1]) });
    }
    if (segments[2] === "tasks" && segments[3]) {
      items[items.length - 1].to = `/services/${segments[1]}`;
      items.push({ label: t("tabs.tasks") });
      items.push({ label: decodeURIComponent(segments[3]) });
    }
    if (segments[2] === "instances" && segments[3]) {
      items[items.length - 1].to = `/services/${segments[1]}`;
      items.push({ label: t("tabs.instances") });
      items.push({ label: decodeURIComponent(segments[3]) });
    }
    return items;
  }

  if (segments[0] === "agents") {
    const items: Array<{ label: string; to?: string }> = [{ label: t("app.agentsNav"), to: "/agents" }];
    if (segments[1]) {
      items.push({ label: decodeURIComponent(segments[1]) });
    }
    if (segments[2] === "deployments" && segments[3]) {
      items[items.length - 1].to = `/agents/${segments[1]}`;
      items.push({ label: "deployments" });
      items.push({ label: decodeURIComponent(segments[3]) });
    }
    return items;
  }

  if (segments[0] === "workers") {
    const items: Array<{ label: string; to?: string }> = [{ label: t("app.workersNav"), to: "/workers" }];
    if (segments[1]) {
      items.push({ label: decodeURIComponent(segments[1]) });
    }
    return items;
  }

  if (segments[0] === "connectors") {
    return [{ label: t("app.connectorsNav") }];
  }

  if (segments[0] === "settings") {
    return [{ label: t("app.notificationsNav") }];
  }

  return [{ label: t("app.commandCenterNav") }];
}
