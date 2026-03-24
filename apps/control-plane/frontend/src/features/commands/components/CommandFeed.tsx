import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { Link } from "react-router-dom";

import { CodeBlock } from "../../../components/ui/CodeBlock";
import { EmptyState } from "../../../components/ui/EmptyState";
import { OverflowDialog } from "../../../components/ui/OverflowDialog";
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
  const [selectedCommandId, setSelectedCommandId] = useState<string | null>(null);

  if (commands.length === 0) {
    return <EmptyState title={emptyTitle} body={emptyBody} />;
  }

  const selectedCommand = commands.find((command) => command.command_id === selectedCommandId) ?? null;

  return (
    <>
      <div className="stack-list">
        {commands.map((command) => {
          const canLinkToInstance =
            serviceName !== undefined && environment !== undefined && lookbackMinutes !== undefined;
          const statusLabel =
            command.status === "pending" && command.session_id === null
              ? t("commands.statusLabel.queued")
              : t(`status.${command.status}`);
          const statusHint = getCommandStatusHint(command, t);
          const errorSummary =
            command.error_code || command.error_message
              ? `${command.error_code ? `${command.error_code} · ` : ""}${command.error_message ?? t("common.noMessage")}`
              : null;

          return (
            <article className="command-card command-card-compact" key={command.command_id}>
              <div className="command-card-header">
                <div className="command-card-copy">
                  <div className="command-card-topline">
                    <strong>{t(`commandKind.${command.kind}`, { defaultValue: command.kind })}</strong>
                    <button
                      aria-label={t("common.moreDetails")}
                      className="ref-ghost-button command-card-more-button"
                      onClick={() => setSelectedCommandId(command.command_id)}
                      title={t("common.moreDetails")}
                      type="button"
                    >
                      <span aria-hidden="true" className="ref-menu-dots">
                        ...
                      </span>
                    </button>
                  </div>
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
                </div>
              </div>

              {statusHint ? <p className="command-status-note">{statusHint}</p> : null}
              {errorSummary ? <p className="command-card-highlight is-danger">{errorSummary}</p> : null}
            </article>
          );
        })}
      </div>

      <OverflowDialog
        className="detail-dialog-card"
        onClose={() => setSelectedCommandId(null)}
        open={selectedCommand !== null}
        title={
          selectedCommand
            ? t("commands.commandDetailsTitle", {
                kind: t(`commandKind.${selectedCommand.kind}`, {
                  defaultValue: selectedCommand.kind,
                }),
              })
            : ""
        }
      >
        {selectedCommand ? (
          <CommandDetailsBody
            command={selectedCommand}
            environment={environment}
            lookbackMinutes={lookbackMinutes}
            serviceName={serviceName}
          />
        ) : null}
      </OverflowDialog>
    </>
  );
}

type CommandDetailsBodyProps = {
  command: AgentCommandSummary;
  serviceName?: string;
  environment?: Environment;
  lookbackMinutes?: number;
};

function CommandDetailsBody({
  command,
  serviceName,
  environment,
  lookbackMinutes,
}: CommandDetailsBodyProps) {
  const { t } = useTranslation();
  const canLinkToInstance =
    serviceName !== undefined && environment !== undefined && lookbackMinutes !== undefined;
  const auditSummary = command.created_by
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
      });

  return (
    <>
      <dl className="detail-dialog-grid">
        <DetailField
          label={t("commands.instanceLabel")}
          value={
            canLinkToInstance ? (
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
              command.node_name ?? formatIdentifierPreview(command.instance_id)
            )
          }
        />
        <DetailField label={t("commands.commandIdLabel")} value={command.command_id} />
        <DetailField label={t("commands.sessionIdLabel")} value={command.session_id ?? t("common.notAvailable")} />
        <DetailField label={t("commands.createdAtLabel")} value={formatDateTime(command.created_at)} />
        <DetailField label={t("commands.updatedAtLabel")} value={formatDateTime(command.updated_at)} />
        <DetailField label={t("commands.durationLabel")} value={formatDurationMs(command.duration_ms)} />
      </dl>

      {Object.keys(command.args).length > 0 ? (
        <DetailSection label={t("commands.argsLabel")}>
          <CodeBlock>{formatCompactJson(command.args)}</CodeBlock>
        </DetailSection>
      ) : null}

      {command.result ? (
        <DetailSection label={t("commands.resultLabel")}>
          <CodeBlock>{formatCompactJson(command.result)}</CodeBlock>
        </DetailSection>
      ) : null}

      <DetailSection label={t("commands.auditLabel")}>
        <p className="command-inline-text">{auditSummary}</p>
      </DetailSection>

      {command.reason ? (
        <DetailSection label={t("commands.reasonLabel")}>
          <p className="command-inline-text">{command.reason}</p>
        </DetailSection>
      ) : null}

      {command.error_code || command.error_message ? (
        <DetailSection label={t("commands.errorLabel")}>
          <p className="command-inline-text">
            {command.error_code ? `${command.error_code} · ` : null}
            {command.error_message ?? t("common.noMessage")}
          </p>
        </DetailSection>
      ) : null}
    </>
  );
}

function DetailField({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="detail-dialog-field">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
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
