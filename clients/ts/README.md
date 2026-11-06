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

Requires **Node >= 22.6.0**. The published `dist/` is plain JavaScript and runs
on any supported Node, but the test suite executes the TypeScript sources
directly via `--experimental-strip-types`, which does not exist before 22.6.

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
await client.submit('t', { ratio: new PyFloat(1.0) });  // digest AND JSON write contain 1.0
await client.submit('t', { ratio: 1 });                 // digest covers 1
```

Payload and metadata are written using the same canonical encoder as the digest,
including nested `PyFloat` values and negative zero (`-0.0`). PostgreSQL JSONB
preserves `1.0`, so Python reads a `float` rather than an `int`. However, JSONB
normalizes negative zero to positive zero and expands exponent notation: e.g.
`-0.0` becomes `0.0`, and `1e+20` becomes `100000000000000000000` (a Python `int`).
These are database limitations shared with Python submissions. Retain the
**original typed request** for idempotent retries; rebuilding it from a stored
row can change the digest. JavaScript reads also cannot distinguish `1.0` from
`1`, so returned values do not reconstruct `PyFloat` wrappers.

### 2. Datetime formatting

`expires_at` participates in the digest, and `isoformat()` ≠ `toISOString()`:

```text
Python  2026-09-17T10:07:00.123456+00:00
JS      2026-09-17T10:07:00.123Z
```

The client normalizes the input string to Python's `datetime.isoformat()` form
without passing through `Date`: `Z` becomes `+00:00`, nonzero fractional seconds
use six digits, and integral seconds have no fraction. The original offset is
retained, so `2026-10-24T18:00:00.123+08:00` becomes
`2026-10-24T18:00:00.123000+08:00`. Naive datetimes, invalid calendar dates, and
more than six fractional digits are rejected. The supported input is a four-digit
calendar date and time with an explicit `Z` or `±HH:MM` offset.

Python's existing digest distinguishes two representations of the same instant
with different offsets. Preserve the original offset when retrying. Returned
database timestamps and cursors still use UTC; they do not retain that original
offset.

**Both are covered through `ExecutionClient.submit`**, with Python golden
vectors and live TS→Python / Python→TS idempotency tests.

### JSON and capture types

`Backend.query` returns JSON/JSONB columns already decoded exactly once. A value
such as `"123"`, `"true"`, `"null"`, `"{}"`, or `"[]"` remains a string through
`submit`, `get`, and `result`. `PostgresBackend` enforces JSON parsers locally
even when `poolOptions.types` supplies raw-text parsers. Custom backends must
decode JSON at their own driver boundary.

Submissions support plain JSON values plus `PyFloat`. Python capture types
(datetime, UUID, bytes, Decimal, tuple/set, Enum, namedtuple) are not implemented
by this client. Special JavaScript objects (`Date`, `Map`, `Set`, buffers, class
instances, including `PyDateTime`) and objects with a `$onestep` key are rejected
on submission rather than silently flattened or interpreted as capture tags.
`PyDateTime` remains a low-level canonical string helper, not a capture encoder.
Python results containing capture tags remain encoded objects on the TS side.

### Compatibility with previously submitted rows

The Python digest algorithm and stored digests are unchanged. Existing Python
requests replay with the same payload types and expiry offset. Earlier TS
versions could store floats as integers and truncate/convert expiries before
hashing. Retrying an affected old key with the corrected original request can
therefore raise `ExecutionConflict`. Fetch the known execution by id and inspect
it before deciding whether a new submission is needed; no fallback digest or
automatic rewrite is applied, since it could accept distinct microsecond
expiries as the same request. Previously discarded float/expiry information
cannot be recovered from the row.

## Cursor caveat

Python's `_decode_cursor` re-encodes the parsed value and compares it to the
inbound string. Only a UTC `isoformat` datetime survives that round trip, so a
cursor carrying a non-UTC offset is rejected by the server itself. `encodeCursor`
therefore always emits UTC.

`PostgresBackend` reads `timestamptz` as text using a pool-local parser and text result format (even if
`poolOptions.binary` is set). This
preserves microseconds in returned executions and page cursors, including rows
less than one millisecond apart. Parsers other than timestamp and JSON/JSONB
are preserved.
Custom backends that return `Date` retain milliseconds but cannot recover
microseconds already discarded by their driver.

## Testing

```bash
npm run test:unit    # submit, golden vectors, row mapping and parser regressions
npm run test:e2e     # live PostgreSQL and Python worker/bidirectional replay tests
npm test             # builds and runs both suites
npm run typecheck
npm run build        # emits the publishable package into dist/
```

The e2e suite needs a **disposable test PostgreSQL database** and the repo's
Python venv with `onestep-sql[postgres]` installed. It resets the execution
tables. It skips when implicit local prerequisites are missing; with an
explicit `ONESTEP_E2E_DSN`, setup failures fail the run instead of skipping.

```bash
ONESTEP_E2E_DSN=postgresql://onestep:onestep@localhost:5432/onestep \
ONESTEP_PYTHON="$PWD/../../.venv/bin/python" \
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

`fixtures/golden.json` is generated with Python's canonical JSON and datetime
formatting, using the production digest formula. Regenerate it with Python
3.11+ after any change to the digest or cursor format. The live tests also
verify those vectors against the production Python client:

```bash
ONESTEP_PYTHON="$PWD/../../.venv/bin/python" npm run golden
```

`ONESTEP_PYTHON` selects the interpreter; when unset the command uses `python3`.
The generator reports a clear error if that interpreter is older than 3.11.

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
