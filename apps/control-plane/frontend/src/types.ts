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
}

export interface Task {
  id: string;
  apiName?: string;
  apiServiceName?: string;
  environment?: 'dev' | 'staging' | 'prod';
  serviceId: string;
  name: string;
  status: 'Running' | 'Idle' | 'Stopped' | 'Failed';
  pipelineSource: string;
  pipelineSourceLabel: string;
  pipelineSink: string;
  pipelineSinkLabel: string;
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
