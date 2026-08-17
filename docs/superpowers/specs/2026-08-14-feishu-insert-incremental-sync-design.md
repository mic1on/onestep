# Feishu Insert Incremental Sync Design

## Status and decision record

This document formalizes the captain-approved design for one production-shaped chain:
immutable operation-log rows in MySQL are read from `view_follow_record_sync` and
inserted into one Feishu Bitable table. `unionKey` is globally unique across every
upstream data source. The MySQL cursor is `(dataCreateTime, unionKey)` and the Feishu
match field is `编号`.

The chosen design is deliberately narrower than a general synchronization system:

1. opt-in Insert mode preloads the destination key field into memory;
2. every buffered send waits for its own durable-or-pre-existing outcome;
3. uncertain batch writes are reconciled by exact key lookup before any repeat;
4. MySQL owns durable, contiguous cursor progress and logical-row retry;
5. cursor writes are coalesced at an event-loop commit boundary.

The alternative designs were rejected for this workload:

- A durable idempotency ledger gives stronger multi-process guarantees but violates
  the approved boundary and adds schema and operational ownership.
- Per-row destination search preserves statelessness but retains the measured
  100,000-search bottleneck.
- CDC, reconciliation snapshots, update/upsert, deletes, and a workflow abstraction
  solve different products and are not part of this change.

There are no unresolved product choices in this design.

## Goals

- Make normal Insert processing require no exact Feishu search per input row.
- Do not acknowledge a MySQL delivery until its specific destination key is either
  confirmed present or its create is confirmed successful.
- Preserve batches of 100 with task concurrency 100; waiting for confirmation must
  not serialize the chain to one record at a time.
- Never blindly repeat a batch after a transport or service outcome that might have
  committed.
- Persist only the MySQL composite cursor. Do not persist Feishu record IDs, payload
  hashes, or an idempotency mapping.
- Retry the same logical MySQL row with incremented `Envelope.attempts`, so
  `MaxAttempts` is effective before later restart recovery.
- Bound startup scanning, in-flight waiters, retries, and recovery work.
- Emit useful counters, sizes, durations, and outcomes without exposing credentials,
  payloads, or business key values.
- Keep current behavior unchanged unless the new Insert index is explicitly enabled.

## Non-goals

- Relation resolution or any related-table record ID handling.
- Retaining destination record IDs.
- A durable idempotency ledger or mapping table.
- Correctness with independent writers targeting the same Feishu table.
- Updates, upserts, deletion propagation, mutable-row synchronization, CDC, or
  snapshot reconciliation.
- Changes to the captain's handler or production deployment.
- A generic runtime batching API. The reliable buffer remains a Feishu sink concern;
  retry and cursor changes remain a MySQL incremental-source concern.

## Existing defects and constraints

At the starting revision `b415779`:

- `DeliveryExecutor` sends to sinks before `delivery.ack()`
  (`src/onestep/runtime/executor.py`, `_apply_success` and `_send_to_sink`). This order
  is correct only if `Sink.send()` means the sink operation has completed.
- `FeishuBitableTableSink.send()` appends below-threshold records to `_buffer` and
  returns. The executor therefore acknowledges and the incremental source can persist
  cursors before Feishu receives the batch.
- Insert uses `_batch_match_and_split()`, which performs one exact search per distinct
  key before batch create. Batch size reduces writes but not normal lookup count.
- `IncrementalDelivery.retry()` only sleeps. It neither increments attempts nor makes
  the same row fetchable again. Because `_fetched_cursor` has already moved beyond the
  row, `MaxAttempts` does not retry that logical row in the running process.
- `IncrementalTableSource.ack_token()` saves once for each in-order acknowledgement.
  With a reliable Feishu batch releasing 100 waiters together, this can turn one
  destination write into roughly 100 cursor-store writes.

The existing Source/Delivery/runtime contract is sufficient. No core API or execution
ordering change is required.

## Configuration contract

The feature is opt-in and limited to the approved safe combination:

| Field | Type | Default | Meaning |
|---|---|---:|---|
| `insert_key_index` | boolean | `false` | Preload and use an in-memory key index for Insert mode. |
| `insert_index_page_size` | integer | `500` | Destination records requested per startup page; normalized to Feishu's maximum 500. |
| `insert_index_max_pages` | integer | `200` | Hard startup scan bound. At defaults, at most 100,000 destination records are examined. |
| `ambiguous_write_max_rounds` | integer | `3` | Maximum exact-search/create reconciliation rounds for one uncertain batch. |
| `batch_size` | integer | `1` | Feishu sink write boundary; the captain uses 100. |
| `flush_interval_s` | number | `1.0` | Maximum age of a partial buffered batch. |

