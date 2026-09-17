/**
 * ExecutionClient — submit, inspect, list and cancel onestep tracked executions.
 *
 * This is the TypeScript counterpart of `onestep.execution.ExecutionClient`
 * (src/onestep/execution.py:374-463). It coordinates with the Python worker
 * through the `onestep_executions` / `onestep_execution_attempts` tables, so a
 * Next.js API route can own submission and polling while a Python worker owns
 * claiming and execution — the split documented in
 * docs/broker/execution-overview.md.
 *
 * Deliberately NOT implemented: claim, heartbeat, complete, reclaim. Those are
 * worker-side lease writes; a second implementation would break the lease
 * semantics the Python worker depends on. Calling them raises
 * WorkerOperationUnsupported from `assertWorkerOnly`.
 */

import { type Backend } from './backend.ts';
import {
  DEFAULT_ATTEMPTS_TABLE,
  DEFAULT_EXECUTIONS_TABLE,
  EXECUTION_COLUMNS,
  type Execution,
  type ExecutionPage,
  type ExecutionStatus,
  mapRow,
} from './schema.ts';
import { encodeCursor, decodeCursor } from './cursor.ts';
import { canonicalize, submissionDigest, PyFloat } from './canonical.ts';
import {
  ExecutionCancelled,
  ExecutionConflict,
  ExecutionExpired,
  ExecutionFailed,
  ExecutionNotFound,
  ExecutionNotReady,
  WorkerOperationUnsupported,
} from './errors.ts';

/** Options accepted by the client. */
export interface ExecutionClientOptions {
  backend: Backend;
  namespace: string;
  executionsTable?: string;
  attemptsTable?: string;
  /** Client-side guard mirroring the server's 1 MiB payload limit. */
  maxPayloadBytes?: number;
  /** Client-side guard mirroring the server's 64 KiB metadata limit. */
  maxMetadataBytes?: number;
}

/** Options for `submit`. */
export interface SubmitOptions {
  idempotencyKey?: string;
  metadata?: Record<string, unknown>;
  /** Delay before the execution becomes claimable, in seconds. */
  delayS?: number | null;
  /** Absolute expiry. Must be timezone-aware (an ISO string with an offset). */
  expiresAt?: string | null;
}

/** Filters for `list`. */
export interface ListOptions {
  taskName?: string;
  status?: ExecutionStatus;
  limit?: number;
  cursor?: string;
}

/** A UUID-v4 identifier generator that does not depend on a Node builtin. */
function randomUuid(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  throw new Error('no UUID source available: requires globalThis.crypto.randomUUID');
}

/**
 * Escape a SQL identifier for safe interpolation.
 *
 * Table names are configuration, not user input, but they are interpolated
 * into every statement, so they are validated rather than trusted.
 */
function quoteIdentifier(name: string): string {
  if (!/^[A-Za-z_][A-Za-z0-9_$]*$/.test(name) || name.length > 64) {
    throw new Error(`invalid SQL identifier: ${name}`);
  }
  return `"${name}"`;
}

/** Compute the ASCII byte length of the canonical form, as Python does. */
function canonicalByteLength(value: unknown): number {
  return Buffer.byteLength(canonicalize(value as never), 'utf8');
}

/**
 * Render a value as JSON text for storage, preserving PyFloat intent.
 *
 * `JSON.stringify` would turn `new PyFloat(1.0)` into `1` (or into a stray
 * object), which would disagree with the digest and hand the Python worker an
 * int where a float was promised. Unwrapping PyFloat to its numeric value
 * first keeps the stored bytes consistent with the digest.
 */
function toJsonText(value: unknown): string {
  return JSON.stringify(unwrapPyFloats(value));
}

/** Recursively replace PyFloat wrappers with their numeric values. */
function unwrapPyFloats(value: unknown): unknown {
  if (value instanceof PyFloat) return value.value;
  if (Array.isArray(value)) return value.map(unwrapPyFloats);
  if (value !== null && typeof value === 'object') {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      out[k] = unwrapPyFloats(v);
    }
    return out;
  }
  return value;
}

