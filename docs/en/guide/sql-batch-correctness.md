---
title: SQL Batch Write Correctness
outline: deep
---

# SQL Table Sink Batch Writes: Semantics and Boundaries

A list/tuple body passed to `send()` is written in one transaction;
`batch_size`, parameter limits, MySQL byte limits, and update-column policies
may split it into multiple statements. A failure of any statement rolls back
the whole send. The transactional guarantee requires a transactional storage
engine on the target table (InnoDB on MySQL).

`skip_null` decides whether to omit a column from the `SET` clause based on
the input Python `None` — it does not depend on how the driver encodes the
value (SQL NULL vs JSON null). Only adjacent rows with the same update-column
set are merged into one statement, so the relative order of duplicate update
keys is preserved and columns absent from the update set never fire
`UPDATE OF column` triggers. `update` mode uses `IS NULL` for NULL keys, with
bind parameters carrying the target column type.

An all-defaults SQLite payload such as `[{}, {}, {}]` executes three
`DEFAULT VALUES` statements through executemany, preserving the input row
count.

## Batch-Local Uniqueness Checks

`upsert` rejects duplicate inputs within the same batch when a unique index
covers them. Plain unique indexes allow multiple NULLs; PostgreSQL
`NULLS NOT DISTINCT` rejects conflicts per database rules. Partial unique
indexes only include input rows that satisfy the predicate. The check spans
the whole send and cannot be bypassed by lowering `batch_size`.

Exact duplicates of plain keys are checked in memory first. The regular path
for integer keys with integer inputs needs no extra DDL. Other reflectable
column unique indexes are validated with a connection-scoped temporary table
that exercises the real types, collations, partial indexes, and MySQL prefix
indexes; the temporary table is dropped after the check. It copies no target
data, defaults, sequences, foreign keys, or triggers, and never writes
validation data into the target table. MySQL unique indexes are inlined into
the CREATE TEMPORARY TABLE statement so a CREATE INDEX implicit commit cannot
break the transaction.

This database validation path requires temporary-table privileges (MySQL
`CREATE TEMPORARY TABLES`, PostgreSQL database-level `TEMP`) and adds the cost
of creating the table and index and loading the validation rows. Insufficient
privileges fail **before** the target table is written — there is no fallback
to an incomplete Python string comparison. The runtime account does not need
ALTER/DROP on the target table. Throughput tests should cover your real
indexes and column types.

Validation rows are chunked by `batch_size` and the dialect budget, one
executemany call per chunk, avoiding a per-row round trip. The driver may
still split a chunk or choose another execution strategy, so one API call
does not guarantee a single SQL statement or network round trip. Beyond
checking the per-row encoded size, the MySQL path also bounds the multi-row
INSERT that asyncmy merges to the current connection's packet limit. All
logical chunks and driver-split sub-batches land in the same unique-indexed
temporary table, so conflict checking still covers the whole send.

The guarantee covers reflected column unique indexes, index columns explicitly
present in the payload, and partial indexes whose predicate inputs are
available. A partial index missing a required predicate field rejects the
batch; column defaults are never guessed. Expression/functional unique
indexes, non-reflected indexes, server-generated values, and unique columns
omitted from the payload are outside this pre-check. They remain the target
database's constraint, but callers must not assume a full batch-local check
happened for them.

This check provides no cross-dialect equivalence guarantee against rows
already in the target or concurrent sends. In particular, MySQL
`ON DUPLICATE KEY UPDATE` can hit any unique key; PostgreSQL/SQLite conflict
targets remain the configured `keys`. Business logic should express record
identity with a single key set and must not treat multiple sends containing
secondary-unique conflicts as commutative operations.

## MySQL Encoded Packet Size

The 4 MiB estimate is only the initial split target, not a safety guarantee.
At execution time the sink reads the current connection's
`@@SESSION.max_allowed_packet` and measures the actual query bytes after
SQLAlchemy type processing and this connection's driver encoding and escaping
(including the COM_QUERY byte), requiring the result to be strictly below the
limit. An over-limit multi-row statement that has not been sent yet is split
further; a single row at or above the limit raises a `PERMANENT` connector
error and rolls back the preceding chunks.

The check covers the batch-uniqueness validation input statements. It uses
asyncmy's real `mogrify` and the connection encoding, including native JSON
serialization and the connection's escaping mode. A per-row executemany check
cannot bound statements the driver merges, so the cursor's `max_stmt_length`
is also capped, preserving any smaller driver-imposed limit, with headroom for
the COM_QUERY byte and strict boundaries. Disconnect errors still follow the
existing rules — not every error 2013 is a payload problem. A permanent
connector error does not trigger the sink's internal retry; whether the
enclosing task retries is decided by the task retry policy. A MySQL session's
temporary tables vanish when the session drops; the explicit DROP is skipped
when the connection is already dead, avoiding a reconnect inside a failing
transaction that would mask the original connection error.

## Verification

`tests/contract/test_onestep_sql_batch_regressions.py` runs the SQLite cases
by default; setting `ONESTEP_MYSQL_DSN` / `ONESTEP_POSTGRES_DSN` runs the
corresponding real-database cases. The MySQL large-packet tests only run with
`ONESTEP_TEST_ALLOW_GLOBAL_MYSQL_SETTINGS=1` and are meant for an isolated
test instance: they temporarily modify the global packet limit, create new
connections, and restore the setting in a finally block. Do not enable that
option against a production instance. The MySQL 8.0/8.4 CI runs this
regression set with the option enabled. The low-packet-limit cases use
32 KiB, covering whole-chunk overflow with a compliant single row, UTF-8 and
escaping, and single-row rejection; the value sits above the default 16 KiB
`net_buffer_length` so a 1 KiB configuration cannot produce a false pass that
never touches the real rejection boundary.
