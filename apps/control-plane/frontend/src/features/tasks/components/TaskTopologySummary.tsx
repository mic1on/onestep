import type {
  ConnectorDescriptor,
  JsonObject,
  RetryDescriptor,
  TaskDashboardSummary,
} from "../../../lib/api/types";

type TaskTopologySnapshot = Pick<
  TaskDashboardSummary,
  "source_kind" | "source_name" | "source_config" | "emit" | "retry_policy"
>;

type ConnectorSummary = {
  title: string;
  detail: string | null;
  inline: string;
};

type ConnectorLike = {
  kind: string | null;
  name: string | null;
  config: JsonObject | null;
};

export function TaskTopologyPreview({
  task,
  isZh,
}: {
  task: TaskTopologySnapshot;
  isZh: boolean;
}) {
  const source = buildConnectorSummary(
    {
      kind: task.source_kind,
      name: task.source_name,
      config: task.source_config,
    },
    isZh,
  );
  const emitSummaries = buildEmitSummaries(task.emit, isZh);

  return (
    <div className="ref-topology-preview">
      <TopologyPreviewRow
        label={isZh ? "源" : "Source"}
        value={source?.inline ?? (isZh ? "无" : "none")}
      />
      <TopologyPreviewRow
        label={isZh ? "发" : "Emit"}
        value={formatEmitPreview(emitSummaries, isZh)}
      />
    </div>
  );
}

export function TaskSourceValue({
  task,
  emptyLabel,
  isZh,
}: {
  task: TaskTopologySnapshot;
  emptyLabel: string;
  isZh: boolean;
}) {
  const source = buildConnectorSummary(
    {
      kind: task.source_kind,
      name: task.source_name,
      config: task.source_config,
    },
    isZh,
  );

  if (source === null) {
    return <strong>{emptyLabel}</strong>;
  }

  return <TopologySummaryCard summary={source} />;
}

export function TaskEmitValue({
  emit,
  emptyLabel,
  isZh,
}: {
  emit: ConnectorDescriptor[];
  emptyLabel: string;
  isZh: boolean;
}) {
  const summaries = buildEmitSummaries(emit, isZh);

  if (summaries.length === 0) {
    return <strong>{emptyLabel}</strong>;
  }

  return (
    <div className="ref-topology-summary-list">
      {summaries.map((summary, index) => (
        <TopologySummaryCard
          key={`${summary.title}:${index}`}
          summary={summary}
        />
      ))}
    </div>
  );
}

export function formatRetryPolicySummary(
  retryPolicy: RetryDescriptor | null,
  isZh: boolean,
) {
  if (retryPolicy === null) {
    return isZh ? "无" : "none";
  }

  const details = [formatRetryKind(retryPolicy.kind, isZh)];
  const maxAttempts = getNumberValue(retryPolicy.config, "max_attempts");
  const delaySeconds = getNumberValue(retryPolicy.config, "delay_s");

  if (maxAttempts !== null) {
    details.push(
      isZh ? `最多 ${maxAttempts} 次` : `up to ${maxAttempts} attempts`,
    );
  }
  if (delaySeconds !== null) {
    details.push(
      isZh
        ? `间隔 ${formatCompactDuration(delaySeconds, true)}`
        : `delay ${formatCompactDuration(delaySeconds, false)}`,
    );
  }

  return details.join(" · ");
}

function TopologyPreviewRow({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="ref-topology-preview-row">
      <span className="ref-topology-preview-label">{label}</span>
      <span className="ref-topology-preview-value">{value}</span>
    </div>
  );
}

function TopologySummaryCard({ summary }: { summary: ConnectorSummary }) {
  return (
    <div className="ref-topology-summary-card">
      <strong>{summary.title}</strong>
      {summary.detail ? <span>{summary.detail}</span> : null}
    </div>
  );
}

function buildEmitSummaries(emit: ConnectorDescriptor[], isZh: boolean) {
  return emit
    .map((connector) => buildConnectorSummary(connector, isZh))
    .filter((summary): summary is ConnectorSummary => summary !== null);
}

