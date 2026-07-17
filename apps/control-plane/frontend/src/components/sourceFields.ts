/**
 * Per-source-kind field extraction for the TopologyFlow source detail panel.
 *
 * The control plane stores whatever config dict the worker agent reported
 * (see onestep `reporter._build_connector_config`). Each connector kind
 * reports a different set of keys, so this module maps a `sourceKind` to the
 * rows that should be rendered. Missing values are omitted; fields that the
 * connector is expected to report but did not (e.g. Kafka reports an empty
 * config) are surfaced as an explicit "Not reported" placeholder instead of
 * fabricated metrics.
 */
import type { MessageKey } from '../i18n';
import type { SourceConfig } from '../types';

export type Translate = (key: MessageKey, values?: Record<string, string | number>) => string;

export interface SourceFieldRow {
  /** i18n key for the row label. */
  labelKey: MessageKey;
  /** Raw config key this row reads from, if any (null for derived rows). */
  configKey: string | null;
  /** Render the value as monospace (identifiers, URLs, schedules). */
  mono?: boolean;
}

export interface SourceDetails {
  /** i18n key for the panel title ({source} placeholder supported). */
  titleKey: MessageKey;
  /** i18n key for the type badge. */
  typeKey: MessageKey;
  /** Rows to render, in order. */
  rows: SourceFieldRow[];
}

/** A connector field the worker is expected to report. */
interface ExpectedField {
  configKey: string;
  labelKey: MessageKey;
  mono?: boolean;
}

/** MySQL-family sources: table/key/cursor-style ingestion. */
const MYSQL_FIELDS: ExpectedField[] = [
  { configKey: 'table', labelKey: 'topology.fieldTable', mono: true },
  { configKey: 'key', labelKey: 'topology.fieldKey', mono: true },
  { configKey: 'cursor', labelKey: 'topology.fieldCursor', mono: true },
  { configKey: 'where', labelKey: 'topology.fieldWhere', mono: true },
  { configKey: 'state_key', labelKey: 'topology.fieldStateKey', mono: true },
  { configKey: 'batch_size', labelKey: 'topology.fieldBatchSize' },
  { configKey: 'poll_interval_s', labelKey: 'topology.fieldPollInterval' },
];

/** Kafka-family sources. Reporter currently emits an empty config, so these
 *  are the fields we *want* to show and will placeholder when absent. */
const KAFKA_FIELDS: ExpectedField[] = [
  { configKey: 'topic', labelKey: 'topology.fieldTopic', mono: true },
  { configKey: 'group_id', labelKey: 'topology.fieldGroup', mono: true },
  { configKey: 'brokers', labelKey: 'topology.fieldBrokers', mono: true },
];

/** Generic fallback: show whichever of a broad set of keys are present. */
const GENERIC_FIELDS: ExpectedField[] = [
  { configKey: 'expression', labelKey: 'topology.fieldExpression', mono: true },
  { configKey: 'seconds', labelKey: 'topology.fieldSeconds' },
  { configKey: 'table', labelKey: 'topology.fieldTable', mono: true },
  { configKey: 'queue', labelKey: 'topology.fieldQueue', mono: true },
  { configKey: 'stream', labelKey: 'topology.fieldStream', mono: true },
  { configKey: 'url', labelKey: 'topology.fieldUrl', mono: true },
  { configKey: 'path', labelKey: 'topology.fieldUrl', mono: true },
  { configKey: 'batch_size', labelKey: 'topology.fieldBatchSize' },
  { configKey: 'poll_interval_s', labelKey: 'topology.fieldPollInterval' },
];

const MYSQL_KINDS = new Set(['mysql_incremental', 'mysql_table_queue', 'binlog', 'mysql']);
const KAFKA_KINDS = new Set(['kafka', 'kafka_topic']);

/** MySQL sink: writes to a table (upsert/append) keyed by given columns. */
const MYSQL_SINK_FIELDS: ExpectedField[] = [
  { configKey: 'table', labelKey: 'topology.fieldTable', mono: true },
  { configKey: 'mode', labelKey: 'topology.sinkWriteMode' },
  { configKey: 'keys', labelKey: 'topology.fieldKey', mono: true },
];

/** HTTP sink: POST/PUT to a URL. Reporter redacts secrets, so url/method are safe. */
const HTTP_SINK_FIELDS: ExpectedField[] = [
  { configKey: 'url', labelKey: 'topology.fieldUrl', mono: true },
  { configKey: 'method', labelKey: 'topology.sinkHttpMethod' },
  { configKey: 'timeout_s', labelKey: 'topology.fieldPollInterval' },
];

const MYSQL_SINK_KINDS = new Set(['mysql_table_sink', 'mysql_sink']);
const HTTP_SINK_KINDS = new Set(['http_sink', 'http']);


function isMySql(kind: string): boolean {
  return MYSQL_KINDS.has(kind);
}

function isKafka(kind: string): boolean {
  return KAFKA_KINDS.has(kind);
}

