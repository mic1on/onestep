/**
 * Error types mirroring onestep's public execution exceptions.
 *
 * Transcribed from src/onestep/execution.py. The HTTP mapping in the
 * docstrings follows docs/broker/execution-overview.md so a thin API layer can
 * translate them directly.
 */

import type { Execution } from './schema.ts';

/** Base class for every client error. */
export class OnestepExecutionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = new.target.name;
  }
}

/**
 * The execution id does not exist in this namespace.
 *
 * API mapping: 404.
 */
export class ExecutionNotFound extends OnestepExecutionError {
  readonly executionId: string;
  constructor(executionId: string) {
    super(`execution not found: ${executionId}`);
    this.executionId = executionId;
  }
}

/**
 * The execution is not in a terminal state yet; keep polling.
 *
 * API mapping: 202 (or 409, depending on your chosen contract).
 */
export class ExecutionNotReady extends OnestepExecutionError {
  readonly execution: Execution;
  constructor(execution: Execution) {
    super(`execution is not ready: ${execution.status}`);
    this.execution = execution;
  }
}

/**
 * The execution finished as `failed`.
 *
 * API mapping: 422.
 */
export class ExecutionFailed extends OnestepExecutionError {
  readonly execution: Execution;
  constructor(execution: Execution) {
    super(`execution failed: ${execution.id}`);
    this.execution = execution;
  }
}

/**
 * The execution was cancelled.
 *
 * API mapping: 409.
 */
export class ExecutionCancelled extends OnestepExecutionError {
  readonly execution: Execution;
  constructor(execution: Execution) {
    super(`execution was cancelled: ${execution.id}`);
    this.execution = execution;
  }
}

/**
 * The execution expired before it ran.
 *
 * API mapping: 410.
 */
export class ExecutionExpired extends OnestepExecutionError {
  readonly execution: Execution;
  constructor(execution: Execution) {
    super(`execution expired: ${execution.id}`);
    this.execution = execution;
  }
}

/**
 * An idempotency key was reused with a different submission.
 *
 * Mirrors ExecutionConflict (machine.py:486-488). This is the error a
 * digest mismatch produces, which is exactly why canonical.ts must match
 * Python byte-for-byte.
 */
export class ExecutionConflict extends OnestepExecutionError {}

/**
 * The payload or metadata exceeded a configured size limit.
 *
 * Mirrors ExecutionEncodingError. The server defaults are 1 MiB payload,
 * 64 KiB metadata, 1 MiB result.
 */
export class ExecutionEncodingError extends OnestepExecutionError {}

/**
 * Raised when a caller tries to use a worker-only capability.
 *
 * The client is deliberately not a participant in the lease protocol: claim,
 * heartbeat, complete and reclaim are worker-side writes. Exposing them here
 * would let a caller break lease semantics that the Python worker relies on,
 * so they are refused explicitly rather than left unimplemented.
 */
export class WorkerOperationUnsupported extends OnestepExecutionError {}
