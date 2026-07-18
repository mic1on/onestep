// Domain view models. Every status / metric field below is computed by the
// control plane; the frontend only formats scalars (uptime, throughput units)
// and maps enum values to i18n labels. No business logic lives here.

export interface Service {
  id: string;
  apiName?: string;
  environment?: 'dev' | 'staging' | 'prod';
  name: string;
  /** Derived by the plane from ServiceListStatus + online instance count. */
  viewStatus: 'running' | 'degraded' | 'stopped';
  /** Reference timestamp the UI uses to render a live "time since" label. */
  uptimeReferenceAt: string | null;
  /** Per-minute fetched throughput over the lookback window (scalar). */
  throughputPerMin: number;
  successRate: number;
  errorCount: number;
  totalInstances: number;
  activeInstances: number;
  standbyInstances: number;
  totalTaskCount: number;
  failingTaskCount: number;
  onlineTaskCount: number;
}

/**
 * Free-form JSON object, mirroring the control-plane source/sink config dict.
 * Kept local to avoid coupling types.ts to api.ts internals.
 */
export type SourceConfig = Record<string, unknown>;
export type TaskCommandKind =
  | 'pause_task'
  | 'resume_task'
  | 'restart_task'
  | 'discard_dead_letters'
  | 'replay_dead_letters'
  | 'run_task_once';

export interface Task {
  id: string;
  apiName?: string;
  apiServiceName?: string;
  environment?: 'dev' | 'staging' | 'prod';
  serviceId: string;
  name: string;
  /** Derived by the plane from online state, pause_requested, error_count, activity. */
  viewStatus: 'running' | 'idle' | 'failed' | 'paused' | 'offline';
  /** Commands currently supported by at least one online runtime target. */
  supportedCommands: TaskCommandKind[];
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
  /** Reference timestamp the UI uses to render a live "time since" label. */
  uptimeReferenceAt: string | null;
  throughputPerMin: number;
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
  /** Derived by the plane from the reported HealthStatus. */
  viewStatus: 'running' | 'starting' | 'failed' | 'stopped';
}

export interface LogEntry {
  timestamp: string;
  /** Derived by the plane from the event kind. */
  level: 'info' | 'warn' | 'error';
  message: string;
  source: string;
}
