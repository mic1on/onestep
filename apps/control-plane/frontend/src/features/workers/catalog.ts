export interface SourceSinkField {
  name: string;
  label: string;
  type: "text" | "number" | "password" | "list" | "select" | "json";
  required: boolean;
  options?: string[];
  placeholder?: string;
  defaultValue?: string | number | string[];
}

export interface SourceSinkTypeSchema {
  label: string;
  needsConnector: boolean;
  fields: SourceSinkField[];
}

export const sourceTypeSchemas: Record<string, SourceSinkTypeSchema> = {
  interval: {
    label: "Interval (schedule)",
    needsConnector: false,
    fields: [
      { name: "minutes", label: "Run every", type: "number", required: false },
      { name: "seconds", label: "Run every", type: "number", required: false },
      { name: "immediate", label: "Startup run", type: "text", required: false },
    ],
  },
  cron: {
    label: "Cron (schedule)",
    needsConnector: false,
    fields: [
      { name: "expression", label: "Cron Expression", type: "text", required: true, placeholder: "0 */5 * * *" },
    ],
  },
  webhook: {
    label: "Webhook (HTTP)",
    needsConnector: false,
    fields: [
      { name: "path", label: "Path", type: "text", required: true, placeholder: "/hook" },
      { name: "methods", label: "Methods", type: "list", required: false, placeholder: "POST" },
      { name: "port", label: "Port", type: "number", required: false },
    ],
  },
  rabbitmq_queue: {
    label: "RabbitMQ Queue",
    needsConnector: true,
    fields: [
      { name: "queue", label: "Queue", type: "text", required: true },
      { name: "exchange", label: "Exchange", type: "text", required: false },
    ],
  },
  redis_stream: {
    label: "Redis Stream",
    needsConnector: true,
    fields: [
      { name: "stream", label: "Stream", type: "text", required: true },
      { name: "group", label: "Consumer Group", type: "text", required: false },
    ],
  },
  sqs_queue: {
    label: "AWS SQS Queue",
    needsConnector: true,
    fields: [
      { name: "url", label: "Queue URL", type: "text", required: true },
    ],
  },
  feishu_bitable_incremental: {
    label: "Feishu Bitable Incremental",
    needsConnector: true,
    fields: [
      { name: "app_token", label: "App Token", type: "text", required: true },
      { name: "table_id", label: "Table ID", type: "text", required: true },
      { name: "cursor_field", label: "Cursor Field", type: "text", required: true },
      { name: "user_id_type", label: "User ID Type", type: "select", required: false, options: ["open_id", "union_id", "user_id"] },
    ],
  },
  mysql_incremental: {
    label: "MySQL Incremental",
    needsConnector: true,
    fields: [
      { name: "table", label: "Table", type: "text", required: true },
      { name: "key", label: "Key Column", type: "text", required: true },
      { name: "cursor", label: "Cursor Columns", type: "list", required: true },
    ],
  },
  mysql_table_queue: {
    label: "MySQL Table Queue",
    needsConnector: true,
    fields: [
      { name: "table", label: "Table", type: "text", required: true },
      { name: "key", label: "Key Column", type: "text", required: true },
    ],
  },
  postgres_incremental: {
    label: "PostgreSQL Incremental",
    needsConnector: true,
    fields: [
      { name: "table", label: "Table", type: "text", required: true },
      { name: "key", label: "Key Column", type: "text", required: true },
      { name: "cursor", label: "Cursor Columns", type: "list", required: true },
    ],
  },
  postgres_table_queue: {
    label: "PostgreSQL Table Queue",
    needsConnector: true,
    fields: [
      { name: "table", label: "Table", type: "text", required: true },
      { name: "key", label: "Key Column", type: "text", required: true },
    ],
  },
};

export const sinkTypeSchemas: Record<string, SourceSinkTypeSchema> = {
  http_sink: {
    label: "HTTP Sink",
    needsConnector: false,
    fields: [
      {
        name: "url",
        label: "URL",
        type: "text",
        required: true,
        placeholder: "https://api.example.com/orders/{{ body.order_id }}",
      },
      {
        name: "method",
        label: "Method",
        type: "select",
        required: false,
        options: ["GET", "POST", "PUT", "PATCH", "DELETE"],
        defaultValue: "POST",
      },
      {
        name: "headers",
        label: "Headers",
        type: "json",
        required: false,
        placeholder: '{\n  "X-Trace-Id": "{{ meta.trace_id }}"\n}',
      },
      {
        name: "params",
        label: "Query params",
        type: "json",
        required: false,
        placeholder: '{\n  "attempt": "{{ attempts }}"\n}',
      },
      {
        name: "body",
        label: "Body",
        type: "json",
        required: false,
        placeholder: '{\n  "order_id": "{{ body.order_id }}"\n}',
      },
      { name: "timeout_s", label: "Timeout seconds", type: "number", required: false, placeholder: "5" },
      {
        name: "success_statuses",
        label: "Success statuses",
        type: "json",
        required: false,
        placeholder: "[200, 202]",
      },
    ],
  },
  rabbitmq_queue: {
    label: "RabbitMQ Queue",
    needsConnector: true,
    fields: [
      { name: "queue", label: "Queue", type: "text", required: true },
    ],
  },
  redis_stream: {
    label: "Redis Stream",
    needsConnector: true,
    fields: [
      { name: "stream", label: "Stream", type: "text", required: true },
    ],
  },
  sqs_queue: {
    label: "AWS SQS Queue",
    needsConnector: true,
    fields: [
      { name: "url", label: "Queue URL", type: "text", required: true },
    ],
  },
  feishu_bitable_table_sink: {
    label: "Feishu Bitable Table Sink",
    needsConnector: true,
    fields: [
      { name: "app_token", label: "App Token", type: "text", required: true },
      { name: "table_id", label: "Table ID", type: "text", required: true },
      { name: "mode", label: "Mode", type: "select", required: false, options: ["upsert", "create", "update"] },
      { name: "match_fields", label: "Match Fields", type: "list", required: false },
      { name: "user_id_type", label: "User ID Type", type: "select", required: false, options: ["open_id", "union_id", "user_id"] },
    ],
  },
  mysql_table_sink: {
    label: "MySQL Table Sink",
    needsConnector: true,
    fields: [
      { name: "table", label: "Table", type: "text", required: true },
      { name: "mode", label: "Mode", type: "select", required: false, options: ["insert", "upsert"] },
      { name: "keys", label: "Upsert Keys", type: "list", required: false },
    ],
  },
  postgres_table_sink: {
    label: "PostgreSQL Table Sink",
    needsConnector: true,
    fields: [
      { name: "table", label: "Table", type: "text", required: true },
      { name: "mode", label: "Mode", type: "select", required: false, options: ["insert", "upsert"] },
      { name: "keys", label: "Upsert Keys", type: "list", required: false },
    ],
  },
};

export const sourceTypeOrder = Object.keys(sourceTypeSchemas).sort();
export const sinkTypeOrder = Object.keys(sinkTypeSchemas).sort();
