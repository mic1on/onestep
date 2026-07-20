import { useMemo, useState } from 'react';
import { Search, Server, ChevronRight, Activity, CheckCircle, AlertCircle, SlidersHorizontal, Check } from 'lucide-react';
import { Service } from '../types';
import { formatThroughput } from '../api';
import { useI18n } from '../i18n';

interface ServicesListProps {
  services: Service[];
  onSelectService: (serviceId: string) => void;
}

interface ServiceStatusMeta {
  label: string;
  dotClassName: string;
  badgeClassName: string;
  Icon: typeof CheckCircle;
}

type ServiceDisplayStatus = 'running' | 'degraded' | 'offline';
type ServiceStatusFilter = 'all' | ServiceDisplayStatus;

function getServiceDisplayStatus(service: Service): ServiceDisplayStatus {
  if (service.viewStatus === 'running' && service.activeInstances > 0) return 'running';
  if (service.viewStatus === 'degraded') return 'degraded';
  return 'offline';
}

function getStatusMeta(service: Service, t: ReturnType<typeof useI18n>['t']): ServiceStatusMeta {
  // The plane computes view_status; the frontend only maps it to a label/style.
  const displayStatus = getServiceDisplayStatus(service);
  if (displayStatus === 'running') {
    return {
      label: t('common.running'),
      dotClassName: 'bg-emerald-500',
      badgeClassName: 'bg-emerald-50 text-emerald-700 border-emerald-200',
      Icon: CheckCircle,
    };
  }
  if (displayStatus === 'degraded') {
    return {
      label: t('common.degraded'),
      dotClassName: 'bg-amber-500',
      badgeClassName: 'bg-amber-50 text-amber-700 border-amber-200',
      Icon: AlertCircle,
    };
  }
  return {
    label: t('common.offline'),
    dotClassName: 'bg-slate-400',
    badgeClassName: 'bg-slate-100 text-slate-600 border-slate-200',
    Icon: Server,
  };
}

function getStatusFilterLabel(status: ServiceStatusFilter, t: ReturnType<typeof useI18n>['t']) {
  if (status === 'running') return t('common.running');
  if (status === 'degraded') return t('common.degraded');
  if (status === 'offline') return t('common.offline');
  return t('status.all');
}