export class ExecutionClient {
  readonly backend: Backend;
  readonly namespace: string;

  private readonly executionsTable: string;
  private readonly attemptsTable: string;
  private readonly maxPayloadBytes: number;
  private readonly maxMetadataBytes: number;

  constructor(options: ExecutionClientOptions) {
    if (!options.namespace || !options.namespace.trim()) {
      throw new Error('namespace must not be empty');
    }
    if (options.namespace.length > 255) {
      throw new Error('namespace must be at most 255 characters');
    }
    this.backend = options.backend;
    this.namespace = options.namespace;
    this.executionsTable = quoteIdentifier(
      options.executionsTable ?? DEFAULT_EXECUTIONS_TABLE,
    );
    this.attemptsTable = quoteIdentifier(
      options.attemptsTable ?? DEFAULT_ATTEMPTS_TABLE,
    );
    this.maxPayloadBytes = options.maxPayloadBytes ?? 1024 * 1024;
    this.maxMetadataBytes = options.maxMetadataBytes ?? 64 * 1024;
  }

  /** Release backend resources. */
  async close(): Promise<void> {
    await this.backend.close();
  }

  /**
   * Submit a task for execution.
   *
   * When `idempotencyKey` is supplied and a row already exists for
   * (namespace, task_name, key), the existing execution is returned — unless
   * the payload differs, in which case ExecutionConflict is thrown. This
   * mirrors the IntegrityError path in machine.py:472-489 and is precisely why
   * the digest must be computed identically in both languages.
   */
  async submit(
    taskName: string,
    payload: unknown,
    options: SubmitOptions = {},
  ): Promise<Execution> {
    assertValidText(taskName, 'task_name', 255);
    const metadata = options.metadata ?? {};
    const idempotencyKey = options.idempotencyKey ?? null;
    if (idempotencyKey !== null) {
      assertValidText(idempotencyKey, 'idempotency_key', 255);
    }

    const expiresAt = normalizeExpiresAt(options.expiresAt ?? null);
    const delayS = options.delayS ?? null;
    if (delayS !== null && (!Number.isFinite(delayS) || delayS < 0)) {
      throw new RangeError('delay_s must be finite and >= 0');
    }

    // Size guards, matching the server's own limits and error type.
    if (canonicalByteLength(payload) > this.maxPayloadBytes) {
      const { ExecutionEncodingError } = await import('./errors.ts');
      throw new ExecutionEncodingError('execution payload exceeds the configured limit');
    }
    if (canonicalByteLength(metadata) > this.maxMetadataBytes) {
      const { ExecutionEncodingError } = await import('./errors.ts');
      throw new ExecutionEncodingError('execution metadata exceeds the configured limit');
    }

    const digest = await submissionDigest({
      namespace: this.namespace,
      taskName,
      payload: payload as never,
      metadata: metadata as never,
      delayS: delayS as number | null,
      expiresAt,
    });

    // Persist the payload as canonical JSON so the bytes the digest covers are
    // the bytes stored. Using plain JSON.stringify here would be subtly wrong:
    // it drops PyFloat intent (1.0 -> 1), so a caller passing PyFloat would
    // have its digest computed over "1.0" while the row stored "1". The Python
    // worker would then see an int where the digest implied a float.
    const payloadJson = toJsonText(payload);
    const metadataJson = toJsonText(metadata);

    const id = randomUuid();
    const rows = await this.backend
      .query(
        `INSERT INTO ${this.executionsTable} (
           id, namespace, task_name, status, payload, metadata,
           idempotency_key, submission_digest, attempts,
           available_at, created_at, updated_at, version, expires_at
         ) VALUES (
           $1, $2, $3, 'queued', $4::jsonb, $5::jsonb,
           $6, $7, 0,
           now() + make_interval(secs => $8), now(), now(), 0, $9
         )
         RETURNING ${EXECUTION_COLUMNS.map(quoteIdentifier).join(', ')}`,
        [
          id,
          this.namespace,
          taskName,
          // jsonb columns: pass JSON text with an explicit cast. `toJsonText`
          // renders PyFloat as a float (not an int) so the stored value agrees
          // with the digest computed above.
          payloadJson,
          metadataJson,
          idempotencyKey,
          idempotencyKey === null ? null : digest,
          delayS ?? 0,
          expiresAt,
        ],
      )
      .catch(async (error: unknown) => {
      // Duplicate idempotency key: resolve the conflict semantics.
      if (!isUniqueViolation(error)) throw error;
      if (idempotencyKey === null) throw error;
      const existing = await this.findByIdempotencyKey(taskName, idempotencyKey);
      if (existing === null) throw error;
      const stored = await this.rawSubmissionDigest(existing.id);
      if (stored !== digest) {
        throw new ExecutionConflict(
          'idempotency key was already used with a different submission',
        );
      }
      return null;
    });

    if (rows === null) {
      // Idempotent replay: return the pre-existing execution.
      const existing = await this.findByIdempotencyKey(taskName, idempotencyKey as string);
      if (existing === null) {
        throw new Error('idempotent replay lost its row');
      }
      return existing;
    }

    return mapRow(rows[0] as Record<string, unknown>);
  }

