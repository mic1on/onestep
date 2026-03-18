import { useState } from "react";
import { useTranslation } from "react-i18next";

import { SegmentedControl } from "../../../components/ui/SegmentedControl";
import { formatDateTime, formatIdentifierPreview } from "../../../lib/formatters";
import type {
  Environment,
  ServiceCommandFanoutResponse,
  ServiceCommandTargetMode,
  TaskCommandKind,
  TaskControlStateSummary,
  TaskInstanceControlState,
} from "../../../lib/api/types";
import { CommandReasonDialog } from "../../commands/components/CommandReasonDialog";
import { useCreateTaskCommandFanoutMutation } from "../../commands/queries";

type TaskCommandControlsProps = {
  serviceName: string;
  taskName: string;
  environment: Environment;
  taskControl: TaskControlStateSummary;
};

const DEFAULT_REPLAY_LIMIT = 10;
const TASK_COMMANDS: TaskCommandKind[] = [
  "pause_task",
  "resume_task",
  "discard_dead_letters",
  "replay_dead_letters",
];

export function TaskCommandControls({
  serviceName,
  taskName,
  environment,
  taskControl,
}: TaskCommandControlsProps) {
  const { t } = useTranslation();
  const mutation = useCreateTaskCommandFanoutMutation(serviceName, taskName, environment);
  const [targetMode, setTargetMode] = useState<ServiceCommandTargetMode>("all_online");
  const [selectedInstanceIds, setSelectedInstanceIds] = useState<string[]>([]);
  const [replayLimit, setReplayLimit] = useState(DEFAULT_REPLAY_LIMIT);
  const [pendingKind, setPendingKind] = useState<TaskCommandKind | null>(null);
  const [reasonDialogKind, setReasonDialogKind] = useState<TaskCommandKind | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<ServiceCommandFanoutResponse | null>(null);

  const taskStates = taskControl.instances;
  const onlineTaskStates = taskStates.filter((state) => state.connectivity === "online");
  const selectedTaskStates = taskStates.filter((state) => selectedInstanceIds.includes(state.instance_id));
  const targetStates = targetMode === "all_online" ? onlineTaskStates : selectedTaskStates;
  const targetCount = targetStates.length;
  const selectionEmpty = targetMode === "selected_instances" && targetCount === 0;
  const onlineSupportingStates = onlineTaskStates.filter((state) => supportsTaskControl(state));
  const pausedOnlineCount = onlineSupportingStates.filter(
    (state) => state.state_known && state.paused,
  ).length;
  const acceptingOnlineCount = onlineSupportingStates.filter(
    (state) => state.state_known && state.accepting_new_work,
  ).length;
  const unknownOnlineCount = onlineSupportingStates.filter((state) => !state.state_known).length;
  const offlineCount = taskStates.filter((state) => state.connectivity !== "online").length;

  function supportedOnlineCount(kind: TaskCommandKind) {
    return targetStates.filter((state) => state.supported_commands.includes(kind)).length;
  }

  function toggleInstance(instanceId: string) {
    setSelectedInstanceIds((current) =>
      current.includes(instanceId)
        ? current.filter((value) => value !== instanceId)
        : [...current, instanceId],
    );
  }

  async function dispatchTaskCommand(kind: TaskCommandKind, reason: string) {
    setSubmitError(null);
    setPendingKind(kind);
    try {
      const result = await mutation.mutateAsync({
        kind,
        args:
          kind === "replay_dead_letters" || kind === "discard_dead_letters"
            ? { limit: replayLimit }
            : undefined,
        timeout_s: resolveTaskCommandTimeoutSeconds(kind),
        reason,
        target_mode: targetMode,
        target_instance_ids: targetMode === "selected_instances" ? selectedInstanceIds : [],
      });
      setLastResult(result);
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : String(error));
      throw error;
    } finally {
      setPendingKind(null);
    }
  }

  return (
    <div className="fanout-control-stack">
      <div className="fanout-section">
        <span className="list-row-label">{t("taskCommandControls.scopeLabel")}</span>
        <SegmentedControl
          ariaLabel={t("taskCommandControls.targetScopeAriaLabel")}
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
            ? t("taskCommandControls.scopeSummary", { count: onlineTaskStates.length })
            : t("serviceCommandFanout.selectedSummary", { count: selectedTaskStates.length })}
        </p>
      </div>

      <div className="fanout-section">
        <span className="list-row-label">{t("taskCommandControls.stateLabel")}</span>
        <div className="fanout-summary-grid">
          <article className="fanout-summary-card">
            <strong>{t("taskCommandControls.summary.paused")}</strong>
            <p>{pausedOnlineCount}</p>
          </article>
          <article className="fanout-summary-card">
            <strong>{t("taskCommandControls.summary.accepting")}</strong>
            <p>{acceptingOnlineCount}</p>
          </article>
          <article className="fanout-summary-card">
            <strong>{t("taskCommandControls.summary.unknown")}</strong>
            <p>{unknownOnlineCount}</p>
          </article>
          <article className="fanout-summary-card">
            <strong>{t("taskCommandControls.summary.offline")}</strong>
            <p>{offlineCount}</p>
          </article>
        </div>
      </div>

      <div className="fanout-section">
        <span className="list-row-label">{t("taskCommandControls.recoverySettingsLabel")}</span>
        <label className="task-command-number-field">
          <span>{t("taskCommandControls.recoveryLimitLabel")}</span>
          <input
            min={1}
            onChange={(event) => {
              const nextValue = Number.parseInt(event.target.value, 10);
              if (Number.isNaN(nextValue) || nextValue < 1) {
                return;
              }
              setReplayLimit(nextValue);
            }}
            step={1}
            type="number"
            value={replayLimit}
          />
        </label>
        <p className="fanout-note">{t("taskCommandControls.recoveryLimitHint")}</p>
      </div>

      <div className="fanout-section">
        <span className="list-row-label">{t("taskCommandControls.actionsLabel")}</span>
        <div className="command-action-grid">
          {TASK_COMMANDS.map((kind) => {
            const supportedCount = supportedOnlineCount(kind);
            const disabledReason =
              onlineTaskStates.length === 0
                ? t("taskCommandControls.noOnlineInstances")
                : selectionEmpty
                  ? t("serviceCommandFanout.noSelectionBody")
                  : supportedCount === 0
                  ? t("taskCommandControls.missingCapability", {
                      capability: t(`commandKind.${kind}`, { defaultValue: kind }),
                    })
                  : null;

            return (
              <div className="command-action-item" key={kind}>
                <button
                  className={kind === "pause_task" ? "button-secondary button-danger" : "button-secondary"}
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
                <p className="command-action-reason">
                  {disabledReason ??
                    t("taskCommandControls.coverageSummary", {
                      supported: supportedCount,
                      total: targetCount,
                    })}
                </p>
              </div>
            );
          })}
        </div>
      </div>

      {targetMode === "selected_instances" ? (
        <div className="fanout-section">
          <div className="fanout-section-header">
            <span className="list-row-label">{t("serviceCommandFanout.selectionLabel")}</span>
            <div className="fanout-inline-actions">
              <button
                className="button-link"
                onClick={() => setSelectedInstanceIds(onlineTaskStates.map((state) => state.instance_id))}
                type="button"
              >
                {t("serviceCommandFanout.selectOnline")}
              </button>
              <button className="button-link" onClick={() => setSelectedInstanceIds([])} type="button">
                {t("serviceCommandFanout.clearSelection")}
              </button>
            </div>
          </div>
          {selectionEmpty ? (
            <p className="fanout-note">{t("serviceCommandFanout.noSelectionBody")}</p>
          ) : null}
        </div>
      ) : null}

      <div className="fanout-section">
        <span className="list-row-label">{t("taskCommandControls.instancesLabel")}</span>
        {taskStates.length ? (
          <div className="task-control-instance-grid">
            {taskStates.map((state) => (
              <article
                className={
                  targetMode === "selected_instances" && selectedInstanceIds.includes(state.instance_id)
                    ? "task-control-instance-card active"
                    : "task-control-instance-card"
                }
                key={state.instance_id}
              >
                <div className="task-control-instance-header">
                  <div className="task-control-instance-title">
                    <strong>{state.node_name}</strong>
                    <span className="command-action-reason">{formatIdentifierPreview(state.instance_id)}</span>
                  </div>
                  {targetMode === "selected_instances" ? (
                    <input
                      checked={selectedInstanceIds.includes(state.instance_id)}
                      onChange={() => toggleInstance(state.instance_id)}
                      type="checkbox"
                    />
                  ) : (
                    <span className="command-action-reason">{t(`status.${state.connectivity}`)}</span>
                  )}
                </div>
                <p className="task-control-instance-state">{describeTaskControlState(t, state)}</p>
                <p className="command-action-reason">
                  {t("taskCommandControls.instanceMeta", {
                    connectivity: t(`status.${state.connectivity}`),
                    health: t(`status.${state.status}`),
                    lastSeen: formatDateTime(state.last_seen_at),
                  })}
                </p>
                {state.state_known ? (
                  <p className="command-action-reason">
                    {t("taskCommandControls.runnerSummary", {
                      parked: state.parked_runner_count ?? 0,
                      total: state.runner_count ?? 0,
                      fetching: state.fetching_runner_count ?? 0,
                      inflight: state.inflight_task_count ?? 0,
                    })}
                  </p>
                ) : null}
              </article>
            ))}
          </div>
        ) : (
          <p className="fanout-note">{t("taskCommandControls.noInstancesBody")}</p>
        )}
      </div>

      {submitError ? <div className="inline-feedback inline-feedback-error">{submitError}</div> : null}

      {lastResult ? (
        <div className="fanout-result-stack">
          <div className="inline-feedback inline-feedback-success">
            {t("taskCommandControls.lastRunSummary", {
              kind: t(`commandKind.${lastResult.kind}`, { defaultValue: lastResult.kind }),
              total: lastResult.counts.total,
            })}
          </div>
          <div className="fanout-summary-grid">
            <article className="fanout-summary-card">
              <strong>{t("serviceCommandFanout.outcome.dispatched")}</strong>
              <p>{lastResult.counts.dispatched}</p>
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
        description={t("taskCommandControls.dialogBody", {
          kind: reasonDialogKind ? t(`commandKind.${reasonDialogKind}`, { defaultValue: reasonDialogKind }) : "",
          taskName,
          count: targetCount,
        })}
        isSubmitting={mutation.isPending}
        onCancel={() => {
          if (mutation.isPending) {
            return;
          }
          setReasonDialogKind(null);
        }}
        onConfirm={async (reason) => {
          if (!reasonDialogKind) {
            return;
          }
          await dispatchTaskCommand(reasonDialogKind, reason);
          setReasonDialogKind(null);
        }}
        open={reasonDialogKind !== null}
        title={t("taskCommandControls.dialogTitle", {
          kind: reasonDialogKind ? t(`commandKind.${reasonDialogKind}`, { defaultValue: reasonDialogKind }) : "",
        })}
      />
    </div>
  );
}

function resolveTaskCommandTimeoutSeconds(kind: TaskCommandKind) {
  if (
    kind === "pause_task"
    || kind === "discard_dead_letters"
    || kind === "replay_dead_letters"
  ) {
    return 120;
  }
  return 30;
}

function supportsTaskControl(state: TaskInstanceControlState) {
  return state.supported_commands.length > 0;
}

function describeTaskControlState(
  t: ReturnType<typeof useTranslation>["t"],
  state: TaskInstanceControlState,
) {
  if (state.connectivity !== "online") {
    return t("taskCommandControls.instanceState.offline");
  }
  if (!supportsTaskControl(state)) {
    return t("taskCommandControls.instanceState.unsupported");
  }
  if (!state.state_known) {
    return t("taskCommandControls.instanceState.unknown");
  }
  if (state.paused) {
    return t("taskCommandControls.instanceState.paused");
  }
  if (state.accepting_new_work) {
    return t("taskCommandControls.instanceState.accepting");
  }
  return t("taskCommandControls.instanceState.transitioning");
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