function buildConnectorSummary(
  connector: ConnectorLike,
  isZh: boolean,
): ConnectorSummary | null {
  const kind = normalizeText(connector.kind);
  const name = normalizeText(connector.name);
  const config = connector.config;

  if (kind === null && name === null) {
    return null;
  }

  const kindLabel = kind ? formatConnectorKind(kind, isZh) : null;
  const highlights = buildConfigHighlights(config, kind, isZh);
  const title = name ?? resolveConnectorTarget(config) ?? kindLabel ?? (isZh ? "未命名" : "unnamed");
  const detailParts = [];

  if (kindLabel !== null) {
    detailParts.push(kindLabel);
  }
  detailParts.push(...highlights);

  const detail = detailParts.length > 0 ? detailParts.join(" · ") : null;
  const inlineParts = [title];
  const preferredHighlight = highlights[0];

  if (preferredHighlight) {
    inlineParts.push(preferredHighlight);
  }

  return {
    title,
    detail,
    inline: inlineParts.join(" · "),
  };
}

function buildConfigHighlights(
  config: JsonObject | null,
  kind: string | null,
  isZh: boolean,
) {
  if (config === null) {
    return [];
  }

  const highlights: string[] = [];
  const seen = new Set<string>();

  const push = (value: string | null) => {
    if (value === null || seen.has(value)) {
      return;
    }
    seen.add(value);
    highlights.push(value);
  };

  const seconds = getNumberValue(config, "seconds");
  const minutes = getNumberValue(config, "minutes");
  const hours = getNumberValue(config, "hours");
  const cron = getStringValue(config, "cron") ?? (kind === "cron" ? getStringValue(config, "expression") : null);

  if (seconds !== null) {
    push(
      isZh
        ? `每 ${formatCompactDuration(seconds, true)} 执行`
        : `every ${formatCompactDuration(seconds, false)}`,
    );
  } else if (minutes !== null) {
    push(
      isZh
        ? `每 ${formatCompactDuration(minutes * 60, true)} 执行`
        : `every ${formatCompactDuration(minutes * 60, false)}`,
    );
  } else if (hours !== null) {
    push(
      isZh
        ? `每 ${formatCompactDuration(hours * 3600, true)} 执行`
        : `every ${formatCompactDuration(hours * 3600, false)}`,
    );
  }

  if (cron !== null) {
    push(`cron ${cron}`);
  }

  push(formatNamedValue(config, "queue", isZh ? "队列" : "queue"));
  push(formatNamedValue(config, "queue_name", isZh ? "队列" : "queue"));
  push(formatNamedValue(config, "topic", "topic"));
  push(formatNamedValue(config, "subscription", isZh ? "订阅" : "subscription"));
  push(formatNamedValue(config, "subject", "subject"));
  push(formatNamedValue(config, "consumer_group", isZh ? "消费组" : "group"));
  push(formatNamedValue(config, "group", isZh ? "消费组" : "group"));
  push(formatNamedValue(config, "stream", "stream"));
  push(formatNamedValue(config, "routing_key", isZh ? "路由键" : "routing key"));
  push(formatNamedValue(config, "table", isZh ? "表" : "table"));
  push(formatNamedValue(config, "bucket", "bucket"));
  push(formatNamedValue(config, "index", "index"));
  push(formatNamedValue(config, "channel", isZh ? "通道" : "channel"));
  push(formatNamedValue(config, "endpoint", "endpoint"));
  push(formatNamedValue(config, "path", "path"));
  push(formatNamedValue(config, "mode", "mode"));

  const prefetch = getNumberValue(config, "prefetch");
  if (prefetch !== null) {
    push(`prefetch ${prefetch}`);
  }

  const batchSize = getNumberValue(config, "batch_size");
  if (batchSize !== null) {
    push(isZh ? `批量 ${batchSize}` : `batch ${batchSize}`);
  }

  const keys = getStringArrayValue(config, "keys");
  if (keys.length > 0) {
    push(
      isZh ? `键 ${keys.join(", ")}` : `keys ${keys.join(", ")}`,
    );
  }

  const immediate = getBooleanValue(config, "immediate");
  if (immediate !== null) {
    push(immediate ? (isZh ? "启动即执行" : "run immediately") : isZh ? "启动后不立即执行" : "no immediate run");
  }

  const overlap = getStringValue(config, "overlap");
  if (overlap !== null) {
    push(isZh ? `重叠策略 ${overlap}` : `overlap ${overlap}`);
  }

  return highlights.slice(0, 4);
}

