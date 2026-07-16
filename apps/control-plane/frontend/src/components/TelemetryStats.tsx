import { Server, Activity, ArrowUpRight, ArrowDownRight, TrendingUp } from 'lucide-react';
import { Service } from '../types';
import { useI18n } from '../i18n';

interface TelemetryStatsProps {
  service: Service;
}

export default function TelemetryStats({ service }: TelemetryStatsProps) {
  const { t } = useI18n();
  const activePercent = (service.activeInstances / service.totalInstances) * 100;
  const isHealthy = service.taskHealth >= 99;
  const isDeclined = service.taskHealthTrend.startsWith('-');

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
      {/* Total Instances */}
      <div className="bg-white border border-slate-200 rounded-xl p-5 flex flex-col justify-between h-40 shadow-xs relative overflow-hidden">
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
              className="h-full bg-indigo-600 rounded-full transition-all duration-500"
              style={{ width: `${activePercent}%` }}
            />
          </div>
          <div className="flex justify-between items-center text-[11px] text-slate-500 font-semibold mt-2">
            <span>{service.activeInstances} {t('common.active')}</span>
            <span>{service.standbyInstances} {t('telemetry.standby')}</span>
          </div>
        </div>
      </div>

      {/* Task Health */}
      <div className="bg-white border border-slate-200 rounded-xl p-5 flex flex-col justify-between h-40 shadow-xs relative overflow-hidden">
        <div>
          <div className="flex justify-between items-start text-slate-400">
            <span className="text-xs uppercase font-bold tracking-wider">{t('common.health')}</span>
            <Activity className={`w-5 h-5 ${isHealthy ? 'text-emerald-500' : 'text-amber-500'}`} />
          </div>
          <div className="flex items-center gap-2 mt-2">
            <span className="text-4xl font-extrabold text-slate-900 font-sans tracking-tight">
              {service.taskHealth}%
            </span>
            <span
              className={`flex items-center gap-0.5 text-xs font-bold px-1.5 py-0.5 rounded-md ${
                isDeclined ? 'bg-rose-50 text-rose-600' : 'bg-emerald-50 text-emerald-600'
              }`}
            >
              {isDeclined ? (
                <ArrowDownRight className="w-3 h-3" />
              ) : (
                <ArrowUpRight className="w-3 h-3" />
              )}
              {service.taskHealthTrend.replace('+', '').replace('-', '')}
            </span>
          </div>
        </div>

        <div className="text-xs text-slate-500 font-medium">
          {service.status === 'running' ? (
            <span className="text-slate-600 flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
              {t('telemetry.allTasksHealthy')}
            </span>
          ) : (
            <span className="text-amber-600 flex items-center gap-1.5 font-semibold">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse" />
              {t('telemetry.degradedHealth')}
            </span>
          )}
        </div>
      </div>

      {/* Telemetry Throughput */}
      <div className="bg-white border border-slate-200 rounded-xl p-5 flex flex-col justify-between h-40 shadow-xs relative overflow-hidden group">
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
            <span className="text-xs uppercase font-bold tracking-wider">{t('common.throughput')}</span>
            <TrendingUp className="w-5 h-5 text-indigo-600" />
          </div>
          <div className="flex items-baseline gap-2 mt-2">
            <span className="text-4xl font-extrabold text-slate-900 font-sans tracking-tight">
              {service.throughput.split(' ')[0]}
            </span>
            <span className="text-xs text-slate-500 font-medium">
              {service.throughput.substring(service.throughput.indexOf(' ') + 1)}
            </span>
          </div>
        </div>

        <div className="text-[11px] text-indigo-600 font-semibold z-10 flex items-center gap-1 hover:underline cursor-pointer">
          {t('telemetry.realtimeRunning')}
        </div>
      </div>
    </div>
  );
}