`insert_key_index: true` is valid only when all of these hold:

- `mode` is lowercase `insert`;
- `match_fields` contains exactly one field;
- `relations` is absent.

Both Python construction and strict YAML validation enforce the same rules. This
restriction keeps the implementation surgical and makes the index a set of canonical
text keys rather than a general expression or composite-key engine. The captain's
field is `编号`.

The source uses existing `mysql_cursor_store` wiring. No new MySQL YAML fields are
required. An explicit stable `state_key` is required in the workload documentation so
renaming a YAML resource cannot reset progress accidentally.

### Canonical key

The indexed match value is normalized as text:

- non-empty strings are stripped;
- finite integers, finite floats, and booleans use their stable string form;
- other values and empty text are permanent payload errors.

The same function processes destination scan values, incoming payload values, and
recovery search values. The key is held only in memory and is never logged. Records in
the destination that lack a usable `编号` are counted as `missing_key_records` and do
not enter the index. Duplicate destination keys collapse in the set and increment an
aggregate duplicate count.

## Components and responsibilities

### Feishu destination key index

`FeishuBitableTableSink.open()` performs the opt-in scan before task runners start.
It repeatedly calls the existing records search endpoint with:

```python
{
    "field_names": [self.match_fields[0]],
}
```

and follows `page_token`, requesting `insert_index_page_size` records. Only the match
field is requested. The scan stops on `has_more == false`. If Feishu still reports
another page after `insert_index_max_pages`, startup fails with a misconfiguration
error; it never starts with a known-truncated index.

The bound is observable as pages, keys, missing-key records, duplicate keys, duration,
configured page size, and configured maximum pages. For the captain's approximately
50,000 destination keys, page size 500 requires about 100 startup requests and remains
below the default 200-page limit.

The set is authoritative for normal processing during that sink instance:

- key present: complete that send as confirmed pre-existing;
- key absent: place it in the reliable buffer without a normal exact lookup;
- confirmed create: add the key before completing its waiters.

No destination record ID is read into durable state or retained by the sink.

### Reliable buffered item and key group

The buffer owns `_PendingInsert` entries rather than bare field dictionaries:

```python
@dataclass
class _PendingInsert:
    key: str
    fields: dict[str, Any]
    waiters: list[asyncio.Future[None]]
    buffered_at: float
    state: _InsertState
```

A key has one buffered payload and one or more waiters. A second concurrent send for
the same absent key joins the existing key group instead of creating a duplicate
record. Insert semantics make the first buffered payload authoritative; all joined
waiters represent the same globally unique operation key.

`send()` performs these actions:

1. reject new work after close begins;
2. parse and canonicalize the match key;
3. if the key is indexed, record an avoided lookup and return successfully;
4. if the key is marked uncertain, perform bounded exact reconciliation before any
   normal buffering;
5. create a waiter and append or join the pending key group under the buffer lock;
6. elect one threshold flusher or ensure one timer flusher exists;
7. release the lock;
8. run the elected flush if applicable, then await this send's waiter.

The lock is never held while a caller awaits its waiter or while network I/O runs.
With concurrency 100 and batch size 100, 100 delivery tasks can enter `send()`; the
100th elects the flush and all 100 complete from one batch result. If fewer than 100
new keys are buffered—for example because some inputs already exist—the independent
flush timer commits the partial batch.

### MySQL logical-row retry queue

The MySQL source retains cursor ordering in `_pending`, and adds a source-local queue
of retry deliveries. A retry delivery contains the same payload and cursor token with
`Envelope.attempts + 1`.

`IncrementalDelivery.retry(delay_s)` delegates to the source. The source immediately
marks that token retrying, which pauses new SQL reads beyond the gap; after the
requested bounded delay, it enqueues that same logical row. `fetch(limit)` drains
ready retry deliveries before issuing a new SQL query and returns no new rows while a
retry token is delayed or in flight. It does not append the cursor token to `_pending`
a second time.

This is the smallest design consistent with the existing contract: runtime policy
continues to call `Delivery.retry()`, while the source decides how its claimed logical
row becomes available again. The business handler contains no retry bookkeeping.

