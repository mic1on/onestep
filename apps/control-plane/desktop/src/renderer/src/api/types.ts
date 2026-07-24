export type Environment = "dev" | "staging" | "prod";
export type HealthStatus =
  | "healthy"
  | "degraded"
  | "offline"
  | "unknown"
  | "ok"
  | "error"
  | "starting";

export type ServiceSummary = {
  name: string;
  environment: Environment;
  health: HealthStatus;
  service_status?: "online" | "attention" | "offline";
  instance_count?: number;
  online_instance_count?: number;
  task_count?: number;
  last_seen_at?: string | null;
};

export type TaskSummary = {
  name: string;
  status?: string | null;
  success_count?: number;
  failure_count?: number;
  last_event_at?: string | null;
};

export type InstanceSummary = {
  instance_id: string;
  service_name?: string;
  status?: string | null;
  last_seen_at?: string | null;
};

export type CommandSummary = {
  id: string;
  status: string;
  command_type?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type WorkerAgentSummary = {
  id: string;
  name: string;
  status?: string | null;
  last_seen_at?: string | null;
};

export type WorkerSummary = {
  id: string;
  name: string;
  status?: string | null;
  updated_at?: string | null;
};

export type ConnectorSummary = {
  id: string;
  name: string;
  type: string;
  updated_at?: string | null;
};

export type PaginatedResponse<T> = {
  items: T[];
  total: number;
  limit?: number;
  offset?: number;
};
