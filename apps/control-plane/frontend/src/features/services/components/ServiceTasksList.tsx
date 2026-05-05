import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { EmptyState } from "../../../components/ui/EmptyState";
import type { Environment, TaskDashboardSummary } from "../../../lib/api/types";
import { formatCount, formatDurationMs } from "../../../lib/formatters";
import { taskPath } from "../../../lib/routes";

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

function ServiceTaskRow({
  serviceName,
  environment,
  lookbackMinutes,
  task,
}: ServiceTaskRowProps) {
  const { t } = useTranslation();
  const failureCount = task.failed + task.dead_lettered;

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
      </div>
    </article>
  );
}
