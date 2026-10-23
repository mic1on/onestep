import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseDbTimestamp } from '../src/schema.ts';
import type * as PostgresModule from '../src/backends/postgres.ts';

// Test the published JavaScript: Node 22.6's experimental stripper leaves a
// TypeScript private constructor in imported source, producing invalid JS.
const { PostgresBackend } = await import(
  new URL('../dist/backends/postgres.js', import.meta.url).href
) as typeof PostgresModule;

test('Date values retain milliseconds and textual timestamps retain microseconds', () => {
  assert.equal(parseDbTimestamp(new Date('2026-10-23T04:05:06.123Z')), '2026-10-23T04:05:06.123000+00:00');
  assert.equal(parseDbTimestamp('2026-10-23 12:05:06.123456+08'), '2026-10-23T04:05:06.123456+00:00');
  assert.equal(parseDbTimestamp('2026-10-23 04:05:06.000001+00'), '2026-10-23T04:05:06.000001+00:00');
  assert.equal(parseDbTimestamp(new Date('2026-10-23T04:05:06Z')), '2026-10-23T04:05:06+00:00');
});

test('Postgres timestamp parser is pool-local and preserves custom parsers for other types', async () => {
  let config: Record<string, unknown> = {};
  const defaultParser = (value: string | Buffer) => new Date(String(value));
  const customParser = (value: string | Buffer) => `custom:${value}`;
  const types = { getTypeParser: (_oid?: number, _format?: string) => defaultParser };
  class Pool {
    constructor(options: Record<string, unknown>) { config = options; }
    async query() { return { rows: [], rowCount: 0 }; }
    async end() {}
  }
  const backend = await PostgresBackend.create({
    connectionString: 'unused', pgModule: { Pool, types },
    poolOptions: { binary: true, types: { getTypeParser: () => customParser } },
  });
  const localTypes = config.types as typeof types;
  const timestamp = '2026-10-23 04:05:06.123456+00';
  assert.equal(localTypes.getTypeParser(1184)(timestamp), timestamp);
  assert.equal(localTypes.getTypeParser(23)('123'), 'custom:123');
  assert.equal(types.getTypeParser(), defaultParser);
  assert.equal(config.binary, false);
  await backend.close();
});
