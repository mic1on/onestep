import { useTranslation } from "react-i18next";

import { EmptyState } from "../../../components/ui/EmptyState";
import { StatusBadge } from "../../../components/ui/StatusBadge";
import type { AgentSessionSummary } from "../../../lib/api/types";
import {
  formatDateTime,
  formatIdentifierPreview,
  formatRelativeTime,
} from "../../../lib/formatters";

type SessionListProps = {
  sessions: AgentSessionSummary[];
  emptyTitle: string;
  emptyBody: string;
};

export function SessionList({ sessions, emptyTitle, emptyBody }: SessionListProps) {
  const { t } = useTranslation();

  if (sessions.length === 0) {
    return <EmptyState title={emptyTitle} body={emptyBody} />;
  }

  return (
    <div className="stack-list">
      {sessions.map((session) => (
        <article className="command-card" key={session.session_id}>
          <div className="command-card-header">
            <div className="command-card-copy">
              <strong>{session.node_name ?? formatIdentifierPreview(session.instance_id)}</strong>
              <p>
                {t("commands.sessionMeta", {
                  relativeTime: formatRelativeTime(session.last_message_at),
                  absoluteTime: formatDateTime(session.connected_at),
                  protocolVersion: session.protocol_version,
                })}
              </p>
              <div className="command-card-meta">
                <span className="code-chip">{formatIdentifierPreview(session.session_id)}</span>
                {session.hostname ? <span>{session.hostname}</span> : null}
              </div>
            </div>
            <div className="row-metrics">
              <StatusBadge value={session.status} />
            </div>
          </div>

          {session.accepted_capabilities.length > 0 ? (
            <div className="command-chip-grid">
              {session.accepted_capabilities.map((capability) => (
                <span className="code-chip" key={capability}>
                  {capability}
                </span>
              ))}
            </div>
          ) : null}
        </article>
      ))}
    </div>
  );
}