When `MaxAttempts` is exhausted, `IncrementalDelivery.fail()` marks the source blocked
at that cursor and the next fetch raises a permanent, privacy-safe
`ConnectorOperationError`. The durable cursor remains before the failed row and the
worker stops rather than accumulating an unbounded suffix behind an uncommittable gap.
A process restart retries from the durable cursor with attempts reset, which is normal
at-least-once restart behavior. Operators must correct a permanent payload or raise
the configured attempt budget before restarting.

### Coalesced cursor commit coordinator

The source continues to advance only through the contiguous prefix of `_pending`
whose tokens were acknowledged after their sink waiters completed.

Instead of saving inside every `ack_token()` call, the first acknowledgement that
makes the head contiguous schedules one `_flush_commits()` task. The task yields once
to the event loop, snapshots the highest contiguous acknowledged token, and performs
one `state.save(state_key, list(highest))`. Acknowledgements arriving during the save
are included in a subsequent loop iteration before the commit task exits.

Exact safety boundary:

- an in-order `ack_token()` that participates in the active commit waits for that
  commit task;
- out-of-order acknowledgements beyond a gap may return, as they do today, but cannot
  move the durable cursor;
- `_pending` entries are removed and `_committed_cursor` changes only after the store
  save succeeds;
- on save failure, pending/acknowledged state remains retryable and the triggering
  acknowledgement receives the error;
- source close drains the active commit task or propagates its error.

A crash before the coalesced save replays already-created rows. The Feishu startup
index confirms them pre-existing, so replay is safe. A cursor is never persisted past
an unconfirmed sink waiter, so coalescing does not introduce data loss.

## Per-record state transitions

```text
FETCHED(attempt=0)
  -> HANDLER_OK
  -> SINK_SEND
       -> KEY_INDEX_HIT -> CONFIRMED_PREEXISTING
       -> BUFFERED -> WRITE_INFLIGHT -> CONFIRMED_CREATED
       -> BUFFERED -> WRITE_INFLIGHT -> RECOVERY_LOOKUP
            -> FOUND -> CONFIRMED_PREEXISTING
            -> MISSING -> RECOVERY_CREATE -> CONFIRMED_CREATED
            -> UNRESOLVED/PERMANENT_ERROR -> SEND_FAILED
  -> ACK_PENDING
       -> OUT_OF_ORDER_ACKED (held behind a gap)
       -> CURSOR_COMMIT_PENDING
       -> CURSOR_COMMITTED

SEND_FAILED
  -> runtime immediate connector retry (same sink instance)
  -> source MaxAttempts retry of SAME_LOGICAL_ROW(attempt + 1)
  -> terminal source block when the configured attempt budget is exhausted
```

A send returns only from `CONFIRMED_PREEXISTING` or `CONFIRMED_CREATED`. It raises for
all error states. Therefore the existing executor's subsequent `delivery.ack()` is no
longer premature.

## Per-batch state transitions

```text
COLLECTING
  -- unique key count == batch_size --> SEALED(reason=threshold)
  -- oldest age == flush_interval_s --> SEALED(reason=timer)
  -- sink close --------------------> SEALED(reason=close)

SEALED -> WRITING
  -> HTTP/API CONFIRMED SUCCESS
       -> index all member keys
       -> complete all member waiters successfully
       -> remove batch
  -> DEFINITE FAILURE
       -> complete all member waiters with the error
       -> remove batch
  -> AMBIGUOUS FAILURE
       -> mark all member keys uncertain
       -> RECONCILING
            -> exact-search only affected keys
            -> complete found keys successfully and index them
            -> batch-create only keys confirmed missing
            -> repeat at most ambiguous_write_max_rounds
            -> complete unresolved keys with error; retain their uncertain marks
```

A successful batch response is confirmed only when the API reports success and its
`records` list contains one response record per submitted unique key. A malformed or
short success response enters reconciliation because the actual write outcome cannot
be assigned safely.

A definite failure is a permanent or misconfigured connector error that proves the
write was rejected. `UNCERTAIN`, disconnected, transient, and throttled write errors
are treated conservatively as ambiguous. Exact-search errors never mean “missing.”

## Ambiguous-outcome recovery

Recovery is local to the affected batch. It never scans the full destination and never
checks unrelated keys.

For each round:

