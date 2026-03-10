import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { EmptyState } from "../../components/ui/EmptyState";
import { Panel } from "../../components/ui/Panel";
import { useConsoleSessionQuery } from "./queries";

function buildNextPath(pathname: string, search: string, hash: string) {
  const next = `${pathname}${search}${hash}`;
  return next === "/" ? "/services?environment=prod" : next;
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
    return (
      <div className="auth-shell">
        <Panel title={t("auth.checkFailedTitle")} subtitle={t("auth.checkFailedSubtitle")} className="auth-panel">
          <EmptyState title={t("auth.checkFailedTitle")} body={String(sessionQuery.error)} />
        </Panel>
      </div>
    );
  }

  if (!sessionQuery.data?.auth_configured || sessionQuery.data.authenticated) {
    return <Outlet />;
  }

  const next = buildNextPath(location.pathname, location.search, location.hash);
  return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />;
}
