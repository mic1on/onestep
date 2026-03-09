import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { EmptyState } from "../../components/ui/EmptyState";
import { Panel } from "../../components/ui/Panel";

export function NotFoundPage() {
  const { t } = useTranslation();

  return (
    <Panel title={t("notFound.panelTitle")} subtitle={t("notFound.panelSubtitle")}>
      <EmptyState
        title={t("notFound.emptyTitle")}
        body={t("notFound.emptyBody")}
      />
      <div className="panel-footer">
        <Link className="button-link" to="/services?environment=prod">
          {t("notFound.backToServices")}
        </Link>
      </div>
    </Panel>
  );
}
