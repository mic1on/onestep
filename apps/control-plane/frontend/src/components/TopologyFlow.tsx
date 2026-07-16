import { ArrowRight, Database, ArrowRightLeft, Server, Cpu, HardDrive, Info } from 'lucide-react';
import { Task } from '../types';
import { useState } from 'react';
import { useI18n } from '../i18n';

interface TopologyFlowProps {
  task: Task;
}

export default function TopologyFlow({ task }: TopologyFlowProps) {
  const { t } = useI18n();
  const [selectedNode, setSelectedNode] = useState<'source' | 'task' | 'sink' | null>(null);

  const isRunning = task.status === 'Running';

  // Node details generator
  const getNodeDetails = () => {
    switch (selectedNode) {
      case 'source':
        return {
          title: t('topology.sourceTitle', { source: task.pipelineSource }),
          type: t('topology.eventIngestion'),
          partitionCount: t('topology.partitions'),
          cluster: `${task.pipelineSource.toLowerCase()}-prod-cluster-01`,
          topic: task.pipelineSourceLabel,
          retention: t('topology.retention'),
          lag: t('topology.messages'),
        };
      case 'task':
        return {
          title: t('topology.taskTitle', { task: task.name }),
          type: t('topology.eventTransform'),
          concurrency: t('topology.workerThreads', { count: task.concurrency }),
          uptime: task.uptime,
          throughput: task.throughputValue,
          retryPolicy: t('topology.exponentialRetries', { count: task.retryAttempts }),
          bufferSize: t('topology.bufferSize'),
        };
      case 'sink':
        return {
          title: t('topology.sinkTitle', { sink: task.pipelineSink }),
          type: t('topology.sinkType'),
          database: 'telemetry',
          table: task.pipelineSinkLabel,
          batchSize: t('topology.batchSize'),
          cluster: `${task.pipelineSink.toLowerCase()}-replica-set-01`,
          writeLatency: '14ms',
        };
      default:
        return null;
    }
  };

  const activeDetails = getNodeDetails();

  return (
    <div className="bg-white border border-slate-200 rounded-xl flex flex-col shadow-xs">
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
        <div className="flex flex-col sm:flex-row items-center gap-4 w-full max-w-2xl justify-between">
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
              }`}
            >
              <Database className="w-6 h-6 text-slate-500 group-hover:text-slate-800 transition-colors" />
            </div>
            <div className="text-center">
              <div className="font-sans text-xs font-bold text-slate-800">{task.pipelineSource}</div>
              <div className="font-mono text-[10px] text-slate-400 font-medium">
                {task.pipelineSourceLabel}
              </div>
            </div>
          </button>

          {/* Connecting Line 1 */}
          <div className="flex-1 flex flex-col sm:flex-row items-center justify-center relative w-1 sm:w-full min-h-[24px] sm:min-h-0">
            <div className="w-0.5 sm:w-full h-8 sm:h-0.5 bg-slate-200" />
            <ArrowRight className="w-4 h-4 text-slate-400 absolute bg-white p-0.5 rounded-full border border-slate-100 rotate-90 sm:rotate-0" />
          </div>

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
              }`}
            >
              <Cpu className="w-8 h-8 text-indigo-600 animate-pulse" />
              {/* Pulsing status dot */}
              <span className="absolute -top-1 -right-1 flex h-3.5 w-3.5">
                <span
                  className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${
                    isRunning ? 'bg-emerald-400' : 'bg-slate-300'
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
          <div className="flex-1 flex flex-col sm:flex-row items-center justify-center relative w-1 sm:w-full min-h-[24px] sm:min-h-0">
            <div className="w-0.5 sm:w-full h-8 sm:h-0.5 bg-slate-200" />
            <ArrowRight className="w-4 h-4 text-slate-400 absolute bg-white p-0.5 rounded-full border border-slate-100 rotate-90 sm:rotate-0" />
          </div>

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
              }`}
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
          <div className="w-full mt-6 bg-indigo-50/40 border border-indigo-100/80 rounded-xl p-4 transition-all animate-fadeIn">
            <div className="flex items-center gap-2 mb-3 border-b border-indigo-100/50 pb-2">
              <Info className="w-4 h-4 text-indigo-600" />
              <h4 className="font-sans text-xs font-bold text-indigo-900">{activeDetails.title}</h4>
              <span className="text-[10px] font-semibold text-slate-400 ml-auto uppercase bg-white border border-slate-100 px-1.5 py-0.5 rounded">
                {activeDetails.type}
              </span>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-xs font-medium text-slate-600">
              {selectedNode === 'source' && (
                <>
                  <div>
                    <span className="text-[10px] text-slate-400 uppercase font-bold block mb-0.5">{t('topology.partitioning')}</span>
                    <span className="text-slate-800 font-semibold">{activeDetails.partitionCount}</span>
                  </div>
                  <div>
                    <span className="text-[10px] text-slate-400 uppercase font-bold block mb-0.5">{t('topology.kafkaCluster')}</span>
                    <span className="font-mono text-slate-800">{activeDetails.cluster}</span>
                  </div>
                  <div>
                    <span className="text-[10px] text-slate-400 uppercase font-bold block mb-0.5">{t('topology.streamLag')}</span>
                    <span className="text-emerald-600 font-bold">{activeDetails.lag}</span>
                  </div>
                  <div>
                    <span className="text-[10px] text-slate-400 uppercase font-bold block mb-0.5">{t('topology.retentionLimit')}</span>
                    <span className="text-slate-800">{activeDetails.retention}</span>
                  </div>
                </>
              )}

              {selectedNode === 'task' && (
                <>
                  <div>
                    <span className="text-[10px] text-slate-400 uppercase font-bold block mb-0.5">{t('topology.activeWorkers')}</span>
                    <span className="text-slate-800 font-bold">{activeDetails.concurrency}</span>
                  </div>
                  <div>
                    <span className="text-[10px] text-slate-400 uppercase font-bold block mb-0.5">{t('topology.uptimeStatus')}</span>
                    <span className="text-slate-800">{activeDetails.uptime}</span>
                  </div>
                  <div>
                    <span className="text-[10px] text-slate-400 uppercase font-bold block mb-0.5">{t('topology.throughputTarget')}</span>
                    <span className="text-indigo-600 font-bold">{activeDetails.throughput}</span>
                  </div>
                  <div>
                    <span className="text-[10px] text-slate-400 uppercase font-bold block mb-0.5">{t('topology.maxBuffer')}</span>
                    <span className="text-slate-800">{activeDetails.bufferSize}</span>
                  </div>
                </>
              )}

              {selectedNode === 'sink' && (
                <>
                  <div>
                    <span className="text-[10px] text-slate-400 uppercase font-bold block mb-0.5">{t('topology.relationalDb')}</span>
                    <span className="text-slate-800 font-semibold">{activeDetails.database}</span>
                  </div>
                  <div>
                    <span className="text-[10px] text-slate-400 uppercase font-bold block mb-0.5">{t('topology.table')}</span>
                    <span className="font-mono text-slate-800">{activeDetails.table}</span>
                  </div>
                  <div>
                    <span className="text-[10px] text-slate-400 uppercase font-bold block mb-0.5">{t('topology.sinkBatchSize')}</span>
                    <span className="text-slate-800 font-semibold">{activeDetails.batchSize}</span>
                  </div>
                  <div>
                    <span className="text-[10px] text-slate-400 uppercase font-bold block mb-0.5">{t('topology.avgWriteLatency')}</span>
                    <span className="text-emerald-600 font-bold">{activeDetails.writeLatency}</span>
                  </div>
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
