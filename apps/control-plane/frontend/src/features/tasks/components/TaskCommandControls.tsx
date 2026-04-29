import { useState } from "react";
import { useTranslation } from "react-i18next";

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
  targetInstanceIds: string[];
};

const DEFAULT_REPLAY_LIMIT = 10;

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
  const availableActions = getTaskActions(onlineTaskStates);
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
      const response = await mutation.mutateAsync({
        kind: action.kind,
        args:
          action.kind === "replay_dead_letters" || action.kind === "discard_dead_letters"
            ? { limit: replayLimit }
            : undefined,
        timeout_s: resolveTaskCommandTimeoutSeconds(action.kind),
        reason,
        target_mode: "selected_instances",
        target_instance_ids: action.targetInstanceIds,
      });
      const deliveredTargetCount = response.counts.dispatched + response.counts.queued;
      setLastRunSummary(
        t("taskCommandControls.lastRunSummary", {
          kind: t(`commandKind.${action.kind}`, { defaultValue: action.kind }),
          total: deliveredTargetCount,
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
        <div className="fanout-summary-grid task-control-summary-grid">
          <article className="fanout-summary-card task-control-summary-card">
            <strong>{t("taskCommandControls.summary.paused")}</strong>
            <p>{pausedOnlineCount}</p>
          </article>
          <article className="fanout-summary-card task-control-summary-card">
            <strong>{t("taskCommandControls.summary.accepting")}</strong>
            <p>{acceptingOnlineCount}</p>
          </article>
          <article className="fanout-summary-card task-control-summary-card">
            <strong>{t("taskCommandControls.summary.unknown")}</strong>
            <p>{unknownOnlineCount}</p>
          </article>
          <article className="fanout-summary-card task-control-summary-card">
            <strong>{t("taskCommandControls.summary.offline")}</strong>
            <p>{offlineCount}</p>
          </article>
        </div>
      </div>

      <div className="fanout-section">
        <span className="list-row-label">{t("taskCommandControls.actionsLabel")}</span>
        {availableActions.length > 0 ? (
          <div className="task-control-action-row">
            {availableActions.map((action) => (
              <button
                className={action.kind === "pause_task" ? "button-secondary button-danger" : "button-secondary"}
                disabled={mutation.isPending}
                key={action.kind}
                onClick={() => setReasonDialogAction(action)}
                type="button"
              >
                {mutation.isPending && submittingAction?.kind === action.kind
                  ? t("commands.dispatchingAction", {
                      kind: t(`commandKind.${action.kind}`, { defaultValue: action.kind }),
                    })
                  : t(`commands.action.${action.kind}`)}
              </button>
            ))}
          </div>
        ) : (
          <p className="fanout-note">{t("taskCommandControls.noOnlineInstances")}</p>
        )}
      </div>

      {submitError ? <div className="inline-feedback inline-feedback-error">{submitError}</div> : null}
      {lastRunSummary ? <div className="inline-feedback inline-feedback-success">{lastRunSummary}</div> : null}

      <CommandReasonDialog
        description={t("taskCommandControls.dialogBody", {
          kind: reasonDialogAction ? t(`commandKind.${reasonDialogAction.kind}`, { defaultValue: reasonDialogAction.kind }) : "",
          taskName,
          count: reasonDialogAction?.targetInstanceIds.length ?? 0,
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

function resolveTaskCommandTimeoutSeconds(kind: TaskCommandKind) {
  if (kind === "pause_task" || kind === "discard_dead_letters" || kind === "replay_dead_letters") {
    return 120;
  }
  return 30;
}

function supportsTaskControl(state: TaskInstanceControlState) {
  return state.supported_commands.length > 0;
}

function getTaskActions(states: TaskInstanceControlState[]) {
  const pauseTargets = states.filter(canPauseTask).map((state) => state.instance_id);
  const resumeTargets = states.filter(canResumeTask).map((state) => state.instance_id);
  const discardTargets = states.filter((state) => canRunTaskCommand(state, "discard_dead_letters")).map((state) => state.instance_id);
  const replayTargets = states.filter((state) => canRunTaskCommand(state, "replay_dead_letters")).map((state) => state.instance_id);
  const actions: PendingTaskAction[] = [];

  if (pauseTargets.length > 0) {
    actions.push({ kind: "pause_task", targetInstanceIds: pauseTargets });
  }
  if (resumeTargets.length > 0) {
    actions.push({ kind: "resume_task", targetInstanceIds: resumeTargets });
  }
  if (discardTargets.length > 0) {
    actions.push({ kind: "discard_dead_letters", targetInstanceIds: discardTargets });
  }
  if (replayTargets.length > 0) {
    actions.push({ kind: "replay_dead_letters", targetInstanceIds: replayTargets });
  }

  return actions;
}

function canRunTaskCommand(state: TaskInstanceControlState, kind: TaskCommandKind) {
  return state.connectivity === "online" && state.supported_commands.includes(kind);
}

function canPauseTask(state: TaskInstanceControlState) {
  return (
    canRunTaskCommand(state, "pause_task") &&
    state.state_known &&
    state.paused !== true &&
    state.accepting_new_work === true &&
    state.pause_requested !== true
  );
}

function canResumeTask(state: TaskInstanceControlState) {
  return canRunTaskCommand(state, "resume_task") && state.state_known && state.paused === true;
}
