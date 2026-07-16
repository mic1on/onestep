import { Service, Task, Instance, LogEntry } from './types';

export const INITIAL_SERVICES: Service[] = [
  {
    id: 'user-auth-service',
    name: 'user-auth-service / prod',
    status: 'running',
    uptime: '4d 12h',
    throughput: '1.2k events/sec',
    throughputValue: 1200,
    successRate: 99.98,
    errorCount24h: 12,
    totalInstances: 24,
    activeInstances: 18,
    standbyInstances: 6,
    taskHealth: 99.9,
    taskHealthTrend: '+0.1%',
  },
  {
    id: 'payment-gateway',
    name: 'Payment Gateway (prod)',
    status: 'running',
    uptime: '12d 6h',
    throughput: '4.2k req/sec',
    throughputValue: 4200,
    successRate: 99.92,
    errorCount24h: 42,
    totalInstances: 16,
    activeInstances: 12,
    standbyInstances: 4,
    taskHealth: 99.5,
    taskHealthTrend: '+0.05%',
  },
  {
    id: 'notification-service',
    name: 'notification-service / prod',
    status: 'degraded',
    uptime: '2d 1h',
    throughput: '340 msg/sec',
    throughputValue: 340,
    successRate: 98.45,
    errorCount24h: 185,
    totalInstances: 8,
    activeInstances: 5,
    standbyInstances: 3,
    taskHealth: 96.2,
    taskHealthTrend: '-1.2%',
  }
];

export const INITIAL_TASKS: Task[] = [
  {
    id: 'ingest-logs',
    serviceId: 'user-auth-service',
    name: 'Ingest-Logs',
    status: 'Running',
    pipelineSource: 'Kafka',
    pipelineSourceLabel: 'logs-raw',
    pipelineSink: 'ClickHouse',
    pipelineSinkLabel: 'system_logs',
    concurrency: 4,
    retryAttempts: 3,
    uptime: '4d 12h',
    throughputValue: '1.2k events/sec',
    throughputNum: 1200,
    successRate: 99.98,
    errorCount: 12,
    configYaml: `task_config:
  id: "ingest-logs-prod-01"
  topology_hash: "a1b2c3d4e5f6"

  execution:
    concurrency: 4
    retry_policy:
      attempts: 3
      strategy: "exponential_backoff"

  source:
    type: "kafka"
    cluster: "kafka-cluster-01"
    topic: "logs-raw"
    consumer_group: "ingest-v2"

  sink:
    type: "clickhouse"
    cluster: "clickhouse-cluster-01"
    database: "telemetry"
    table: "system_logs"
    batch_size: 5000`
  },
  {
    id: 'process-metrics',
    serviceId: 'user-auth-service',
    name: 'Process-Metrics',
    status: 'Running',
    pipelineSource: 'Prometheus',
    pipelineSourceLabel: 'raw-metrics',
    pipelineSink: 'InfluxDB',
    pipelineSinkLabel: 'aggregate_metrics',
    concurrency: 8,
    retryAttempts: 5,
    uptime: '4d 12h',
    throughputValue: '2.4k points/sec',
    throughputNum: 2400,
    successRate: 99.95,
    errorCount: 8,
    configYaml: `task_config:
  id: "process-metrics-prod-01"
  topology_hash: "d4e5f6g7h8i9"

  execution:
    concurrency: 8
    retry_policy:
      attempts: 5
      strategy: "exponential_backoff"

  source:
    type: "prometheus"
    cluster: "prom-cluster-02"
    topic: "metrics-scrape"
    consumer_group: "metrics-v1"

  sink:
    type: "influxdb"
    cluster: "influx-db-cluster-01"
    database: "metrics"
    table: "node_metrics"
    batch_size: 10000`
  },
  {
    id: 'payment-validation',
    serviceId: 'payment-gateway',
    name: 'Payment-Validation',
    status: 'Running',
    pipelineSource: 'RabbitMQ',
    pipelineSourceLabel: 'tx-pending',
    pipelineSink: 'PostgreSQL',
    pipelineSinkLabel: 'ledger_verified',
    concurrency: 12,
    retryAttempts: 3,
    uptime: '12d 6h',
    throughputValue: '1.5k tx/sec',
    throughputNum: 1500,
    successRate: 99.99,
    errorCount: 2,
    configYaml: `task_config:
  id: "payment-validation-prod"
  topology_hash: "v1a2l3i4d5a6"

  execution:
    concurrency: 12
    retry_policy:
      attempts: 3
      strategy: "immediate_retry"

  source:
    type: "rabbitmq"
    cluster: "rabbit-mq-cluster-05"
    topic: "payments-raw"
    consumer_group: "payment-validator"

  sink:
    type: "postgresql"
    cluster: "rds-postgres-prod"
    database: "payment_records"
    table: "verified_tx"
    batch_size: 2000`
  },
  {
    id: 'fraud-analyzer',
    serviceId: 'payment-gateway',
    name: 'Fraud-Analyzer',
    status: 'Running',
    pipelineSource: 'Kafka',
    pipelineSourceLabel: 'tx-stream',
    pipelineSink: 'Redis',
    pipelineSinkLabel: 'blacklists',
    concurrency: 6,
    retryAttempts: 4,
    uptime: '12d 6h',
    throughputValue: '2.7k tx/sec',
    throughputNum: 2700,
    successRate: 99.85,
    errorCount: 40,
    configYaml: `task_config:
  id: "fraud-analyzer-prod"
  topology_hash: "f7r8a9u0d1a2"

  execution:
    concurrency: 6
    retry_policy:
      attempts: 4
      strategy: "exponential_backoff"

  source:
    type: "kafka"
    cluster: "kafka-cluster-tx"
    topic: "tx-stream"
    consumer_group: "fraud-agent"

  sink:
    type: "redis"
    cluster: "redis-cluster-cache"
    database: "0"
    table: "fraud_scores"
    batch_size: 1500`
  },
  {
    id: 'email-dispatcher',
    serviceId: 'notification-service',
    name: 'Email-Dispatcher',
    status: 'Running',
    pipelineSource: 'ScyllaDB',
    pipelineSourceLabel: 'pending-emails',
    pipelineSink: 'SES-Gateway',
    pipelineSinkLabel: 'delivered-emails',
    concurrency: 2,
    retryAttempts: 2,
    uptime: '2d 1h',
    throughputValue: '180 mail/sec',
    throughputNum: 180,
    successRate: 98.9,
    errorCount: 65,
    configYaml: `task_config:
  id: "email-dispatcher-prod"
  topology_hash: "e3m1a4i5l2d9"

  execution:
    concurrency: 2
    retry_policy:
      attempts: 2
      strategy: "linear_retry"

  source:
    type: "scylladb"
    cluster: "scylla-notification"
    topic: "email-queue"
    consumer_group: "dispatcher-group"

  sink:
    type: "ses-gateway"
    cluster: "aws-ses-us-east-1"
    database: "emails"
    table: "sent"
    batch_size: 100`
  },
  {
    id: 'sms-relay',
    serviceId: 'notification-service',
    name: 'SMS-Relay',
    status: 'Stopped',
    pipelineSource: 'ActiveMQ',
    pipelineSourceLabel: 'sms-pending',
    pipelineSink: 'Twilio',
    pipelineSinkLabel: 'sms-sent',
    concurrency: 4,
    retryAttempts: 3,
    uptime: '0d 0h',
    throughputValue: '0 sms/sec',
    throughputNum: 0,
    successRate: 100.0,
    errorCount: 120,
    configYaml: `task_config:
  id: "sms-relay-prod"
  topology_hash: "s9m8s7r6e5l4"

  execution:
    concurrency: 4
    retry_policy:
      attempts: 3
      strategy: "exponential_backoff"

  source:
    type: "activemq"
    cluster: "active-mq-queue"
    topic: "sms-queue"
    consumer_group: "relay-group"

  sink:
    type: "twilio"
    cluster: "twilio-api-direct"
    database: "messages"
    table: "delivered"
    batch_size: 50`
  }
];

