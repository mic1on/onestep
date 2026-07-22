import { ArrowRight, Database, ArrowRightLeft, Server, Cpu, HardDrive, Info } from 'lucide-react';
import { Task } from '../types';
import { type CSSProperties, useState } from 'react';
import { useI18n } from '../i18n';
import { formatRelativeTime, type ResourceCatalogEntry } from '../api';
import { buildSourceDetails, buildSinkDetails, resolveRowValue } from './sourceFields';

interface TopologyFlowProps {
  task: Task;
  resourceCatalog: ResourceCatalogEntry[];
}

const MIN_FLOW_DURATION_SECONDS = 0.75;
const MAX_FLOW_DURATION_SECONDS = 2.4;
const MAX_THROUGHPUT_FOR_SPEED = 120;

type TopologyFlowStyle = CSSProperties & {
  '--topology-flow-duration': string;
  '--topology-flow-delay': string;
  '--topology-stage-duration': string;
};

const topologyFlowStyles = `
.topology-flow-track {
  isolation: isolate;
}

.topology-flow-track-active {
  background: rgba(99, 102, 241, 0.16);
}

.topology-flow-track-active::after {
  content: "";
  position: absolute;
  left: -2px;
  right: -2px;
  top: 0;
  height: 48%;
  border-radius: 9999px;
  background: linear-gradient(180deg, transparent, rgba(79, 70, 229, 0.86), transparent);
  animation: topology-flow-sweep-y var(--topology-flow-duration) linear infinite;
}

.topology-flow-packet {
  position: absolute;
  left: 50%;
  top: 0;
  z-index: 1;
  width: 0.375rem;
  height: 0.375rem;
  border-radius: 9999px;
  background: #4f46e5;
  box-shadow: 0 0 0 4px rgba(79, 70, 229, 0.12);
  transform: translate(-50%, -50%);
  animation: topology-flow-packet-y var(--topology-flow-duration) linear infinite;
}

.topology-flow-packet-delay {
  animation-delay: var(--topology-flow-delay);
  opacity: 0.58;
}

.topology-flow-node-source-active {
  animation: topology-flow-source-glow var(--topology-stage-duration) var(--ease-move) infinite;
}

.topology-flow-node-handler-active {
  animation: topology-flow-handler-breathe var(--topology-stage-duration) var(--ease-move) infinite;
}

.topology-flow-node-sink-active {
  animation: topology-flow-sink-glow var(--topology-stage-duration) var(--ease-move) infinite;
  animation-delay: 0.22s;
}

@media (min-width: 640px) {
  .topology-flow-track-active::after {
    inset: -2px auto -2px 0;
    width: 46%;
    height: auto;
    background: linear-gradient(90deg, transparent, rgba(79, 70, 229, 0.86), transparent);
    animation-name: topology-flow-sweep-x;
  }

  .topology-flow-packet {
    left: 0;
    top: 50%;
    animation-name: topology-flow-packet-x;
  }
}

@media (prefers-reduced-motion: reduce) {
  .topology-flow-packet {
    display: none;
  }
}

@keyframes topology-flow-packet-x {
  from {
    left: 0;
  }
  to {
    left: 100%;
  }
}

@keyframes topology-flow-packet-y {
  from {
    top: 0;
  }
  to {
    top: 100%;
  }
}

@keyframes topology-flow-sweep-x {
  from {
    transform: translateX(-90%);
  }
  to {
    transform: translateX(260%);
  }
}

@keyframes topology-flow-sweep-y {
  from {
    transform: translateY(-90%);
  }
  to {
    transform: translateY(260%);
  }
}

@keyframes topology-flow-source-glow {
  0%,
  100% {
    border-color: rgb(226, 232, 240);
    filter: drop-shadow(0 0 0 rgba(8, 145, 178, 0));
  }
  18% {
    border-color: rgb(8, 145, 178);
    filter: drop-shadow(0 0 5px rgba(8, 145, 178, 0.16));
  }
}

@keyframes topology-flow-handler-breathe {
  0%,
  100% {
    transform: scale(1);
    filter: drop-shadow(0 0 0 rgba(79, 70, 229, 0));
  }
  48% {
    transform: scale(1.015);
    filter: drop-shadow(0 0 5px rgba(79, 70, 229, 0.14));
  }
}

@keyframes topology-flow-sink-glow {
  0%,
  100% {
    border-color: rgb(226, 232, 240);
    filter: drop-shadow(0 0 0 rgba(5, 150, 105, 0));
  }
  72% {
    border-color: rgb(5, 150, 105);
    filter: drop-shadow(0 0 5px rgba(5, 150, 105, 0.16));
  }
}
`;

