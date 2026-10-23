/**
 * PostgreSQL backend for the TypeScript ExecutionClient.
 *
 * Verified against the schema the Python runtime actually creates — see
 * plugins/onestep-sql/src/onestep_sql/postgres/execution_schema.py. Relevant
 * facts observed from a live `\d onestep_executions`:
 *
 *   - payload / metadata / result / error are `jsonb`. `pg` already parses
 *     jsonb into JS objects, so values must NOT be JSON.stringify'd on write
 *     (passing an object lets the driver encode it) and must tolerate arriving
 *     as either an object or a string.
 *   - timestamps are `timestamp with time zone`. Read them as text through
 *     a pool-local parser so keyset cursors preserve all six fractional digits.
 *     JavaScript Date truncates microseconds before row mapping can recover them.
 *
 * Uses its own pg.Pool. The `pg` module is an optional peer dependency.
 */

import { type Backend } from '../backend.ts';

/** A minimal structural type for the parts of pg we use. */
interface PgPool {
  query(config: { text: string; values?: unknown[] }): Promise<{ rows: Record<string, unknown>[]; rowCount: number | null }>;
  end(): Promise<void>;
}

interface PgTypeParsers {
  getTypeParser(oid: number, format?: string): (value: string) => unknown;
}

interface PgModule {
  types: PgTypeParsers;
  Pool: new (config: Record<string, unknown>) => PgPool;
}

export interface PostgresBackendOptions {
  /** libpq connection string, e.g. postgresql://user:pass@host:5432/db */
  connectionString: string;
  /** Maximum pooled connections. */
  max?: number;
  /** Extra pg.Pool options; timestamp parsers and text result format are enforced. */
  poolOptions?: Record<string, unknown>;
  /** Injectable pg module (for tests); defaults to a dynamic import. */
  pgModule?: PgModule;
}

/**
 * Backend over `pg`.
 *
 * Note on transaction scope: this backend issues statements over pool
 * connections without an explicit BEGIN/COMMIT wrapper. That is safe for the
 * operations the client performs, with one deliberate exception handled by the
 * client itself — `cancel` needs `SELECT ... FOR UPDATE` and its follow-up
 * UPDATE on the *same* connection, so it goes through `PgTransaction` below.
 */
export class PostgresBackend implements Backend {
  private readonly pool: PgPool;
  private closed = false;

  private constructor(pool: PgPool) {
    this.pool = pool;
  }

  /** Build a backend, loading `pg` dynamically. */
  static async create(options: PostgresBackendOptions): Promise<PostgresBackend> {
    const pg = options.pgModule ?? (await loadPg());
    const inheritedTypes = (options.poolOptions?.types as PgTypeParsers | undefined) ?? pg.types;
    const pool = new pg.Pool({
      connectionString: options.connectionString,
      max: options.max ?? 10,
      ...(options.poolOptions ?? {}),
      // Keep raw microseconds before any driver Date/binary conversion.
      binary: false,
      types: {
        getTypeParser(oid: number, format?: string) {
          // The tracked-execution schema uses TIMESTAMPTZ (OID 1184).
          // Keep caller parsers for every other type; never mutate pg.types.
          if (oid === 1184) return (value: string) => value;
          return inheritedTypes.getTypeParser(oid, format);
        },
      },
    });
    return new PostgresBackend(pool);
  }

  async query(sql: string, values: unknown[] = []): Promise<Record<string, unknown>[]> {
    this.assertOpen();
    const result = await this.pool.query({ text: sql, values });
    return result.rows;
  }

  async execute(sql: string, values: unknown[] = []): Promise<number> {
    this.assertOpen();
    const result = await this.pool.query({ text: sql, values });
    return result.rowCount ?? 0;
  }

  /**
   * Run a function inside a transaction on a single connection.
   *
   * Required for `cancel`, whose `SELECT ... FOR UPDATE` must hold its row lock
   * across the subsequent UPDATE — exactly as the Python backend does with
   * `engine.begin()`.
   */
  async transaction<T>(fn: (tx: Backend) => Promise<T>): Promise<T> {
    this.assertOpen();
    const pool = this.pool as PgPool & {
      connect?: () => Promise<PgClient>;
    };
    if (typeof pool.connect !== 'function') {
      throw new Error('pg Pool does not expose connect(); cannot run a transaction');
    }
    const client = await pool.connect();
    const tx: Backend = {
      async query(sql, values = []) {
        const r = await client.query({ text: sql, values });
        return r.rows;
      },
      async execute(sql, values = []) {
        const r = await client.query({ text: sql, values });
        return r.rowCount ?? 0;
      },
      async close() {
        /* owned by the transaction wrapper */
      },
    };
    try {
      await client.query({ text: 'BEGIN' });
      const result = await fn(tx);
      await client.query({ text: 'COMMIT' });
      return result;
    } catch (error) {
      try {
        await client.query({ text: 'ROLLBACK' });
      } catch {
        /* the original error is more useful */
      }
      throw error;
    } finally {
      client.release();
    }
  }

  async close(): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    await this.pool.end();
  }

  private assertOpen(): void {
    if (this.closed) throw new Error('backend is closed');
  }
}

/** The subset of a pg client used by `transaction`. */
interface PgClient {
  query(config: { text: string; values?: unknown[] }): Promise<{ rows: Record<string, unknown>[]; rowCount: number | null }>;
  release(): void;
}

async function loadPg(): Promise<PgModule> {
  try {
    const mod = (await import('pg')) as unknown as { default?: PgModule } & PgModule;
    return mod.default ?? mod;
  } catch (error) {
    throw new Error(
      'the "pg" package is required for the PostgreSQL backend; install it with `npm i pg`',
      { cause: error },
    );
  }
}
