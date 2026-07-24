import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { Activity, AlertCircle, CheckCircle2, Clock3, Database, Timer } from 'lucide-react';
import {
  MAX_TASK_METRIC_LOOKBACK_MINUTES,
  TASK_METRIC_LOOKBACK_PRESETS,
  type TaskMetricChartPointSummary,
} from '../api';
import { useI18n } from '../i18n';

interface ResourceChartProps {
  windows: TaskMetricChartPointSummary[];
  lookbackMinutes: number;
  onLookbackMinutesChange: (minutes: number) => void;
  isLoading?: boolean;
  error?: string | null;
}

interface ChartPoint {
  id: string;
  time: string;
  timeRange: string;
  fetched: number;
  failed: number;
  succeeded: number;
  inflight: number;
  p95DurationMs: number | null;
  reportedWindowCount: number;
}

const DEFAULT_CHART_SIZE = { width: 900, height: 220 };
const MIN_CHART_WIDTH = 320;
const MIN_CHART_HEIGHT = 180;

function formatTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function formatTimeRange(start: string, end: string) {
  return `${formatTime(start)}-${formatTime(end)}`;
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

function normalizeLookbackMinutes(value: number) {
  if (!Number.isFinite(value)) return null;
  return Math.min(MAX_TASK_METRIC_LOOKBACK_MINUTES, Math.max(1, Math.trunc(value)));
}

export default function ResourceChart({
  windows,
  lookbackMinutes,
  onLookbackMinutesChange,
  isLoading = false,
  error = null,
}: ResourceChartProps) {
  const { t } = useI18n();
  const chartFrameRef = useRef<HTMLDivElement | null>(null);
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
  const [activeMetric, setActiveMetric] = useState<'all' | 'fetched' | 'failed'>('all');
  const [isCustomLookbackOpen, setIsCustomLookbackOpen] = useState(false);
  const [customLookbackValue, setCustomLookbackValue] = useState(String(lookbackMinutes));
  const [chartSize, setChartSize] = useState(DEFAULT_CHART_SIZE);

  useEffect(() => {
    const element = chartFrameRef.current;
    if (!element) return;

    const updateSize = () => {
      const rect = element.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;

      const nextSize = {
        width: Math.max(MIN_CHART_WIDTH, Math.round(rect.width)),
        height: Math.max(MIN_CHART_HEIGHT, Math.round(rect.height)),
      };
      setChartSize((current) =>
        current.width === nextSize.width && current.height === nextSize.height ? current : nextSize,
      );
    };

    updateSize();
    if (typeof ResizeObserver === 'undefined') return;

    const observer = new ResizeObserver(updateSize);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const data = useMemo<ChartPoint[]>(() => {
    return [...windows]
      .sort((left, right) => new Date(left.bucket_started_at).getTime() - new Date(right.bucket_started_at).getTime())
      .map((point) => ({
        id: `${point.bucket_started_at}/${point.bucket_ended_at}`,
        time: formatTime(point.bucket_started_at),
        timeRange: formatTimeRange(point.bucket_started_at, point.bucket_ended_at),
        fetched: point.fetched,
        failed: point.failed + point.dead_lettered + point.timeouts,
        succeeded: point.succeeded,
        inflight: point.inflight,
        p95DurationMs: point.p95_duration_ms,
        reportedWindowCount: point.reported_window_count,
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

  const width = chartSize.width;
  const height = chartSize.height;
  const paddingX = width < 480 ? 32 : 44;
  const paddingY = 22;
  // Reserve a dedicated band at the bottom of the viewBox for X-axis labels,
  // so they are never crowded against the edge (previously labels sat at
  // y = height - 4 and got clipped/shrunk out of view).
  const xAxisSpace = 36;
  const chartBottom = height - paddingY - xAxisSpace;
  const plotHeight = chartBottom - paddingY;
  const maxValue = Math.max(1, ...data.flatMap((point) => [point.fetched, point.failed]));

  const getCoordinates = (index: number, value: number) => {
    const spread = Math.max(data.length - 1, 1);
    const x = paddingX + (index / spread) * (width - 2 * paddingX);
    const y = chartBottom - (value / maxValue) * plotHeight;
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
  const hasFailures = totals.failed > 0;
  const showFailedSeries = activeMetric === 'failed' || (activeMetric === 'all' && hasFailures);
  const yAxisValues = useMemo(() => {
    if (visibleMaxLabel <= 4) {
      return Array.from({ length: visibleMaxLabel + 1 }, (_, index) => index);
    }

    const tickStep = Math.ceil(visibleMaxLabel / 4);
    const values: number[] = [];
    for (let value = 0; value < visibleMaxLabel; value += tickStep) {
      values.push(value);
    }
    values.push(visibleMaxLabel);
    return values;
  }, [visibleMaxLabel]);

  // Show each minute in the default 15-minute view, then thin longer lookbacks.
  const maxLabels = data.length <= 16 ? data.length : 8;
  const labelStride = data.length > maxLabels ? Math.ceil(data.length / maxLabels) : 1;
  const shouldShowLabel = (index: number) => {
    if (data.length <= maxLabels) return true;
    // Always show the last label; otherwise only on stride boundaries.
    return index === data.length - 1 || index % labelStride === 0;
  };
  const tooltipPosition = (() => {
    if (hoveredIndex === null || !hasData) return null;

    const point = data[hoveredIndex];
    const value = activeMetric === 'failed' ? point.failed : point.fetched;
    const { x, y } = getCoordinates(hoveredIndex, value);
    const leftPercent = Math.min(Math.max((x / width) * 100, 14), 86);
    const placeBelowPoint = y < height * 0.42;

    if (placeBelowPoint) {
      return {
        left: `${leftPercent}%`,
        top: `${Math.min((y / height) * 100 + 8, 68)}%`,
      };
    }

    return {
      left: `${leftPercent}%`,
      bottom: `${Math.min(((height - y) / height) * 100 + 8, 68)}%`,
    };
  })();
  const applyLookbackMinutes = (minutes: number) => {
    const next = normalizeLookbackMinutes(minutes);
    if (next === null) {
      setCustomLookbackValue(String(lookbackMinutes));
      return;
    }
    setCustomLookbackValue(String(next));
    if (next !== lookbackMinutes) {
      onLookbackMinutesChange(next);
    }
  };
  const applyPresetLookback = (minutes: number) => {
    setIsCustomLookbackOpen(false);
    if (minutes !== lookbackMinutes) {
      onLookbackMinutesChange(minutes);
    }
  };
  const handleCustomLookbackSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    applyLookbackMinutes(Number(customLookbackValue));
  };

  return (
    <div className="relative flex h-[400px] flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xs">
      <div className="p-4 border-b border-slate-200 bg-slate-50 rounded-t-xl shrink-0">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <h3 className="font-sans text-sm font-bold text-slate-800 flex items-center gap-2">
              <Activity className="w-4 h-4 text-indigo-600" />
              <span>{t('chart.taskMetrics')}</span>
            </h3>

            <div className="flex flex-wrap items-center gap-2 text-[10px] font-bold">
              <div
                role="group"
                aria-label={`${t('chart.taskMetrics')} ${t('chart.lookbackMinutes')}`}
                className="flex items-center gap-1 rounded-md border border-slate-200 bg-white p-1"
              >
                {TASK_METRIC_LOOKBACK_PRESETS.map((minutes) => (
                  <button
                    key={minutes}
                    type="button"
                    aria-pressed={!isCustomLookbackOpen && lookbackMinutes === minutes}
                    onClick={() => applyPresetLookback(minutes)}
                    className={`h-6 rounded px-2 transition-colors ${
                      !isCustomLookbackOpen && lookbackMinutes === minutes
                        ? 'bg-indigo-50 text-indigo-600'
                        : 'text-slate-500 hover:bg-slate-50 hover:text-slate-700'
                    }`}
                  >
                    {minutes}m
                  </button>
                ))}
                <button
                  type="button"
                  aria-pressed={isCustomLookbackOpen}
                  onClick={() => setIsCustomLookbackOpen(true)}
                  className={`h-6 rounded px-2 transition-colors ${
                    isCustomLookbackOpen
                      ? 'bg-indigo-50 text-indigo-600'
                      : 'text-slate-500 hover:bg-slate-50 hover:text-slate-700'
                  }`}
                >
                  {t('chart.customLookback')}
                </button>
              </div>

              {isCustomLookbackOpen && (
                <form onSubmit={handleCustomLookbackSubmit} className="flex items-center gap-1.5">
                  <input
                    aria-label={t('chart.lookbackMinutes')}
                    type="number"
                    min={1}
                    max={MAX_TASK_METRIC_LOOKBACK_MINUTES}
                    value={customLookbackValue}
                    onChange={(event) => setCustomLookbackValue(event.target.value)}
                    className="h-7 w-14 rounded-md border border-slate-200 bg-white px-2 text-right font-mono text-slate-700 outline-none transition focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100"
                  />
                  <span className="text-slate-400">{t('chart.minutesUnit')}</span>
                  <button
                    type="submit"
                    className="h-7 rounded-md border border-slate-200 bg-white px-2 text-slate-600 transition-colors hover:bg-slate-100"
                  >
                    {t('chart.applyLookback')}
                  </button>
                </form>
              )}
            </div>
          </div>

          <div className="flex gap-2 items-center shrink-0 text-[10px] font-bold">
            <button
              type="button"
              onClick={() => setActiveMetric('all')}
              className={`px-2 py-1 rounded transition-colors ${
                activeMetric === 'all' ? 'bg-slate-200/70 text-slate-800' : 'text-slate-400 hover:text-slate-600'
              }`}
            >
              {t('chart.all')}
            </button>
            <button
              type="button"
              onClick={() => setActiveMetric('fetched')}
              className={`flex items-center gap-1.5 px-2 py-1 rounded transition-colors ${
                activeMetric === 'fetched' ? 'bg-indigo-50 text-indigo-600' : 'text-slate-400 hover:text-slate-600'
              }`}
            >
              <span className="w-2 h-2 rounded-full bg-indigo-600" />
              {t('chart.fetched')}
            </button>
            <button
              type="button"
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

      <div ref={chartFrameRef} data-testid="task-metrics-chart-frame" className="relative min-h-0 flex-1 select-none bg-white p-5">
        {isLoading && (
          <div
            aria-live="polite"
            className="ui-panel-state-enter absolute inset-0 z-20 flex items-center justify-center bg-white/80 text-xs font-bold text-slate-500"
            role="status"
          >
            {t('chart.loadingMetrics')}
          </div>
        )}

        {!isLoading && error && (
          <div
            className="ui-panel-state-enter absolute inset-0 z-20 flex items-center justify-center bg-white px-6 text-center"
            role="alert"
          >
            <div>
              <AlertCircle className="mx-auto mb-2 h-5 w-5 text-amber-500" />
              <div className="text-xs font-bold text-slate-700">{t('chart.metricsUnavailable')}</div>
              <div className="mt-1 text-[11px] font-medium text-slate-400">{error}</div>
            </div>
          </div>
        )}

        {!isLoading && !error && !hasData && (
          <div
            className="ui-panel-state-enter absolute inset-0 z-20 flex items-center justify-center bg-white px-6 text-center"
            role="status"
          >
            <div>
              <Activity className="mx-auto mb-2 h-5 w-5 text-slate-300" />
              <div className="text-xs font-bold text-slate-700">{t('chart.noMetrics')}</div>
              <div className="mt-1 text-[11px] font-medium text-slate-400">{t('chart.noMetricsHint')}</div>
            </div>
          </div>
        )}

        <svg
          data-testid="task-metrics-chart"
          viewBox={`0 0 ${width} ${height}`}
          className={`block h-full w-full ${hasData ? '' : 'opacity-20'}`}
          onMouseLeave={() => setHoveredIndex(null)}
        >
          <defs>
            <linearGradient id="fetched-grad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#4f46e5" stopOpacity="0.15" />
              <stop offset="100%" stopColor="#4f46e5" stopOpacity="0.01" />
            </linearGradient>
          </defs>

          {yAxisValues.map((value) => {
            const ratio = value / visibleMaxLabel;
            const y = chartBottom - ratio * plotHeight;
            return (
              <g key={value} className="opacity-15">
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
                y={chartBottom + 24}
                textAnchor="middle"
                fontSize="9"
                fontWeight="bold"
                fill="#737685"
                className="font-mono opacity-80"
              >
                {point.time}
              </text>
            );
          })}

          {hasData && (
            <line
              x1={paddingX}
              y1={chartBottom}
              x2={width - paddingX}
              y2={chartBottom}
              stroke="#cbd5e1"
              strokeWidth="1"
              className="opacity-70"
            />
          )}

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
              y2={chartBottom}
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
          {hasData && showFailedSeries && (
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
                {showFailedSeries && (
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

        {hoveredIndex !== null && hasData && tooltipPosition && (
          <div
            role="tooltip"
            className="absolute min-w-[190px] max-w-[240px] -translate-x-1/2 bg-[#1a1c24] border border-slate-700/50 rounded-lg p-2.5 text-[10px] text-white shadow-lg pointer-events-none z-10 transition-all font-sans"
            style={tooltipPosition}
          >
            <div className="font-bold border-b border-slate-700/50 pb-1 mb-1 font-mono text-slate-300 flex justify-between gap-4">
              <span>{t('chart.time', { time: data[hoveredIndex].timeRange })}</span>
              {data[hoveredIndex].reportedWindowCount > 0 && (
                <span className="text-emerald-400">{t('chart.reported')}</span>
              )}
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
              {showFailedSeries && (
                <div className="flex items-center gap-1.5 font-semibold">
                  <AlertCircle className="w-3 h-3 text-rose-400" />
                  <span>{t('chart.failures')}:</span>
                  <span className="font-mono font-bold text-rose-300">{data[hoveredIndex].failed}</span>
                </div>
              )}
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
