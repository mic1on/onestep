import { useTranslation } from "react-i18next";

import type { TaskEventSummary } from "../../lib/api/types";

type TaskEventFailureDetailsProps = {
  event: TaskEventSummary;
};

export function TaskEventFailureDetails({ event }: TaskEventFailureDetailsProps) {
  const { t } = useTranslation();

  return (
    <div className="event-failure">
      <div className="row-metrics">
        <span>{event.exception_type ?? event.failure_kind ?? t("common.taskEvent")}</span>
        <span>{event.message ?? t("common.noMessage")}</span>
      </div>
      {event.traceback ? (
        <details className="traceback-details">
          <summary>{t("common.viewTraceback")}</summary>
          <pre className="traceback-block">{event.traceback}</pre>
        </details>
      ) : null}
    </div>
  );
}
