import { useState, useMemo } from 'react';
import { Activity, Cpu, HardDrive, Zap } from 'lucide-react';
import { useI18n } from '../i18n';

interface ChartPoint {
  time: string;
  cpu: number;
  memory: number;
}

export default function ResourceChart() {
  const { t } = useI18n();
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
  const [loadMultiplier, setLoadMultiplier] = useState(1.0);
  const [activeMetric, setActiveMetric] = useState<'both' | 'cpu' | 'memory'>('both');

  // Generate resource tracking data
  const data: ChartPoint[] = useMemo(() => {
    return [
      { time: '10:00', cpu: Math.min(Math.round(18 * loadMultiplier), 100), memory: 34 },
      { time: '10:15', cpu: Math.min(Math.round(42 * loadMultiplier), 100), memory: 38 },
      { time: '10:30', cpu: Math.min(Math.round(30 * loadMultiplier), 100), memory: 40 },
      { time: '10:45', cpu: Math.min(Math.round(65 * loadMultiplier), 100), memory: 55 },
      { time: '11:00', cpu: Math.min(Math.round(92 * loadMultiplier), 100), memory: 68 },
    ];
  }, [loadMultiplier]);

  // Click handler to spike the CPU
  const triggerSpike = () => {
    setLoadMultiplier(1.8);
    setTimeout(() => {
      setLoadMultiplier(1.0);
    }, 5000);
  };

  // Convert points to SVG coordinates
  // Grid coordinates: width = 500, height = 200
  // X: 10:00 is at 40, 11:00 is at 460
  // Y: 0 is at 180, 100% is at 20
  const width = 500;
  const height = 200;
  const paddingX = 40;
  const paddingY = 20;

  const getCoordinates = (index: number, value: number) => {
    const x = paddingX + (index / (data.length - 1)) * (width - 2 * paddingX);
    const y = height - paddingY - (value / 100) * (height - 2 * paddingY);
    return { x, y };
  };

  // Build SVG path commands
  const cpuPath = useMemo(() => {
    return data
      .map((pt, i) => {
        const { x, y } = getCoordinates(i, pt.cpu);
        return `${i === 0 ? 'M' : 'L'} ${x} ${y}`;
      })
      .join(' ');
  }, [data]);

  const memoryPath = useMemo(() => {
    return data
      .map((pt, i) => {
        const { x, y } = getCoordinates(i, pt.memory);
        return `${i === 0 ? 'M' : 'L'} ${x} ${y}`;
      })
      .join(' ');
  }, [data]);

  // Fill area under CPU path
  const cpuAreaPath = useMemo(() => {
    if (data.length === 0) return '';
    const start = getCoordinates(0, 0);
    const end = getCoordinates(data.length - 1, 0);
    const points = data.map((pt, i) => {
      const { x, y } = getCoordinates(i, pt.cpu);
      return `L ${x} ${y}`;
    });
    return `M ${start.x} ${start.y} ${points.join(' ')} L ${end.x} ${end.y} Z`;
  }, [data]);

  return (
    <div className="bg-white border border-slate-200 rounded-xl flex flex-col h-[340px] shadow-xs relative overflow-hidden">
      {/* Chart Header Bar */}
      <div className="p-4 border-b border-slate-200 flex justify-between items-center bg-slate-50 rounded-t-xl shrink-0">
        <h3 className="font-sans text-sm font-bold text-slate-800 flex items-center gap-2">
          <Activity className="w-4 h-4 text-indigo-600" />
          <span>{t('chart.resourceConsumption')}</span>
        </h3>

        <div className="flex gap-4 items-center shrink-0">
          {/* Legend Toggle Buttons */}
          <div className="flex gap-2 text-[10px] font-bold">
            <button
              onClick={() => setActiveMetric('both')}
              className={`px-2 py-1 rounded transition-colors ${
                activeMetric === 'both' ? 'bg-slate-200/60 text-slate-800' : 'text-slate-400 hover:text-slate-600'
              }`}
            >
              {t('chart.all')}
            </button>
            <button
              onClick={() => setActiveMetric('cpu')}
              className={`flex items-center gap-1.5 px-2 py-1 rounded transition-colors ${
                activeMetric === 'cpu' ? 'bg-indigo-50 text-indigo-600' : 'text-slate-400 hover:text-slate-600'
              }`}
            >
              <span className="w-2 h-2 rounded-full bg-indigo-600" /> {t('chart.cpu')}
            </button>
            <button
              onClick={() => setActiveMetric('memory')}
              className={`flex items-center gap-1.5 px-2 py-1 rounded transition-colors ${
                activeMetric === 'memory' ? 'bg-slate-100 text-slate-700' : 'text-slate-400 hover:text-slate-600'
              }`}
            >
              <span className="w-2 h-2 rounded-full bg-slate-400" /> {t('chart.memory')}
            </button>
          </div>

          {/* Load Spike simulation trigger */}
          <button
            onClick={triggerSpike}
            className={`flex items-center gap-1 px-2.5 py-1 text-[10px] font-extrabold rounded-md border transition-all ${
              loadMultiplier > 1.0
                ? 'bg-rose-50 border-rose-200 text-rose-600 animate-pulse'
                : 'bg-white hover:bg-amber-50 border-amber-200 text-amber-600'
            }`}
            title={t('chart.injectLoadTitle')}
          >
            <Zap className="w-3 h-3" />
            <span>{loadMultiplier > 1.0 ? t('chart.spiked') : t('button.loadTest')}</span>
          </button>
        </div>
      </div>

      {/* Main Chart viewport */}
      <div className="flex-1 p-4 relative flex items-end justify-center select-none bg-white">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="w-full h-full"
          onMouseLeave={() => setHoveredIndex(null)}
        >
          <defs>
            <linearGradient id="cpu-grad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#4f46e5" stopOpacity="0.15" />
              <stop offset="100%" stopColor="#4f46e5" stopOpacity="0.01" />
            </linearGradient>
          </defs>

          {/* Grid lines (Y-axis grid lines) */}
          {[0, 25, 50, 75, 100].map((gridVal) => {
            const y = height - paddingY - (gridVal / 100) * (height - 2 * paddingY);
            return (
              <g key={gridVal} className="opacity-10">
                <line
                  x1={paddingX}
                  y1={y}
                  x2={width - paddingX}
                  y2={y}
                  stroke="#737685"
                  strokeWidth="1"
                />
                <text
                  x={paddingX - 10}
                  y={y + 4}
                  textAnchor="end"
                  fontSize="8"
                  fontWeight="bold"
                  fill="#737685"
                  className="font-mono"
                >
                  {gridVal}
                </text>
              </g>
            );
          })}

          {/* X Axis labels */}
          {data.map((pt, idx) => {
            const x = paddingX + (idx / (data.length - 1)) * (width - 2 * paddingX);
            return (
              <text
                key={idx}
                x={x}
                y={height - 4}
                textAnchor="middle"
                fontSize="8"
                fontWeight="bold"
                fill="#737685"
                className="font-mono opacity-60"
              >
                {pt.time}
              </text>
            );
          })}

          {/* Transparent Hover Interceptor Zones */}
          {data.map((_, idx) => {
            const x = paddingX + (idx / (data.length - 1)) * (width - 2 * paddingX);
            const colWidth = (width - 2 * paddingX) / (data.length - 1);
            return (
              <rect
                key={idx}
                x={x - colWidth / 2}
                y={0}
                width={colWidth}
                height={height}
                fill="transparent"
                onMouseEnter={() => setHoveredIndex(idx)}
                className="cursor-pointer"
              />
            );
          })}

          {/* Hover indicator guide line */}
          {hoveredIndex !== null && (
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

          {/* CPU Area Fill */}
          {activeMetric !== 'memory' && (
            <path d={cpuAreaPath} fill="url(#cpu-grad)" className="transition-all duration-300" />
          )}

          {/* CPU Path Line */}
          {activeMetric !== 'memory' && (
            <path
              d={cpuPath}
              fill="none"
              stroke="#4f46e5"
              strokeWidth="2.5"
              strokeLinecap="round"
              className="transition-all duration-300"
            />
          )}

          {/* Memory Path Line */}
          {activeMetric !== 'cpu' && (
            <path
              d={memoryPath}
              fill="none"
              stroke="#737685"
              strokeWidth="2"
              strokeDasharray="4,4"
              strokeLinecap="round"
              className="transition-all duration-300"
            />
          )}

          {/* Points circles on line */}
          {data.map((pt, idx) => {
            const cpuPt = getCoordinates(idx, pt.cpu);
            const memPt = getCoordinates(idx, pt.memory);
            const isPointHovered = hoveredIndex === idx;

            return (
              <g key={idx}>
                {activeMetric !== 'memory' && (
                  <circle
                    cx={cpuPt.x}
                    cy={cpuPt.y}
                    r={isPointHovered ? 5 : 3}
                    fill="#4f46e5"
                    stroke="#ffffff"
                    strokeWidth="1.5"
                    className="transition-all duration-150"
                  />
                )}
                {activeMetric !== 'cpu' && (
                  <circle
                    cx={memPt.x}
                    cy={memPt.y}
                    r={isPointHovered ? 4.5 : 2.5}
                    fill="#737685"
                    stroke="#ffffff"
                    strokeWidth="1"
                    className="transition-all duration-150"
                  />
                )}
              </g>
            );
          })}
        </svg>

        {/* Hover Tooltip Popup Overlay */}
        {hoveredIndex !== null && (
          <div
            className="absolute bg-[#1a1c24] border border-slate-700/50 rounded-lg p-2.5 text-[10px] text-white shadow-lg pointer-events-none z-10 transition-all font-sans"
            style={{
              left: `${(hoveredIndex / (data.length - 1)) * 75 + 12}%`,
              bottom: '50px',
            }}
          >
            <div className="font-bold border-b border-slate-700/50 pb-1 mb-1 font-mono text-slate-300 flex justify-between gap-4">
              <span>{t('chart.time', { time: data[hoveredIndex].time })}</span>
              <span className="text-emerald-400">{t('chart.stable')}</span>
            </div>
            <div className="space-y-1">
              <div className="flex items-center gap-1.5 font-semibold">
                <Cpu className="w-3 h-3 text-blue-400" />
                <span>{t('chart.cpuLoad')}</span>
                <span className="font-mono font-bold text-blue-400">{data[hoveredIndex].cpu}%</span>
              </div>
              <div className="flex items-center gap-1.5 font-semibold">
                <HardDrive className="w-3 h-3 text-slate-400" />
                <span>{t('chart.memoryUsage')}</span>
                <span className="font-mono font-bold text-slate-300">{data[hoveredIndex].memory}%</span>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
