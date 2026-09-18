/**
 * Unit tests for the database-row mapping, especially the
 * `ExecutionErrorDetail` mirror. The e2e suite covers this against a live
 * PostgreSQL instance; these cases keep the normalization contract pinned
 * without a database (see e2e.test.ts for the prerequisite skip).
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { mapRow } from '../src/schema.ts';

function makeRow(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 'b8d6e3a0-1111-4222-8333-444455556666',
    namespace: 'agent-api',
    task_name: 'sync',
    status: 'retrying',
    payload: null,
    metadata: {},
    result: null,
    error: {
      kind: 'error',
      exception_type: 'RuntimeError',
      stage: 'sink',
      sinks_succeeded: 'warehouse,audit',
      sinks_remaining: 'billing',
    },
    attempts: 1,
    created_at: '2026-01-01 00:00:00',
    available_at: '2026-01-01 00:00:01',
    started_at: '2026-01-01 00:00:02',
    finished_at: null,
    cancel_requested_at: null,
    expires_at: null,
    version: 2,
    ...overrides,
  };
}

test('mapRow normalizes sink replay window fields on the error detail', () => {
  const execution = mapRow(makeRow());
  assert.equal(execution.status, 'retrying');
  assert.ok(execution.error);
  assert.equal(execution.error.kind, 'error');
  assert.equal(execution.error.exceptionType, 'RuntimeError');
  assert.equal(execution.error.stage, 'sink');
  assert.equal(execution.error.sinksSucceeded, 'warehouse,audit');
  assert.equal(execution.error.sinksRemaining, 'billing');
});

test('mapRow keeps sink replay fields absent when the stored error omits them', () => {
  const execution = mapRow(
    makeRow({
      error: { kind: 'error', exception_type: 'ValueError', stage: 'handler' },
    }),
  );
  assert.ok(execution.error);
  assert.equal(execution.error.sinksSucceeded, undefined);
  assert.equal(execution.error.sinksRemaining, undefined);
});

test('mapRow accepts JSON-string error columns with sink fields', () => {
  const execution = mapRow(
    makeRow({
      error: JSON.stringify({
        kind: 'error',
        exception_type: 'RuntimeError',
        stage: 'sink',
        sinks_succeeded: 'first',
        sinks_remaining: 'second',
      }),
    }),
  );
  assert.ok(execution.error);
  assert.equal(execution.error.sinksSucceeded, 'first');
  assert.equal(execution.error.sinksRemaining, 'second');
});
