import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { EmptyState } from "../../../components/ui/EmptyState";
import { StatusBadge } from "../../../components/ui/StatusBadge";
import type { Environment, InstanceSummary } from "../../../lib/api/types";
import { formatDateTime } from "../../../lib/formatters";
import { instancePath } from "../../../lib/routes";

type ServiceInstancesListProps = {
  serviceName: string;
  environment: Environment;
  lookbackMinutes: number;
  instances: InstanceSummary[];
  limit?: number;
};

export function ServiceInstancesList({
  serviceName,
  environment,
  lookbackMinutes,
  instances,
  limit,
}: ServiceInstancesListProps) {
  const { t } = useTranslation();
  const visibleInstances = limit ? instances.slice(0, limit) : instances;

  if (visibleInstances.length === 0) {
    return (
      <EmptyState
        title={t("serviceInstancesList.emptyTitle")}
        body={t("serviceInstancesList.emptyBody")}
      />
    );
  }

  return (
    <div className="stack-list">
      {visibleInstances.map((instance) => (
        <Link
          key={instance.instance_id}
          className="list-row"
          to={instancePath(serviceName, instance.instance_id, {
            environment,
            lookback_minutes: lookbackMinutes,
          })}
        >
          <div>
            <strong>{instance.node_name}</strong>
            <p>
              {t("serviceInstancesList.instanceMeta", {
                instanceId: instance.instance_id,
                lastSync: formatDateTime(instance.last_sync_at),
              })}
            </p>
          </div>
          <div className="row-metrics">
            {instance.active_session ? <StatusBadge value={instance.active_session.status} /> : null}
            <StatusBadge value={instance.connectivity} />
            <StatusBadge value={instance.status} />
          </div>
        </Link>
      ))}
    </div>
  );
}
