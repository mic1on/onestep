import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { ExecutionClient } from '../src/client.ts';
import type { Backend } from '../src/backend.ts';
import { PyFloat, PyDateTime } from '../src/canonical.ts';
import { ExecutionConflict, ExecutionEncodingError } from '../src/errors.ts';
import { fromTagged, type Tagged } from './tagged.ts';

const golden = JSON.parse(readFileSync(new URL('../fixtures/golden.json', import.meta.url), 'utf8')) as {
  digest: Array<{ label: string; namespace: string; taskName: string; payload: Tagged; metadata: Tagged;
    delayS: string | null; expiresAt: string | null; expectedDigest: string; payloadJson: string; metadataJson: string }>;
  expiry: Array<{ input: string; normalized: string; expectedDigest: string }>;
};

/** Models the backend contract: PostgreSQL has already decoded JSON columns. */
class MemoryBackend implements Backend {
  inserts: unknown[][] = [];
  rows: Record<string, unknown>[] = [];
  async query(sql: string, values: unknown[]): Promise<Record<string, unknown>[]> {
    if (sql.startsWith('INSERT')) {
      this.inserts.push(values);
      if (values[5] !== null && this.rows.some((row) => row.idempotency_key === values[5])) {
        throw Object.assign(new Error('duplicate key'), { code: '23505' });
      }
      const stamp = '2026-01-01T00:00:00+00:00';
      const row = {
        id: values[0], namespace: values[1], task_name: values[2], status: 'queued',
        payload: JSON.parse(values[3] as string), metadata: JSON.parse(values[4] as string),
        idempotency_key: values[5], submission_digest: values[6], expires_at: values[8],
        created_at: stamp, available_at: stamp, attempts: 0, version: 0,
      };
      this.rows.push(row);
      return [row];
    }
    if (sql.includes('idempotency_key =')) return this.rows.filter((row) => row.idempotency_key === values[2]);
    return this.rows.filter((row) => row.id === values[1]);
  }
  async execute(): Promise<number> { throw new Error('unused'); }
  async close(): Promise<void> {}
}

test('submit/get/result preserve decoded JSON strings and real scalar/container types', async () => {
  for (const value of ['123', 'true', 'null', '{}', '[]', '"quoted"', 'ordinary', 123, true, false, null, { n: 1 }, [1]]) {
    const backend = new MemoryBackend();
    const client = new ExecutionClient({ backend, namespace: 'demo' });
    const submitted = await client.submit('echo', value);
    assert.deepEqual(submitted.payload, value);
    assert.deepEqual((await client.get(submitted.id))?.payload, value);
    Object.assign(backend.rows[0], { status: 'succeeded', result: value });
    assert.deepEqual(await client.result(submitted.id), value);
  }
});

test('submit persists Python canonical payload/metadata and digest for every golden vector', async () => {
  for (const vector of golden.digest) {
    const backend = new MemoryBackend();
    const client = new ExecutionClient({ backend, namespace: vector.namespace });
    const options = { idempotencyKey: vector.label, metadata: fromTagged(vector.metadata) as Record<string, unknown>,
      delayS: vector.delayS === null ? null : Number(vector.delayS), expiresAt: vector.expiresAt };
    const payload = fromTagged(vector.payload);
    const first = await client.submit(vector.taskName, payload, options);
    assert.equal(backend.inserts[0][3], vector.payloadJson, vector.label);
    assert.equal(backend.inserts[0][4], vector.metadataJson, vector.label);
    assert.equal(backend.inserts[0][6], vector.expectedDigest, vector.label);
    assert.equal(backend.inserts[0][8], vector.expiresAt, vector.label);
    assert.equal((await client.submit(vector.taskName, payload, options)).id, first.id);
  }
});

test('plain negative zero and nested PyFloat keep their spelling in INSERT parameters', async () => {
  const backend = new MemoryBackend();
  const client = new ExecutionClient({ backend, namespace: 'demo' });
  const payload = { i: 1, xs: [new PyFloat(1), -0, { n: new PyFloat(-0) }] };
  await client.submit('echo', payload, { metadata: { n: new PyFloat(2) } });
  assert.equal(backend.inserts[0][3], '{"i":1,"xs":[1.0,-0.0,{"n":-0.0}]}');
  assert.equal(backend.inserts[0][4], '{"n":2.0}');
});

test('submit normalizes expiry spelling without changing microseconds or offset', async () => {
  for (const vector of golden.expiry) {
    const backend = new MemoryBackend();
    const client = new ExecutionClient({ backend, namespace: 'demo' });
    const first = await client.submit('orders.sync', 1, { idempotencyKey: 'expiry', expiresAt: vector.input });
    assert.equal(backend.inserts[0][8], vector.normalized, vector.input);
    assert.equal(backend.inserts[0][6], vector.expectedDigest, vector.input);
    assert.equal((await client.submit('orders.sync', 1, { idempotencyKey: 'expiry', expiresAt: vector.normalized })).id, first.id);
  }
});

test('expiry differences smaller than a millisecond remain conflicts', async () => {
  const backend = new MemoryBackend();
  const client = new ExecutionClient({ backend, namespace: 'demo' });
  await client.submit('echo', 1, { idempotencyKey: 'micro', expiresAt: '2026-10-24T10:00:00.123456+00:00' });
  await assert.rejects(client.submit('echo', 1, { idempotencyKey: 'micro', expiresAt: '2026-10-24T10:00:00.123457+00:00' }), ExecutionConflict);
});

test('submit rejects invalid dates and unsupported precision before sending SQL', async () => {
  const backend = new MemoryBackend();
  const client = new ExecutionClient({ backend, namespace: 'demo' });
  for (const expiresAt of [
    'not-a-date', '2026-10-24T10:00:00', '2026-02-29T10:00:00Z', '2026-04-31T10:00:00Z',
    '0000-01-01T00:00:00Z', '2026-10-24T24:00:00Z', '2026-10-24T10:60:00Z',
    '2026-10-24T10:00:60Z', '2026-10-24T10:00:00+24:00', '2026-10-24T10:00:00+08:60',
    '2026-10-24T10:00:00.1234567Z',
  ]) await assert.rejects(client.submit('echo', 1, { expiresAt }), /expires_at/);
  assert.equal(backend.inserts.length, 0);
});

test('submit rejects capture tags and unsupported special objects without silently flattening them', async () => {
  const backend = new MemoryBackend();
  const client = new ExecutionClient({ backend, namespace: 'demo' });
  for (const value of [new Date(), new Map(), new Set(), new PyDateTime('2026-10-24T00:00:00+00:00'),
    Buffer.from('bytes'), { $onestep: { type: 'tuple', values: [] } }]) {
    await assert.rejects(client.submit('echo', { nested: [value] }), ExecutionEncodingError);
    await assert.rejects(client.submit('echo', 1, { metadata: { nested: value } }), ExecutionEncodingError);
  }
  assert.equal(backend.inserts.length, 0);
});
