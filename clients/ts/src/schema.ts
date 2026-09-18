/**
 * Schema constants and row mapping for onestep tracked execution.
 *
 * Transcribed from plugins/onestep-sql/src/onestep_sql/mysql/execution_schema.py
 * (MySQL) and its PostgreSQL sibling. The column set is identical across both
 * backends; only dialect details differ.
 *
 * These names are a wire contract. Changing one here without changing the
 * Python schema breaks the client silently.
 */

export const EXECUTION_STATUSES = [
  'queued',
  'running',
  'retrying',
  'succeeded',
  'failed',
  'cancel_requested',
  'cancelled',
  'expired',
] as const;

export type ExecutionStatus = (typeof EXECUTION_STATUSES)[number];

/**
 * The four terminal statuses.
 *
 * Mirrors TERMINAL_EXECUTION_STATUSES in src/onestep/execution.py:28-35.
 */
export const TERMINAL_STATUSES: ReadonlySet<ExecutionStatus> = new Set([
  'succeeded',
  'failed',
  'cancelled',
  'expired',
]);

export function isTerminal(status: ExecutionStatus): boolean {
  return TERMINAL_STATUSES.has(status);
}

/** Default table names, matching ExecutionStateMachine.__init__. */
export const DEFAULT_EXECUTIONS_TABLE = 'onestep_executions';
export const DEFAULT_ATTEMPTS_TABLE = 'onestep_execution_attempts';

/**
 * Column names of the executions table, in a stable order.
 *
 * Kept explicit (rather than `SELECT *`) so a future additive column in Python
 * cannot silently change result shapes here.
 */
export const EXECUTION_COLUMNS = [
  'id',
  'namespace',
  'task_name',
  'status',
  'payload',
  'metadata',
  'result',
  'error',
  'idempotency_key',
  'submission_digest',
  'attempts',
  'available_at',
  'lease_token',
  'lease_expires_at',
  'worker_id',
  'cancel_reason',
  'cancel_requested_at',
  'expires_at',
  'created_at',
  'updated_at',
  'started_at',
  'finished_at',
  'version',
] as const;

/**
 * An execution row as the client sees it.
 *
 * Mirrors the public `Execution` dataclass (src/onestep/execution.py:106-140).
 * Internal coordination columns (lease_token, worker_id, ...) are intentionally
 * not surfaced — the client is not a participant in the lease protocol.
 */
export interface Execution {
  id: string;
  namespace: string;
  taskName: string;
  status: ExecutionStatus;
  payload: unknown;
  metadata: Record<string, unknown>;
  result: unknown | null;
  error: ExecutionErrorDetail | null;
  attempts: number;
  createdAt: string;
  availableAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  cancelRequestedAt: string | null;
  expiresAt: string | null;
  version: number;
  terminal: boolean;
}

/** Normalized failure detail, mirroring `ExecutionErrorDetail`. */
export interface ExecutionErrorDetail {
  kind: string;
  exceptionType: string;
  stage?: string;
  backend?: string;
  operation?: string;
  connectorKind?: string;
  /** Comma-joined emit sink names already written before a sink-stage failure. */
  sinksSucceeded?: string;
  /** Comma-joined emit sink names a retry would replay after a sink-stage failure. */
  sinksRemaining?: string;
}

/** A page of executions plus the cursor for the next page. */
export interface ExecutionPage {
  items: Execution[];
  nextCursor: string | null;
}

/**
 * Parse a database timestamp into Python isoformat-in-UTC form.
 *
 * The MySQL backend reads DATETIME(6) under a UTC-pinned session, and the
 * schema deliberately stores microseconds. Drivers may hand back either a Date
 * or a string depending on configuration, so both are handled.
 */
export function parseDbTimestamp(value: unknown): string {
  if (value === null || value === undefined) {
    throw new Error('expected a timestamp, got null');
  }
  if (value instanceof Date) {
    return isoformatUtc(value, null);
  }
  const text = String(value);
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) {
    throw new Error(`unparseable timestamp: ${text}`);
  }
  // Preserve microsecond digits the driver passed through as text.
  const micro = text.match(/\.(\d+)/);
  return isoformatUtc(date, micro ? micro[1] : null);
}

/** Emit Python `isoformat()` for a UTC instant, keeping microseconds. */
function isoformatUtc(date: Date, fractionalDigits: string | null): string {
  const pad = (n: number, w = 2) => String(n).padStart(w, '0');
  const base =
    `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}` +
    `T${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}:${pad(date.getUTCSeconds())}`;
  let frac = '';
  if (fractionalDigits) {
    const digits = fractionalDigits.replace(/0+$/, '');
    if (digits.length > 0) frac = '.' + digits.padEnd(6, '0').slice(0, 6);
  }
  return `${base}${frac}+00:00`;
}

/** Convert a nullable DB timestamp. */
function parseNullableTimestamp(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  return parseDbTimestamp(value);
}

/** Parse a JSON column that may arrive as a string or an object. */
function parseJsonColumn(value: unknown): unknown {
  if (value === null || value === undefined) return null;
  if (typeof value === 'string') {
    try {
      return JSON.parse(value);
    } catch {
      return value;
    }
  }
  return value;
}

/** Map a raw driver row to an `Execution`. */
export function mapRow(row: Record<string, unknown>): Execution {
  const status = String(row.status) as ExecutionStatus;
  if (!(EXECUTION_STATUSES as readonly string[]).includes(status)) {
    throw new Error(`unknown execution status from database: ${status}`);
  }
  const rawError = parseJsonColumn(row.error);
  return {
    id: String(row.id),
    namespace: String(row.namespace),
    taskName: String(row.task_name),
    status,
    payload: parseJsonColumn(row.payload),
    metadata: (parseJsonColumn(row.metadata) ?? {}) as Record<string, unknown>,
    result: parseJsonColumn(row.result),
    error: rawError === null ? null : normalizeErrorDetail(rawError),
    attempts: Number(row.attempts),
    createdAt: parseDbTimestamp(row.created_at),
    availableAt: parseDbTimestamp(row.available_at),
    startedAt: parseNullableTimestamp(row.started_at),
    finishedAt: parseNullableTimestamp(row.finished_at),
    cancelRequestedAt: parseNullableTimestamp(row.cancel_requested_at),
    expiresAt: parseNullableTimestamp(row.expires_at),
    version: Number(row.version),
    terminal: isTerminal(status),
  };
}

/**
 * Normalize a stored error object into `ExecutionErrorDetail`.
 *
 * The Python dataclass uses snake_case fields; the public TS shape is camelCase.
 */
function normalizeErrorDetail(value: unknown): ExecutionErrorDetail {
  const obj = (value ?? {}) as Record<string, unknown>;
  const detail: ExecutionErrorDetail = {
    kind: String(obj.kind ?? 'unknown'),
    exceptionType: String(obj.exception_type ?? obj.exceptionType ?? 'unknown'),
  };
  const mapping: Array<[keyof ExecutionErrorDetail, string]> = [
    ['stage', 'stage'],
    ['backend', 'backend'],
    ['operation', 'operation'],
    ['connectorKind', 'connector_kind'],
    ['sinksSucceeded', 'sinks_succeeded'],
    ['sinksRemaining', 'sinks_remaining'],
  ];
  for (const [target, source] of mapping) {
    const raw = obj[source];
    if (raw !== null && raw !== undefined) {
      (detail as unknown as Record<string, unknown>)[target] = String(raw);
    }
  }
  return detail;
}
