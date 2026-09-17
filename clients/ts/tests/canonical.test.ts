/**
 * Cross-language compatibility tests for the canonical encoder.
 *
 * Every assertion here compares against a digest/cursor produced by the real
 * Python implementation (see fixtures/generate_golden.py). If these pass, the
 * TS client's idempotency keys are byte-compatible with the Python server.
 *
 * Run: npm test
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import {
  canonicalize,
  canonicalSha256,
  formatPyFloat,
  PyFloat,
  type CanonicalValue,
} from '../src/canonical.ts';
import { fromTagged, type Tagged } from './tagged.ts';

const here = dirname(fileURLToPath(import.meta.url));

interface DigestVector {
  label: string;
  namespace: string;
  taskName: string;
  payload: Tagged;
  metadata: Tagged;
  delayS: string | null;
  expiresAt: string | null;
  expectedDigest: string;
}

interface CursorVector {
  label: string;
  createdAt: string;
  id: string;
  expectedCursor: string;
}

interface Golden {
  digest: DigestVector[];
  cursor: CursorVector[];
}

const golden: Golden = JSON.parse(
  readFileSync(join(here, '..', 'fixtures', 'golden.json'), 'utf-8'),
);

test('digest vectors match the Python implementation', async () => {
  for (const v of golden.digest) {
    const actual = await canonicalSha256({
      namespace: v.namespace,
      task_name: v.taskName,
      payload: fromTagged(v.payload),
      metadata: fromTagged(v.metadata),
      // delay_s is always a float in Python's digest payload.
      delay_s: v.delayS === null ? null : new PyFloat(Number(v.delayS)),
      expires_at: v.expiresAt,
    } as unknown as CanonicalValue);
    assert.equal(actual, v.expectedDigest, `digest mismatch for case "${v.label}"`);
  }
});

test('float formatting matches Python repr', () => {
  // The two cases that a naive JSON.stringify implementation gets wrong.
  assert.equal(formatPyFloat(1.0), '1.0');
  assert.equal(formatPyFloat(3.0), '3.0');
  assert.equal(formatPyFloat(-0.0), '-0.0');
  assert.equal(formatPyFloat(0.0), '0.0');
  assert.equal(formatPyFloat(1.5), '1.5');
  assert.equal(formatPyFloat(0.1 + 0.2), '0.30000000000000004');
  // Python switches to exponent form at >=1e16 and <1e-4; JS switches at 1e21.
  // Verified against CPython: repr(1e15)=='1000000000000000.0', repr(1e16)=='1e+16'.
  assert.equal(formatPyFloat(1e15), '1000000000000000.0');
  assert.equal(formatPyFloat(1e16), '1e+16');
  assert.equal(formatPyFloat(1e20), '1e+20');
  assert.equal(formatPyFloat(1e21), '1e+21');
  assert.equal(formatPyFloat(1e30), '1e+30');
  assert.equal(formatPyFloat(1e-4), '0.0001');
  assert.equal(formatPyFloat(1e-5), '1e-05');
  assert.equal(formatPyFloat(1e-7), '1e-07');
});

test('integral floats are distinguishable from ints', () => {
  // {"i":1,"f":1.0} must NOT collapse to {"i":1,"f":1}.
  const withInt = canonicalize({ i: 1 } as CanonicalValue);
  const withFloat = canonicalize({ i: new PyFloat(1.0) } as CanonicalValue);
  assert.equal(withInt, '{"i":1}');
  assert.equal(withFloat, '{"i":1.0}');
  assert.notEqual(withInt, withFloat);
});

test('keys are sorted by code point and separators are tight', () => {
  assert.equal(
    canonicalize({ b: 1, a: 2 } as CanonicalValue),
    '{"a":2,"b":1}',
  );
  assert.equal(canonicalize({ a: [1, 2] } as CanonicalValue), '{"a":[1,2]}');
});

test('non-ASCII is escaped with ensure_ascii semantics', () => {
  assert.equal(canonicalize('中' as CanonicalValue), '"\\u4e2d"');
  assert.equal(canonicalize('a\nb' as CanonicalValue), '"a\\nb"');
  assert.equal(canonicalize('"q"' as CanonicalValue), '"\\"q\\""');
  // Astral plane becomes a surrogate pair.
  assert.equal(canonicalize('🎉' as CanonicalValue), '"\\ud83c\\udf89"');
});

test('cursor vectors match the Python implementation', async () => {
  // The cursor encoder is exercised in the client module; these vectors pin
  // the exact base64url/no-padding/unsorted-key shape Python produces.
  const { encodeCursor } = await import('../src/cursor.ts');
  for (const v of golden.cursor) {
    assert.equal(
      encodeCursor(v.createdAt, v.id),
      v.expectedCursor,
      `cursor mismatch for case "${v.label}"`,
    );
  }
});
