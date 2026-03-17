import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

import type { TaskEventSummary } from "../../lib/api/types";

type TaskEventFailureDetailsProps = {
  event: TaskEventSummary;
};

export function TaskEventFailureDetails({ event }: TaskEventFailureDetailsProps) {
  const { t } = useTranslation();
  const [isTracebackOpen, setIsTracebackOpen] = useState(false);
  const tracebackId = useId();
  const tracebackToggleLabel = isTracebackOpen ? t("common.hideTraceback") : t("common.viewTraceback");

  return (
    <div className="event-failure">
      <div className="failure-summary">
        <div className="row-metrics failure-copy">
          <span>{event.exception_type ?? event.failure_kind ?? t("common.taskEvent")}</span>
          <span>{event.message ?? t("common.noMessage")}</span>
        </div>
        {event.traceback ? (
          <button
            type="button"
            className={`traceback-toggle${isTracebackOpen ? " is-active" : ""}`}
            aria-controls={tracebackId}
            aria-expanded={isTracebackOpen}
            aria-label={tracebackToggleLabel}
            title={tracebackToggleLabel}
            onClick={() => setIsTracebackOpen((open) => !open)}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path
                d="M12 3 2.8 19a1 1 0 0 0 .87 1.5h16.66A1 1 0 0 0 21.2 19L12 3Zm0 5.25a1 1 0 0 1 1 1V13a1 1 0 1 1-2 0V9.25a1 1 0 0 1 1-1Zm0 9a1.25 1.25 0 1 1 0-2.5 1.25 1.25 0 0 1 0 2.5Z"
                fill="currentColor"
              />
            </svg>
          </button>
        ) : null}
      </div>
      {event.traceback && isTracebackOpen ? (
        <div id={tracebackId} className="traceback-panel">
          <pre className="traceback-block">{event.traceback}</pre>
        </div>
      ) : null}
    </div>
  );
}
