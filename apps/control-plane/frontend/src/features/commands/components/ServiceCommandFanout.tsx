import { useState } from "react";
import { useTranslation } from "react-i18next";

import { EmptyState } from "../../../components/ui/EmptyState";
import { SegmentedControl } from "../../../components/ui/SegmentedControl";
import { StatusBadge } from "../../../components/ui/StatusBadge";
import { useToast } from "../../../components/ui/ToastProvider";
import { CommandReasonDialog } from "./CommandReasonDialog";
import { useCreateServiceCommandFanoutMutation } from "../queries";
import { commandSupportsQueueing } from "../capabilities";
import type {
  AgentCommandKind,
  Environment,
  InstanceSummary,
  ServiceCommandFanoutResponse,
  ServiceCommandTargetMode,
} from "../../../lib/api/types";
import { formatIdentifierPreview } from "../../../lib/formatters";

type ServiceCommandFanoutProps = {
  serviceName: string;
  environment: Environment;
  instances: InstanceSummary[];
};

const FANOUT_COMMANDS: Exclude<AgentCommandKind, "shutdown">[] = [
  "sync_now",
  "flush_metrics",
  "flush_events",
  "ping",
  "drain",
  "restart",
];

export function ServiceCommandFanout({
  serviceName,
  environment,
  instances,
}: ServiceCommandFanoutProps) {
  const { t } = useTranslation();
  const { pushToast } = useToast();
  const mutation = useCreateServiceCommandFanoutMutation(serviceName, environment);
  const [targetMode, setTargetMode] = useState<ServiceCommandTargetMode>("all_online");
  const [offlineBehavior, setOfflineBehavior] = useState<"skip" | "queue">("skip");
  const [selectedInstanceIds, setSelectedInstanceIds] = useState<string[]>([]);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<ServiceCommandFanoutResponse | null>(null);
  const [pendingKind, setPendingKind] = useState<Exclude<AgentCommandKind, "shutdown"> | null>(null);
  const [reasonDialogKind, setReasonDialogKind] = useState<Exclude<AgentCommandKind, "shutdown"> | null>(null);

  const onlineInstances = instances.filter((instance) => instance.connectivity === "online");
  const selectedInstances = instances.filter((instance) =>
    selectedInstanceIds.includes(instance.instance_id),
  );
  const targetCount = targetMode === "all_online" ? onlineInstances.length : selectedInstances.length;
  const selectionEmpty = targetMode === "selected_instances" && targetCount === 0;

  function toggleInstance(instanceId: string) {
    setSelectedInstanceIds((current) =>
      current.includes(instanceId)
        ? current.filter((value) => value !== instanceId)
        : [...current, instanceId],
    );
  }

  async function dispatchFanout(kind: Exclude<AgentCommandKind, "shutdown">, reason: string) {
    setSubmitError(null);
    setPendingKind(kind);
    try {
      const result = await mutation.mutateAsync({
        kind,
        args: kind === "ping" ? { nonce: Date.now() } : {},
        timeout_s: resolveFanoutTimeoutSeconds(kind),
        reason,
        target_mode: targetMode,
        target_instance_ids: targetMode === "selected_instances" ? selectedInstanceIds : [],
        offline_behavior: offlineBehavior,
      });
      setLastResult(result);
      pushToast({
        tone: "success",
        message: t("serviceCommandFanout.lastRunSummary", {
          kind: t(`commandKind.${result.kind}`, { defaultValue: result.kind }),
          total: result.counts.total,
        }),
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setSubmitError(message);
      pushToast({ tone: "error", message });
      throw error;
    } finally {
      setPendingKind(null);
    }
  }

  if (instances.length === 0) {
    return (
      <EmptyState
        title={t("serviceCommandFanout.noInstancesTitle")}
        body={t("serviceCommandFanout.noInstancesBody")}
      />
    );
  }

  return (
    <div className="fanout-control-stack">
      <div className="fanout-section">
        <span className="list-row-label">{t("serviceCommandFanout.targetScopeLabel")}</span>
        <SegmentedControl
          ariaLabel={t("serviceCommandFanout.targetScopeAriaLabel")}
          onChange={(value) => setTargetMode(value)}
          options={[
            {
              label: t("serviceCommandFanout.targetScope.allOnline"),
              value: "all_online",
            },
            {
              label: t("serviceCommandFanout.targetScope.selectedInstances"),
              value: "selected_instances",
            },
          ]}
          value={targetMode}
        />
        <p className="fanout-note">
          {targetMode === "all_online"
            ? t("serviceCommandFanout.targetScopeSummary", { count: onlineInstances.length })
            : t("serviceCommandFanout.selectedSummary", { count: selectedInstances.length })}
        </p>
      </div>

      <div className="fanout-section">
        <span className="list-row-label">{t("serviceCommandFanout.offlineBehaviorLabel")}</span>
        <SegmentedControl
          ariaLabel={t("serviceCommandFanout.offlineBehaviorAriaLabel")}
          onChange={(value) => setOfflineBehavior(value)}
          options={[
            {
              label: t("serviceCommandFanout.offlineBehavior.skip"),
              value: "skip",
            },
            {
              label: t("serviceCommandFanout.offlineBehavior.queue"),
              value: "queue",
            },
          ]}
          value={offlineBehavior}
        />
        <p className="fanout-note">
          {offlineBehavior === "queue"
            ? t("serviceCommandFanout.offlineBehaviorHint.queue")
            : t("serviceCommandFanout.offlineBehaviorHint.skip")}
        </p>
      </div>

      {targetMode === "selected_instances" ? (
        <div className="fanout-section">
          <div className="fanout-section-header">
            <span className="list-row-label">{t("serviceCommandFanout.selectionLabel")}</span>
            <div className="fanout-inline-actions">
              <button
                className="button-link"
                onClick={() => setSelectedInstanceIds(onlineInstances.map((instance) => instance.instance_id))}
                type="button"
              >
                {t("serviceCommandFanout.selectOnline")}
              </button>
              <button className="button-link" onClick={() => setSelectedInstanceIds([])} type="button">
                {t("serviceCommandFanout.clearSelection")}
              </button>
            </div>
          </div>
          <div className="fanout-instance-grid">
            {instances.map((instance) => {
              const checked = selectedInstanceIds.includes(instance.instance_id);
              return (
                <label className={checked ? "fanout-instance-option active" : "fanout-instance-option"} key={instance.instance_id}>
                  <input
                    checked={checked}
                    onChange={() => toggleInstance(instance.instance_id)}
                    type="checkbox"
                  />
                  <div className="fanout-instance-copy">
                    <strong>{instance.node_name}</strong>
                    <p>{formatIdentifierPreview(instance.instance_id)}</p>
                  </div>
                  <div className="row-metrics">
                    {instance.active_session ? <StatusBadge value={instance.active_session.status} /> : null}
                    <StatusBadge value={instance.connectivity} />
                    <StatusBadge value={instance.status} />
                  </div>
                </label>
              );
            })}
          </div>
          {selectionEmpty ? (
            <p className="fanout-note">{t("serviceCommandFanout.noSelectionBody")}</p>
          ) : null}
        </div>
      ) : null}

      <div className="fanout-section">
        <span className="list-row-label">{t("serviceCommandFanout.actionsLabel")}</span>
        <div className="command-action-grid">
          {FANOUT_COMMANDS.map((kind) => {
            const disabledReason =
              offlineBehavior === "queue" && !commandSupportsQueueing(kind)
                ? t("commands.disabledReason.queueUnavailable", {
                    kind: t(`commandKind.${kind}`, { defaultValue: kind }),
                  })
                : null;

            return (
              <div className="command-action-item" key={kind}>
                <button
                  className={kind === "restart" ? "button-secondary button-danger" : "button-secondary"}
                  disabled={mutation.isPending || targetCount === 0 || selectionEmpty || disabledReason !== null}
                  onClick={() => setReasonDialogKind(kind)}
                  type="button"
                >
                  {mutation.isPending && pendingKind === kind
                    ? t("commands.dispatchingAction", {
                        kind: t(`commandKind.${kind}`, { defaultValue: kind }),
                      })
                    : t(`commands.action.${kind}`)}
                </button>
                {disabledReason ? <p className="command-action-reason">{disabledReason}</p> : null}
              </div>
            );
          })}
        </div>
      </div>

      {lastResult ? (
        <div className="fanout-result-stack">
          <div className="fanout-summary-grid">
            <article className="fanout-summary-card">
              <strong>{t("serviceCommandFanout.outcome.dispatched")}</strong>
              <p>{lastResult.counts.dispatched}</p>
            </article>
            <article className="fanout-summary-card">
              <strong>{t("serviceCommandFanout.outcome.queued")}</strong>
              <p>{lastResult.counts.queued}</p>
            </article>
            <article className="fanout-summary-card">
              <strong>{t("serviceCommandFanout.outcome.skipped")}</strong>
              <p>{lastResult.counts.skipped}</p>
            </article>
            <article className="fanout-summary-card">
              <strong>{t("serviceCommandFanout.outcome.rejected")}</strong>
              <p>{lastResult.counts.rejected}</p>
            </article>
          </div>

          <div className="fanout-result-groups">
            <OutcomeGroup
              emptyBody={t("serviceCommandFanout.emptyOutcomeBody")}
              items={lastResult.dispatched}
              title={t("serviceCommandFanout.outcome.dispatched")}
            />
            <OutcomeGroup
              emptyBody={t("serviceCommandFanout.emptyOutcomeBody")}
              items={lastResult.queued}
              title={t("serviceCommandFanout.outcome.queued")}
            />
            <OutcomeGroup
              emptyBody={t("serviceCommandFanout.emptyOutcomeBody")}
              items={lastResult.skipped}
              title={t("serviceCommandFanout.outcome.skipped")}
            />
            <OutcomeGroup
              emptyBody={t("serviceCommandFanout.emptyOutcomeBody")}
              items={lastResult.rejected}
              title={t("serviceCommandFanout.outcome.rejected")}
            />
          </div>
        </div>
      ) : null}

      <CommandReasonDialog
        description={t("commandReasonDialog.serviceFanoutBody", {
          count: targetCount,
        })}
        isSubmitting={mutation.isPending}
        onCancel={() => setReasonDialogKind(null)}
        onConfirm={async (reason) => {
          if (!reasonDialogKind) {
            return;
          }
          await dispatchFanout(reasonDialogKind, reason);
          setReasonDialogKind(null);
        }}
        open={reasonDialogKind !== null}
        title={t("commandReasonDialog.serviceFanoutTitle", {
          kind: reasonDialogKind ? t(`commandKind.${reasonDialogKind}`, { defaultValue: reasonDialogKind }) : "",
        })}
      />
    </div>
  );
}

function resolveFanoutTimeoutSeconds(kind: Exclude<AgentCommandKind, "shutdown">) {
  if (kind === "restart") {
    return 30;
  }
  if (kind === "drain") {
    return 120;
  }
  if (kind === "ping") {
    return 10;
  }
  return 20;
}

type OutcomeGroupProps = {
  title: string;
  emptyBody: string;
  items: ServiceCommandFanoutResponse["dispatched"];
};

function OutcomeGroup({ title, emptyBody, items }: OutcomeGroupProps) {
  return (
    <section className="fanout-result-group">
      <h4>{title}</h4>
      {items.length === 0 ? (
        <p className="fanout-note">{emptyBody}</p>
      ) : (
        <div className="fanout-result-list">
          {items.map((item) => (
            <article className="fanout-result-item" key={`${title}:${item.instance_id}`}>
              <strong>{item.node_name ?? formatIdentifierPreview(item.instance_id)}</strong>
              <p>
                {item.reason_message ??
                  (item.command_id ? formatIdentifierPreview(item.command_id) : null) ??
                  (item.session_id ? formatIdentifierPreview(item.session_id) : null) ??
                  formatIdentifierPreview(item.instance_id)}
              </p>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
