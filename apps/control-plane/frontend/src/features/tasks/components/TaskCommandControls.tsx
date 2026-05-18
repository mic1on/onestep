import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { OverflowDialog } from "../../../components/ui/OverflowDialog";
import { useToast } from "../../../components/ui/ToastProvider";
import { ApiTimeoutError } from "../../../lib/api/client";
import type {
  Environment,
  TaskCommandKind,
  TaskControlStateSummary,
  TaskInstanceControlState,
} from "../../../lib/api/types";
import { formatIdentifierPreview } from "../../../lib/formatters";
import { CommandReasonDialog } from "../../commands/components/CommandReasonDialog";
import { DestructiveCommandReviewDialog } from "../../commands/components/DestructiveCommandReviewDialog";
import { getCommandRiskLevel, isDestructiveCommand } from "../../commands/capabilities";
import { useCreateTaskCommandFanoutMutation } from "../../commands/queries";

type TaskCommandControlsProps = {
  serviceName: string;
  taskName: string;
  environment: Environment;
  taskControl: TaskControlStateSummary;
  onIssueChange?: (issue: { message: string; timeout: boolean } | null) => void;
};

type PendingTaskAction = {
  kind: TaskCommandKind;
  targetInstanceIds: string[];
};

const DEFAULT_REPLAY_LIMIT = 10;
const DEFAULT_RUN_TASK_PAYLOAD = "{\n  \"trigger\": \"manual\"\n}";

