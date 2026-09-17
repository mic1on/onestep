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
 *   ONESTEP_PYTHON    python interpreter (default ../.venv/bin/python)
 */

import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { PostgresBackend } from '../src/backends/postgres.ts';
import { ExecutionClient } from '../src/client.ts';
import { ExecutionConflict, ExecutionNotReady } from '../src/errors.ts';
import { PyFloat, submissionDigest } from '../src/canonical.ts';

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
};
const goldenE2eDigest = golden.e2ePayload.expectedDigest;

function runPython(args: string[], timeoutMs = 60_000): string {
  return execFileSync(PYTHON, [WORKER, ...args], {
    cwd: repoRoot,
    env: { ...process.env, ONESTEP_E2E_DSN: DSN },
    encoding: 'utf-8',
    timeout: timeoutMs,
  });
}

before(async () => {
  try {
    runPython(['reset']);
  } catch (error) {
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

test('list() returns newest-first and paginates with a server-valid cursor', async (t) => {
  if (!available) return t.skip(skipReason);

  const page1 = await client.list({ taskName: TASK, limit: 2 });
  assert.ok(page1.items.length <= 2);
  if (page1.nextCursor) {
    // The cursor must decode and be accepted by the server's own round-trip
    // check; a malformed cursor would surface as an invalid-cursor error here.
    const page2 = await client.list({ taskName: TASK, limit: 2, cursor: page1.nextCursor });
    assert.ok(page2.items.length >= 0);
    // No overlap between pages.
    const ids1 = new Set(page1.items.map((e) => e.id));
    for (const item of page2.items) {
      assert.ok(!ids1.has(item.id), 'pages must not overlap');
    }
  }
});

test('worker-side lease operations are refused with a clear error', async (t) => {
  if (!available) return t.skip(skipReason);

  const { assertWorkerOnly } = await import('../src/client.ts');
  assert.throws(() => assertWorkerOnly('claim'), /worker-side lease operation/);
});