  /** Fetch one execution by id, or null when it does not exist. */
  async get(executionId: string): Promise<Execution | null> {
    assertUuid(executionId, 'execution_id');
    const rows = await this.backend.query(
      `SELECT ${EXECUTION_COLUMNS.map(quoteIdentifier).join(', ')}
         FROM ${this.executionsTable}
        WHERE namespace = $1 AND id = $2`,
      [this.namespace, executionId],
    );
    if (rows.length === 0) return null;
    return mapRow(rows[0] as Record<string, unknown>);
  }

  /**
   * List executions, newest first, with keyset pagination.
   *
   * The ordering and cursor predicate mirror machine.py:502-534 exactly:
   * `ORDER BY created_at DESC, id DESC` and
   * `created_at < c OR (created_at = c AND id < i)`.
   */
  async list(options: ListOptions = {}): Promise<ExecutionPage> {
    const limit = options.limit ?? 50;
    if (!Number.isInteger(limit) || limit < 1) {
      throw new RangeError('limit must be a positive integer');
    }

    const where: string[] = ['namespace = $1'];
    const values: unknown[] = [this.namespace];
    if (options.taskName !== undefined) {
      values.push(options.taskName);
      where.push(`task_name = $${values.length}`);
    }
    if (options.status !== undefined) {
      values.push(options.status);
      where.push(`status = $${values.length}`);
    }
    if (options.cursor !== undefined) {
      const decoded = decodeCursor(options.cursor);
      values.push(decoded.createdAt);
      const createdParam = `$${values.length}`;
      values.push(decoded.id);
      const idParam = `$${values.length}`;
      where.push(`(created_at < ${createdParam} OR (created_at = ${createdParam} AND id < ${idParam}))`);
    }

    // Fetch limit + 1 to detect whether another page exists.
    values.push(limit + 1);
    const limitParam = `$${values.length}`;

    const rows = await this.backend.query(
      `SELECT ${EXECUTION_COLUMNS.map(quoteIdentifier).join(', ')}
         FROM ${this.executionsTable}
        WHERE ${where.join(' AND ')}
        ORDER BY created_at DESC, id DESC
        LIMIT ${limitParam}`,
      values,
    );

    const hasMore = rows.length > limit;
    const selected = rows.slice(0, limit).map((row) => mapRow(row));
    let nextCursor: string | null = null;
    if (hasMore && selected.length > 0) {
      const last = selected[selected.length - 1] as Execution;
      nextCursor = encodeCursor(last.createdAt, last.id);
    }
    return { items: selected, nextCursor };
  }

