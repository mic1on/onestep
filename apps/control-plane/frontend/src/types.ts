export interface Service {
  id: string;
  apiName?: string;
  environment?: 'dev' | 'staging' | 'prod';
  name: string;
  status: 'running' | 'degraded' | 'stopped';
  uptime: string;
  throughput: string;
  throughputValue: number; // For rendering charts or calculations
  successRate: number;
  errorCount24h: number;
  totalInstances: number;
  activeInstances: number;
  standbyInstances: number;
  taskHealth: number;
  taskHealthTrend: string;
  totalTaskCount: number;
  failingTaskCount: number;
  onlineTaskCount: number;
}

/**
 * Free-form JSON object, mirroring the control-plane source/sink config dict.
 * Kept local to avoid coupling types.ts to api.ts internals.
 */
export type SourceConfig = Record<string, unknown>;

export interface Task {
  id: string;
  apiName?: string;
  apiServiceName?: string;
  environment?: 'dev' | 'staging' | 'prod';
  serviceId: string;
  name: string;
  status: 'Running' | 'Idle' | 'Stopped' | 'Failed' | 'Offline';
  pipelineSource: string;
  pipelineSourceLabel: string;
  /**
   * Raw connector kind reported by the worker (e.g. "mysql_incremental",
   * "kafka_topic", "interval"). Used to drive per-kind detail rendering.
   * Falls back to pipelineSource when the backend did not provide it.
   */
  sourceKind: string | null;
  /** Raw source config dict as reported by the worker agent. */
  sourceConfig: SourceConfig | null;
  sourceName: string | null;
  pipelineSink: string;
  pipelineSinkLabel: string;
  /** Raw sink (emit[0]) kind reported by the worker. */
  sinkKind: string | null;
  /** Raw sink config dict as reported by the worker agent. */
  sinkConfig: SourceConfig | null;
  sinkName: string | null;
  concurrency: number;
  retryAttempts: number;
  uptime: string;
  throughputValue: string;
  throughputNum: number;
  successRate: number;
  errorCount: number;
  configYaml: string;
}

export interface Instance {
  uuid: string;
  apiServiceName?: string;
  environment?: 'dev' | 'staging' | 'prod';
  serviceId: string;
  hostname: string;
  nodeName: string;
  pid: number;
  version: string;
  status: 'Running' | 'Starting' | 'Failed' | 'Stopped';
}

export interface LogEntry {
  timestamp: string;
  level: 'info' | 'warn' | 'error' | 'debug';
  message: string;
  source: string;
}