export const INITIAL_INSTANCES: Instance[] = [
  // user-auth-service instances
  {
    uuid: '8b2f4c-9a12',
    serviceId: 'user-auth-service',
    hostname: 'prod-svc-01',
    nodeName: 'node-a',
    pid: 1234,
    version: 'v1.2.0',
    status: 'Running',
  },
  {
    uuid: 'a1e9d3-44f0',
    serviceId: 'user-auth-service',
    hostname: 'prod-svc-02',
    nodeName: 'node-b',
    pid: 5678,
    version: 'v1.2.0',
    status: 'Running',
  },
  {
    uuid: 'c7d2e1-88bc',
    serviceId: 'user-auth-service',
    hostname: 'prod-svc-03',
    nodeName: 'node-c',
    pid: 9012,
    version: 'v1.2.1',
    status: 'Starting',
  },
  {
    uuid: 'f4a5b6-21aa',
    serviceId: 'user-auth-service',
    hostname: 'prod-svc-04',
    nodeName: 'node-a',
    pid: 3456,
    version: 'v1.2.0',
    status: 'Failed',
  },
  {
    uuid: 'e2b5d1-72bc',
    serviceId: 'user-auth-service',
    hostname: 'prod-svc-05',
    nodeName: 'node-b',
    pid: 7890,
    version: 'v1.2.0',
    status: 'Running',
  },
  {
    uuid: 'f1a9b2-38cd',
    serviceId: 'user-auth-service',
    hostname: 'prod-svc-06',
    nodeName: 'node-c',
    pid: 2345,
    version: 'v1.2.0',
    status: 'Running',
  },
  {
    uuid: '6b5e4d-12ab',
    serviceId: 'user-auth-service',
    hostname: 'prod-svc-07',
    nodeName: 'node-a',
    pid: 6789,
    version: 'v1.2.1',
    status: 'Stopped',
  },

  // payment-gateway instances
  {
    uuid: 'p1a2b3-0012',
    serviceId: 'payment-gateway',
    hostname: 'pay-gw-01',
    nodeName: 'node-x',
    pid: 4321,
    version: 'v2.4.1',
    status: 'Running',
  },
  {
    uuid: 'p2c3d4-1123',
    serviceId: 'payment-gateway',
    hostname: 'pay-gw-02',
    nodeName: 'node-y',
    pid: 8765,
    version: 'v2.4.1',
    status: 'Running',
  },
  {
    uuid: 'p3e4f5-2234',
    serviceId: 'payment-gateway',
    hostname: 'pay-gw-03',
    nodeName: 'node-z',
    pid: 1098,
    version: 'v2.4.1',
    status: 'Running',
  },
  {
    uuid: 'p4g5h6-3345',
    serviceId: 'payment-gateway',
    hostname: 'pay-gw-04',
    nodeName: 'node-x',
    pid: 5432,
    version: 'v2.4.2-beta',
    status: 'Failed',
  },

  // notification-service instances
  {
    uuid: 'n1a2b3-5561',
    serviceId: 'notification-service',
    hostname: 'notif-svc-01',
    nodeName: 'node-alpha',
    pid: 9911,
    version: 'v3.0.4',
    status: 'Running',
  },
  {
    uuid: 'n2b3c4-6672',
    serviceId: 'notification-service',
    hostname: 'notif-svc-02',
    nodeName: 'node-beta',
    pid: 9912,
    version: 'v3.0.4',
    status: 'Running',
  },
  {
    uuid: 'n3c4d5-7783',
    serviceId: 'notification-service',
    hostname: 'notif-svc-03',
    nodeName: 'node-alpha',
    pid: 9913,
    version: 'v3.0.5',
    status: 'Failed',
  },
];