  /**
   * Request cancellation.
   *
   * Three branches, transcribed from machine.py:536-585:
   *   - queued / retrying  -> cancelled immediately (terminal), finished_at set
   *   - running            -> cancel_requested (the worker converges later)
   *   - anything else      -> returned unchanged (already terminal)
   *
   * `cancel_requested` is a request, not a guarantee: the worker converges to
   * `cancelled` at its next checkpoint.
   */
  async cancel(executionId: string, options: { reason?: string } = {}): Promise<Execution | null> {
    assertUuid(executionId, 'execution_id');
    const reason = options.reason ?? null;
    if (reason !== null && reason.length > 500) {
      throw new RangeError('reason must be at most 500 characters');
    }
    const normalizedReason = reason === null || reason.trim() === '' ? null : reason.trim();

    // The status decision and its UPDATE must share one transaction: the
    // SELECT ... FOR UPDATE holds the row lock so a concurrent worker claim
    // cannot interleave between the read and the write. This mirrors the
    // Python backend's `async with self.engine.begin()` in machine.py:543-585.
    await this.runInTransaction(async (tx) => {
      const locked = await tx.query(
        `SELECT status, version FROM ${this.executionsTable}
          WHERE namespace = $1 AND id = $2
          FOR UPDATE`,
        [this.namespace, executionId],
      );
      if (locked.length === 0) return;

      const status = String((locked[0] as Record<string, unknown>).status);
      const version = Number((locked[0] as Record<string, unknown>).version);

      // Three branches, transcribed exactly from machine.py:556-574.
      if (status === 'queued' || status === 'retrying') {
        // Not yet running: cancelling is immediate and terminal.
        await tx.execute(
          `UPDATE ${this.executionsTable}
              SET status = 'cancelled',
                  cancel_reason = $1,
                  cancel_requested_at = now(),
                  finished_at = now(),
                  updated_at = now(),
                  version = $2
            WHERE namespace = $3 AND id = $4`,
          [normalizedReason, version + 1, this.namespace, executionId],
        );
      } else if (status === 'running') {
        // Running: only a request. The worker converges at its next checkpoint.
        await tx.execute(
          `UPDATE ${this.executionsTable}
              SET status = 'cancel_requested',
                  cancel_reason = $1,
                  cancel_requested_at = now(),
                  updated_at = now(),
                  version = $2
            WHERE namespace = $3 AND id = $4`,
          [normalizedReason, version + 1, this.namespace, executionId],
        );
      }
      // Terminal statuses fall through unchanged, matching the Python `else`
      // branch which returns the row without writing.
    });

    return this.get(executionId);
  }

  /**
   * Run `fn` inside a single-connection transaction when the backend supports
   * it, otherwise fall back to sequential statements.
   *
   * The transaction path is required for `cancel` correctness on PostgreSQL.
   */
  private async runInTransaction<T>(
    fn: (tx: Backend) => Promise<T>,
  ): Promise<T | null> {
    const maybe = this.backend as Backend & {
      transaction?: <U>(inner: (tx: Backend) => Promise<U>) => Promise<U>;
    };
    if (typeof maybe.transaction === 'function') {
      return maybe.transaction(fn);
    }
    // Backends without explicit transaction support (e.g. a test double) run
    // the statements in sequence. Acceptable only for single-writer scenarios.
    return fn(this.backend);
  }

  /**
   * Return the successful result, or raise based on the terminal status.
   *
   * Mirrors ExecutionClient.result (src/onestep/execution.py:451-463).
   */
  async result(executionId: string): Promise<unknown> {
    const execution = await this.get(executionId);
    if (execution === null) throw new ExecutionNotFound(executionId);
    switch (execution.status) {
      case 'succeeded':
        return execution.result;
      case 'failed':
        throw new ExecutionFailed(execution);
      case 'cancelled':
        throw new ExecutionCancelled(execution);
      case 'expired':
        throw new ExecutionExpired(execution);
      default:
        throw new ExecutionNotReady(execution);
    }
  }

