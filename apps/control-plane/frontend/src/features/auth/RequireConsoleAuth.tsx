import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { EmptyState } from "../../components/ui/EmptyState";
import { Panel } from "../../components/ui/Panel";
import { SessionExpiredError } from "../../lib/api/client";
import { useConsoleSessionQuery } from "./queries";

function buildNextPath(pathname: string, search: string, hash: string) {
  const next = `${pathname}${search}${hash}`;
  return next === "/" ? "/services?environment=all" : next;
}

export function RequireConsoleAuth() {
  const { t } = useTranslation();
  const location = useLocation();
  const sessionQuery = useConsoleSessionQuery();

  if (sessionQuery.isPending) {
    return (
      <div className="auth-shell">
        <Panel title={t("auth.checkingTitle")} subtitle={t("auth.checkingSubtitle")} className="auth-panel">
          <div className="loading-block">{t("auth.checkingBody")}</div>
        </Panel>
      </div>
    );
  }

  if (sessionQuery.error) {
    const isExpired = sessionQuery.error instanceof SessionExpiredError;
    return (
      <div className="auth-shell">
        <Panel title={t("auth.checkFailedTitle")} subtitle={t("auth.checkFailedSubtitle")} className="auth-panel">
          <EmptyState
            title={isExpired ? t("auth.loginTitle") : t("auth.checkFailedTitle")}
            body={isExpired ? t("auth.checkingSubtitle") : String(sessionQuery.error)}
          />
        </Panel>
      </div>
    );
  }

  if (sessionQuery.data?.bootstrap_required) {
    return (
      <div className="auth-shell">
        <Panel title={t("auth.loginTitle")} subtitle={t("auth.checkFailedSubtitle")} className="auth-panel">
          <EmptyState
            title={t("auth.loginTitle")}
            body="Local admin bootstrap is required before console access."
          />
        </Panel>
      </div>
    );
  }

  if ((!sessionQuery.data?.auth_configured && !sessionQuery.data?.bootstrap_required) || sessionQuery.data?.authenticated) {
    return <Outlet />;
  }

  const next = buildNextPath(location.pathname, location.search, location.hash);
  return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />;
}
