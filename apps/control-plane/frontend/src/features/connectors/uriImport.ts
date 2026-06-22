import type { ConnectorType } from "../../lib/api/types";

export type ConnectorUriImportStatus =
  | "invalid"
  | "missing_region"
  | "scheme_mismatch"
  | "unsupported";

export type ConnectorUriImportResult =
  | {
      ok: true;
      config: Record<string, string>;
      secret: Record<string, string>;
      suggestedName: string | null;
    }
  | {
      ok: false;
      status: ConnectorUriImportStatus;
    };

const supportedSchemes: Partial<Record<ConnectorType, Set<string>>> = {
  http: new Set(["http", "https"]),
  mysql: new Set(["mysql", "mysql2"]),
  postgres: new Set(["postgres", "postgresql"]),
  rabbitmq: new Set(["amqp", "amqps"]),
  redis: new Set(["redis", "rediss"]),
  sqs: new Set(["http", "https"]),
};

export function canImportConnectorUri(type: ConnectorType) {
  return type in supportedSchemes;
}

export function importConnectorUri(
  type: ConnectorType,
  value: string,
): ConnectorUriImportResult {
  const url = parseUri(value);
  if (!url) {
    return { ok: false, status: "invalid" };
  }

  const allowedSchemes = supportedSchemes[type];
  if (!allowedSchemes) {
    return { ok: false, status: "unsupported" };
  }

  const scheme = url.protocol.replace(/:$/, "").toLowerCase();
  if (!allowedSchemes.has(scheme)) {
    return { ok: false, status: "scheme_mismatch" };
  }

  if (type === "http") {
    return {
      ok: true,
      config: {},
      secret: { url: value.trim() },
      suggestedName: url.hostname || null,
    };
  }

  if (type === "sqs") {
    return parseSqsUrl(url);
  }

  if (!url.hostname) {
    return { ok: false, status: "invalid" };
  }

  const config: Record<string, string> = { host: url.hostname };
  const secret: Record<string, string> = {};
  setIfPresent(config, "port", url.port);
  setIfPresent(config, "username", decodeUrlComponent(url.username));
  setIfPresent(secret, "password", decodeUrlComponent(url.password));

  if (type === "mysql" || type === "postgres") {
    setIfPresent(config, "database", firstPathSegment(url));
  }

  if (type === "redis") {
    setIfPresent(config, "database", firstPathSegment(url));
  }

  if (type === "rabbitmq") {
    const vhost = firstPathSegment(url);
    setIfPresent(config, "vhost", vhost || (url.pathname === "/" ? "/" : ""));
  }

  return {
    ok: true,
    config,
    secret,
    suggestedName: suggestName(type, url),
  };
}

function parseUri(value: string) {
  try {
    return new URL(value.trim());
  } catch {
    return null;
  }
}

function parseSqsUrl(url: URL): ConnectorUriImportResult {
  const region = extractSqsRegion(url);
  if (!region) {
    return { ok: false, status: "missing_region" };
  }
  return {
    ok: true,
    config: { region_name: region },
    secret: {},
    suggestedName: lastPathSegment(url) ?? url.hostname,
  };
}

function extractSqsRegion(url: URL) {
  const explicitRegion = url.searchParams.get("region");
  if (explicitRegion) {
    return explicitRegion;
  }
  const host = url.hostname.toLowerCase();
  const regionMatch = /^sqs[.-]([a-z0-9-]+)\./.exec(host);
  return regionMatch?.[1] ?? null;
}

function firstPathSegment(url: URL) {
  const segment = url.pathname.replace(/^\/+/, "").split("/")[0] ?? "";
  return decodeUrlComponent(segment);
}

function lastPathSegment(url: URL) {
  const segments = url.pathname.split("/").filter(Boolean);
  const last = segments.at(-1);
  return last ? decodeUrlComponent(last) : null;
}

function suggestName(type: ConnectorType, url: URL) {
  if (type === "mysql" || type === "postgres") {
    return firstPathSegment(url) || url.hostname || null;
  }
  return url.hostname || null;
}

function setIfPresent(target: Record<string, string>, key: string, value: string) {
  if (value) {
    target[key] = value;
  }
}

function decodeUrlComponent(value: string) {
  if (!value) {
    return "";
  }
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}
