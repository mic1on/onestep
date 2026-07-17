import { describe, expect, it, vi } from 'vitest';
import { buildSourceDetails, resolveRowValue } from './sourceFields';
import { buildSinkDetails } from './sourceFields';
import type { Translate } from './sourceFields';

// Stub translate: returns the key (prefixed) so tests assert on behavior, not
// localized copy. Real i18n coverage lives in i18n.test.ts.
const t: Translate = vi.fn((key: string) => `[${key}]`) as unknown as Translate;

describe('buildSourceDetails', () => {
  it('classifies mysql_incremental as a MySQL source (table-centric type badge)', () => {
    const details = buildSourceDetails('mysql_incremental', { table: 'orders' }, 'mysql.orders');
    expect(details.typeKey).toBe('topology.relationalDb');
    expect(details.titleKey).toBe('topology.sourceTitle');
    // Expected MySQL fields are present in display order.
    const keys = details.rows.map((r) => r.configKey);
    expect(keys).toContain('table');
    expect(keys).toContain('cursor');
    expect(keys).toContain('batch_size');
  });

  it('classifies kafka_topic as a Kafka source', () => {
    const details = buildSourceDetails('kafka_topic', {}, null);
    expect(details.typeKey).toBe('topology.kafkaCluster');
    const keys = details.rows.map((r) => r.configKey);
    expect(keys).toContain('topic');
    expect(keys).toContain('group_id');
    expect(keys).toContain('brokers');
  });

  it('falls back to the generic event-ingestion type for other kinds', () => {
    const details = buildSourceDetails('rabbitmq_queue', { queue: 'work' }, null);
    expect(details.typeKey).toBe('topology.eventIngestion');
  });
});

describe('resolveRowValue', () => {
  it('renders real MySQL config values from the reported config dict', () => {
    const config = { table: 'orders', cursor: 'updated_at', batch_size: 500 };
    const details = buildSourceDetails('mysql_incremental', config, 'mysql.orders');
    const tableRow = details.rows.find((r) => r.configKey === 'table')!;
    const cursorRow = details.rows.find((r) => r.configKey === 'cursor')!;
    const batchRow = details.rows.find((r) => r.configKey === 'batch_size')!;

    expect(resolveRowValue(tableRow, 'mysql_incremental', config, null, t)).toEqual({
      value: 'orders',
      mono: true,
      placeholder: false,
    });

    expect(resolveRowValue(cursorRow, 'mysql_incremental', config, null, t)).toEqual({
      value: 'updated_at',
      mono: true,
      placeholder: false,
    });

    // Numeric config values are stringified for display.
    expect(resolveRowValue(batchRow, 'mysql_incremental', config, null, t)).toEqual({
      value: '500',
      mono: false,
      placeholder: false,
    });
  });

  it('shows "Not reported" for missing MySQL fields instead of hiding them', () => {
    const details = buildSourceDetails('mysql_incremental', { table: 'orders' }, 'mysql.orders');
    const cursorRow = details.rows.find((r) => r.configKey === 'cursor')!;
    const resolved = resolveRowValue(cursorRow, 'mysql_incremental', { table: 'orders' }, null, t);
    expect(resolved).toEqual({ value: '[topology.notReported]', mono: false, placeholder: true });
  });

  it('shows "Not reported" for Kafka when the connector reported an empty config', () => {
    const details = buildSourceDetails('kafka_topic', {}, null);
    const topicRow = details.rows.find((r) => r.configKey === 'topic')!;
    const resolved = resolveRowValue(topicRow, 'kafka_topic', {}, null, t);
    expect(resolved).toEqual({ value: '[topology.notReported]', mono: false, placeholder: true });
  });

  it('omits absent fields for generic kinds (only present keys are shown)', () => {
    const details = buildSourceDetails('rabbitmq_queue', { queue: 'work' }, null);
    const queueRow = details.rows.find((r) => r.configKey === 'queue')!;
    const streamRow = details.rows.find((r) => r.configKey === 'stream')!;

    expect(resolveRowValue(queueRow, 'rabbitmq_queue', { queue: 'work' }, null, t)).toEqual({
      value: 'work',
      mono: true,
      placeholder: false,
    });
    expect(resolveRowValue(streamRow, 'rabbitmq_queue', { queue: 'work' }, null, t)).toBeNull();
  });

  it('uses the connector name when present, placeholder when absent', () => {
    const details = buildSourceDetails('kafka_topic', {}, null);
    const nameRow = details.rows.find((r) => r.labelKey === 'topology.connectorName')!;

    const withName = resolveRowValue(nameRow, 'kafka_topic', {}, 'kafka.events', t);
    expect(withName).toEqual({ value: 'kafka.events', mono: true, placeholder: false });

    const withoutName = resolveRowValue(nameRow, 'kafka_topic', {}, null, t);
    expect(withoutName).toEqual({ value: '[topology.notReported]', mono: true, placeholder: true });
  });

  it('always surfaces the raw kind in the Source Type row', () => {
    const details = buildSourceDetails('mysql_table_queue', {}, null);
    const kindRow = details.rows.find((r) => r.labelKey === 'topology.sourceKind')!;
    const resolved = resolveRowValue(kindRow, 'mysql_table_queue', {}, null, t);
    expect(resolved).toEqual({ value: 'mysql_table_queue', mono: true, placeholder: false });
  });
});

