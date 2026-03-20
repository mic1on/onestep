import { useState } from "react";
import { useTranslation } from "react-i18next";

import { StatusBadge } from "../../../components/ui/StatusBadge";
import { formatDateTime, formatIdentifierPreview } from "../../../lib/formatters";
import type {
  Environment,
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

type PendingTaskAction = {
  kind: TaskCommandKind;
  instance: TaskInstanceControlState;
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
  const [replayLimit, setReplayLimit] = useState(DEFAULT_REPLAY_LIMIT);
  const [reasonDialogAction, setReasonDialogAction] = useState<PendingTaskAction | null>(null);
  const [submittingAction, setSubmittingAction] = useState<PendingTaskAction | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [lastRunSummary, setLastRunSummary] = useState<string | null>(null);

  const taskStates = taskControl.instances;
  const onlineTaskStates = taskStates.filter((state) => state.connectivity === "online");
  const onlineSupportingStates = onlineTaskStates.filter((state) => supportsTaskControl(state));
  const pausedOnlineCount = onlineSupportingStates.filter((state) => state.state_known && state.paused).length;
  const acceptingOnlineCount = onlineSupportingStates.filter(
    (state) => state.state_known && state.accepting_new_work,
  ).length;
  const unknownOnlineCount = onlineSupportingStates.filter((state) => !state.state_known).length;
  const offlineCount = taskStates.filter((state) => state.connectivity !== "online").length;

  async function dispatchTaskCommand(action: PendingTaskAction, reason: string) {
    setSubmitError(null);
    setLastRunSummary(null);
    setSubmittingAction(action);
    try {
      await mutation.mutateAsync({
        kind: action.kind,
        args:
          action.kind === "replay_dead_letters" || action.kind === "discard_dead_letters"
            ? { limit: replayLimit }
            : undefined,
        timeout_s: resolveTaskCommandTimeoutSeconds(action.kind),
        reason,
        target_mode: "selected_instances",
        target_instance_ids: [action.instance.instance_id],
      });
      setLastRunSummary(
        t("taskCommandControls.lastRunSummary", {
          kind: t(`commandKind.${action.kind}`, { defaultValue: action.kind }),
          total: 1,
        }),
      );
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : String(error));
      throw error;
    } finally {
      setSubmittingAction(null);
    }
  }

  return (
    <div className="fanout-control-stack">
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
        <span className="list-row-label">{t("taskCommandControls.instancesLabel")}</span>
        {taskStates.length ? (
          <div className="task-control-instance-grid">
            {taskStates.map((state) => (
              <TaskControlInstanceCard
                key={state.instance_id}
                mutationPending={mutation.isPending}
                onDispatch={(kind) => setReasonDialogAction({ kind, instance: state })}
                pendingKind={
                  submittingAction?.instance.instance_id === state.instance_id
                    ? submittingAction.kind
                    : null
                }
                state={state}
              />
            ))}
          </div>
        ) : (
          <p className="fanout-note">{t("taskCommandControls.noInstancesBody")}</p>
        )}
      </div>

      {submitError ? <div className="inline-feedback inline-feedback-error">{submitError}</div> : null}
      {lastRunSummary ? <div className="inline-feedback inline-feedback-success">{lastRunSummary}</div> : null}

      <CommandReasonDialog
        description={t("taskCommandControls.dialogBody", {
          kind: reasonDialogAction ? t(`commandKind.${reasonDialogAction.kind}`, { defaultValue: reasonDialogAction.kind }) : "",
          taskName,
          count: reasonDialogAction ? 1 : 0,
        })}
        isSubmitting={mutation.isPending}
        onCancel={() => {
          if (mutation.isPending) {
            return;
          }
          setReasonDialogAction(null);
        }}
        onConfirm={async (reason) => {
          if (!reasonDialogAction) {
            return;
          }
          await dispatchTaskCommand(reasonDialogAction, reason);
          setReasonDialogAction(null);
        }}
        open={reasonDialogAction !== null}
        title={t("taskCommandControls.dialogTitle", {
          kind: reasonDialogAction ? t(`commandKind.${reasonDialogAction.kind}`, { defaultValue: reasonDialogAction.kind }) : "",
        })}
      />
    </div>
  );
}

type TaskControlInstanceCardProps = {
  state: TaskInstanceControlState;
  mutationPending: boolean;
  pendingKind: TaskCommandKind | null;
  onDispatch: (kind: TaskCommandKind) => void;
};

function TaskControlInstanceCard({
  state,
  mutationPending,
  pendingKind,
  onDispatch,
}: TaskControlInstanceCardProps) {
  const { t } = useTranslation();
  const cardNote = getTaskControlCardNote(t, state);

  return (
    <article className="task-control-instance-card">
      <div className="task-control-instance-header">
        <div className="task-control-instance-title">
          <strong>{state.node_name}</strong>
          <span className="command-action-reason">{formatIdentifierPreview(state.instance_id)}</span>
        </div>
        <div className="row-metrics">
          <StatusBadge value={state.connectivity} />
          <StatusBadge value={state.status} />
        </div>
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
      <div className="task-control-action-row">
        {TASK_COMMANDS.map((kind) => {
          const disabledReason = getTaskCommandDisabledReason(t, state, kind);
          return (
            <button
              className={kind === "pause_task" ? "button-secondary button-danger" : "button-secondary"}
              disabled={mutationPending || disabledReason !== null}
              key={kind}
              onClick={() => onDispatch(kind)}
              type="button"
            >
              {mutationPending && pendingKind === kind
                ? t("commands.dispatchingAction", {
                    kind: t(`commandKind.${kind}`, { defaultValue: kind }),
                  })
                : t(`commands.action.${kind}`)}
            </button>
          );
        })}
      </div>
      {cardNote ? <p className="command-action-reason">{cardNote}</p> : null}
    </article>
  );
}

function resolveTaskCommandTimeoutSeconds(kind: TaskCommandKind) {
  if (kind === "pause_task" || kind === "discard_dead_letters" || kind === "replay_dead_letters") {
    return 120;
  }
  return 30;
}

function supportsTaskControl(state: TaskInstanceControlState) {
  return state.supported_commands.length > 0;
}

function getTaskCommandDisabledReason(
  t: ReturnType<typeof useTranslation>["t"],
  state: TaskInstanceControlState,
  kind: TaskCommandKind,
) {
  if (state.connectivity !== "online") {
    return t("taskCommandControls.noOnlineInstances");
  }
  if (!state.supported_commands.includes(kind)) {
    return t("taskCommandControls.missingCapability", {
      capability: t(`commandKind.${kind}`, { defaultValue: kind }),
    });
  }
  return null;
}

function getTaskControlCardNote(
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
  return null;
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