export const INITIAL_LOGS: LogEntry[] = [
  { timestamp: '10:50:12', level: 'info', message: 'Kafka Consumer initialized successfully.', source: 'Ingest-Logs' },
  { timestamp: '10:50:15', level: 'info', message: 'Connected to ClickHouse database "telemetry" on port 8123.', source: 'Ingest-Logs' },
  { timestamp: '10:50:18', level: 'info', message: 'Starting pipeline execution with concurrency of 4.', source: 'Ingest-Logs' },
  { timestamp: '10:51:00', level: 'info', message: 'Processed 5,000 events. Batch sink committed.', source: 'Ingest-Logs' },
  { timestamp: '10:52:05', level: 'warn', message: 'Delay detected in Kafka cluster broker partition 2. Retrying read...', source: 'Ingest-Logs' },
  { timestamp: '10:52:08', level: 'info', message: 'Kafka connection recovered. Processing backlog.', source: 'Ingest-Logs' },
  { timestamp: '10:53:12', level: 'info', message: 'Processed 5,000 events. Batch sink committed.', source: 'Ingest-Logs' },
  { timestamp: '10:53:45', level: 'error', message: 'Failed to write batch to ClickHouse: Connection timeout. Queueing to disk buffer.', source: 'Ingest-Logs' },
  { timestamp: '10:53:50', level: 'info', message: 'Retrying ClickHouse bulk write... Success.', source: 'Ingest-Logs' },
  { timestamp: '10:54:10', level: 'info', message: 'Processed 5,000 events. Batch sink committed.', source: 'Ingest-Logs' },
  { timestamp: '10:55:00', level: 'info', message: 'Metrics collector agent started scraping prod-svc-01.', source: 'Process-Metrics' },
  { timestamp: '10:55:02', level: 'info', message: 'Pushed 120 metrics data points to InfluxDB.', source: 'Process-Metrics' },
];

export const MOCK_TRACES = [
  { id: 'tr-88291', timestamp: '2026-07-14T10:53:45.102Z', path: 'logs-raw [partition 2]', duration: '241ms', status: 504, error: 'ClickHouse timeout' },
  { id: 'tr-88292', timestamp: '2026-07-14T10:52:05.441Z', path: 'logs-raw [partition 1]', duration: '12ms', status: 200, error: null },
  { id: 'tr-88293', timestamp: '2026-07-14T10:50:18.990Z', path: 'logs-raw [partition 0]', duration: '89ms', status: 200, error: null },
  { id: 'tr-88294', timestamp: '2026-07-14T10:48:11.233Z', path: 'logs-raw [partition 2]', duration: '310ms', status: 500, error: 'Database is locked' },
  { id: 'tr-88295', timestamp: '2026-07-14T10:45:02.112Z', path: 'logs-raw [partition 1]', duration: '45ms', status: 200, error: null },
];
