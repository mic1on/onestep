import { Link, useParams, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { EmptyState } from "../../components/ui/EmptyState";
import { PageHeader } from "../../components/ui/PageHeader";
import { Panel } from "../../components/ui/Panel";
import { StatCard } from "../../components/ui/StatCard";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { useTaskDetailQuery } from "../../features/tasks/queries";
import {
  formatCompactJson,
  formatCount,
  formatDateTime,
  formatDurationMs,
  formatIdentifierPreview,
} from "../../lib/formatters";
import { createSearch, parseEnvironment, parseLookback } from "../../lib/params";

export function TaskDetailPage() {
  const { t } = useTranslation();
  const { serviceName, taskName } = useParams<{ serviceName: string; taskName: string }>();
  const [searchParams] = useSearchParams();

  if (!serviceName || !taskName) {
    return <EmptyState title={t("taskDetail.missingTitle")} body={t("taskDetail.missingBody")} />;
  }

  const environment = parseEnvironment(searchParams);
  const lookbackMinutes = parseLookback(searchParams, 60);
  const query = useTaskDetailQuery(serviceName, taskName, environment, lookbackMinutes);

  if (query.error) {
    return <EmptyState title={t("taskDetail.loadErrorTitle")} body={String(query.error)} />;
  }

  const payload = query.data;
  const summary = payload?.summary;

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow={t("taskDetail.eyebrow", { environment: t(`environment.${environment}`) })}
        title={taskName}
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
            / {t("taskDetail.subtitleSuffix", { lookbackMinutes })}
          </span>
        }
      />

      {query.isPending ? <div className="loading-block hero-block">{t("taskDetail.loading")}</div> : null}

      {summary ? (
        <>
          <section className="stats-grid">
            <StatCard label={t("taskDetail.succeeded")} value={formatCount(summary.succeeded)} tone="success" />
            <StatCard
              label={t("taskDetail.failedDlq")}
              value={formatCount(summary.failed + summary.dead_lettered)}
              tone={summary.failed + summary.dead_lettered > 0 ? "danger" : "default"}
            />
            <StatCard
              label={t("taskDetail.retried")}
              value={formatCount(summary.retried)}
              hint={t("taskDetail.retriedHint")}
            />
            <StatCard label={t("taskDetail.maxP95")} value={formatDurationMs(summary.max_p95_duration_ms)} />
          </section>

          <div className="two-column-grid">
            <Panel title={t("taskDetail.configurationTitle")} subtitle={t("taskDetail.configurationSubtitle")}>
              <dl className="definition-grid">
                <div>
                  <dt>{t("taskDetail.description")}</dt>
                  <dd>{summary.description ?? t("common.notAvailable")}</dd>
                </div>
                <div>
                  <dt>{t("taskDetail.source")}</dt>
                  <dd>{summary.source_name ?? t("common.notAvailable")}</dd>
                </div>
                <div>
                  <dt>{t("taskDetail.sourceKind")}</dt>
                  <dd>{summary.source_kind ?? t("common.notAvailable")}</dd>
                </div>
                <div>
                  <dt>{t("taskDetail.concurrency")}</dt>
                  <dd>{summary.concurrency ?? t("common.notAvailable")}</dd>
                </div>
                <div>
                  <dt>{t("taskDetail.timeout")}</dt>
                  <dd>{summary.timeout_s ? `${summary.timeout_s}s` : t("common.none")}</dd>
                </div>
                <div>
                  <dt>{t("taskDetail.topologyHash")}</dt>
                  <dd title={summary.topology_hash ?? t("common.notAvailable")}>
                    {formatIdentifierPreview(summary.topology_hash)}
                  </dd>
                </div>
                <div>
                  <dt>{t("taskDetail.lastEvent")}</dt>
                  <dd>{formatDateTime(summary.last_event_at)}</dd>
                </div>
              </dl>
              <div className="json-stack">
                <div>
                  <p className="json-label">{t("taskDetail.emit")}</p>
                  <pre className="json-block">{formatCompactJson(summary.emit)}</pre>
                </div>
                <div>
                  <p className="json-label">{t("taskDetail.retryPolicy")}</p>
                  <pre className="json-block">{formatCompactJson(summary.retry_policy)}</pre>
                </div>
                <div>
                  <p className="json-label">{t("taskDetail.sourceConfig")}</p>
                  <pre className="json-block">{formatCompactJson(summary.source_config)}</pre>
                </div>
              </div>
            </Panel>

            <Panel title={t("taskDetail.eventProfileTitle")} subtitle={t("taskDetail.eventProfileSubtitle")}>
              <div className="badge-row">
                <StatusBadge
                  value={summary.failed > 0 ? "drift" : "consistent"}
                  label={t("taskDetail.failedBadge", { count: summary.event_counts.failed })}
                />
                <StatusBadge value="starting" label={t("taskDetail.retriedBadge", { count: summary.event_counts.retried })} />
                <StatusBadge
                  value="consistent"
                  label={t("taskDetail.succeededBadge", { count: summary.event_counts.succeeded })}
                />
              </div>
              <dl className="definition-grid">
                <div>
                  <dt>{t("taskDetail.cancelled")}</dt>
                  <dd>{summary.event_counts.cancelled}</dd>
                </div>
                <div>
                  <dt>{t("taskDetail.succeededEvents")}</dt>
                  <dd>{summary.event_counts.succeeded}</dd>
                </div>
                <div>
                  <dt>{t("taskDetail.windowCount")}</dt>
                  <dd>{summary.metric_window_count}</dd>
                </div>
                <div>
                  <dt>{t("taskDetail.weightedAvg")}</dt>
                  <dd>{formatDurationMs(summary.weighted_avg_duration_ms)}</dd>
                </div>
              </dl>
            </Panel>
          </div>

          <div className="two-column-grid">
            <Panel title={t("taskDetail.recentWindowsTitle")} subtitle={t("taskDetail.recentWindowsSubtitle")}>
              {payload?.recent_metric_windows.length ? (
                <div className="stack-list">
                  {payload.recent_metric_windows.map((window) => (
                    <article className="event-row" key={window.window_id}>
                      <div>
                        <strong>{window.window_id}</strong>
                        <p>
                          {t("taskDetail.windowRange", {
                            start: formatDateTime(window.window_started_at),
                            end: formatDateTime(window.window_ended_at),
                          })}
                        </p>
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
                <EmptyState title={t("taskDetail.noWindowsTitle")} body={t("taskDetail.noWindowsBody")} />
              )}
            </Panel>

            <Panel title={t("taskDetail.recentEventsTitle")} subtitle={t("taskDetail.recentEventsSubtitle")}>
              {payload?.recent_events.length ? (
                <div className="stack-list">
                  {payload.recent_events.map((event) => (
                    <article className="event-row" key={event.event_id}>
                      <div>
                        <strong>{t(`eventKind.${event.kind}`, { defaultValue: event.kind })}</strong>
                        <p>{formatDateTime(event.occurred_at)}</p>
                      </div>
                      <div className="row-metrics">
                        <span>{event.failure_kind ?? t("common.taskEvent")}</span>
                        <span>{event.message ?? t("common.noMessage")}</span>
                      </div>
                    </article>
                  ))}
                </div>
              ) : (
                <EmptyState title={t("taskDetail.noEventsTitle")} body={t("taskDetail.noEventsBody")} />
              )}
            </Panel>
          </div>
        </>
      ) : null}
    </div>
  );
}
