import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";

import { CodeBlock } from "../../../components/ui/CodeBlock";
import { EmptyState } from "../../../components/ui/EmptyState";
import { StatusBadge } from "../../../components/ui/StatusBadge";
import type { AgentCommandSummary, Environment } from "../../../lib/api/types";
import {
  formatCompactJson,
  formatDateTime,
  formatDurationMs,
  formatIdentifierPreview,
  formatRelativeTime,
} from "../../../lib/formatters";
import { instancePath } from "../../../lib/routes";

type CommandFeedProps = {
  commands: AgentCommandSummary[];
  emptyTitle: string;
  emptyBody: string;
  serviceName?: string;
  environment?: Environment;
  lookbackMinutes?: number;
};

export function CommandFeed({
  commands,
  emptyTitle,
  emptyBody,
  serviceName,
  environment,
  lookbackMinutes,
}: CommandFeedProps) {
  const { t } = useTranslation();

  if (commands.length === 0) {
    return <EmptyState title={emptyTitle} body={emptyBody} />;
  }

  return (
    <div className="stack-list">
      {commands.map((command) => {
        const canLinkToInstance =
          serviceName !== undefined && environment !== undefined && lookbackMinutes !== undefined;
        const statusLabel =
          command.status === "pending" && command.session_id === null
            ? t("commands.statusLabel.queued")
            : t(`status.${command.status}`);
        const statusHint = getCommandStatusHint(command, t);

        return (
          <article className="command-card" key={command.command_id}>
            <div className="command-card-header">
              <div className="command-card-copy">
                <strong>{t(`commandKind.${command.kind}`, { defaultValue: command.kind })}</strong>
                <p>
                  {t("commands.commandMeta", {
                    relativeTime: formatRelativeTime(command.created_at),
                    absoluteTime: formatDateTime(command.created_at),
                    timeout: command.timeout_s,
                  })}
                </p>
                <div className="command-card-meta">
                  {canLinkToInstance ? (
                    <Link
                      className="inline-link"
                      to={instancePath(serviceName, command.instance_id, {
                        environment,
                        lookback_minutes: lookbackMinutes,
                      })}
                    >
                      {command.node_name ?? formatIdentifierPreview(command.instance_id)}
                    </Link>
                  ) : (
                    <span>{command.node_name ?? formatIdentifierPreview(command.instance_id)}</span>
                  )}
                  <span className="code-chip">{formatIdentifierPreview(command.command_id)}</span>
                </div>
              </div>
              <div className="row-metrics">
                <StatusBadge label={statusLabel} value={command.status} />
                {command.duration_ms !== null ? (
                  <span className="code-chip">{formatDurationMs(command.duration_ms)}</span>
                ) : null}
                {command.session_id ? (
                  <span className="code-chip">{formatIdentifierPreview(command.session_id)}</span>
                ) : null}
              </div>
            </div>

            {statusHint ? <p className="command-status-note">{statusHint}</p> : null}

            {Object.keys(command.args).length > 0 ? (
              <div className="command-card-block">
                <span className="list-row-label">{t("commands.argsLabel")}</span>
                <CodeBlock>{formatCompactJson(command.args)}</CodeBlock>
              </div>
            ) : null}

            {command.result ? (
              <div className="command-card-block">
                <span className="list-row-label">{t("commands.resultLabel")}</span>
                <CodeBlock>{formatCompactJson(command.result)}</CodeBlock>
              </div>
            ) : null}

            {command.duration_ms !== null ? (
              <div className="command-card-block">
                <span className="list-row-label">{t("commands.durationLabel")}</span>
                <p className="command-inline-text">{formatDurationMs(command.duration_ms)}</p>
              </div>
            ) : null}

            {command.created_by || command.reason || command.source_surface !== "unknown" ? (
              <div className="command-card-block">
                <span className="list-row-label">{t("commands.auditLabel")}</span>
                <p className="command-inline-text">
                  {command.created_by
                    ? t("commands.auditMeta", {
                        createdBy: command.created_by,
                        sourceSurface: t(`sourceSurface.${command.source_surface}`, {
                          defaultValue: command.source_surface,
                        }),
                      })
                    : t("commands.auditMetaNoActor", {
                        sourceSurface: t(`sourceSurface.${command.source_surface}`, {
                          defaultValue: command.source_surface,
                        }),
                      })}
                </p>
              </div>
            ) : null}

            {command.reason ? (
              <div className="command-card-block">
                <span className="list-row-label">{t("commands.reasonLabel")}</span>
                <p className="command-inline-text">{command.reason}</p>
              </div>
            ) : null}

            {command.error_code || command.error_message ? (
              <div className="command-card-block">
                <span className="list-row-label">{t("commands.errorLabel")}</span>
                <p className="command-inline-text">
                  {command.error_code ? `${command.error_code} · ` : null}
                  {command.error_message ?? t("common.noMessage")}
                </p>
              </div>
            ) : null}
          </article>
        );
      })}
    </div>
  );
}

function getCommandStatusHint(
  command: AgentCommandSummary,
  t: TFunction,
) {
  if (command.status === "pending" && command.session_id === null) {
    return t("commands.statusHint.queued");
  }
  if (command.status === "dispatched") {
    return t("commands.statusHint.dispatched");
  }
  if (command.status === "expired") {
    return t("commands.statusHint.expired");
  }
  if (command.status === "timeout") {
    return t("commands.statusHint.timeout");
  }
  return null;
}
