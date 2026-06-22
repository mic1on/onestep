import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { EmptyState } from "../../../components/ui/EmptyState";
import { OverflowDialog } from "../../../components/ui/OverflowDialog";
import { StatusBadge } from "../../../components/ui/StatusBadge";
import { VibeDetailField } from "../../../components/ui/VibeDetailField";
import { VibeInlineButton } from "../../../components/ui/VibeInlineButton";
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
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);

  if (sessions.length === 0) {
    return <EmptyState title={emptyTitle} body={emptyBody} />;
  }

  const selectedSession = sessions.find((session) => session.session_id === selectedSessionId) ?? null;

  return (
    <>
      <div className="stack-list">
        {sessions.map((session) => (
          <article className="command-card command-card-compact" key={session.session_id}>
            <div className="command-card-header">
              <div className="command-card-copy">
                <div className="command-card-topline">
                  <strong>{session.node_name ?? formatIdentifierPreview(session.instance_id)}</strong>
                  <VibeInlineButton
                    aria-label={t("common.moreDetails")}
                    className="command-card-more-button"
                    onClick={() => setSelectedSessionId(session.session_id)}
                    title={t("common.moreDetails")}
                    variant="ghost"
                  >
                    <span aria-hidden="true" className="ref-menu-dots">
                      ...
                    </span>
                  </VibeInlineButton>
                </div>
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
                  <span className="code-chip">
                    {t("commands.acceptedCapabilitiesCount", {
                      count: session.accepted_capabilities.length,
                    })}
                  </span>
                </div>
              </div>
              <div className="row-metrics">
                <StatusBadge value={session.status} />
              </div>
            </div>
          </article>
        ))}
      </div>

      <OverflowDialog
        className="detail-dialog-card"
        onClose={() => setSelectedSessionId(null)}
        open={selectedSession !== null}
        title={t("commands.sessionDetailsTitle")}
      >
        {selectedSession ? <SessionDetailsBody session={selectedSession} /> : null}
      </OverflowDialog>
    </>
  );
}

function SessionDetailsBody({ session }: { session: AgentSessionSummary }) {
  const { t } = useTranslation();
  const shouldShowCapabilities =
    session.capabilities.length > 0 &&
    !sameStringList(session.capabilities, session.accepted_capabilities);

  return (
    <>
      <dl className="detail-dialog-grid">
        <VibeDetailField label={t("commands.instanceLabel")} value={session.instance_id} />
        <VibeDetailField label={t("commands.sessionIdLabel")} value={session.session_id} />
        <VibeDetailField label={t("commands.hostLabel")} value={session.hostname ?? t("common.notAvailable")} />
        <VibeDetailField label={t("commands.protocolLabel")} value={session.protocol_version} />
        <VibeDetailField label={t("commands.connectedAtLabel")} value={formatDateTime(session.connected_at)} />
        <VibeDetailField label={t("commands.disconnectedAtLabel")} value={formatDateTime(session.disconnected_at)} />
        <VibeDetailField label={t("commands.lastMessageLabel")} value={formatDateTime(session.last_message_at)} />
      </dl>

      {session.accepted_capabilities.length > 0 ? (
        <DetailSection label={t("commands.acceptedCapabilitiesLabel")}>
          <div className="detail-dialog-chip-grid">
            {session.accepted_capabilities.map((capability) => (
              <span className="code-chip" key={capability}>
                {capability}
              </span>
            ))}
          </div>
        </DetailSection>
      ) : null}

      {shouldShowCapabilities ? (
        <DetailSection label={t("commands.capabilitiesLabel")}>
          <div className="detail-dialog-chip-grid">
            {session.capabilities.map((capability) => (
              <span className="code-chip" key={capability}>
                {capability}
              </span>
            ))}
          </div>
        </DetailSection>
      ) : null}
    </>
  );
}

function DetailSection({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section className="detail-dialog-section">
      <span className="list-row-label">{label}</span>
      {children}
    </section>
  );
}

function sameStringList(left: string[], right: string[]) {
  if (left.length !== right.length) {
    return false;
  }

  return left.every((value, index) => value === right[index]);
}