  /**
   * Poll until the execution reaches a terminal status.
   *
   * Backoff follows the guidance in docs/broker/execution-overview.md:
   * 1/2/4/8s, then capped between 10 and 30 seconds.
   */
  async wait(
    executionId: string,
    options: { timeoutMs?: number; signal?: AbortSignal } = {},
  ): Promise<Execution> {
    const timeoutMs = options.timeoutMs ?? 300_000;
    const startedAt = Date.now();
    let delayMs = 1000;
    for (;;) {
      const execution = await this.get(executionId);
      if (execution === null) throw new ExecutionNotFound(executionId);
      if (execution.terminal) return execution;
      if (Date.now() - startedAt >= timeoutMs) {
        throw new ExecutionNotReady(execution);
      }
      if (options.signal?.aborted) {
        throw new ExecutionNotReady(execution);
      }
      await sleep(delayMs, options.signal);
      delayMs = Math.min(Math.max(delayMs * 2, 10_000), 30_000);
    }
  }

  /** Internal: fetch by idempotency key within this namespace. */
  private async findByIdempotencyKey(
    taskName: string,
    idempotencyKey: string,
  ): Promise<Execution | null> {
    const rows = await this.backend.query(
      `SELECT ${EXECUTION_COLUMNS.map(quoteIdentifier).join(', ')}
         FROM ${this.executionsTable}
        WHERE namespace = $1 AND task_name = $2 AND idempotency_key = $3`,
      [this.namespace, taskName, idempotencyKey],
    );
    if (rows.length === 0) return null;
    return mapRow(rows[0] as Record<string, unknown>);
  }

  /** Internal: read the stored digest for conflict comparison. */
  private async rawSubmissionDigest(executionId: string): Promise<string | null> {
    const rows = await this.backend.query(
      `SELECT submission_digest FROM ${this.executionsTable}
        WHERE namespace = $1 AND id = $2`,
      [this.namespace, executionId],
    );
    if (rows.length === 0) return null;
    const value = (rows[0] as Record<string, unknown>).submission_digest;
    return value === null || value === undefined ? null : String(value);
  }
}

/**
 * Refuse worker-only operations explicitly.
 *
 * Exported so an API layer can give a clear error instead of a TypeError when
 * someone mistakes this client for a worker.
 */
export function assertWorkerOnly(operation: string): never {
  throw new WorkerOperationUnsupported(
    `"${operation}" is a worker-side lease operation and is not available in the ` +
      `TypeScript client. Run it from the Python worker instead.`,
  );
}

function assertValidText(value: string, field: string, maximum: number): void {
  if (typeof value !== 'string') throw new TypeError(`${field} must be a string`);
  if (value.trim() === '') throw new Error(`${field} must not be empty`);
  if (value.length > maximum) {
    throw new Error(`${field} must be at most ${maximum} characters`);
  }
}

const UUID_RE = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;

function assertUuid(value: string, field: string): void {
  if (!UUID_RE.test(value)) throw new Error(`${field} must be a UUID`);
}

/**
 * Normalize an expiry to Python `isoformat()` in UTC.
 *
 * The server rejects naive datetimes (src/onestep/execution.py:70-77) and the
 * MySQL dialect converts to UTC before binding, so the client must send an
 * unambiguous aware instant.
 */
function normalizeExpiresAt(value: string | null): string | null {
  if (value === null) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    throw new Error('expires_at must be a valid datetime');
  }
  // Require an explicit offset or Z, matching the server's aware-only rule.
  if (!/([+-]\d{2}:\d{2}|Z)$/.test(value)) {
    throw new Error('expires_at must be timezone-aware (include an offset or Z)');
  }
  return date.toISOString().replace(/\.\d{3}Z$/, (m) => m.replace('.000Z', 'Z')).replace(/Z$/, '+00:00');
}

/** Detect a unique-constraint violation across both supported drivers. */
function isUniqueViolation(error: unknown): boolean {
  const code = (error as { code?: string } | null)?.code;
  // MySQL: ER_DUP_ENTRY. PostgreSQL: 23505.
  return code === 'ER_DUP_ENTRY' || code === '23505';
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    if (signal) {
      signal.addEventListener(
        'abort',
        () => {
          clearTimeout(timer);
          reject(new Error('aborted'));
        },
        { once: true },
      );
    }
  });
}