export default function ServicesList({ services, onSelectService }: ServicesListProps) {
  const { t } = useI18n();
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<ServiceStatusFilter>('all');
  const [isStatusFilterOpen, setIsStatusFilterOpen] = useState(false);

  const filteredServices = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return services.filter((svc) => {
      const description = svc.description?.toLowerCase() ?? '';
      const matchesSearch =
        !query ||
        svc.name.toLowerCase().includes(query) ||
        svc.id.toLowerCase().includes(query) ||
        description.includes(query);
      const matchesStatus =
        statusFilter === 'all' || getServiceDisplayStatus(svc) === statusFilter;
      return matchesSearch && matchesStatus;
    });
  }, [services, searchQuery, statusFilter]);

  const statusFilters: ServiceStatusFilter[] = ['all', 'running', 'degraded', 'offline'];

  return (
    <div className="max-w-7xl mx-auto space-y-6 animate-fadeIn">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h2 className="text-2xl font-extrabold text-slate-900 tracking-tight font-sans">
            {t('servicesList.title')}
          </h2>
          <p className="text-sm text-slate-500 font-medium mt-0.5">
            {t('servicesList.subtitle', { count: services.length })}
          </p>
        </div>
      </div>

      {/* Search bar */}
      <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-xs">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <div className="relative w-full max-w-sm">
            <Search className="w-4 h-4 text-slate-400 absolute left-3 top-3" />
            <input
              type="text"
              placeholder={t('servicesList.filterPlaceholder')}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full bg-white border border-slate-200 rounded-lg pl-9 pr-4 py-2 text-xs focus:outline-hidden focus:ring-1 focus:ring-indigo-600 focus:border-indigo-600 font-medium"
            />
          </div>

          <div className="relative shrink-0">
            <button
              type="button"
              aria-haspopup="true"
              aria-expanded={isStatusFilterOpen}
              onClick={() => setIsStatusFilterOpen((isOpen) => !isOpen)}
              className="flex min-h-9 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-600 transition-colors hover:bg-slate-50 focus:outline-hidden focus:ring-1 focus:ring-indigo-600 focus:border-indigo-600"
            >
              <SlidersHorizontal className="w-3.5 h-3.5" />
              <span>
                {t('servicesList.statusFilter', {
                  status: getStatusFilterLabel(statusFilter, t),
                })}
              </span>
            </button>

            {isStatusFilterOpen && (
              <div className="absolute right-0 z-30 mt-1 w-40 rounded-lg border border-slate-200 bg-white py-1 text-xs font-medium shadow-lg">
                {statusFilters.map((filter) => (
                  <button
                    key={filter}
                    type="button"
                    onClick={() => {
                      setStatusFilter(filter);
                      setIsStatusFilterOpen(false);
                    }}
                    className="flex w-full items-center justify-between px-3 py-2 text-left text-slate-700 transition-colors hover:bg-slate-50 focus:outline-hidden focus:bg-slate-50"
                  >
                    <span>{getStatusFilterLabel(filter, t)}</span>
                    {statusFilter === filter && <Check className="w-3.5 h-3.5 text-indigo-600" />}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Services table */}
      <div className="bg-white border border-slate-200 rounded-xl overflow-hidden shadow-xs">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="bg-slate-50 border-b border-slate-200 text-slate-500 font-bold uppercase tracking-wider">
                <th className="p-4 font-bold">{t('servicesList.columnService')}</th>
                <th className="p-4 font-bold">{t('common.status')}</th>
                <th className="p-4 font-bold">{t('common.throughput')}</th>
                <th className="p-4 font-bold">{t('servicesList.columnTasks')}</th>
                <th className="p-4 font-bold">{t('common.instances')}</th>
                <th className="p-4 font-bold">{t('common.health')}</th>
                <th className="p-4 font-bold text-right">{t('servicesList.columnActions')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {filteredServices.length === 0 ? (
                <tr>
                  <td colSpan={7} className="p-8 text-center text-slate-400 font-medium">
                    {t('servicesList.empty')}
                  </td>
                </tr>
              ) : (
                filteredServices.map((svc) => {
                  const status = getStatusMeta(svc, t);
                  const inactiveInstances = Math.max(svc.totalInstances - svc.activeInstances, 0);
                  return (
                    <tr
                      key={svc.id}
                      onClick={() => onSelectService(svc.id)}
                      className="hover:bg-slate-50/55 transition-colors font-medium text-slate-700 cursor-pointer group"
                    >
                      <td className="p-4">
                        <div className="flex items-center gap-3">
                          <div className="w-9 h-9 rounded-lg bg-indigo-50 flex items-center justify-center text-indigo-600 shrink-0">
                            <Server className="w-4 h-4" />
                          </div>
                          <div className="min-w-0">
                            <div className="font-bold text-slate-900 truncate">{svc.name}</div>
                            <div className="text-[11px] text-slate-400 font-mono truncate">{svc.id}</div>
                            {svc.description && (
                              <div className="mt-0.5 max-w-sm truncate text-[11px] font-medium text-slate-500">
                                {svc.description}
                              </div>
                            )}
                          </div>
                        </div>
                      </td>
                      <td className="p-4">
                        <span
                          className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md font-bold uppercase tracking-wider text-[10px] border ${status.badgeClassName}`}
                        >
                          <span className={`w-1.5 h-1.5 rounded-full ${status.dotClassName}`} />
                          {status.label}
                        </span>
                      </td>
                      <td className="p-4 font-semibold text-slate-700">{formatThroughput(svc.throughputPerMin)}</td>
                      <td className="p-4">
                        <div className="flex items-center gap-2">
                          <span
                            className={`font-bold ${
                              svc.failingTaskCount > 0 ? 'text-amber-600' : 'text-slate-900'
                            }`}
                          >
                            {svc.onlineTaskCount}
                          </span>
                          <span className="text-slate-400">/ {svc.totalTaskCount}</span>
                          {svc.failingTaskCount > 0 && (
                            <span className="text-[10px] font-semibold text-amber-600">
                              ({svc.failingTaskCount} {t('servicesList.failing')})
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="p-4">
                        <div className="flex items-center gap-2">
                          <span className="font-bold text-slate-900">{svc.activeInstances}</span>
                          <span className="text-slate-400">/ {svc.totalInstances}</span>
                          {inactiveInstances > 0 && (
                            <span className="text-[10px] text-slate-400 font-semibold">
                              ({inactiveInstances} {t('telemetry.inactive')})
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="p-4">
                        <div className="flex items-center gap-2">
                          <Activity className="w-3.5 h-3.5 text-slate-400" />
                          <span
                            className={`font-bold ${
                              svc.successRate >= 99
                                ? 'text-emerald-600'
                                : svc.successRate > 0
                                ? 'text-amber-600'
                                : 'text-slate-400'
                            }`}
                          >
                            {svc.successRate}%
                          </span>
                        </div>
                      </td>
                      <td className="p-4 text-right">
                        <span className="inline-flex items-center gap-1 text-indigo-600 font-bold text-[11px] group-hover:gap-1.5 transition-all">
                          {t('button.viewTaskDetails')}
                          <ChevronRight className="w-3.5 h-3.5" />
                        </span>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
