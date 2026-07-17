import { useMemo, useState } from 'react';
import { Activity, AlertCircle, CheckCircle2, Clock3, Database, Timer } from 'lucide-react';
import type { TaskMetricWindowSummary } from '../api';
import { useI18n } from '../i18n';

interface ResourceChartProps {
  windows: TaskMetricWindowSummary[];
  isLoading?: boolean;
  error?: string | null;
}

interface ChartPoint {
  id: string;
  time: string;
  fetched: number;
  failed: number;
  succeeded: number;
  inflight: number;
  p95DurationMs: number | null;
}

function formatTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function formatDuration(value: number | null) {
  if (value === null) {
    return '-';
  }
  if (value >= 1000) {
    return `${(value / 1000).toFixed(1)}s`;
  }
  return `${Math.round(value)}ms`;
}

export default function ResourceChart({ windows, isLoading = false, error = null }: ResourceChartProps) {
  const { t } = useI18n();
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
  const [activeMetric, setActiveMetric] = useState<'all' | 'fetched' | 'failed'>('all');

  const data = useMemo<ChartPoint[]>(() => {
    return [...windows]
      .sort((left, right) => new Date(left.window_ended_at).getTime() - new Date(right.window_ended_at).getTime())
      .map((window) => ({
        id: window.window_id,
        time: formatTime(window.window_ended_at),
        fetched: window.fetched,
        failed: window.failed + window.dead_lettered + window.timeouts,
        succeeded: window.succeeded,
        inflight: window.inflight,
        p95DurationMs: window.p95_duration_ms,
      }));
  }, [windows]);

  const totals = useMemo(() => {
    return data.reduce(
      (summary, point) => ({
        fetched: summary.fetched + point.fetched,
        failed: summary.failed + point.failed,
        succeeded: summary.succeeded + point.succeeded,
        inflight: Math.max(summary.inflight, point.inflight),
      }),
      { fetched: 0, failed: 0, succeeded: 0, inflight: 0 },
    );
  }, [data]);

  const width = 500;
  const height = 200;
  const paddingX = 40;
  const paddingY = 22;
  const maxValue = Math.max(1, ...data.flatMap((point) => [point.fetched, point.failed]));

  const getCoordinates = (index: number, value: number) => {
    const spread = Math.max(data.length - 1, 1);
    const x = paddingX + (index / spread) * (width - 2 * paddingX);
    const y = height - paddingY - (value / maxValue) * (height - 2 * paddingY);
    return { x, y };
  };

  const buildPath = (key: 'fetched' | 'failed') => {
    return data
      .map((point, index) => {
        const { x, y } = getCoordinates(index, point[key]);
        return `${index === 0 ? 'M' : 'L'} ${x} ${y}`;
      })
      .join(' ');
  };

  const fetchedPath = useMemo(() => buildPath('fetched'), [data, maxValue]);
  const failedPath = useMemo(() => buildPath('failed'), [data, maxValue]);
  const fetchedAreaPath = useMemo(() => {
    if (data.length === 0) return '';
    const start = getCoordinates(0, 0);
    const end = getCoordinates(data.length - 1, 0);
    const points = data.map((point, index) => {
      const { x, y } = getCoordinates(index, point.fetched);
      return `L ${x} ${y}`;
    });
    return `M ${start.x} ${start.y} ${points.join(' ')} L ${end.x} ${end.y} Z`;
  }, [data, maxValue]);

  const hasData = data.length > 0;
  const visibleMaxLabel = maxValue >= 10 ? Math.ceil(maxValue / 10) * 10 : maxValue;

  // Thin X-axis labels so they don't overlap when there are many windows.
  // Aim for at most ~6 evenly spaced labels across the chart width.
  const maxLabels = 6;
  const labelStride = data.length > maxLabels ? Math.ceil(data.length / maxLabels) : 1;
  const shouldShowLabel = (index: number) => {
    if (data.length <= maxLabels) return true;
    // Always show the last label; otherwise only on stride boundaries.
    return index === data.length - 1 || index % labelStride === 0;
  };

  return (
    <div className="bg-white border border-slate-200 rounded-xl flex flex-col h-[340px] shadow-xs relative overflow-hidden">
      <div className="p-4 border-b border-slate-200 flex justify-between items-center bg-slate-50 rounded-t-xl shrink-0">
        <h3 className="font-sans text-sm font-bold text-slate-800 flex items-center gap-2">
          <Activity className="w-4 h-4 text-indigo-600" />
          <span>{t('chart.taskMetrics')}</span>
        </h3>

        <div className="flex gap-2 items-center shrink-0 text-[10px] font-bold">
          <button
            onClick={() => setActiveMetric('all')}
            className={`px-2 py-1 rounded transition-colors ${
              activeMetric === 'all' ? 'bg-slate-200/70 text-slate-800' : 'text-slate-400 hover:text-slate-600'
            }`}
          >
            {t('chart.all')}
          </button>
          <button
            onClick={() => setActiveMetric('fetched')}
            className={`flex items-center gap-1.5 px-2 py-1 rounded transition-colors ${
              activeMetric === 'fetched' ? 'bg-indigo-50 text-indigo-600' : 'text-slate-400 hover:text-slate-600'
            }`}
          >
            <span className="w-2 h-2 rounded-full bg-indigo-600" />
            {t('chart.fetched')}
          </button>
          <button
            onClick={() => setActiveMetric('failed')}
            className={`flex items-center gap-1.5 px-2 py-1 rounded transition-colors ${
              activeMetric === 'failed' ? 'bg-rose-50 text-rose-600' : 'text-slate-400 hover:text-slate-600'
            }`}
          >
            <span className="w-2 h-2 rounded-full bg-rose-500" />
            {t('chart.failures')}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-4 gap-0 border-b border-slate-100 bg-white px-4 py-3 text-xs">
        <div className="flex items-center gap-2">
          <Database className="h-3.5 w-3.5 text-indigo-600" />
          <div>
            <div className="font-bold text-slate-900">{totals.fetched}</div>
            <div className="text-[10px] font-semibold text-slate-400">{t('chart.fetched')}</div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
          <div>
            <div className="font-bold text-slate-900">{totals.succeeded}</div>
            <div className="text-[10px] font-semibold text-slate-400">{t('chart.succeeded')}</div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <AlertCircle className="h-3.5 w-3.5 text-rose-500" />
          <div>
            <div className="font-bold text-slate-900">{totals.failed}</div>
            <div className="text-[10px] font-semibold text-slate-400">{t('chart.failures')}</div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Clock3 className="h-3.5 w-3.5 text-slate-500" />
          <div>
            <div className="font-bold text-slate-900">{totals.inflight}</div>
            <div className="text-[10px] font-semibold text-slate-400">{t('chart.maxInflight')}</div>
          </div>
        </div>
      </div>

      <div className="flex-1 p-4 relative flex items-end justify-center select-none bg-white">
        {isLoading && (
          <div className="absolute inset-0 z-20 flex items-center justify-center bg-white/80 text-xs font-bold text-slate-500">
            {t('chart.loadingMetrics')}
          </div>
        )}

        {!isLoading && error && (
          <div className="absolute inset-0 z-20 flex items-center justify-center bg-white px-6 text-center">
            <div>
              <AlertCircle className="mx-auto mb-2 h-5 w-5 text-amber-500" />
              <div className="text-xs font-bold text-slate-700">{t('chart.metricsUnavailable')}</div>
              <div className="mt-1 text-[11px] font-medium text-slate-400">{error}</div>
            </div>
          </div>
        )}

        {!isLoading && !error && !hasData && (
          <div className="absolute inset-0 z-20 flex items-center justify-center bg-white px-6 text-center">
            <div>
              <Activity className="mx-auto mb-2 h-5 w-5 text-slate-300" />
              <div className="text-xs font-bold text-slate-700">{t('chart.noMetrics')}</div>
              <div className="mt-1 text-[11px] font-medium text-slate-400">{t('chart.noMetricsHint')}</div>
            </div>
          </div>
        )}

        <svg
          viewBox={`0 0 ${width} ${height}`}
          className={`w-full h-full ${hasData ? '' : 'opacity-20'}`}
          onMouseLeave={() => setHoveredIndex(null)}
        >
          <defs>
            <linearGradient id="fetched-grad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#4f46e5" stopOpacity="0.15" />
              <stop offset="100%" stopColor="#4f46e5" stopOpacity="0.01" />
            </linearGradient>
          </defs>

          {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
            const value = Math.round(visibleMaxLabel * ratio);
            const y = height - paddingY - ratio * (height - 2 * paddingY);
            return (
              <g key={ratio} className="opacity-15">
                <line x1={paddingX} y1={y} x2={width - paddingX} y2={y} stroke="#737685" strokeWidth="1" />
                <text
                  x={paddingX - 10}
                  y={y + 4}
                  textAnchor="end"
                  fontSize="8"
                  fontWeight="bold"
                  fill="#737685"
                  className="font-mono"
                >
                  {value}
                </text>
              </g>
            );
          })}

          {data.map((point, index) => {
            if (!shouldShowLabel(index)) return null;
            const { x } = getCoordinates(index, 0);
            return (
              <text
                key={point.id}
                x={x}
                y={height - 4}
                textAnchor="middle"
                fontSize="8"
                fontWeight="bold"
                fill="#737685"
                className="font-mono opacity-60"
              >
                {point.time}
              </text>
            );
          })}

          {data.map((_, index) => {
            const { x } = getCoordinates(index, 0);
            const colWidth = (width - 2 * paddingX) / Math.max(data.length - 1, 1);
            return (
              <rect
                key={index}
                x={x - colWidth / 2}
                y={0}
                width={colWidth}
                height={height}
                fill="transparent"
                onMouseEnter={() => setHoveredIndex(index)}
                className="cursor-pointer"
              />
            );
          })}

          {hoveredIndex !== null && hasData && (
            <line
              x1={getCoordinates(hoveredIndex, 0).x}
              y1={paddingY}
              x2={getCoordinates(hoveredIndex, 0).x}
              y2={height - paddingY}
              stroke="#4f46e5"
              strokeDasharray="3,3"
              strokeWidth="1.5"
              className="opacity-40"
            />
          )}

          {hasData && activeMetric !== 'failed' && (
            <path d={fetchedAreaPath} fill="url(#fetched-grad)" className="transition-all duration-300" />
          )}
          {hasData && activeMetric !== 'failed' && (
            <path
              d={fetchedPath}
              fill="none"
              stroke="#4f46e5"
              strokeWidth="2.5"
              strokeLinecap="round"
              className="transition-all duration-300"
            />
          )}
          {hasData && activeMetric !== 'fetched' && (
            <path
              d={failedPath}
              fill="none"
              stroke="#f43f5e"
              strokeWidth="2"
              strokeDasharray="4,4"
              strokeLinecap="round"
              className="transition-all duration-300"
            />
          )}

          {data.map((point, index) => {
            const fetchedPoint = getCoordinates(index, point.fetched);
            const failedPoint = getCoordinates(index, point.failed);
            const isPointHovered = hoveredIndex === index;
            return (
              <g key={point.id}>
                {activeMetric !== 'failed' && (
                  <circle
                    cx={fetchedPoint.x}
                    cy={fetchedPoint.y}
                    r={isPointHovered ? 5 : 3}
                    fill="#4f46e5"
                    stroke="#ffffff"
                    strokeWidth="1.5"
                    className="transition-all duration-150"
                  />
                )}
                {activeMetric !== 'fetched' && (
                  <circle
                    cx={failedPoint.x}
                    cy={failedPoint.y}
                    r={isPointHovered ? 4.5 : 2.5}
                    fill="#f43f5e"
                    stroke="#ffffff"
                    strokeWidth="1"
                    className="transition-all duration-150"
                  />
                )}
              </g>
            );
          })}
        </svg>

        {hoveredIndex !== null && hasData && (
          <div
            className="absolute bg-[#1a1c24] border border-slate-700/50 rounded-lg p-2.5 text-[10px] text-white shadow-lg pointer-events-none z-10 transition-all font-sans"
            style={{
              left: `${Math.min((hoveredIndex / Math.max(data.length - 1, 1)) * 75 + 12, 78)}%`,
              bottom: '40px',
            }}
          >
            <div className="font-bold border-b border-slate-700/50 pb-1 mb-1 font-mono text-slate-300 flex justify-between gap-4">
              <span>{t('chart.time', { time: data[hoveredIndex].time })}</span>
              <span className="text-emerald-400">{t('chart.reported')}</span>
            </div>
            <div className="space-y-1">
              <div className="flex items-center gap-1.5 font-semibold">
                <Database className="w-3 h-3 text-indigo-400" />
                <span>{t('chart.fetched')}:</span>
                <span className="font-mono font-bold text-indigo-300">{data[hoveredIndex].fetched}</span>
              </div>
              <div className="flex items-center gap-1.5 font-semibold">
                <CheckCircle2 className="w-3 h-3 text-emerald-400" />
                <span>{t('chart.succeeded')}:</span>
                <span className="font-mono font-bold text-emerald-300">{data[hoveredIndex].succeeded}</span>
              </div>
              <div className="flex items-center gap-1.5 font-semibold">
                <AlertCircle className="w-3 h-3 text-rose-400" />
                <span>{t('chart.failures')}:</span>
                <span className="font-mono font-bold text-rose-300">{data[hoveredIndex].failed}</span>
              </div>
              <div className="flex items-center gap-1.5 font-semibold">
                <Timer className="w-3 h-3 text-slate-400" />
                <span>{t('chart.p95Duration')}:</span>
                <span className="font-mono font-bold text-slate-300">
                  {formatDuration(data[hoveredIndex].p95DurationMs)}
                </span>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
