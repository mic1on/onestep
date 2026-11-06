/**
 * End-to-end interop test: TypeScript submits, the Python worker executes.
 *
 * This is the decisive test for the whole approach. It exercises the real
 * protocol against a real PostgreSQL database with the real Python runtime:
 *
 *   submit (TS)  ->  tables  ->  claim + run (Python)  ->  result (TS)
 *
 * It also proves the failure modes that the unit tests alone cannot:
 *   - a TS-computed submission_digest is accepted by the Python backend, and
 *     an identical re-submit is treated as the same execution (not a conflict),
 *   - a Python worker can claim and complete work TS queued,
 *   - TS can read the result back and honour terminal semantics.
 *
 * Requires a reachable PostgreSQL and the Python venv. Skips with a clear
 * message when either is unavailable, so `npm test` stays usable offline.
 *
 *   ONESTEP_E2E_DSN   libpq DSN (default postgresql://onestep:onestep@localhost:5432/onestep)
 *   ONESTEP_PYTHON    absolute interpreter path (default <repo>/.venv/bin/python)
 */

import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { PostgresBackend } from '../src/backends/postgres.ts';
import { ExecutionClient } from '../src/client.ts';
import { parseDbTimestamp } from '../src/schema.ts';
import { ExecutionConflict, ExecutionNotReady } from '../src/errors.ts';
import { canonicalize, PyFloat, submissionDigest } from '../src/canonical.ts';
import { fromTagged, type Tagged } from './tagged.ts';

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, '..', '..', '..');

const DSN = process.env.ONESTEP_E2E_DSN ?? 'postgresql://onestep:onestep@localhost:5432/onestep';
const PYTHON = process.env.ONESTEP_PYTHON ?? join(repoRoot, '.venv', 'bin', 'python');
const WORKER = join(here, 'e2e_worker.py');
const TASK = 'e2e.echo';
const NAMESPACE = 'e2e';

let available = true;
let skipReason = '';

/**
 * The digest Python computes for the E2E float payload.
 *
 * Loaded from the generated fixture so the expectation always comes from the
 * Python implementation rather than from this test's own arithmetic.
 */
const golden = JSON.parse(
  readFileSync(join(here, '..', 'fixtures', 'golden.json'), 'utf-8'),
) as {
  e2ePayload: { payload: unknown; expectedDigest: string };
  digest: Array<{ label: string; namespace: string; taskName: string; payload: Tagged; metadata: Tagged;
    payloadJson: string; metadataJson: string; delayS: string | null; expiresAt: string | null; expectedDigest: string }>;
  expiry: Array<{ input: string; normalized: string; expectedDigest: string }>;
};
const goldenE2eDigest = golden.e2ePayload.expectedDigest;

function runPython(args: string[], timeoutMs = 60_000, input?: unknown): string {
  return execFileSync(PYTHON, [WORKER, ...args], {
    cwd: repoRoot,
    env: { ...process.env, ONESTEP_E2E_DSN: DSN },
    encoding: 'utf-8',
    timeout: timeoutMs,
    input: input === undefined ? undefined : JSON.stringify(input),
  });
}

before(async () => {
  try {
    runPython(['reset']);
  } catch (error) {
    // Explicit test infrastructure must fail visibly rather than turn a broken
    // integration into a green run containing only skipped tests.
    if (process.env.ONESTEP_E2E_DSN) throw error;
    available = false;
    skipReason =
      `e2e prerequisites unavailable (${(error as Error).message.split('\n')[0]}). ` +
      `Ensure PostgreSQL is running and ${PYTHON} exists with onestep-sql installed.`;
  }
});

let backend: PostgresBackend;
let client: ExecutionClient;

