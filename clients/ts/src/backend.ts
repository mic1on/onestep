/**
 * Backend driver abstraction.
 *
 * Kept deliberately small: the client performs four operations (insert one
 * row, select one row, keyset-select a page, update one row). No ORM, because
 * the schema is a fixed wire contract and an ORM would add a second place for
 * it to drift.
 */

/** A backend that can execute the handful of statements the client needs. */
export interface Backend {
  /**
   * Run a query in an open transaction and return the rows.
   *
   * `values` are bound parameters; `$1`-style placeholders are used by the
   * PostgreSQL backend and rewritten for MySQL by its implementation.
   */
  query(sql: string, values: unknown[]): Promise<Record<string, unknown>[]>;

  /** Run a statement that returns the affected row count. */
  execute(sql: string, values: unknown[]): Promise<number>;

  /** Close pooled resources. */
  close(): Promise<void>;
}

/** Placeholder style understood by a backend. */
export type PlaceholderStyle = 'dollar' | 'question';

/**
 * Rewrite `$1, $2` placeholders to `?` for MySQL.
 *
 * Written manually rather than by regex to avoid mangling `$` inside string
 * literals; the client's SQL is small and fixed, so a scan is sufficient.
 */
export function toQuestionPlaceholders(sql: string): string {
  return sql.replace(/\$(\d+)/g, '?');
}
