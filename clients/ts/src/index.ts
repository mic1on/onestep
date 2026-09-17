/**
 * @onestep/client — TypeScript ExecutionClient for onestep tracked execution.
 *
 * Coordinates with a Python onestep worker through the shared execution tables:
 *
 *   Next.js API route (this client)          Python worker (unmodified)
 *     submit / get / list / cancel      onestep_executions + attempts
 *                                       claim / heartbeat / complete
 *
 * The worker side is intentionally out of scope. See docs/broker/execution-overview.md
 * in the onestep repository for the two-process architecture.
 */

export { ExecutionClient, assertWorkerOnly } from './client.ts';
export type { ExecutionClientOptions, SubmitOptions, ListOptions } from './client.ts';

export { PostgresBackend } from './backends/postgres.ts';

export type { Backend, PlaceholderStyle } from './backend.ts';
export { toQuestionPlaceholders } from './backend.ts';

export {
  EXECUTION_STATUSES,
  TERMINAL_STATUSES,
  isTerminal,
  mapRow,
  parseDbTimestamp,
  DEFAULT_EXECUTIONS_TABLE,
  DEFAULT_ATTEMPTS_TABLE,
  EXECUTION_COLUMNS,
} from './schema.ts';
export type { Execution, ExecutionPage, ExecutionStatus, ExecutionErrorDetail } from './schema.ts';

export {
  OnestepExecutionError,
  ExecutionNotFound,
  ExecutionNotReady,
  ExecutionFailed,
  ExecutionCancelled,
  ExecutionExpired,
  ExecutionConflict,
  ExecutionEncodingError,
  WorkerOperationUnsupported,
} from './errors.ts';

export { encodeCursor, decodeCursor, toPythonIsoformatUtc } from './cursor.ts';
export type { DecodedCursor } from './cursor.ts';

export {
  canonicalize,
  canonicalSha256,
  formatPyFloat,
  submissionDigest,
  PyFloat,
  PyDateTime,
} from './canonical.ts';
export type { CanonicalValue } from './canonical.ts';