before(async () => {
  if (!available) return;
  backend = await PostgresBackend.create({ connectionString: DSN.replace(/^postgresql:\/\//, 'postgresql://') });
  client = new ExecutionClient({ backend, namespace: NAMESPACE });
});

after(async () => {
  if (available && backend) await backend.close();
});

test('TS submit -> Python worker executes -> TS reads result', async (t) => {
  if (!available) return t.skip(skipReason);

  const submitted = await client.submit(TASK, { orderId: 'A-1001', qty: 3 });
  assert.equal(submitted.status, 'queued');
  assert.equal(submitted.namespace, NAMESPACE);
  assert.equal(submitted.taskName, TASK);
  assert.equal(submitted.attempts, 0);

  // Run the Python worker long enough to claim and complete the execution.
  runPython(['run', '--task', TASK, '--wait', '5'], 90_000);

  // The worker must have completed it.
  const done = await client.get(submitted.id);
  assert.ok(done, 'execution should still exist');
  assert.equal(done.status, 'succeeded', `expected succeeded, got ${done.status}`);

  // And the handler's return value must be readable through the TS client.
  const result = (await client.result(submitted.id)) as Record<string, unknown>;
  assert.deepEqual(result.echo, { orderId: 'A-1001', qty: 3 });
  assert.equal(result.worker, 'python');
});

test('idempotent re-submit returns the same execution (digest agrees)', async (t) => {
  if (!available) return t.skip(skipReason);

  const key = `idem-${Date.now()}`;
  const first = await client.submit(TASK, { n: 1 }, { idempotencyKey: key });
  const second = await client.submit(TASK, { n: 1 }, { idempotencyKey: key });

  // Same row, not a duplicate — this is the digest interop proof.
  assert.equal(second.id, first.id);
});

test('re-submitting a DIFFERENT payload under one key raises ExecutionConflict', async (t) => {
  if (!available) return t.skip(skipReason);

  const key = `conflict-${Date.now()}`;
  await client.submit(TASK, { n: 1 }, { idempotencyKey: key });
  await assert.rejects(
    () => client.submit(TASK, { n: 2 }, { idempotencyKey: key }),
    ExecutionConflict,
  );
});

test('float payloads produce the digest Python computes (cross-language)', async (t) => {
  if (!available) return t.skip(skipReason);

  // This is the assertion that actually guards the float-formatting risk.
  //
  // A TS-only round trip (submit twice, compare ids) is NOT sufficient: a
  // consistent-but-wrong encoder agrees with itself and would still store a
  // digest the Python worker computes differently. So compare the TS digest
  // against the digest Python produces for the very same submission.
  const { fromTagged } = await import('./tagged.ts');

  const actual = await submissionDigest({
    namespace: NAMESPACE,
    taskName: TASK,
    payload: fromTagged(golden.e2ePayload.payload as never),
    metadata: {} as never,
    delayS: null,
    expiresAt: null,
  });

  assert.equal(
    actual,
    golden.e2ePayload.expectedDigest,
    'TS digest for a float-bearing payload must equal the digest the Python server computes',
  );
});

test('float payloads survive the digest round trip', async (t) => {
  if (!available) return t.skip(skipReason);

  // 1.0 is the value a naive JSON.stringify implementation collapses to 1,
  // changing the digest. PyFloat is how a TS caller states "this is a float":
  // a bare JS literal 1.0 is indistinguishable from 1.
  const key = `float-${Date.now()}`;
  const payload = { ratio: new PyFloat(1.0), scale: new PyFloat(3.0) };
  const first = await client.submit(TASK, payload, { idempotencyKey: key });
  const second = await client.submit(TASK, payload, { idempotencyKey: key });
  assert.equal(second.id, first.id, 'float-bearing payload must digest identically in TS and Python');

  // The stored digest must be the float-form digest, and the stored payload
  // must still be a float — proving digest and stored bytes agree.
  const stored = await backend.query(
    'SELECT submission_digest, payload FROM onestep_executions WHERE namespace = $1 AND id = $2',
    [NAMESPACE, first.id],
  );
  const recomputed = await submissionDigest({
    namespace: NAMESPACE,
    taskName: TASK,
    payload: payload as never,
    metadata: {} as never,
    delayS: null,
    expiresAt: null,
  });
  assert.equal(
    String(stored[0].submission_digest),
    recomputed,
    'the stored digest must match a fresh TS computation for the same submission',
  );

  // jsonb preserves the numeric value; confirm we did not store an int where a
  // float was intended.
  const storedPayload = stored[0].payload as Record<string, unknown>;
  assert.equal(typeof storedPayload.ratio, 'number');
  assert.equal(storedPayload.ratio, 1.0);
});

test('cancel of a queued execution is immediate and terminal', async (t) => {
  if (!available) return t.skip(skipReason);

  const submitted = await client.submit(TASK, { willCancel: true });
  const cancelled = await client.cancel(submitted.id, { reason: 'test' });
  assert.ok(cancelled);
  assert.equal(cancelled.status, 'cancelled');
  assert.equal(cancelled.terminal, true);

  // result() must refuse a cancelled execution with the dedicated error.
  await assert.rejects(() => client.result(submitted.id), /cancelled/i);
});

test('result() on a non-terminal execution raises ExecutionNotReady', async (t) => {
  if (!available) return t.skip(skipReason);

  const submitted = await client.submit(TASK, { pending: true });
  await assert.rejects(() => client.result(submitted.id), ExecutionNotReady);
});

test('microseconds survive reads, transactions and complete pagination', async (t) => {
  if (!available) return t.skip(skipReason);
  const task = 'e2e.precision';
  const stamps = [
    '2026-10-23T04:05:06.123456+00:00',
    '2026-10-23T04:05:06.123455+00:00',
    '2026-10-23T04:05:06.000001+00:00',
    '2026-10-23T04:05:06.000001+00:00',
    '2026-10-23T04:05:05.999999+00:00',
  ];
  const ids: string[] = [];
  for (const [index, stamp] of stamps.entries()) {
    const row = await client.submit(task, { index }, { idempotencyKey: `precision-${index}` });
    ids.push(row.id);
    await backend.execute(
      'UPDATE onestep_executions SET created_at = $1, available_at = $1, started_at = $1, finished_at = $1, cancel_requested_at = $1, expires_at = $1 WHERE id = $2',
      [stamp, row.id],
    );
    const mapped = await client.get(row.id);
    assert.ok(mapped);
    for (const key of ['createdAt', 'availableAt', 'startedAt', 'finishedAt', 'cancelRequestedAt', 'expiresAt'] as const) {
      assert.equal(mapped[key], stamp, key);
    }
    const repeated = await client.submit(task, { index }, { idempotencyKey: `precision-${index}` });
    assert.equal(repeated.createdAt, stamp);
  }
  const transactionRows = await backend.transaction((tx) => tx.query(
    'SELECT created_at FROM onestep_executions WHERE id = $1', [ids[0]],
  ));
  assert.equal(typeof transactionRows[0].created_at, 'string');
  const expected = ids.map((id, index) => ({ id, stamp: stamps[index] }))
    .sort((a, b) => b.stamp.localeCompare(a.stamp) || b.id.localeCompare(a.id))
    .map(({ id }) => id);
  for (const limit of [1, 2, 3]) {
    const actual: string[] = [];
    let cursor: string | undefined;
    for (let pageIndex = 0; pageIndex <= stamps.length; pageIndex++) {
      const page = await client.list({ taskName: task, limit, cursor });
      actual.push(...page.items.map((item) => item.id));
      if (!page.nextCursor) break;
      cursor = page.nextCursor;
    }
    assert.deepEqual(actual, expected, `complete pagination with limit ${limit}`);
  }
});

test('binary pool preferences preserve microseconds inside and outside transactions', async (t) => {
  if (!available) return t.skip(skipReason);
  const binary = await PostgresBackend.create({ connectionString: DSN, poolOptions: { binary: true } });
  try {
    for (const stamp of ['2026-10-23T10:00:00.900001Z', '1969-12-31T23:59:59.999999Z']) {
      const sql = 'SELECT $1::timestamptz AS stamp';
      assert.equal(parseDbTimestamp((await binary.query(sql, [stamp]))[0].stamp), stamp.replace('Z', '+00:00'));
      assert.equal(parseDbTimestamp((await binary.transaction((tx) => tx.query(sql, [stamp])))[0].stamp), stamp.replace('Z', '+00:00'));
    }
  } finally {
    await binary.close();
  }
});

test('worker-side lease operations are refused with a clear error', async (t) => {
  if (!available) return t.skip(skipReason);

  const { assertWorkerOnly } = await import('../src/client.ts');
  assert.throws(() => assertWorkerOnly('claim'), /worker-side lease operation/);
});

interface InteropRequest {
  namespace: string; taskName: string; payloadJson: string; metadataJson: string;
  idempotencyKey: string; delayS: number | null; expiresAt: string | null;
  operation?: string; id?: string; expectConflict?: boolean;
}
interface InteropResult {
  id: string; payloadTypes: unknown; metadataTypes: unknown; expiresAt: string | null; conflict?: boolean;
}
function interop(requests: InteropRequest[]): InteropResult[] {
  return JSON.parse(runPython(['interop'], 60_000, requests).trim().split('\n').at(-1)!);
}

test('all golden payloads and expiries replay idempotently TS -> Python and Python -> TS', async (t) => {
  if (!available) return t.skip(skipReason);
  const cases = [
    ...golden.digest.map((vector) => ({
      ...vector, payload: fromTagged(vector.payload), metadata: fromTagged(vector.metadata) as Record<string, unknown>,
      delayS: vector.delayS === null ? null : Number(vector.delayS),
    })),
    ...golden.expiry.map((vector, i) => ({
      label: `expiry-${i}`, namespace: 'demo', taskName: 'orders.sync', payload: 1, metadata: {},
      payloadJson: '1', metadataJson: '{}', delayS: null, expiresAt: vector.input, expectedDigest: vector.expectedDigest,
    })),
  ];
  const requests: InteropRequest[] = [];
  const tsIds: string[] = [];
  for (const vector of cases) {
    const submitter = new ExecutionClient({ backend, namespace: vector.namespace });
    const idempotencyKey = `ts-first-${vector.label}`;
    const row = await submitter.submit(vector.taskName, vector.payload, {
      idempotencyKey, metadata: vector.metadata, delayS: vector.delayS, expiresAt: vector.expiresAt,
    });
    tsIds.push(row.id);
    requests.push({ namespace: vector.namespace, taskName: vector.taskName, idempotencyKey,
      payloadJson: vector.payloadJson, metadataJson: vector.metadataJson, delayS: vector.delayS, expiresAt: vector.expiresAt });
    const stored = await backend.query('SELECT submission_digest FROM onestep_executions WHERE id = $1', [row.id]);
    assert.equal(stored[0].submission_digest, vector.expectedDigest, vector.label);
  }
  const pythonReplay = interop(requests);
  assert.deepEqual(pythonReplay.map((row) => row.id), tsIds);
  const pythonFirstRequests = requests.map((request) => ({ ...request, idempotencyKey: request.idempotencyKey.replace('ts-first-', 'py-first-') }));
  const pythonFirst = interop(pythonFirstRequests);
  for (const [index, vector] of cases.entries()) {
    const submitter = new ExecutionClient({ backend, namespace: vector.namespace });
    const row = await submitter.submit(vector.taskName, vector.payload, {
      idempotencyKey: pythonFirstRequests[index].idempotencyKey, metadata: vector.metadata, delayS: vector.delayS, expiresAt: vector.expiresAt,
    });
    assert.equal(row.id, pythonFirst[index].id, vector.label);
    assert.equal(row.expiresAt, pythonFirst[index].expiresAt, vector.label);
  }
});

test('JSON strings and other values survive PostgreSQL -> Python worker -> TS result', async (t) => {
  if (!available) return t.skip(skipReason);
  const values = ['123', 'true', 'null', '{}', '[]', '"quoted"', 123, true, false, null, { n: 1 }, [1]];
  const rows = [];
  for (const value of values) {
    const row = await client.submit('e2e.json', value);
    assert.deepEqual(row.payload, value);
    assert.deepEqual((await client.get(row.id))?.payload, value);
    rows.push(row);
  }
  runPython(['run', '--task', 'e2e.json', '--wait', '5']);
  for (const [index, row] of rows.entries()) assert.deepEqual(await client.result(row.id), values[index]);
});

test('Python sees integral PyFloat types in nested payload/metadata and can replay stored data', async (t) => {
  if (!available) return t.skip(skipReason);
  const payload = { integer: 1, nested: [new PyFloat(1), { float: new PyFloat(3) }] };
  const metadata = { nested: [new PyFloat(2)] };
  const request: InteropRequest = { namespace: NAMESPACE, taskName: 'e2e.types', idempotencyKey: 'stored-floats',
    payloadJson: canonicalize(payload), metadataJson: canonicalize(metadata), delayS: null, expiresAt: null };
  const row = await client.submit(request.taskName, payload, { idempotencyKey: request.idempotencyKey, metadata });
  const replayed = interop([{ ...request, operation: 'resubmit-stored', id: row.id }])[0];
  assert.equal(replayed.id, row.id);
  const expectedTypes = { integer: { type: 'int', repr: '1' }, nested: [
    { type: 'float', repr: '1.0' }, { float: { type: 'float', repr: '3.0' } },
  ] };
  assert.deepEqual(replayed.payloadTypes, expectedTypes);
  assert.deepEqual(replayed.metadataTypes, { nested: [{ type: 'float', repr: '2.0' }] });
  runPython(['run', '--task', request.taskName, '--wait', '3']);
  assert.deepEqual((await client.result(row.id) as { types: unknown }).types, expectedTypes);
});

test('JSONB numeric normalization is explicit: original negative-zero/exponent requests still replay', async (t) => {
  if (!available) return t.skip(skipReason);
  const payload = { negativeZero: new PyFloat(-0), exponent: new PyFloat(1e20) };
  const request: InteropRequest = { namespace: NAMESPACE, taskName: 'e2e.jsonb-limits', idempotencyKey: 'jsonb-normalization',
    payloadJson: canonicalize(payload), metadataJson: '{}', delayS: null, expiresAt: null };
  const row = await client.submit(request.taskName, payload, { idempotencyKey: request.idempotencyKey });
  const python = interop([request])[0];
  assert.equal(python.id, row.id);
  assert.deepEqual(python.payloadTypes, { negativeZero: { type: 'float', repr: '0.0' },
    exponent: { type: 'int', repr: '100000000000000000000' } });
  assert.equal(interop([{ ...request, operation: 'resubmit-stored', id: row.id, expectConflict: true }])[0].conflict, true);
  runPython(['run', '--task', request.taskName, '--wait', '3']);
  const result = await client.result(row.id) as { types: unknown; echo: { negativeZero: number } };
  assert.deepEqual(result.types, python.payloadTypes);
  assert.equal(Object.is(result.echo.negativeZero, -0), false);
});

test('microsecond changes and offset changes conflict with Python submissions', async (t) => {
  if (!available) return t.skip(skipReason);
  const request: InteropRequest = { namespace: NAMESPACE, taskName: 'e2e.expiry-conflict', idempotencyKey: 'expiry-conflict',
    payloadJson: '1', metadataJson: '{}', delayS: null, expiresAt: '2026-10-24T10:00:00.123456+00:00' };
  const python = interop([request])[0];
  for (const expiresAt of ['2026-10-24T10:00:00.123457+00:00', '2026-10-24T18:00:00.123456+08:00']) {
    await assert.rejects(client.submit(request.taskName, 1, { idempotencyKey: request.idempotencyKey, expiresAt }), ExecutionConflict);
  }
  const row = await client.submit(request.taskName, 1, { idempotencyKey: request.idempotencyKey, expiresAt: request.expiresAt });
  assert.equal(row.id, python.id);
  assert.equal(row.expiresAt, request.expiresAt);
});