describe('buildSinkDetails / sink resolution', () => {
  it('classifies mysql_table_sink as a relational sink', () => {
    const details = buildSinkDetails('mysql_table_sink', { table: 'orders_audit' }, 'mysql.audit');
    expect(details.typeKey).toBe('topology.relationalDb');
    expect(details.rows.map((r) => r.configKey)).toEqual(
      expect.arrayContaining(['table', 'mode', 'keys']),
    );
  });

  it('classifies http_sink as an event-ingestion sink', () => {
    const details = buildSinkDetails('http_sink', { url: 'https://example.com' }, 'ses.gateway');
    expect(details.typeKey).toBe('topology.eventIngestion');
  });

  it('renders real mysql_table_sink config values', () => {
    const config = { table: 'orders_audit', mode: 'upsert', keys: 'id' };
    const details = buildSinkDetails('mysql_table_sink', config, 'mysql.audit');
    const tableRow = details.rows.find((r) => r.configKey === 'table')!;
    const modeRow = details.rows.find((r) => r.configKey === 'mode')!;
    const keysRow = details.rows.find((r) => r.configKey === 'keys')!;

    expect(resolveRowValue(tableRow, 'mysql_table_sink', config, null, t, true)).toEqual({
      value: 'orders_audit',
      mono: true,
      placeholder: false,
    });
    expect(resolveRowValue(modeRow, 'mysql_table_sink', config, null, t, true)).toEqual({
      value: 'upsert',
      mono: false,
      placeholder: false,
    });
    expect(resolveRowValue(keysRow, 'mysql_table_sink', config, null, t, true)).toEqual({
      value: 'id',
      mono: true,
      placeholder: false,
    });
  });

  it('shows "Not reported" for missing mysql_table_sink fields (isSink path)', () => {
    const details = buildSinkDetails('mysql_table_sink', { table: 'orders_audit' }, 'mysql.audit');
    const modeRow = details.rows.find((r) => r.configKey === 'mode')!;
    const resolved = resolveRowValue(modeRow, 'mysql_table_sink', { table: 'orders_audit' }, null, t, true);
    expect(resolved).toEqual({ value: '[topology.notReported]', mono: false, placeholder: true });
  });

  it('renders http_sink url and method', () => {
    const config = { url: 'https://api.example.com/send', method: 'POST' };
    const details = buildSinkDetails('http_sink', config, 'ses.gateway');
    const urlRow = details.rows.find((r) => r.configKey === 'url')!;
    const methodRow = details.rows.find((r) => r.configKey === 'method')!;
    expect(resolveRowValue(urlRow, 'http_sink', config, null, t, true)).toEqual({
      value: 'https://api.example.com/send',
      mono: true,
      placeholder: false,
    });
    expect(resolveRowValue(methodRow, 'http_sink', config, null, t, true)).toEqual({
      value: 'POST',
      mono: false,
      placeholder: false,
    });
  });

  it('omits absent fields for generic sink kinds', () => {
    const details = buildSinkDetails('clickhouse', { table: 'logs' }, 'clickhouse.analytics');
    const tableRow = details.rows.find((r) => r.configKey === 'table')!;
    const streamRow = details.rows.find((r) => r.configKey === 'stream')!;
    expect(resolveRowValue(tableRow, 'clickhouse', { table: 'logs' }, null, t, true)).toEqual({
      value: 'logs',
      mono: true,
      placeholder: false,
    });
    expect(resolveRowValue(streamRow, 'clickhouse', { table: 'logs' }, null, t, true)).toBeNull();
  });
});
