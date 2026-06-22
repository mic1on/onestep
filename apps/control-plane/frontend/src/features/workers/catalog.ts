export interface SourceSinkField {
  name: string;
  label: string;
  type: "text" | "number" | "password" | "list" | "select";
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
      { name: "minutes", label: "Minutes", type: "number", required: false },
      { name: "seconds", label: "Seconds", type: "number", required: false },
      { name: "immediate", label: "Immediate", type: "text", required: false },
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
      { name: "url", label: "URL", type: "text", required: true, placeholder: "https://..." },
      {
        name: "method",
        label: "Method",
        type: "select",
        required: false,
        options: ["GET", "POST", "PUT", "PATCH", "DELETE"],
        defaultValue: "POST",
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
