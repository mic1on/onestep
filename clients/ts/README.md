# @mic1on/onestep-client (TypeScript)

A thin TypeScript client for onestep **tracked execution**. It lets a Next.js API
route (or any Node service) submit, inspect, list and cancel long-running tasks
while a **Python onestep worker** — unmodified — claims and executes them.

```text
Next.js route handler                 Python worker (unchanged)
  ExecutionClient ----------------> onestep_executions + onestep_execution_attempts
  submit / get / list / cancel       claim / heartbeat / complete / reclaim
```

## Scope

This package deliberately implements **only the client half** of the protocol.
`claim`, `heartbeat`, `complete` and `reclaim` are worker-side lease operations;
reimplementing them in TypeScript would let a second writer violate the lease
semantics the Python runtime depends on. Calling them raises a clear error
(`assertWorkerOnly`).

Currently implemented backend: **PostgreSQL**. MySQL uses different DDL and
placeholders and is not implemented here.

## Install

```bash
npm i @mic1on/onestep-client pg
```

## Usage

```ts
import { ExecutionClient, PostgresBackend } from '@mic1on/onestep-client';

const backend = await PostgresBackend.create({
  connectionString: process.env.DATABASE_URL!,
});
const client = new ExecutionClient({ backend, namespace: 'orders' });

// Submit — returns immediately with an id; the Python worker picks it up.
const execution = await client.submit(
  'orders.sync',
  { orderId: 'A-1001' },
  { idempotencyKey: 'order-A-1001' },   // safe to retry
);

// Poll (1/2/4/8s, capped at 10–30s), or use wait().
const done = await client.wait(execution.id, { timeoutMs: 300_000 });
if (done.status === 'succeeded') {
  console.log(await client.result(execution.id));
}
```

Map the errors to HTTP exactly as `docs/broker/execution-overview.md` describes:

| Error | HTTP |
| --- | --- |
| `ExecutionNotFound` | 404 |
| `ExecutionNotReady` | 202 or 409 |
| `ExecutionFailed` | 422 |
| `ExecutionCancelled` | 409 |
| `ExecutionExpired` | 410 |

## Two cross-language traps this package exists to handle

The `submission_digest` is computed in Python as
`sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True))`.
JavaScript's `JSON.stringify` is **not** equivalent, and a naive port breaks
idempotency in production: the same key and payload compute a different digest
and the server raises `ExecutionConflict`.

### 1. Float formatting

Python keeps `.0` for integral floats; JS drops it.

```text
payload {"v": 1.0}
  Python  {"v":1.0}  -> a779d16f9064abd1
  JS      {"v":1}    -> f6ecab47d58fecd2   (conflict)
```

Python also switches to exponent notation at `1e16`; JS switches at `1e21`. And
because JSON cannot distinguish `1` from `1.0`, a TS caller must state float
intent explicitly with `PyFloat`:

```ts
import { PyFloat } from '@mic1on/onestep-client';
await client.submit('t', { ratio: new PyFloat(1.0) });  // digest covers 1.0
await client.submit('t', { ratio: 1 });                 // digest covers 1
```

### 2. Datetime formatting

`expires_at` participates in the digest, and `isoformat()` ≠ `toISOString()`:

```text
Python  2026-09-17T10:07:00.123456+00:00
JS      2026-09-17T10:07:00.123Z
```

The client normalizes to Python's UTC `isoformat` form and rejects naive
datetimes, matching the server's own rule.

**Both are covered by golden-vector tests** against digests produced by the real
Python implementation.

## Cursor caveat

Python's `_decode_cursor` re-encodes the parsed value and compares it to the
inbound string. Only a UTC `isoformat` datetime survives that round trip, so a
cursor carrying a non-UTC offset is rejected by the server itself. `encodeCursor`
therefore always emits UTC.

## Testing

```bash
npm run test:unit    # 6 golden-vector tests, no infrastructure needed
npm run test:e2e     # 9 live tests: TS submits -> Python worker executes
npm run typecheck
npm run build        # emits the publishable package into dist/
```

The e2e suite needs a reachable PostgreSQL and the repo's Python venv with
`onestep-sql` installed. It skips with an explanation when either is missing.

```bash
ONESTEP_E2E_DSN=postgresql://onestep:onestep@localhost:5432/onestep \
ONESTEP_PYTHON=../.venv/bin/python \
npm run test:e2e
```

### Build and publish

Source files import each other with explicit `.ts` extensions so the test runner
can execute them with no build step. `tsconfig.build.json` handles the publish
build, using `rewriteRelativeImportExtensions` to emit `.js` specifiers and
excluding tests from `dist/`. The two configs exist for that reason — do not
merge them.

```bash
npm run build          # must succeed before publishing; prepublishOnly enforces it
npm pack --dry-run     # inspect the tarball (dist/ + README only)
```

Publishing is handled by `.github/workflows/npm-client.yml`, which requires both
a `ts-v*` tag push and an explicit `publish_npm` input.

### Regenerating the golden vectors

`fixtures/golden.json` is generated from the production Python algorithm, not
hand-written. Regenerate it after any change to the digest or cursor format:

```bash
npm run golden
```

## Design notes

- **No ORM.** The schema is a wire contract; raw SQL keeps it visible in one
  place. Column names live in `src/schema.ts`.
- **`cancel` is three branches, not one.** `queued`/`retrying` become
  `cancelled` immediately (terminal, `finished_at` set); `running` becomes
  `cancel_requested` (the worker converges later); terminal statuses are
  returned unchanged. The read and write share one transaction holding
  `SELECT ... FOR UPDATE`, matching the Python backend.
- **`result()` mirrors terminal semantics** — success returns the value, every
  other terminal status raises its dedicated error, non-terminal raises
  `ExecutionNotReady`.
