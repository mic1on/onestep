import { useTranslation } from "react-i18next";

import { EmptyState } from "../../../components/ui/EmptyState";
import type { TaskEventSummary } from "../../../lib/api/types";
import { formatDateTime, formatRelativeTime } from "../../../lib/formatters";

type ServiceEventsFeedProps = {
  events: TaskEventSummary[];
  emptyTitle: string;
  emptyBody: string;
};

export function ServiceEventsFeed({
  events,
  emptyTitle,
  emptyBody,
}: ServiceEventsFeedProps) {
  const { t } = useTranslation();

  if (events.length === 0) {
    return <EmptyState title={emptyTitle} body={emptyBody} />;
  }

  return (
    <div className="stack-list">
      {events.map((event) => (
        <article className="event-row" key={event.event_id}>
          <div>
            <strong>{event.task_name}</strong>
            <p>
              {t("serviceEventsFeed.eventMeta", {
                kind: t(`eventKind.${event.kind}`, { defaultValue: event.kind }),
                relativeTime: formatRelativeTime(event.occurred_at),
                absoluteTime: formatDateTime(event.occurred_at),
              })}
            </p>
          </div>
          <div className="row-metrics">
            <span>{event.failure_kind ?? t("common.taskEvent")}</span>
            <span>{event.message ?? t("common.noMessage")}</span>
          </div>
        </article>
      ))}
    </div>
  );
}