function resolveConnectorTarget(config: JsonObject | null) {
  if (config === null) {
    return null;
  }

  const targetKeys = [
    "queue",
    "queue_name",
    "topic",
    "subscription",
    "subject",
    "stream",
    "table",
    "bucket",
    "index",
    "channel",
    "endpoint",
    "path",
  ] as const;

  for (const key of targetKeys) {
    const value = getStringValue(config, key);
    if (value !== null) {
      return value;
    }
  }

  return null;
}

function formatEmitPreview(
  summaries: ConnectorSummary[],
  isZh: boolean,
) {
  if (summaries.length === 0) {
    return isZh ? "无" : "none";
  }
  if (summaries.length === 1) {
    return summaries[0].inline;
  }
  return isZh
    ? `${summaries[0].inline} 等 ${summaries.length} 个目标`
    : `${summaries[0].inline} and ${summaries.length - 1} more`;
}

function formatConnectorKind(kind: string, isZh: boolean) {
  const labels: Record<string, { zh: string; en: string }> = {
    cron: { zh: "Cron", en: "Cron" },
    interval: { zh: "定时任务", en: "Interval" },
    rabbitmq_queue: { zh: "RabbitMQ 队列", en: "RabbitMQ queue" },
    mysql_table_sink: { zh: "MySQL 表写入", en: "MySQL table sink" },
  };

  const direct = labels[kind];
  if (direct) {
    return isZh ? direct.zh : direct.en;
  }

  const normalized = kind.replaceAll(/[_-]+/g, " ").trim();
  if (!normalized) {
    return kind;
  }

  return normalized
    .split(" ")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function formatRetryKind(kind: string, isZh: boolean) {
  const labels: Record<string, { zh: string; en: string }> = {
    max_attempts: { zh: "按次数重试", en: "Max attempts" },
    no_retry: { zh: "不重试", en: "No retry" },
  };

  const direct = labels[kind];
  if (direct) {
    return isZh ? direct.zh : direct.en;
  }

  return formatConnectorKind(kind, isZh);
}

function formatNamedValue(
  config: JsonObject,
  key: string,
  label: string,
) {
  const value = getStringValue(config, key);
  if (value === null) {
    return null;
  }
  return `${label} ${value}`;
}

function formatCompactDuration(seconds: number, isZh: boolean) {
  if (seconds % 86400 === 0) {
    const days = seconds / 86400;
    return isZh ? `${days} 天` : `${days}d`;
  }
  if (seconds % 3600 === 0) {
    const hours = seconds / 3600;
    return isZh ? `${hours} 小时` : `${hours}h`;
  }
  if (seconds % 60 === 0) {
    const minutes = seconds / 60;
    return isZh ? `${minutes} 分钟` : `${minutes}m`;
  }
  return isZh ? `${seconds} 秒` : `${seconds}s`;
}

function getStringValue(config: JsonObject, key: string) {
  const value = config[key];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function getNumberValue(config: JsonObject, key: string) {
  const value = config[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function getBooleanValue(config: JsonObject, key: string) {
  const value = config[key];
  return typeof value === "boolean" ? value : null;
}

function getStringArrayValue(config: JsonObject, key: string) {
  const value = config[key];
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is string => typeof item === "string" && item.trim().length > 0);
}

function normalizeText(value: string | null) {
  if (value === null) {
    return null;
  }
  const normalized = value.trim();
  return normalized ? normalized : null;
}
