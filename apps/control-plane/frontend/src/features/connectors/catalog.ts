import type { ConnectorType } from "../../lib/api/types";

export interface ConnectorField {
  name: string;
  label: string;
  type: "text" | "password";
  required: boolean;
  placeholder?: string;
}

export interface ConnectorTypeSchema {
  label: string;
  fields: ConnectorField[];
}

export const connectorTypeSchemas: Record<ConnectorType, ConnectorTypeSchema> = {
  mysql: {
    label: "MySQL",
    fields: [
      {
        name: "host",
        label: "Host",
        type: "text",
        required: true,
        placeholder: "10.0.0.1",
      },
      {
        name: "port",
        label: "Port",
        type: "text",
        required: false,
        placeholder: "3306",
      },
      {
        name: "database",
        label: "Database",
        type: "text",
        required: true,
        placeholder: "orders",
      },
      {
        name: "username",
        label: "Username",
        type: "text",
        required: false,
        placeholder: "user@example.com",
      },
      {
        name: "password",
        label: "Password",
        type: "password",
        required: true,
        placeholder: "secret",
      },
    ],
  },
  postgres: {
    label: "PostgreSQL",
    fields: [
      {
        name: "host",
        label: "Host",
        type: "text",
        required: true,
        placeholder: "db.example.internal",
      },
      {
        name: "port",
        label: "Port",
        type: "text",
        required: false,
        placeholder: "5432",
      },
      {
        name: "database",
        label: "Database",
        type: "text",
        required: true,
        placeholder: "warehouse",
      },
      {
        name: "username",
        label: "Username",
        type: "text",
        required: false,
        placeholder: "user@example.com",
      },
      {
        name: "password",
        label: "Password",
        type: "password",
        required: true,
        placeholder: "secret",
      },
    ],
  },
  redis: {
    label: "Redis",
    fields: [
      {
        name: "host",
        label: "Host",
        type: "text",
        required: true,
        placeholder: "redis.example.internal",
      },
      {
        name: "port",
        label: "Port",
        type: "text",
        required: false,
        placeholder: "6379",
      },
      {
        name: "database",
        label: "Database",
        type: "text",
        required: false,
        placeholder: "0",
      },
      {
        name: "username",
        label: "Username",
        type: "text",
        required: false,
      },
      {
        name: "password",
        label: "Password",
        type: "password",
        required: false,
        placeholder: "secret",
      },
    ],
  },
  rabbitmq: {
    label: "RabbitMQ",
    fields: [
      {
        name: "host",
        label: "Host",
        type: "text",
        required: true,
        placeholder: "rabbitmq.example.internal",
      },
      {
        name: "port",
        label: "Port",
        type: "text",
        required: false,
        placeholder: "5672",
      },
      {
        name: "vhost",
        label: "Virtual Host",
        type: "text",
        required: false,
        placeholder: "/",
      },
      {
        name: "username",
        label: "Username",
        type: "text",
        required: false,
      },
      {
        name: "password",
        label: "Password",
        type: "password",
        required: false,
        placeholder: "secret",
      },
    ],
  },
  sqs: {
    label: "AWS SQS",
    fields: [
      {
        name: "region_name",
        label: "Region",
        type: "text",
        required: false,
        placeholder: "us-east-1",
      },
    ],
  },
  feishu_bitable: {
    label: "Feishu Bitable",
    fields: [
      { name: "app_id", label: "App ID", type: "text", required: true },
      { name: "app_secret", label: "App Secret", type: "password", required: true },
      {
        name: "base_url",
        label: "Base URL",
        type: "text",
        required: false,
        placeholder: "https://open.feishu.cn",
      },
    ],
  },
  http: {
    label: "HTTP Endpoint",
    fields: [
      {
        name: "url",
        label: "URL",
        type: "password",
        required: true,
        placeholder: "https://...",
      },
    ],
  },
};

export const connectorTypeOrder: ConnectorType[] = [
  "mysql",
  "postgres",
  "redis",
  "rabbitmq",
  "sqs",
  "feishu_bitable",
  "http",
];