/** Fields expected for a given source kind, in display order. */
function expectedFields(kind: string): ExpectedField[] {
  if (isMySql(kind)) return MYSQL_FIELDS;
  if (isKafka(kind)) return KAFKA_FIELDS;
  return GENERIC_FIELDS;
}

/** Fields expected for a given sink kind, in display order. */
function expectedSinkFields(kind: string): ExpectedField[] {
  if (MYSQL_SINK_KINDS.has(kind)) return MYSQL_SINK_FIELDS;
  if (HTTP_SINK_KINDS.has(kind)) return HTTP_SINK_FIELDS;
  return GENERIC_FIELDS;
}

/**
 * Whether absent fields should be shown as an explicit "Not reported"
 * placeholder (true for connectors that report a known, fixed schema) rather
 * than silently hidden (true only for generic/unknown kinds).
 */
function expectPlaceholder(kind: string, isSink: boolean): boolean {
  if (isSink) {
    return MYSQL_SINK_KINDS.has(kind) || HTTP_SINK_KINDS.has(kind);
  }
  return isMySql(kind) || isKafka(kind);
}

/**
 * Build the source detail descriptor for a task.
 *
 * Behavior:
 * - Always leads with "Source Type" (the kind) and "Connector Name".
 * - For each expected field: if present in config, show the value; if absent,
 *   show a "Not reported" placeholder (callers decide via `placeholderFor`).
 * - Returns enough info for the caller to render without importing i18n keys.
 */
export function buildSourceDetails(
  kind: string,
  config: SourceConfig | null,
  connectorName: string | null,
): SourceDetails {
  const fields = expectedFields(kind);

  const rows: SourceFieldRow[] = [
    { labelKey: 'topology.sourceKind', configKey: null },
    { labelKey: 'topology.connectorName', configKey: null },
    ...fields.map((field) => ({ labelKey: field.labelKey, configKey: field.configKey, mono: field.mono })),
  ];

  // MySQL sources are table/cursor ingestion; Kafka is a stream. Generic kinds
  // are best described as queues/schedules.
  const typeKey: MessageKey = isMySql(kind)
    ? 'topology.relationalDb'
    : isKafka(kind)
      ? 'topology.kafkaCluster'
      : 'topology.eventIngestion';

  return {
    titleKey: 'topology.sourceTitle',
    typeKey,
    rows,
  };
}

/**
 * Build the sink detail descriptor for a task. Mirrors buildSourceDetails but
 * uses the sink field schema and an analytics/storage-oriented type badge.
 */
export function buildSinkDetails(
  kind: string,
  config: SourceConfig | null,
  connectorName: string | null,
): SourceDetails {
  const fields = expectedSinkFields(kind);

  const rows: SourceFieldRow[] = [
    { labelKey: 'topology.sourceKind', configKey: null },
    { labelKey: 'topology.connectorName', configKey: null },
    ...fields.map((field) => ({ labelKey: field.labelKey, configKey: field.configKey, mono: field.mono })),
  ];

  const typeKey: MessageKey = MYSQL_SINK_KINDS.has(kind)
    ? 'topology.relationalDb'
    : HTTP_SINK_KINDS.has(kind)
      ? 'topology.eventIngestion'
      : 'topology.sinkType';

  return {
    titleKey: 'topology.sinkTitle',
    typeKey,
    rows,
  };
}

/**
 * Resolve a single row to a display string.
 * Returns null if the row should be skipped entirely (no value and not an
 * expected/placeholder field).
 *
 * `isSink` selects the sink field schema and placeholder policy; source and
 * sink share the same rendering contract otherwise.
 */
export function resolveRowValue(
  row: SourceFieldRow,
  kind: string,
  config: SourceConfig | null,
  connectorName: string | null,
  t: Translate,
  isSink = false,
): { value: string; mono: boolean; placeholder: boolean } | null {
  // Derived rows (type / connector name).
  if (row.configKey === null) {
    if (row.labelKey === 'topology.sourceKind') {
      return { value: kind, mono: true, placeholder: false };
    }
    if (row.labelKey === 'topology.connectorName') {
      const name = connectorName?.trim();
      return { value: name || t('topology.notReported'), mono: true, placeholder: !name };
    }
    return null;
  }

  const raw = config ? config[row.configKey] : undefined;
  if (raw !== undefined && raw !== null && raw !== '') {
    return { value: formatValue(raw), mono: !!row.mono, placeholder: false };
  }

  // Connectors with a known fixed schema surface absent fields as an explicit
  // "Not reported" placeholder, so the user sees the connector did not report
  // them rather than the field silently disappearing. Generic/unknown kinds
  // only show fields that are actually present.
  if (expectPlaceholder(kind, isSink)) {
    return { value: t('topology.notReported'), mono: false, placeholder: true };
  }

  return null;
}

function formatValue(value: unknown): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) return value.map(formatValue).join(', ');
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}
