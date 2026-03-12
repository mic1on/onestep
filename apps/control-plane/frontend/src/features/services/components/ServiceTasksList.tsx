import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { EmptyState } from "../../../components/ui/EmptyState";
import { StatusBadge } from "../../../components/ui/StatusBadge";
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
        <Link
          key={task.task_name}
          className="list-row"
          to={taskPath(serviceName, task.task_name, {
            environment,
            lookback_minutes: lookbackMinutes,
          })}
        >
          <div className="list-row-copy">
            <strong>{task.task_name}</strong>
            <p className="list-row-description">
              <span className="list-row-label">{t("taskDetail.description")}</span>
              <span>{task.description ?? t("common.notAvailable")}</span>
            </p>
          </div>
          <div className="row-metrics">
            <StatusBadge
              value={task.failed + task.dead_lettered > 0 ? "drift" : "consistent"}
              label={t("metrics.ok", { value: formatCount(task.succeeded) })}
            />
            <span>{t("metrics.fail", { value: formatCount(task.failed + task.dead_lettered) })}</span>
            <span>{t("metrics.p95", { value: formatDurationMs(task.max_p95_duration_ms) })}</span>
          </div>
        </Link>
      ))}
    </div>
  );
}