export function isTopologyFlowActive(task: Pick<Task, 'viewStatus' | 'throughputPerMin'>) {
  return task.viewStatus === 'running' && Number.isFinite(task.throughputPerMin) && task.throughputPerMin > 0;
}

export function getTopologyFlowDurationSeconds(throughputPerMin: number) {
  if (!Number.isFinite(throughputPerMin) || throughputPerMin <= 0) return MAX_FLOW_DURATION_SECONDS;
  const normalized = Math.min(
    1,
    Math.log10(Math.min(throughputPerMin, MAX_THROUGHPUT_FOR_SPEED) + 1) /
      Math.log10(MAX_THROUGHPUT_FOR_SPEED + 1),
  );
  return Number(
    (MAX_FLOW_DURATION_SECONDS - normalized * (MAX_FLOW_DURATION_SECONDS - MIN_FLOW_DURATION_SECONDS)).toFixed(2),
  );
}

function coreQueueName(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  const withoutQuery = trimmed.split(/[?#]/, 1)[0].replace(/\/+$/, '');
  const lastSegment = withoutQuery.split('/').pop()?.trim();
  return lastSegment || trimmed;
}

export function getTopologySourceLabel(
  task: Pick<Task, 'pipelineSourceLabel' | 'sourceKind' | 'sourceConfig' | 'sourceName'>,
) {
  if (task.sourceKind !== 'sqs_queue') return task.pipelineSourceLabel;
  return (
    coreQueueName(task.sourceConfig?.url) ??
    coreQueueName(task.sourceName) ??
    coreQueueName(task.pipelineSourceLabel) ??
    task.pipelineSourceLabel
  );
}

function TopologyConnector({ isFlowing, testId }: { isFlowing: boolean; testId: string }) {
  return (
    <div
      data-testid={`${testId}-frame`}
      className="flex-1 flex flex-col sm:flex-row items-center justify-center relative w-1 sm:w-full min-h-[24px] sm:min-h-0 sm:h-20 sm:self-start"
    >
      <div
        data-testid={testId}
        data-flowing={isFlowing ? 'true' : 'false'}
        className={`topology-flow-track relative w-0.5 sm:w-full h-8 sm:h-0.5 rounded-full bg-slate-200 overflow-hidden ${
          isFlowing ? 'topology-flow-track-active' : ''
        }`}
      >
        {isFlowing && (
          <>
            <span data-testid="topology-flow-packet" aria-hidden="true" className="topology-flow-packet" />
            <span
              data-testid="topology-flow-packet"
              aria-hidden="true"
              className="topology-flow-packet topology-flow-packet-delay"
            />
          </>
        )}
      </div>
      <ArrowRight className="w-4 h-4 text-slate-400 absolute bg-white p-0.5 rounded-full border border-slate-100 rotate-90 sm:rotate-0" />
    </div>
  );
}

export default function TopologyFlow({ task, resourceCatalog }: TopologyFlowProps) {
  const { t } = useI18n();
  const [selectedNode, setSelectedNode] = useState<'source' | 'task' | 'sink' | null>(null);

  const isRunning = task.viewStatus === 'running';
  const isFlowing = isTopologyFlowActive(task);
  const sourceLabel = getTopologySourceLabel(task);
  const flowDurationSeconds = getTopologyFlowDurationSeconds(task.throughputPerMin);
  const topologyStyle = {
    '--topology-flow-duration': `${flowDurationSeconds}s`,
    '--topology-flow-delay': `${(-flowDurationSeconds * 0.48).toFixed(2)}s`,
    '--topology-stage-duration': `${Math.max(1.6, flowDurationSeconds * 1.8).toFixed(2)}s`,
  } as TopologyFlowStyle;

  const kind = task.sourceKind ?? task.pipelineSource;
  const sourceDetails = buildSourceDetails(kind, resourceCatalog);
  const sourceRows = sourceDetails.rows
    .map((row) => {
      const resolved = resolveRowValue(row, kind, task.sourceConfig, task.sourceName, t);
      return resolved ? { labelKey: row.labelKey, label: row.label, configKey: row.configKey, ...resolved } : null;
    })
    .filter((row): row is NonNullable<typeof row> => row !== null);

  const sinkKind = task.sinkKind ?? task.pipelineSink;
  const sinkDetails = buildSinkDetails(sinkKind, resourceCatalog);
  const sinkRows = sinkDetails.rows
    .map((row) => {
      const resolved = resolveRowValue(row, sinkKind, task.sinkConfig, task.sinkName, t);
      return resolved ? { labelKey: row.labelKey, label: row.label, configKey: row.configKey, ...resolved } : null;
    })
    .filter((row): row is NonNullable<typeof row> => row !== null);

  // Node details generator for the task and sink panels (the source panel is
  // rendered separately via sourceRows above).
  const getNodeDetails = () => {
    switch (selectedNode) {
      case 'source':
        return {
          title: t(sourceDetails.titleKey, { source: task.pipelineSource }),
          type: sourceDetails.typeLabel,
        };
      case 'task':
        return {
          title: t('topology.taskTitle', { task: task.name }),
          type: t('topology.eventTransform'),
          concurrency: t('topology.workerThreads', { count: task.concurrency }),
          uptime: formatRelativeTime(task.uptimeReferenceAt, t),
          throughput: `${task.throughputPerMin}/min`,
          retryPolicy: t('topology.exponentialRetries', { count: task.retryAttempts }),
        };
      case 'sink':
        return {
          title: t(sinkDetails.titleKey, { sink: task.pipelineSink }),
          type: sinkDetails.typeLabel,
        };
      default:
        return null;
    }
  };

  const activeDetails = getNodeDetails();

  return (
    <div className="bg-white border border-slate-200 rounded-xl flex flex-col shadow-xs">
      <style>{topologyFlowStyles}</style>
      <div className="p-4 border-b border-slate-200 flex justify-between items-center bg-slate-50 rounded-t-xl">
        <h3 className="font-sans text-sm font-bold text-slate-800 flex items-center gap-2">
          <ArrowRightLeft className="w-4 h-4 text-indigo-600" />
          <span>{t('topology.title')}</span>
        </h3>
        <span className="text-[11px] text-slate-500 font-medium hidden sm:inline">
          {t('topology.clickHint')}
        </span>
      </div>

      <div className="p-6 md:p-8 flex flex-col items-center justify-center bg-slate-50/30 min-h-[220px]">
        {/* Topology Diagram */}
        <div
          data-testid="topology-flow-diagram"
          data-flowing={isFlowing ? 'true' : 'false'}
          style={topologyStyle}
          className="flex flex-col sm:flex-row items-center gap-4 w-full max-w-2xl justify-between"
        >
          {/* Source node */}
          <button
            onClick={() => setSelectedNode('source')}
            className={`flex flex-col items-center gap-2 group focus:outline-hidden transition-all ${
              selectedNode === 'source' ? 'scale-105' : ''
            }`}
          >
            <div
              className={`w-16 h-16 bg-white border-2 rounded-lg flex items-center justify-center shadow-xs transition-all ${
                selectedNode === 'source'
                  ? 'border-indigo-600 ring-3 ring-indigo-100'
                  : 'border-slate-200 group-hover:border-slate-400'
              } ${isFlowing ? 'topology-flow-node-source-active' : ''}`}
            >
              <Database className="w-6 h-6 text-slate-500 group-hover:text-slate-800 transition-colors" />
            </div>
            <div className="text-center">
              <div className="font-sans text-xs font-bold text-slate-800">{task.pipelineSource}</div>
              <div className="font-mono text-[10px] text-slate-400 font-medium">
                {sourceLabel}
              </div>
            </div>
          </button>

          {/* Connecting Line 1 */}
          <TopologyConnector isFlowing={isFlowing} testId="topology-source-connector" />

          {/* Current Node (Focus) */}
          <button
            onClick={() => setSelectedNode('task')}
            className={`flex flex-col items-center gap-2 group focus:outline-hidden transition-all ${
              selectedNode === 'task' ? 'scale-105' : ''
            }`}
          >
            <div
              className={`w-20 h-20 bg-indigo-50/50 border-2 rounded-xl flex items-center justify-center shadow-sm relative transition-all ${
                selectedNode === 'task'
                  ? 'border-indigo-600 ring-4 ring-indigo-100'
                  : 'border-indigo-600/80 group-hover:border-indigo-600 group-hover:shadow-md'
              } ${isFlowing ? 'topology-flow-node-handler-active' : ''}`}
            >
              <Cpu className="w-8 h-8 text-indigo-600" />
              {/* Pulsing status dot */}
              <span className="absolute -top-1 -right-1 flex h-3.5 w-3.5">
                <span
                  className={`absolute inline-flex h-full w-full rounded-full opacity-75 ${
                    isFlowing ? 'animate-ping bg-emerald-400' : isRunning ? 'bg-emerald-400' : 'bg-slate-300'
                  }`}
                />
                <span
                  className={`relative inline-flex rounded-full h-3.5 w-3.5 border-2 border-white ${
                    isRunning ? 'bg-emerald-500' : 'bg-slate-400'
                  }`}
                />
              </span>
            </div>
            <div className="text-center">
              <div className="font-sans text-xs font-extrabold text-indigo-600">{task.name}</div>
              <div className="font-mono text-[10px] text-slate-400 font-medium">{t('topology.taskProcessing')}</div>
            </div>
          </button>

          {/* Connecting Line 2 */}
          <TopologyConnector isFlowing={isFlowing} testId="topology-sink-connector" />

          {/* Sink node */}
          <button
            onClick={() => setSelectedNode('sink')}
            className={`flex flex-col items-center gap-2 group focus:outline-hidden transition-all ${
              selectedNode === 'sink' ? 'scale-105' : ''
            }`}
          >
            <div
              className={`w-16 h-16 bg-white border-2 rounded-lg flex items-center justify-center shadow-xs transition-all ${
                selectedNode === 'sink'
                  ? 'border-indigo-600 ring-3 ring-indigo-100'
                  : 'border-slate-200 group-hover:border-slate-400'
              } ${isFlowing ? 'topology-flow-node-sink-active' : ''}`}
            >
              <HardDrive className="w-6 h-6 text-slate-500 group-hover:text-slate-800 transition-colors" />
            </div>
            <div className="text-center">
              <div className="font-sans text-xs font-bold text-slate-800">{task.pipelineSink}</div>
              <div className="font-mono text-[10px] text-slate-400 font-medium">
                {task.pipelineSinkLabel}
              </div>
            </div>
          </button>
        </div>

        {/* Node detail slide-over or collapsible info block */}
        {activeDetails && (
          <div className="ui-panel-state-enter mt-6 w-full rounded-lg border border-indigo-100/80 bg-indigo-50/40 p-4">
            <div className="flex items-center gap-2 mb-3 border-b border-indigo-100/50 pb-2">
              <Info className="w-4 h-4 text-indigo-600" />
              <h4 className="font-sans text-xs font-bold text-indigo-900">{activeDetails.title}</h4>
              <span className="text-[10px] font-semibold text-slate-400 ml-auto uppercase bg-white border border-slate-100 px-1.5 py-0.5 rounded">
                {activeDetails.type}
              </span>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-xs font-medium text-slate-600">
              {selectedNode === 'source' &&
                sourceRows.map((row) => (
                  <div key={row.labelKey ?? row.configKey ?? row.label} className="min-w-0">
                    <span className="text-[10px] text-slate-400 uppercase font-bold block mb-0.5">
                      {row.labelKey ? t(row.labelKey) : row.label}
                    </span>
                    <span
                      className={`block break-all ${
                        row.mono ? 'font-mono' : 'font-semibold'
                      } ${row.placeholder ? 'text-slate-400 italic' : 'text-slate-800'}`}
                    >
                      {row.value}
                    </span>
                  </div>
                ))}

              {selectedNode === 'task' && (
                <>
                  <div>
                    <span className="text-[10px] text-slate-400 uppercase font-bold block mb-0.5">{t('topology.activeWorkers')}</span>
                    <span className="text-slate-800 font-bold">{activeDetails.concurrency}</span>
                  </div>
                  <div>
                    <span className="text-[10px] text-slate-400 uppercase font-bold block mb-0.5">{t('common.lastRun')}</span>
                    <span className="text-slate-800">{activeDetails.uptime}</span>
                  </div>
                  <div>
                    <span className="text-[10px] text-slate-400 uppercase font-bold block mb-0.5">{t('topology.throughputTarget')}</span>
                    <span className="text-indigo-600 font-bold">{activeDetails.throughput}</span>
                  </div>
                </>
              )}

              {selectedNode === 'sink' &&
                sinkRows.map((row) => (
                  <div key={row.labelKey ?? row.configKey ?? row.label} className="min-w-0">
                    <span className="text-[10px] text-slate-400 uppercase font-bold block mb-0.5">
                      {row.labelKey ? t(row.labelKey) : row.label}
                    </span>
                    <span
                      className={`block break-all ${
                        row.mono ? 'font-mono' : 'font-semibold'
                      } ${row.placeholder ? 'text-slate-400 italic' : 'text-slate-800'}`}
                    >
                      {row.value}
                    </span>
                  </div>
                ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
