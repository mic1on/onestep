import { Server, Activity, TrendingUp } from 'lucide-react';
import { type CSSProperties } from 'react';
import { Service } from '../types';
import { useI18n } from '../i18n';

interface TelemetryStatsProps {
  service: Service;
}

export default function TelemetryStats({ service }: TelemetryStatsProps) {
  const { t } = useI18n();
  const activePercent = service.totalInstances > 0 ? (service.activeInstances / service.totalInstances) * 100 : 0;
  const isOffline = service.viewStatus === 'stopped' || service.activeInstances === 0;
  const isHealthy = !isOffline && service.successRate >= 99;
  const inactiveInstances = Math.max(service.totalInstances - service.activeInstances, 0);
  const hasTraffic = !isOffline && service.throughputPerMin > 0;

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 lg:gap-6">
      {/* Total Instances */}
      <div
        data-testid="telemetry-total-instances"
        className="relative hidden h-32 flex-col justify-between overflow-hidden rounded-xl border border-slate-200 bg-white p-3 shadow-xs sm:flex sm:h-40 sm:p-5"
      >
        <div>
          <div className="flex justify-between items-start text-slate-400">
            <span className="text-xs uppercase font-bold tracking-wider">{t('telemetry.totalInstances')}</span>
            <Server className="w-5 h-5 text-slate-400" />
          </div>
          <div className="flex items-baseline gap-2 mt-2">
            <span className="text-4xl font-extrabold text-slate-900 font-sans tracking-tight">
              {service.totalInstances}
            </span>
            <span className="text-xs text-slate-500 font-medium">{t('telemetry.acrossRegions')}</span>
          </div>
        </div>

        <div>
          <div className="w-full h-1.5 bg-slate-100 rounded-full overflow-hidden">
            <div
              aria-hidden="true"
              className="ui-progress-fill h-full rounded-full bg-indigo-600"
              style={{ '--ui-progress': activePercent / 100 } as CSSProperties}
            />
          </div>
          <div className="flex justify-between items-center text-[11px] text-slate-500 font-semibold mt-2">
            <span>{service.activeInstances} {t('common.active')}</span>
            <span>{inactiveInstances} {t('telemetry.inactive')}</span>
          </div>
        </div>
      </div>

      {/* Task Health */}
      <div
        data-testid="telemetry-health"
        className="relative flex h-32 flex-col justify-between overflow-hidden rounded-xl border border-slate-200 bg-white p-3 shadow-xs sm:h-40 sm:p-5"
      >
        <div>
          <div className="flex justify-between items-start text-slate-400">
            <span className="text-[10px] uppercase font-bold tracking-wider sm:text-xs">{t('common.health')}</span>
            <Activity className={`w-4 h-4 sm:w-5 sm:h-5 ${isHealthy ? 'text-emerald-500' : 'text-amber-500'}`} />
          </div>
          <div className="flex items-center gap-2 mt-1.5 sm:mt-2">
            <span className="text-3xl font-extrabold text-slate-900 font-sans tracking-tight sm:text-4xl">
              {service.successRate}%
            </span>
            {service.failingTaskCount > 0 && (
              <span className="flex items-center gap-0.5 rounded-md bg-rose-50 px-1.5 py-0.5 text-[10px] font-bold text-rose-600 sm:text-xs">
                {service.failingTaskCount} {t('servicesList.failing')}
              </span>
            )}
          </div>
        </div>

        <div className="text-[11px] text-slate-500 font-medium sm:text-xs">
          {isOffline ? (
            <span className="text-slate-500 flex items-center gap-1.5 font-semibold">
              <span className="w-1.5 h-1.5 rounded-full bg-slate-400" />
              {service.totalInstances === 0 ? t('telemetry.noInstances') : t('telemetry.serviceOffline')}
            </span>
          ) : service.viewStatus === 'running' ? (
            <span className="text-slate-600 flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
              {t('telemetry.allTasksHealthy')}
            </span>
          ) : (
            <span className="text-amber-600 flex items-center gap-1.5 font-semibold">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />
              {t('telemetry.degradedHealth')}
            </span>
          )}
        </div>
      </div>

      {/* Telemetry Throughput */}
      <div
        data-testid="telemetry-throughput"
        className="group relative flex h-32 flex-col justify-between overflow-hidden rounded-xl border border-slate-200 bg-white p-3 shadow-xs sm:h-40 sm:p-5"
      >
        {/* SVG Area Chart Wave Underlay */}
        <div className="absolute inset-x-0 bottom-0 h-16 pointer-events-none select-none opacity-30 group-hover:opacity-40 transition-opacity">
          <svg className="w-full h-full" preserveAspectRatio="none" viewBox="0 0 100 100">
            <defs>
              <linearGradient id="gradient-wave" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#4f46e5" stopOpacity="0.6" />
                <stop offset="100%" stopColor="#4f46e5" stopOpacity="0" />
              </linearGradient>
            </defs>
            <path
              d="M0,80 C15,65 25,90 40,55 C55,20 70,75 85,35 C95,15 100,25 100,25 L100,100 L0,100 Z"
              fill="url(#gradient-wave)"
            />
            <path
              d="M0,80 C15,65 25,90 40,55 C55,20 70,75 85,35 C95,15 100,25 100,25"
              fill="none"
              stroke="#4f46e5"
              strokeWidth="2.5"
            />
          </svg>
        </div>

        <div className="z-10">
          <div className="flex justify-between items-start text-slate-400">
            <span className="text-[10px] uppercase font-bold tracking-wider sm:text-xs">{t('common.throughput')}</span>
            <TrendingUp className="w-4 h-4 text-indigo-600 sm:w-5 sm:h-5" />
          </div>
          <div className="flex items-baseline gap-2 mt-1.5 sm:mt-2">
            <span className="text-3xl font-extrabold text-slate-900 font-sans tracking-tight sm:text-4xl">
              {service.throughputPerMin}
            </span>
            <span className="text-[11px] text-slate-500 font-medium sm:text-xs">/min</span>
          </div>
        </div>

        <div
          className={`z-10 flex items-center gap-1 text-[11px] font-semibold sm:text-xs ${
            hasTraffic ? 'text-indigo-600 hover:underline cursor-pointer' : 'text-slate-500'
          }`}
        >
          {hasTraffic ? t('telemetry.realtimeRunning') : t('telemetry.noLiveTraffic')}
        </div>
      </div>
    </div>
  );
}
