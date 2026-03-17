import { Link, useParams, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { EmptyState } from "../../components/ui/EmptyState";
import { PageHeader } from "../../components/ui/PageHeader";
import { Panel } from "../../components/ui/Panel";
import { StatCard } from "../../components/ui/StatCard";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { TaskEventFailureDetails } from "../../components/ui/TaskEventFailureDetails";
import { useInstanceDetailQuery } from "../../features/instances/queries";
import {
  formatCompactJson,
  formatCount,
  formatDateTime,
  formatDurationMs,
  formatIdentifierPreview,
  formatRelativeTime,
} from "../../lib/formatters";
import { createSearch, parseEnvironment, parseLookback } from "../../lib/params";

export function InstanceDetailPage() {
  const { t } = useTranslation();
  const { serviceName, instanceId } = useParams<{ serviceName: string; instanceId: string }>();
  const [searchParams] = useSearchParams();

  if (!serviceName || !instanceId) {
    return <EmptyState title={t("instanceDetail.missingTitle")} body={t("instanceDetail.missingBody")} />;
  }

  const environment = parseEnvironment(searchParams);
  const lookbackMinutes = parseLookback(searchParams, 60);
  const query = useInstanceDetailQuery(serviceName, instanceId, environment, lookbackMinutes);

  if (query.error) {
    return <EmptyState title={t("instanceDetail.loadErrorTitle")} body={String(query.error)} />;
  }

  const payload = query.data;
  const instance = payload?.instance;

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow={t("instanceDetail.eyebrow", { environment: t(`environment.${environment}`) })}
        title={instance?.node_name ?? instanceId}
        subtitle={
          <span>
            <Link
              className="inline-link"
              to={`/services/${serviceName}${createSearch({
                environment,
                lookback_minutes: lookbackMinutes,
              })}`}
            >
              {serviceName}
            </Link>{" "}
            / {t("instanceDetail.subtitleSuffix", { lookbackMinutes })}
          </span>
        }
      />

      {query.isPending ? <div className="loading-block hero-block">{t("instanceDetail.loading")}</div> : null}

      {instance ? (
        <>
          <section className="stats-grid">
            <StatCard
              label={t("instanceDetail.connectivity")}
              value={t(`status.${instance.connectivity}`)}
              tone={instance.connectivity === "online" ? "success" : "warning"}
            />
            <StatCard
              label={t("instanceDetail.health")}
              value={t(`status.${instance.status}`, { defaultValue: instance.status })}
              tone={instance.status === "ok" ? "success" : "warning"}
            />
            <StatCard label={t("instanceDetail.lastSeen")} value={formatRelativeTime(instance.last_seen_at)} />
            <StatCard label={t("instanceDetail.lastSync")} value={formatDateTime(instance.last_sync_at)} />
          </section>

          <div className="two-column-grid">
            <Panel title={t("instanceDetail.runtimeSnapshotTitle")} subtitle={t("instanceDetail.runtimeSnapshotSubtitle")}>
              <div className="badge-row">
                <StatusBadge value={instance.connectivity} />
                <StatusBadge value={instance.status} />
              </div>
              <dl className="definition-grid">
                <div>
                  <dt>{t("instanceDetail.instanceId")}</dt>
                  <dd>{instance.instance_id}</dd>
                </div>
                <div>
                  <dt>{t("instanceDetail.host")}</dt>
                  <dd>{instance.hostname ?? t("common.notAvailable")}</dd>
                </div>
                <div>
                  <dt>{t("instanceDetail.pid")}</dt>
                  <dd>{instance.pid ?? t("common.notAvailable")}</dd>
                </div>
                <div>
                  <dt>{t("instanceDetail.deployment")}</dt>
                  <dd>{instance.deployment_version}</dd>
                </div>
                <div>
                  <dt>{t("instanceDetail.onestep")}</dt>
                  <dd>{instance.onestep_version ?? t("common.notAvailable")}</dd>
                </div>
                <div>
                  <dt>{t("instanceDetail.python")}</dt>
                  <dd>{instance.python_version ?? t("common.notAvailable")}</dd>
                </div>
                <div>
                  <dt>{t("instanceDetail.startedAt")}</dt>
                  <dd>{formatDateTime(instance.started_at)}</dd>
                </div>
                <div>
                  <dt>{t("instanceDetail.topologyHash")}</dt>
                  <dd title={instance.last_topology_hash ?? t("common.notAvailable")}>
                    {formatIdentifierPreview(instance.last_topology_hash)}
                  </dd>
                </div>
              </dl>
            </Panel>

            <Panel title={t("instanceDetail.appSnapshotTitle")} subtitle={t("instanceDetail.appSnapshotSubtitle")}>
              <pre className="json-block">{formatCompactJson(payload?.app_snapshot)}</pre>
            </Panel>
          </div>

          <div className="two-column-grid">
            <Panel title={t("instanceDetail.recentWindowsTitle")} subtitle={t("instanceDetail.recentWindowsSubtitle")}>
              {payload?.recent_metric_windows.length ? (
                <div className="stack-list">
                  {payload.recent_metric_windows.map((window) => (
                    <article className="event-row" key={window.window_id}>
                      <div>
                        <strong>{window.task_name}</strong>
                        <p>{window.window_id}</p>
                      </div>
                      <div className="row-metrics">
                        <span>{t("metrics.ok", { value: formatCount(window.succeeded) })}</span>
                        <span>{t("metrics.fail", { value: formatCount(window.failed) })}</span>
                        <span>{t("metrics.p95", { value: formatDurationMs(window.p95_duration_ms) })}</span>
                      </div>
                    </article>
                  ))}
                </div>
              ) : (
                <EmptyState title={t("instanceDetail.noWindowsTitle")} body={t("instanceDetail.noWindowsBody")} />
              )}
            </Panel>

            <Panel title={t("instanceDetail.recentEventsTitle")} subtitle={t("instanceDetail.recentEventsSubtitle")}>
              {payload?.recent_events.length ? (
                <div className="stack-list">
                  {payload.recent_events.map((event) => (
                    <article className="event-row" key={event.event_id}>
                      <div>
                        <strong>{event.task_name}</strong>
                        <p>
                          {t(`eventKind.${event.kind}`, { defaultValue: event.kind })} · {formatDateTime(event.occurred_at)}
                        </p>
                      </div>
                      <TaskEventFailureDetails event={event} />
                    </article>
                  ))}
                </div>
              ) : (
                <EmptyState title={t("instanceDetail.noEventsTitle")} body={t("instanceDetail.noEventsBody")} />
              )}
            </Panel>
          </div>
        </>
      ) : null}
    </div>
  );
}
