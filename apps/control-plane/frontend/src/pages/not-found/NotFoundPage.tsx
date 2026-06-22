import { useTranslation } from "react-i18next";

import { EmptyState } from "../../components/ui/EmptyState";
import { Panel } from "../../components/ui/Panel";
import { VibeLinkButton } from "../../components/ui/VibeLinkButton";

export function NotFoundPage() {
  const { t } = useTranslation();

  return (
    <div className="ref-console-page signal-console-empty-page">
      <section className="signal-console-hero signal-console-empty-hero">
        <div className="signal-console-hero-copy">
          <span className="signal-console-kicker">{t("notFound.panelTitle")}</span>
          <div className="signal-console-metric">
            <strong>404</strong>
            <span>{t("notFound.emptyTitle")}</span>
          </div>
          <p className="signal-console-hero-note">{t("notFound.panelSubtitle")}</p>
        </div>
      </section>

      <Panel
        title={t("notFound.emptyTitle")}
        subtitle={t("notFound.emptyBody")}
        className="signal-console-empty-panel"
      >
        <EmptyState title={t("notFound.emptyTitle")} body={t("notFound.emptyBody")} />
        <div className="panel-footer">
          <VibeLinkButton to="/services?environment=all">
            {t("notFound.backToServices")}
          </VibeLinkButton>
        </div>
      </Panel>
    </div>
  );
}
