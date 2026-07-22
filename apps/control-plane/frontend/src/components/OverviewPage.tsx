import { useEffect, useState, useCallback } from 'react';
import {
  Server,
  Boxes,
  ListChecks,
  Plug,
  CheckCircle,
  AlertCircle,
  ChevronRight,
  RefreshCw,
  Circle,
} from 'lucide-react';
import { Service } from '../types';
import {
  isAuthRequiredError,
  type ServiceSummaryStats,
  type RecentEvent,
  type Environment,
  loadRecentEvents,
} from '../api';
import { useI18n } from '../i18n';

interface OverviewPageProps {
  services: Service[];
  serviceSummary: ServiceSummaryStats;
  sourceKindCounts: Record<string, number>;
  environment: Environment | undefined;
  onSelectService: (serviceId: string) => void;
  onAuthRequired: () => void;
}

const EVENT_POLL_INTERVAL_MS = 10_000;

interface EventKindMeta {
  label: string;
  badgeClassName: string;
  dotClassName: string;
}

function getEventKindMeta(kind: string, t: ReturnType<typeof useI18n>['t']): EventKindMeta {
  let label: string;
  switch (kind) {
    case 'failed':
      label = t('event.failed');
      break;
    case 'dead_lettered':
      label = t('event.dead_lettered');
      break;
    case 'retried':
      label = t('event.retried');
      break;
    case 'succeeded':
      label = t('event.succeeded');
      break;
    case 'started':
      label = t('event.started');
      break;
    case 'cancelled':
      label = t('event.cancelled');
      break;
    default:
      label = kind;
  }
  if (kind === 'failed' || kind === 'dead_lettered') {
    return {
      label,
      badgeClassName: 'bg-rose-50 text-rose-700 border-rose-200',
      dotClassName: 'bg-rose-500',
    };
  }
  if (kind === 'retried') {
    return {
      label,
      badgeClassName: 'bg-amber-50 text-amber-700 border-amber-200',
      dotClassName: 'bg-amber-500',
    };
  }
  if (kind === 'succeeded') {
    return {
      label,
      badgeClassName: 'bg-emerald-50 text-emerald-700 border-emerald-200',
      dotClassName: 'bg-emerald-500',
    };
  }
  if (kind === 'started') {
    return {
      label,
      badgeClassName: 'bg-indigo-50 text-indigo-700 border-indigo-200',
      dotClassName: 'bg-indigo-500',
    };
  }
  return {
    label,
    badgeClassName: 'bg-slate-100 text-slate-600 border-slate-200',
    dotClassName: 'bg-slate-400',
  };
}

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const diffMs = Date.now() - then;
  const sec = Math.floor(diffMs / 1000);
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h`;
  const day = Math.floor(hr / 24);
  return `${day}d`;
}

export default function OverviewPage({
  services,
  serviceSummary,
  sourceKindCounts,
  environment,
  onSelectService,
  onAuthRequired,
}: OverviewPageProps) {
  const { t } = useI18n();

  const [events, setEvents] = useState<RecentEvent[]>([]);
  const [isLoadingEvents, setIsLoadingEvents] = useState(false);
  const [eventsError, setEventsError] = useState<string | null>(null);

  const refreshEvents = useCallback(async () => {
    setIsLoadingEvents(true);
    setEventsError(null);
    try {
      const items = await loadRecentEvents(environment, 20);
      setEvents(items);
    } catch (error) {
      if (isAuthRequiredError(error)) {
        onAuthRequired();
        return;
      }
      setEventsError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsLoadingEvents(false);
    }
  }, [environment, onAuthRequired]);

  useEffect(() => {
    void refreshEvents();
    const interval = setInterval(() => {
      void refreshEvents();
    }, EVENT_POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [refreshEvents]);

  const onlineServicePct =
    serviceSummary.total_services > 0
      ? Math.round((serviceSummary.online_services / serviceSummary.total_services) * 100)
      : 0;
  const onlineInstancePct =
    serviceSummary.total_instances > 0
      ? Math.round((serviceSummary.online_instances / serviceSummary.total_instances) * 100)
      : 0;
  const onlineTaskCount = Math.max(serviceSummary.total_tasks - serviceSummary.failing_tasks, 0);
  const sourceKinds = Object.entries(sourceKindCounts).sort((a, b) => b[1] - a[1]);

  return (
    <div className="ui-page-enter mx-auto max-w-7xl space-y-6">
      {/* Header */}
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-3xl font-bold text-slate-900 tracking-tight font-sans">
            {t('nav.globalOverview')}
          </h2>
          <p className="text-sm text-slate-500 font-medium">{t('overview.subtitle')}</p>
        </div>
        <button
          onClick={() => void refreshEvents()}
          disabled={isLoadingEvents}
          className="flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] font-bold text-slate-600 transition-colors hover:bg-slate-100 disabled:opacity-50"
        >
          <RefreshCw className={`h-3 w-3 ${isLoadingEvents ? 'animate-spin' : ''}`} />
          <span>{t('overview.refreshActivity')}</span>
        </button>
      </div>

      {/* Stats bento cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5">
        {/* Services */}
        <StatCard
          icon={<Server className="w-4 h-4 text-indigo-600" />}
          label={t('overview.totalServices')}
          value={serviceSummary.total_services}
          footer={
            <Footnote
              ok={serviceSummary.online_services}
              warn={serviceSummary.attention_services}
              bad={serviceSummary.offline_services}
              okLabel={t('overview.online')}
              warnLabel={t('overview.attention')}
              badLabel={t('overview.offline')}
              pct={onlineServicePct}
            />
          }
        />
        {/* Instances */}
        <StatCard
          icon={<Boxes className="w-4 h-4 text-indigo-600" />}
          label={t('overview.totalInstances')}
          value={serviceSummary.total_instances}
          footer={
            <Footnote
              ok={serviceSummary.online_instances}
              bad={serviceSummary.total_instances - serviceSummary.online_instances}
              okLabel={t('overview.online')}
              badLabel={t('overview.offline')}
              pct={onlineInstancePct}
            />
          }
        />
        {/* Tasks */}
        <StatCard
          icon={<ListChecks className="w-4 h-4 text-indigo-600" />}
          label={t('overview.totalTasks')}
          value={serviceSummary.total_tasks}
          footer={
            serviceSummary.failing_tasks > 0 ? (
              <span className="flex items-center gap-1.5 text-rose-600 font-semibold">
                <AlertCircle className="w-3 h-3" />
                {t('overview.failingTasks', { count: serviceSummary.failing_tasks })}
              </span>
            ) : (
              <span className="flex items-center gap-1.5 text-emerald-600 font-semibold">
                <CheckCircle className="w-3 h-3" />
                {t('overview.allHealthy')}
              </span>
            )
          }
          subValue={`${onlineTaskCount}/${serviceSummary.total_tasks}`}
        />
        {/* Source connectors */}
        <StatCard
          icon={<Plug className="w-4 h-4 text-indigo-600" />}
          label={t('overview.sourceConnectors')}
          value={sourceKinds.length}
          footer={
            sourceKinds.length > 0 ? (
              <div className="flex flex-wrap gap-1">
                {sourceKinds.slice(0, 4).map(([kind, count]) => (
                  <span
                    key={kind}
                    className="text-[10px] font-bold bg-slate-100 text-slate-600 px-1.5 py-0.5 rounded"
                  >
                    {kind} ×{count}
                  </span>
                ))}
              </div>
            ) : (
              <span className="text-[11px] text-slate-400 font-medium">{t('overview.noConnectors')}</span>
            )
          }
        />
      </div>

      {/* Two-column: services list + activity feed */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left: services list */}
        <div className="lg:col-span-2 bg-white border border-slate-200 rounded-xl p-6 shadow-xs">
          <h3 className="font-bold text-slate-900 text-sm mb-4">{t('overview.registry')}</h3>
          <div className="space-y-2">
            {services.length === 0 ? (
              <p className="text-xs text-slate-400 font-medium py-6 text-center">
                {t('overview.noServices')}
              </p>
            ) : (
              services.map((svc) => {
                const status = getServiceStatusMeta(svc);
                return (
                  <div
                    key={svc.id}
                    onClick={() => onSelectService(svc.id)}
                    className="flex items-center justify-between p-3.5 hover:bg-slate-50 rounded-lg cursor-pointer border border-transparent hover:border-slate-200 transition-all group"
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <span className={`w-2 h-2 rounded-full shrink-0 ${status.dotClassName}`} />
                      <div className="min-w-0">
                        <div className="flex items-center gap-1.5">
                          <h4 className="font-bold text-slate-800 text-sm truncate">{svc.name}</h4>
                          <span className="text-[11px] text-slate-400 font-mono shrink-0">
                            / {svc.environment}
                          </span>
                        </div>
                        <span className="text-[11px] text-slate-400 font-mono">{svc.id}</span>
                      </div>
                    </div>
                    <div className="flex gap-6 items-center text-xs font-bold text-slate-600 shrink-0">
                      <div className="text-center">
                        <span className="text-[10px] text-slate-400 block mb-0.5">{t('overview.tasks')}</span>
                        <span>{svc.onlineTaskCount}/{svc.totalTaskCount}</span>
                      </div>
                      <div className="text-center">
                        <span className="text-[10px] text-slate-400 block mb-0.5">{t('common.instances')}</span>
                        <span>{svc.activeInstances}/{svc.totalInstances}</span>
                      </div>
                      <ChevronRight className="w-4 h-4 text-slate-300 group-hover:text-indigo-600 transition-colors" />
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* Right: activity feed */}
        <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-xs flex flex-col">
          <div className="flex items-center justify-between border-b border-slate-100 pb-3 mb-3">
            <h3 className="font-bold text-slate-900 text-sm">{t('overview.recentActivity')}</h3>
            <span className="flex items-center gap-1 text-[10px] font-bold text-slate-400">
              <Circle className="w-1.5 h-1.5 fill-emerald-500 text-emerald-500 animate-pulse" />
              {t('overview.live')}
            </span>
          </div>
          <div className="space-y-1 overflow-y-auto max-h-[480px]">
            {eventsError ? (
              <p className="text-xs text-rose-500 font-medium py-6 text-center">{eventsError}</p>
            ) : events.length === 0 ? (
              <p className="text-xs text-slate-400 font-medium py-6 text-center">
                {t('overview.noActivity')}
              </p>
            ) : (
              events.map((event) => {
                const meta = getEventKindMeta(event.kind, t);
                return (
                  <div
                    key={event.event_id}
                    className="flex gap-2.5 p-2 rounded-md hover:bg-slate-50 transition-colors"
                  >
                    <span className={`w-1.5 h-1.5 rounded-full mt-1.5 shrink-0 ${meta.dotClassName}`} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <span
                          className={`inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wide border ${meta.badgeClassName}`}
                        >
                          {meta.label}
                        </span>
                        <span className="text-xs font-bold text-slate-800 truncate">
                          {event.service_name}
                        </span>
                        <span className="text-[10px] text-slate-400 font-mono">/{event.environment}</span>
                      </div>
                      <p className="text-[11px] text-slate-600 font-medium mt-0.5 truncate">
                        {event.task_name}
                        {event.message ? ` · ${event.message}` : ''}
                      </p>
                      <span className="text-[10px] text-slate-400 font-medium">
                        {relativeTime(event.occurred_at)} {t('overview.ago')}
                      </span>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

interface StatCardProps {
  icon: React.ReactNode;
  label: string;
  value: number;
  subValue?: string;
  footer: React.ReactNode;
}

function StatCard({ icon, label, value, subValue, footer }: StatCardProps) {
  return (
    <div className="bg-white border border-slate-200 rounded-xl p-4 flex flex-col justify-between h-32 shadow-xs">
      <div className="flex justify-between items-start">
        <span className="text-[10px] text-slate-400 uppercase font-bold tracking-wider">{label}</span>
        <div className="w-7 h-7 rounded-lg bg-indigo-50 flex items-center justify-center">{icon}</div>
      </div>
      <div>
        <div className="flex items-baseline gap-2">
          <span className="text-2xl font-extrabold text-slate-900 font-sans tracking-tight">{value}</span>
          {subValue && <span className="text-xs text-slate-400 font-medium">{subValue}</span>}
        </div>
      </div>
      <div className="text-[11px] font-semibold">{footer}</div>
    </div>
  );
}

interface FootnoteProps {
  ok: number;
  warn?: number;
  bad?: number;
  okLabel: string;
  warnLabel?: string;
  badLabel?: string;
  pct: number;
}

function Footnote({ ok, warn = 0, bad = 0, okLabel, warnLabel, badLabel, pct }: FootnoteProps) {
  return (
    <div className="space-y-1.5">
      <div className="w-full h-1 bg-slate-100 rounded-full overflow-hidden">
        <div className="h-full bg-emerald-500 rounded-full transition-all" style={{ width: `${pct}%` }} />
      </div>
      <div className="flex items-center gap-2 text-slate-500">
        <span className="text-emerald-600">{okLabel} {ok}</span>
        {warnLabel && warn > 0 && <span className="text-amber-600">{warnLabel} {warn}</span>}
        {badLabel && bad > 0 && <span className="text-slate-500">{badLabel} {bad}</span>}
      </div>
    </div>
  );
}

interface ServiceStatusMeta {
  dotClassName: string;
}

function getServiceStatusMeta(service: Service): ServiceStatusMeta {
  if (service.viewStatus === 'running' && service.activeInstances > 0) {
    return { dotClassName: 'bg-emerald-500' };
  }
  if (service.viewStatus === 'degraded') {
    return { dotClassName: 'bg-amber-500' };
  }
  return { dotClassName: 'bg-slate-400' };
}