1. exact-search every unresolved key with `page_size=2` and bounded concurrency 20;
2. more than one match is a permanent destination uniqueness error;
3. one match confirms success and adds the key to the index;
4. zero matches confirms the key is currently missing;
5. create only the confirmed-missing subset as one batch;
6. validate that batch response; on another ambiguous result, begin the next round.

Found-key waiters may complete while unresolved members continue recovery. A definite
create failure completes only the keys submitted in that failed create; already found
keys remain successful.

Unresolved keys remain in an in-memory `uncertain_keys` set even after their current
waiters receive an error. A runtime retry of such a key must exact-search first and may
create only after a confirmed miss. Thus the executor's built-in immediate retry and
the source's later logical-row retry cannot blindly duplicate an uncertain write.

On process restart the full destination scan reconstructs the index and replaces the
non-durable uncertainty set. With no durable ledger, the design necessarily relies on
Feishu list/search reflecting accepted creates by the next bounded recovery or restart
scan. This is the strongest supported ambiguity handling within the explicit no-ledger
boundary; it is not an exactly-once or multi-writer guarantee.

## Startup and restart flow

1. Build resources and validate strict YAML.
2. Open the MySQL connector and cursor store.
3. Open `mysql_incremental`; load the existing cursor list under the explicit
   `state_key`. Missing state means the beginning of the view.
4. Open the Feishu sink; page `编号` into the bounded in-memory set.
5. Only after every resource opens successfully, start task runners.
6. Fetch from `(dataCreateTime, unionKey) > committed_cursor`, ordered by the same
   tuple and limited by source batch size and available task concurrency.
7. Process with task concurrency 100 and sink batch size 100.

After a crash:

- any cursor already saved is not refetched;
- any destination write confirmed before an unsaved cursor can replay;
- the rebuilt destination key index turns that replay into confirmed pre-existing
  success without another create;
- any ambiguous accepted write is discovered by the startup scan, subject to the
  Feishu visibility assumption above.

## Shutdown and cancellation

Runtime drain first stops fetches and waits for in-flight deliveries. Those deliveries
remain in flight while their sink waiters are pending, so a normal drain naturally
allows threshold or timer flushes to finish.

`FeishuBitableTableSink.close()` then:

1. atomically rejects new sends and cancels only a separate scheduled timer;
2. seals and flushes any remaining buffer with `reason=close`;
3. applies the same ambiguous recovery policy;
4. completes every remaining waiter successfully or exceptionally;
5. verifies the pending-key map and in-flight waiter count are zero.

If close itself cannot finish because network/recovery fails, all unresolved waiters
receive the final connector error before close raises. No Future is left pending.
Cancelled send coroutines do not remove buffered key groups; close still flushes or
fails them. Since cancelled deliveries are not acknowledged, restart replay is safe.

`IncrementalTableSource.close()` awaits an active cursor commit task and clears queued
retry envelopes only after preserving the durable cursor. Queued or in-flight rows
that were not acknowledged replay after restart.

## Single-writer limitation

Correct normal operation requires one active `FeishuBitableTableSink` writer for the
configured `(app_token, table_id)`.

The in-memory set is updated only by this sink. A second process, manual destination
inserts, or another application can create a key after startup without updating the
set, causing a create race. Feishu does not provide a plugin-controlled unique
constraint that closes that race. No process lock is added.

“Multiple data sources” in the approved workload means their immutable rows are
already unified by `view_follow_record_sync` and globally unique `unionKey`; it does
not mean multiple independent destination writers. Deployment must use one active
worker instance for this table. Scaling occurs through `concurrency: 100` inside that
worker.

## Performance model

Let:

- `D` be destination keys at startup;
- `N` be incoming rows;
- `E` be incoming keys already in the startup/current index;
- `B` be Feishu batch size (100);
- `P` be startup page size (500);
- `A` be ambiguous batches (normally zero).

Normal Feishu request count is approximately:

```text
startup scans = ceil(D / P)
batch creates = ceil((N - E) / B)
normal exact searches = 0
recovery exact searches <= A * B * ambiguous_write_max_rounds
```

At `D=50,000`, `N=100,000`, and 50,000 overlapping keys, normal traffic is about 100
startup pages plus 500 creates: 600 data requests, excluding token acquisition. With
no overlap it is about 1,100 requests. The current path is about 100,000 exact searches
plus writes.

