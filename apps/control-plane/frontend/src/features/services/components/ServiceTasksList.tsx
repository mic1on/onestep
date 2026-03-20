import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { EmptyState } from "../../../components/ui/EmptyState";
import type {
  Environment,
  TaskCommandKind,
  TaskDashboardSummary,
  TaskInstanceControlState,
} from "../../../lib/api/types";
import { formatCount, formatDurationMs } from "../../../lib/formatters";
import { taskPath } from "../../../lib/routes";
import { CommandReasonDialog } from "../../commands/components/CommandReasonDialog";
import { useCreateTaskCommandFanoutMutation } from "../../commands/queries";
import { useTaskDetailQuery } from "../../tasks/queries";

type ServiceTasksListProps = {
  serviceName: string;
  environment: Environment;
  lookbackMinutes: number;
  tasks: TaskDashboardSummary[];
  limit?: number;
};

export function ServiceTasksList({
  serviceName,
  environment,
  lookbackMinutes,
  tasks,
  limit,
}: ServiceTasksListProps) {
  const { t } = useTranslation();
  const visibleTasks = limit ? tasks.slice(0, limit) : tasks;

  if (visibleTasks.length === 0) {
    return (
      <EmptyState
        title={t("serviceTasksList.emptyTitle")}
        body={t("serviceTasksList.emptyBody")}
      />
    );
  }

  return (
    <div className="stack-list">
      {visibleTasks.map((task) => (
        <ServiceTaskRow
          key={task.task_name}
          environment={environment}
          lookbackMinutes={lookbackMinutes}
          serviceName={serviceName}
          task={task}
        />
      ))}
    </div>
  );
}

type ServiceTaskRowProps = {
  serviceName: string;
  environment: Environment;
  lookbackMinutes: number;
  task: TaskDashboardSummary;
};

type TaskQuickAction = {
  kind: Extract<TaskCommandKind, "pause_task" | "resume_task">;
  targetInstanceIds: string[];
};

function ServiceTaskRow({
  serviceName,
  environment,
  lookbackMinutes,
  task,
}: ServiceTaskRowProps) {
  const { t } = useTranslation();
  const taskDetailQuery = useTaskDetailQuery(serviceName, task.task_name, environment, lookbackMinutes);
  const commandMutation = useCreateTaskCommandFanoutMutation(serviceName, task.task_name, environment);
  const [pendingAction, setPendingAction] = useState<TaskQuickAction | null>(null);
  const quickActions = getTaskQuickActions(taskDetailQuery.data?.task_control.instances ?? []);
  const failureCount = task.failed + task.dead_lettered;

  async function handleConfirm(reason: string) {
    if (!pendingAction) {
      return;
    }

    await commandMutation.mutateAsync({
      kind: pendingAction.kind,
      timeout_s: resolveTaskCommandTimeoutSeconds(pendingAction.kind),
      reason,
      target_mode: "selected_instances",
      target_instance_ids: pendingAction.targetInstanceIds,
    });
    setPendingAction(null);
  }

  return (
    <article className="list-row task-list-row">
      <div className="task-list-row-main">
        <Link
          className="task-list-link"
          to={taskPath(serviceName, task.task_name, {
            environment,
            lookback_minutes: lookbackMinutes,
          })}
        >
          <div className="list-row-copy task-list-copy">
            <div className="task-list-title-row">
              <strong>{task.task_name}</strong>
            </div>
            <p className="list-row-description task-list-description">
              <span>{task.description ?? t("common.notAvailable")}</span>
            </p>
            <div className="task-list-metrics">
              <div className="task-list-metric">
                <span className="task-list-metric-label">{t("taskDetail.succeeded")}</span>
                <strong>{formatCount(task.succeeded)}</strong>
              </div>
              <div className={failureCount > 0 ? "task-list-metric is-danger" : "task-list-metric"}>
                <span className="task-list-metric-label">{t("taskDetail.failedDlq")}</span>
                <strong>{formatCount(failureCount)}</strong>
              </div>
              <div className="task-list-metric">
                <span className="task-list-metric-label">{t("taskDetail.maxP95")}</span>
                <strong>{formatDurationMs(task.max_p95_duration_ms)}</strong>
              </div>
            </div>
          </div>
        </Link>

        {quickActions.length > 0 ? (
          <div className="task-list-actions">
            {quickActions.map((action) => (
              <button
                className={action.kind === "pause_task" ? "button-secondary button-danger" : "button-secondary"}
                disabled={commandMutation.isPending}
                key={action.kind}
                onClick={() => setPendingAction(action)}
                type="button"
              >
                {commandMutation.isPending && pendingAction?.kind === action.kind
                  ? t("commands.dispatchingAction", {
                      kind: t(`commandKind.${action.kind}`, { defaultValue: action.kind }),
                    })
                  : t(`commands.action.${action.kind}`)}
              </button>
            ))}
          </div>
        ) : null}
      </div>

      <CommandReasonDialog
        description={t("taskCommandControls.dialogBody", {
          kind: pendingAction ? t(`commandKind.${pendingAction.kind}`, { defaultValue: pendingAction.kind }) : "",
          taskName: task.task_name,
          count: pendingAction?.targetInstanceIds.length ?? 0,
        })}
        isSubmitting={commandMutation.isPending}
        onCancel={() => {
          if (commandMutation.isPending) {
            return;
          }
          setPendingAction(null);
        }}
        onConfirm={handleConfirm}
        open={pendingAction !== null}
        title={t("taskCommandControls.dialogTitle", {
          kind: pendingAction ? t(`commandKind.${pendingAction.kind}`, { defaultValue: pendingAction.kind }) : "",
        })}
      />
    </article>
  );
}

function getTaskQuickActions(states: TaskInstanceControlState[]) {
  const actionableStates = states.filter(isKnownControllableTaskState);
  const pauseTargets = actionableStates.filter(canPauseTask).map((state) => state.instance_id);
  const resumeTargets = actionableStates.filter(canResumeTask).map((state) => state.instance_id);
  const actions: TaskQuickAction[] = [];

  if (pauseTargets.length > 0) {
    actions.push({ kind: "pause_task", targetInstanceIds: pauseTargets });
  }

  if (resumeTargets.length > 0) {
    actions.push({ kind: "resume_task", targetInstanceIds: resumeTargets });
  }

  return actions;
}

function isKnownControllableTaskState(state: TaskInstanceControlState) {
  return state.connectivity === "online" && state.state_known && state.supported_commands.length > 0;
}

function canPauseTask(state: TaskInstanceControlState) {
  return (
    state.supported_commands.includes("pause_task") &&
    state.paused !== true &&
    state.accepting_new_work === true &&
    state.pause_requested !== true
  );
}

function canResumeTask(state: TaskInstanceControlState) {
  return state.supported_commands.includes("resume_task") && state.paused === true;
}

function resolveTaskCommandTimeoutSeconds(kind: TaskQuickAction["kind"]) {
  return kind === "pause_task" ? 120 : 30;
}
