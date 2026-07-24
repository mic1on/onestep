import { describe, expect, it, vi } from 'vitest';
import type { ResourceCatalogEntry } from '../api';
import { buildSinkDetails, buildSourceDetails, resolveRowValue } from './sourceFields';
import type { Translate } from './sourceFields';

const t: Translate = vi.fn((key: string) => `[${key}]`) as unknown as Translate;

const catalog: ResourceCatalogEntry[] = [
  {
    type: 'mysql_incremental',
    roles: ['source'],
    label: 'MySQL Incremental',
    connector_types: ['mysql'],
    fields: [
      { name: 'table', type: 'string', required: true, secret: false, options: [] },
      { name: 'key', type: 'string', required: true, secret: false, options: [] },
      { name: 'cursor', type: 'string_list', required: true, secret: false, options: [] },
      { name: 'batch_size', type: 'integer', required: false, secret: false, options: [] },
      { name: 'poll_interval_s', type: 'number', required: false, secret: false, options: [] },
    ],
    topology_fields: ['table', 'key', 'cursor', 'batch_size', 'poll_interval_s'],
  },
  {
    type: 'http_sink',
    roles: ['sink'],
    label: 'HTTP Sink',
    connector_types: [],
    fields: [
      { name: 'url', type: 'string', required: true, secret: true, options: [] },
      { name: 'method', type: 'string', required: false, secret: false, options: ['POST', 'PUT'] },
      { name: 'timeout_s', type: 'number', required: false, secret: false, options: [] },
    ],
    topology_fields: ['url', 'method', 'timeout_s'],
  },
];

describe('catalog-driven source details', () => {
  it('builds source rows from catalog topology_fields in order', () => {
    const details = buildSourceDetails('mysql_incremental', catalog);

    expect(details.typeLabel).toBe('MySQL Incremental');
    expect(details.titleKey).toBe('topology.sourceTitle');
    expect(details.rows.map((row) => row.configKey)).toEqual([
      null,
      null,
      'table',
      'key',
      'cursor',
      'batch_size',
      'poll_interval_s',
    ]);
    expect(details.rows.find((row) => row.configKey === 'poll_interval_s')?.label).toBe('Poll Interval S');
  });

  it('does not invent fallback field rows for unknown kinds', () => {
    const details = buildSourceDetails('unknown_source', catalog);

    expect(details.typeLabel).toBe('unknown_source');
    expect(details.rows.map((row) => row.configKey)).toEqual([null, null]);
  });
});

describe('catalog-driven row resolution', () => {
  it('renders reported catalog field values', () => {
    const config = { table: 'orders', cursor: ['updated_at', 'id'], batch_size: 500 };
    const details = buildSourceDetails('mysql_incremental', catalog);
    const tableRow = details.rows.find((row) => row.configKey === 'table')!;
    const cursorRow = details.rows.find((row) => row.configKey === 'cursor')!;
    const batchRow = details.rows.find((row) => row.configKey === 'batch_size')!;

    expect(resolveRowValue(tableRow, 'mysql_incremental', config, null, t)).toEqual({
      value: 'orders',
      mono: true,
      placeholder: false,
    });
    expect(resolveRowValue(cursorRow, 'mysql_incremental', config, null, t)).toEqual({
      value: 'updated_at, id',
      mono: true,
      placeholder: false,
    });
    expect(resolveRowValue(batchRow, 'mysql_incremental', config, null, t)).toEqual({
      value: '500',
      mono: false,
      placeholder: false,
    });
  });

  it('shows not reported for missing catalog topology fields', () => {
    const details = buildSourceDetails('mysql_incremental', catalog);
    const cursorRow = details.rows.find((row) => row.configKey === 'cursor')!;

    expect(resolveRowValue(cursorRow, 'mysql_incremental', { table: 'orders' }, null, t)).toEqual({
      value: '[topology.notReported]',
      mono: false,
      placeholder: true,
    });
  });

  it('uses derived kind and connector rows independently of catalog fields', () => {
    const details = buildSourceDetails('mysql_incremental', catalog);
    const kindRow = details.rows.find((row) => row.labelKey === 'topology.sourceKind')!;
    const nameRow = details.rows.find((row) => row.labelKey === 'topology.connectorName')!;

    expect(resolveRowValue(kindRow, 'mysql_incremental', {}, null, t)).toEqual({
      value: 'mysql_incremental',
      mono: true,
      placeholder: false,
    });
    expect(resolveRowValue(nameRow, 'mysql_incremental', {}, 'mysql.orders', t)).toEqual({
      value: 'mysql.orders',
      mono: true,
      placeholder: false,
    });
    expect(resolveRowValue(nameRow, 'mysql_incremental', {}, null, t)).toEqual({
      value: '[topology.notReported]',
      mono: true,
      placeholder: true,
    });
  });
});

describe('catalog-driven sink details', () => {
  it('builds sink rows from catalog topology_fields', () => {
    const details = buildSinkDetails('http_sink', catalog);

    expect(details.typeLabel).toBe('HTTP Sink');
    expect(details.titleKey).toBe('topology.sinkTitle');
    expect(details.rows.map((row) => row.configKey)).toEqual([null, null, 'url', 'method', 'timeout_s']);
  });

  it('renders sink values using the same catalog row contract', () => {
    const config = { url: 'https://example.com', method: 'POST' };
    const details = buildSinkDetails('http_sink', catalog);
    const urlRow = details.rows.find((row) => row.configKey === 'url')!;
    const methodRow = details.rows.find((row) => row.configKey === 'method')!;

    expect(resolveRowValue(urlRow, 'http_sink', config, null, t)).toEqual({
      value: 'https://example.com',
      mono: true,
      placeholder: false,
    });
    expect(resolveRowValue(methodRow, 'http_sink', config, null, t)).toEqual({
      value: 'POST',
      mono: false,
      placeholder: false,
    });
  });
});