export function TaskCommandControls({
  serviceName,
  taskName,
  environment,
  taskControl,
  onIssueChange,
}: TaskCommandControlsProps) {
  const { i18n, t } = useTranslation();
  const { pushToast } = useToast();
  const isZh = Boolean(i18n.resolvedLanguage?.startsWith("zh"));
  const mutation = useCreateTaskCommandFanoutMutation(serviceName, taskName, environment);
  const [replayLimit, setReplayLimit] = useState(DEFAULT_REPLAY_LIMIT);
  const [reasonDialogAction, setReasonDialogAction] = useState<PendingTaskAction | null>(null);
  const [submittingAction, setSubmittingAction] = useState<PendingTaskAction | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [lastRunSummary, setLastRunSummary] = useState<string | null>(null);
  const [manualRunTargetIds, setManualRunTargetIds] = useState<string[]>([]);
  const [manualRunPayloadText, setManualRunPayloadText] = useState(DEFAULT_RUN_TASK_PAYLOAD);
  const [manualRunReason, setManualRunReason] = useState("");
  const [manualRunError, setManualRunError] = useState<string | null>(null);
  const [manualRunOpen, setManualRunOpen] = useState(false);

  const taskStates = taskControl.instances;
  const onlineTaskStates = taskStates.filter((state) => state.connectivity === "online");
  const availableActions = getTaskActions(onlineTaskStates);
  const manualRunStates = onlineTaskStates.filter((state) => canRunTaskCommand(state, "run_task_once"));
  const onlineSupportingStates = onlineTaskStates.filter((state) => supportsTaskControl(state));
  const pausedOnlineCount = onlineSupportingStates.filter((state) => state.state_known && state.paused).length;
  const acceptingOnlineCount = onlineSupportingStates.filter(
    (state) => state.state_known && state.accepting_new_work,
  ).length;
  const unknownOnlineCount = onlineSupportingStates.filter((state) => !state.state_known).length;
  const offlineCount = taskStates.filter((state) => state.connectivity !== "online").length;

  useEffect(() => {
    setManualRunTargetIds((current) => {
      const allowedIds = new Set(manualRunStates.map((state) => state.instance_id));
      const next = current.filter((instanceId) => allowedIds.has(instanceId));
      return next.length === current.length ? current : next;
    });
  }, [manualRunStates]);

  useEffect(() => {
    if (!manualRunOpen) {
      setManualRunPayloadText(DEFAULT_RUN_TASK_PAYLOAD);
      setManualRunReason("");
      setManualRunError(null);
    }
  }, [manualRunOpen]);

  async function dispatchTaskCommand(action: PendingTaskAction, reason: string) {
    setSubmitError(null);
    setLastRunSummary(null);
    setManualRunError(null);
    onIssueChange?.(null);
    setSubmittingAction(action);
    try {
      const response = await mutation.mutateAsync({
        kind: action.kind,
        args:
          action.kind === "replay_dead_letters" || action.kind === "discard_dead_letters"
            ? { limit: replayLimit }
            : action.kind === "run_task_once"
              ? { payload: parseManualRunPayload(manualRunPayloadText) }
            : undefined,
        timeout_s: resolveTaskCommandTimeoutSeconds(action.kind),
        reason,
        target_mode: "selected_instances",
        target_instance_ids: action.targetInstanceIds,
      });
      const deliveredTargetCount = response.counts.dispatched + response.counts.queued;
      const message = t("taskCommandControls.lastRunSummary", {
          kind: t(`commandKind.${action.kind}`, { defaultValue: action.kind }),
          total: deliveredTargetCount,
        });
      setLastRunSummary(message);
      onIssueChange?.(null);
      pushToast({ tone: "success", message });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setSubmitError(message);
      onIssueChange?.({
        message,
        timeout: error instanceof ApiTimeoutError,
      });
      pushToast({ tone: "error", message });
      throw error;
    } finally {
      setSubmittingAction(null);
    }
  }

  function toggleManualRunTarget(instanceId: string) {
    setManualRunTargetIds((current) =>
      current.includes(instanceId)
        ? current.filter((value) => value !== instanceId)
        : [...current, instanceId],
    );
  }

  async function handleManualRunSubmit() {
    const normalizedReason = manualRunReason.trim();
    if (!normalizedReason) {
      setManualRunError(t("taskCommandControls.manualRun.reasonRequired"));
      return;
    }
    if (manualRunTargetIds.length === 0) {
      setManualRunError(t("taskCommandControls.manualRun.selectionRequired"));
      return;
    }

    try {
      parseManualRunPayload(manualRunPayloadText);
    } catch (error) {
      setManualRunError(error instanceof Error ? error.message : String(error));
      return;
    }

    await dispatchTaskCommand(
      { kind: "run_task_once", targetInstanceIds: manualRunTargetIds },
      normalizedReason,
    );
    setManualRunReason("");
    setManualRunError(null);
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
        {availableActions.length > 0 || manualRunStates.length > 0 ? (
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
            {manualRunStates.length > 0 ? (
              <button
                className="button-secondary"
                disabled={mutation.isPending}
                onClick={() => setManualRunOpen(true)}
                type="button"
              >
                {mutation.isPending && submittingAction?.kind === "run_task_once"
                  ? t("commands.dispatchingAction", {
                      kind: t("commandKind.run_task_once", { defaultValue: "run_task_once" }),
                    })
                  : t("commands.action.run_task_once")}
              </button>
            ) : null}
          </div>
        ) : (
          <p className="fanout-note">{t("taskCommandControls.noOnlineInstances")}</p>
        )}
      </div>

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
        open={reasonDialogAction !== null && !isDestructiveCommand(reasonDialogAction.kind)}
        title={t("taskCommandControls.dialogTitle", {
          kind: reasonDialogAction ? t(`commandKind.${reasonDialogAction.kind}`, { defaultValue: reasonDialogAction.kind }) : "",
        })}
      />
      <DestructiveCommandReviewDialog
        commandLabel={reasonDialogAction ? t(`commandKind.${reasonDialogAction.kind}`, { defaultValue: reasonDialogAction.kind }) : ""}
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
        open={reasonDialogAction !== null && isDestructiveCommand(reasonDialogAction.kind)}
        riskLevel={reasonDialogAction ? getCommandRiskLevel(reasonDialogAction.kind) : "critical"}
        targetSummary={
          reasonDialogAction
            ? isZh
              ? `目标 ${reasonDialogAction.targetInstanceIds.length} 个实例`
              : `${reasonDialogAction.targetInstanceIds.length} target instance${reasonDialogAction.targetInstanceIds.length === 1 ? "" : "s"}`
            : ""
        }
        title={t("taskCommandControls.dialogTitle", {
          kind: reasonDialogAction ? t(`commandKind.${reasonDialogAction.kind}`, { defaultValue: reasonDialogAction.kind }) : "",
        })}
      />

      <OverflowDialog
        className="detail-dialog-card task-manual-run-dialog"
        description={t("taskCommandControls.manualRun.subtitle", { count: manualRunStates.length })}
        overlayClassName="task-manual-run-dialog-overlay"
        onClose={() => {
          if (mutation.isPending) {
            return;
          }
          setManualRunOpen(false);
        }}
        open={manualRunOpen}
        title={t("taskCommandControls.manualRun.title")}
      >
        <div className="task-manual-run-dialog-body">
          <section className="task-manual-run-dialog-section task-manual-run-dialog-section-targets">
            <div className="task-manual-run-dialog-toolbar">
              <div className="fanout-inline-actions">
                <button
                  className="button-link"
                  onClick={() => setManualRunTargetIds(manualRunStates.map((state) => state.instance_id))}
                  type="button"
                >
                  {t("taskCommandControls.manualRun.selectAll")}
                </button>
                <button className="button-link" onClick={() => setManualRunTargetIds([])} type="button">
                  {t("taskCommandControls.manualRun.clearSelection")}
                </button>
              </div>
            </div>
            <div className="task-manual-instance-list">
              {manualRunStates.map((state) => {
                const checked = manualRunTargetIds.includes(state.instance_id);
                return (
                  <label
                    className={checked ? "task-manual-instance-row active" : "task-manual-instance-row"}
                    key={state.instance_id}
                  >
                    <span className="task-manual-instance-check" aria-hidden="true">
                      {checked ? "✓" : ""}
                    </span>
                    <input checked={checked} onChange={() => toggleManualRunTarget(state.instance_id)} type="checkbox" />
                    <div className="task-manual-instance-copy">
                      <strong>{state.node_name}</strong>
                      <p>{formatIdentifierPreview(state.instance_id)}</p>
                    </div>
                    <div className="task-manual-instance-state">
                      <span>{state.accepting_new_work ? t("taskCommandControls.summary.accepting") : t("taskCommandControls.summary.paused")}</span>
                      <span>{state.runner_count ?? 0}/{state.inflight_task_count ?? 0}</span>
                    </div>
                  </label>
                );
              })}
            </div>
          </section>

          <section className="task-manual-run-dialog-section task-manual-run-dialog-section-payload">
            <label className="dialog-field task-manual-run-field">
              <span>{t("taskCommandControls.manualRun.payloadLabel")}</span>
              <textarea
                className="task-manual-run-payload"
                onChange={(event) => setManualRunPayloadText(event.target.value)}
                placeholder={t("taskCommandControls.manualRun.payloadPlaceholder")}
                rows={12}
                value={manualRunPayloadText}
              />
            </label>
          </section>

          <section className="task-manual-run-dialog-section task-manual-run-dialog-section-reason">
            <label className="dialog-field task-manual-run-field">
              <span>{t("taskCommandControls.manualRun.reasonLabel")}</span>
              <textarea
                className="task-manual-run-reason"
                onChange={(event) => setManualRunReason(event.target.value)}
                placeholder={t("taskCommandControls.manualRun.reasonPlaceholder")}
                rows={3}
                value={manualRunReason}
              />
            </label>
          </section>

          {manualRunError || submitError ? (
            <p className="fanout-note inline-feedback inline-feedback-error">{manualRunError ?? submitError}</p>
          ) : null}

          <div className="dialog-actions task-manual-run-actions">
            <button
              className="button-link"
              disabled={mutation.isPending}
              onClick={() => setManualRunOpen(false)}
              type="button"
            >
              {t("commandReasonDialog.cancel")}
            </button>
            <button
              className="button-secondary"
              disabled={mutation.isPending}
              onClick={() => void handleManualRunSubmit()}
              type="button"
            >
              {mutation.isPending && submittingAction?.kind === "run_task_once"
                ? t("commands.dispatchingAction", {
                    kind: t("commandKind.run_task_once", { defaultValue: "run_task_once" }),
                  })
                : t("commands.action.run_task_once")}
            </button>
          </div>
        </div>
      </OverflowDialog>
    </div>
  );
}

function resolveTaskCommandTimeoutSeconds(kind: TaskCommandKind) {
  if (
    kind === "pause_task" ||
    kind === "discard_dead_letters" ||
    kind === "replay_dead_letters" ||
    kind === "run_task_once"
  ) {
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
  return state.connectivity === "online" && state.state_known && state.supported_commands.includes(kind);
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

function parseManualRunPayload(payloadText: string) {
  let parsed: unknown;
  try {
    parsed = JSON.parse(payloadText);
  } catch {
    throw new Error("Payload must be valid JSON.");
  }
  if (parsed === null || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new Error("Payload must be a JSON object.");
  }
  return parsed as Record<string, unknown>;
}
