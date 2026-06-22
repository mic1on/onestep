import { describe, expect, it } from "vitest";

import { canImportConnectorUri, importConnectorUri } from "./uriImport";

describe("importConnectorUri", () => {
  it("imports MySQL URI fields and decodes credentials", () => {
    const result = importConnectorUri(
      "mysql",
      "mysql://ops%40example.com:pa%40ss@10.0.0.1:3307/orders",
    );

    expect(result).toEqual({
      ok: true,
      config: {
        host: "10.0.0.1",
        port: "3307",
        database: "orders",
        username: "ops@example.com",
      },
      secret: { password: "pa@ss" },
      suggestedName: "orders",
    });
  });

  it("imports Redis database from URI path", () => {
    const result = importConnectorUri("redis", "rediss://:secret@redis.internal:6380/2");

    expect(result).toEqual({
      ok: true,
      config: {
        host: "redis.internal",
        port: "6380",
        database: "2",
      },
      secret: { password: "secret" },
      suggestedName: "redis.internal",
    });
  });

  it("imports RabbitMQ virtual host from encoded URI path", () => {
    const result = importConnectorUri("rabbitmq", "amqp://guest:guest@mq.internal:5672/%2F");

    expect(result).toEqual({
      ok: true,
      config: {
        host: "mq.internal",
        port: "5672",
        vhost: "/",
        username: "guest",
      },
      secret: { password: "guest" },
      suggestedName: "mq.internal",
    });
  });

  it("imports SQS region from queue URL", () => {
    const result = importConnectorUri(
      "sqs",
      "https://sqs.us-east-1.amazonaws.com/123456789012/orders",
    );

    expect(result).toEqual({
      ok: true,
      config: { region_name: "us-east-1" },
      secret: {},
      suggestedName: "orders",
    });
  });

  it("stores HTTP URI in the secret url field", () => {
    const result = importConnectorUri("http", "https://example.com/webhooks/orders");

    expect(result).toEqual({
      ok: true,
      config: {},
      secret: { url: "https://example.com/webhooks/orders" },
      suggestedName: "example.com",
    });
  });

  it("rejects mismatched URI schemes", () => {
    expect(importConnectorUri("postgres", "mysql://user:pass@db/orders")).toEqual({
      ok: false,
      status: "scheme_mismatch",
    });
  });

  it("does not offer URI import for Feishu Bitable", () => {
    expect(canImportConnectorUri("feishu_bitable")).toBe(false);
  });
});