Memory is `O(min(D, page_size * max_pages) + B + concurrency)` key/waiter entries. At
default bounds, at most 100,000 scanned records can contribute keys. The implementation
benchmark measures request counts and bounded objects, not wall-clock performance
against production.

## Observability and privacy

Plugins emit structured log records with numeric aggregate fields. They do not add a
new core metrics API. Existing task events continue to expose end-to-end outcomes;
connector logs supply the missing internal stages.

### MySQL fields

- `event=mysql_incremental_fetch`: `fetch_count`, `requested_limit`, `row_count`,
  `duration_s`, `pending_cursor_rows`, `fetched_cursor_lag_rows`.
- `event=mysql_incremental_retry`: `retry_count`, `attempt`, `delay_s`,
  `pending_cursor_rows`.
- `event=mysql_incremental_cursor_commit`: `cursor_save_count`,
  `coalesced_ack_count`, `duration_s`, `outcome`, `pending_cursor_rows`,
  `fetched_cursor_lag_rows`.

`fetched_cursor_lag_rows` is a count of fetched tokens not covered by the committed
prefix. Composite cursor values themselves are not logged.

### Feishu fields

- `event=feishu_insert_index_scan`: `scan_pages`, `scan_keys`,
  `missing_key_records`, `duplicate_keys`, `duration_s`, `outcome`,
  `page_size`, `max_pages`.
- `event=feishu_insert_buffer`: `buffered_batch_size`, `oldest_batch_age_s`,
  `inflight_waiter_count`, `flush_reason`.
- `event=feishu_insert_batch_write`: `batch_size`, `duration_s`, `outcome`,
  `flush_reason`, `recovery_round`.
- `event=feishu_insert_lookup`: cumulative `normal_lookup_avoided_count`,
  `recovery_lookup_count`, `outcome`.
- `event=feishu_insert_retry`: `retry_count`, `recovery_round`, `unresolved_count`.

Allowed outcomes are bounded strings such as `success`, `preexisting`, `ambiguous`,
`permanent_error`, and `exhausted`. Logs never include DSNs, app secrets, app tokens,
payloads, field values, `unionKey`, `编号`, record IDs, search bodies, or cursor values.
Tests inspect `LogRecord` extras and serialized messages for privacy.

## Migration and rolling upgrade

1. Release the reliable Feishu sink and MySQL retry/commit changes with
   `insert_key_index` defaulting to false.
2. Add durable `mysql_cursor_store` and the explicit `state_key` to strict YAML before
   enabling the index.
3. Stop the old worker completely. Do not overlap old and new workers; this is both a
   rolling-upgrade safeguard and the single-writer requirement.
4. Start the new worker with `insert_key_index: true` and inspect the startup scan
   completion record before accepting source processing.

If the old deployment used only `InMemoryCursorStore`, there is no cursor to migrate.
The new worker starts from the beginning, but the destination preload skips all keys
already present. This replay is intentional and avoids inventing a cursor from an
unknown in-memory position.

The persisted cursor representation remains the existing JSON list, so a new MySQL
plugin can read old durable state and the previous plugin can read newly committed
state.

### Rollback

- To retain reliability while reducing change surface, set `insert_key_index: false`;
  Insert falls back to exact searches but keeps per-item completion waiters and safe
  cursor acknowledgement.
- A binary rollback must stop the new worker first. Do not run old and new binaries
  concurrently.
- Do not roll back to a Feishu plugin whose buffered `send()` returns before write
  completion while incremental cursor persistence is enabled. If such a rollback is
  unavoidable, stop processing and reconcile destination/source state before resume.
- The durable cursor needs no schema downgrade.

## Correct strict YAML for the captain workload

```yaml
apiVersion: onestep/v1alpha1
kind: App

app:
  name: follow-record-sync
  shutdown_timeout_s: 120
  strict_env: true

resources:
  mysql_source:
    type: mysql
    dsn: "${MYSQL_DSN}"

  mysql_cursors:
    type: mysql_cursor_store
    connector: mysql_source
    table: onestep_cursor
    auto_create: true

  follow_records:
    type: mysql_incremental
    connector: mysql_source
    table: view_follow_record_sync
    key: unionKey
    cursor: [dataCreateTime, unionKey]
    batch_size: 1000
    poll_interval_s: 1.0
    state: mysql_cursors
    state_key: follow-record-sync-v1

  feishu:
    type: feishu_bitable
    app_id: "${FEISHU_APP_ID}"
    app_secret: "${FEISHU_APP_SECRET}"
    timeout_s: 10.0

  follow_record_table:
    type: feishu_bitable_table_sink
    connector: feishu
    app_token: "${FEISHU_APP_TOKEN}"
    table_id: "${FEISHU_TABLE_ID}"
    mode: insert
    match_fields: [编号]
    batch_size: 100
    flush_interval_s: 1.0
    insert_key_index: true
    insert_index_page_size: 500
    insert_index_max_pages: 200
    ambiguous_write_max_rounds: 3

tasks:
  - name: insert_follow_records
    source: follow_records
    emit: follow_record_table
    handler:
      ref: app.tasks.follow_record:map_follow_record
    concurrency: 100
    timeout_s: 120
    retry:
      type: max_attempts
      max_attempts: 3
      delay_s: 1.0
    config:
      batch_size: 100
```

All enum-like values (`type`, `mode`, and retry `type`) are lowercase. The schema's
`apiVersion` and `kind` retain their required spelling.

`tasks[].config.batch_size` is only handler data available as
`ctx.task_config["batch_size"]`; it does not batch runtime deliveries. The independent
controls are:

- `follow_records.batch_size`: maximum rows returned by one source fetch, further
  capped by available task concurrency;
- `follow_record_table.batch_size`: Feishu write batch size;
- `tasks[].concurrency`: maximum concurrent delivery executions.

## Test strategy

### Feishu unit and integration-style tests

- Startup scans all pages, requests only `编号`, updates the index, and fails closed at
  the page limit.
- Python and strict YAML validation accept only the supported opt-in combination.
- Existing keys complete without exact search or write.
- 100 concurrent absent sends produce one batch create, and no send completes while
  the write is blocked.
- Timer and close flushes complete every member; write failure raises through every
  member; cancellation leaves no unresolved Future.
- Duplicate concurrent keys create once and complete all joined waiters.
- Confirmed creates update the set before waiter completion.
- Ambiguous success, partial visibility, repeated ambiguity, duplicate matches,
  recovery exhaustion, and retry-after-exhaustion follow the defined state machine.
- A real local HTTP server simulates connection loss after accepting a batch, then
  exposes accepted keys to search; no duplicate create occurs.
- Structured logs expose aggregate fields and omit secrets, payloads, keys, cursor
  values, and record IDs.

### MySQL tests

- A sink failure followed by `MaxAttempts(3)` executes the same cursor token with
  attempts 0, 1, and 2.
- Retry does not append duplicate `_pending` tokens, pauses new SQL reads while the
  cursor gap is retrying, and dispatches the ready retry before any later row.
- Exhaustion leaves durable state before the failed row and blocks further fetch.
- Out-of-order successes cannot move the cursor across a failed/waiting row.
- 100 contiguous concurrent acknowledgements produce one coalesced cursor save in the
  normal scheduling case.
- Save failure leaves tokens pending and a later retry can persist the same prefix.
- Source close drains a pending save.
- Existing SQLite restart and cursor tie-break tests remain green.

### End-to-end contract and benchmark

A fake MySQL source/Feishu connector chain runs 100 concurrent deliveries through the
real `DeliveryExecutor` ordering and proves the durable cursor remains unchanged until
the Feishu batch is confirmed.

A deterministic 100,000-row synthetic request-count benchmark preloads 50,000
existing destination keys in 100 pages, then processes 100,000 keys with 50,000
overlap. It asserts:

- 100 startup scans;
- zero normal exact searches;
- 500 batch creates at size 100;
- 600 total destination data requests, excluding token acquisition;
- no retained record IDs and no pending waiters at completion.

The benchmark uses fakes and count assertions rather than production timing, making
it stable in CI.

## Compatibility and release impact

No stable onestep core API changes. Existing Feishu configurations preserve per-row
search behavior because `insert_key_index` defaults to false. Reliable completion
changes the timing contract of buffered sends to match the documented Sink contract:
`send()` now means the item has completed rather than merely entered a private buffer.

The Feishu and MySQL plugins each receive a backward-compatible minor release because
they add public configuration/behavior and change operational guarantees. Their
`pyproject.toml`, root `CHANGELOG.md`, and `uv.lock` entries are updated using repository
release conventions. Documentation updates cover both plugins and the cross-plugin
example.
