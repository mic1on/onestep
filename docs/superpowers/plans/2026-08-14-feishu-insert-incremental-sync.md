# Feishu Insert Incremental Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the single-writer `view_follow_record_sync` MySQL incremental → Feishu Bitable Insert chain fast and crash-safe by preloading destination keys, awaiting per-item batch outcomes, reconciling ambiguous writes, retrying the same MySQL row, and coalescing contiguous cursor commits.

**Architecture:** Keep the change inside `FeishuBitableTableSink` and `IncrementalTableSource`; do not add a runtime batching abstraction. The Feishu sink pages one configured Insert match field into an in-memory set at open, groups concurrent sends behind per-key waiters, batch-creates only absent keys, and exact-searches only uncertain batch members; MySQL requeues the same logical delivery with incremented attempts and persists only the highest contiguous acknowledged cursor once per event-loop commit wave.

**Tech Stack:** Python 3.9+, asyncio, onestep Source/Delivery/Sink contracts, SQLAlchemy async MySQL/SQLite, Feishu Bitable HTTP API, strict YAML resource plugins, pytest, uv/hatch builds, VitePress docs.

---

## Read first

- Design contract: `docs/superpowers/specs/2026-08-14-feishu-insert-incremental-sync-design.md`
- Runtime ordering: `src/onestep/runtime/executor.py:100-179,530-619`
- Source loop/concurrency: `src/onestep/runtime/runner.py:45-109`
- Plugin lifecycle: `src/onestep/app.py:691-752`
- MySQL incremental source: `plugins/onestep-mysql/src/onestep_mysql/connector.py:638-760`
- Feishu sink and batch matching: `plugins/onestep-feishu-bitable/src/onestep_feishu_bitable/connector.py:763-977,1091-1163,1293-1311`
- MySQL strict resources: `plugins/onestep-mysql/src/onestep_mysql/resources.py`
- Feishu strict resources: `plugins/onestep-feishu-bitable/src/onestep_feishu_bitable/resources.py`

## File map and responsibilities

**Create**

- `plugins/onestep-feishu-bitable/tests/test_feishu_insert_incremental_chain.py` — cross-plugin `DeliveryExecutor` proof and deterministic 100k request-count benchmark; no production I/O.
- `example/mysql_feishu_insert.yaml` — corrected lowercase strict YAML for the approved workload.

**Modify**

- `plugins/onestep-feishu-bitable/src/onestep_feishu_bitable/connector.py` — destination index scan, key normalization, reliable per-key waiters, threshold/timer/close flush, uncertain-write reconciliation, privacy-safe Feishu logs.
- `plugins/onestep-feishu-bitable/src/onestep_feishu_bitable/resources.py` — Feishu option catalog, strict validation, and builder wiring.
- `plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py` — focused index, waiter, recovery, shutdown, privacy, and strict YAML tests using the existing fake/local HTTP style.
- `plugins/onestep-feishu-bitable/tests/test_feishu_bitable_plugin.py` — catalog assertions for new public fields.
- `plugins/onestep-feishu-bitable/README.md` — concise Insert-index example and single-writer/recovery caveats.
- `plugins/onestep-feishu-bitable/pyproject.toml` — backward-compatible minor version bump.
- `plugins/onestep-mysql/src/onestep_mysql/connector.py` — logical-row retry queue, terminal gap block, coalesced cursor commits, MySQL aggregate logs.
- `plugins/onestep-mysql/tests/test_mysql_incremental.py` — retry identity/attempt, gap, coalescing, save failure, close, and privacy tests.
- `plugins/onestep-mysql/README.md` — durable incremental cursor and retry semantics.
- `plugins/onestep-mysql/pyproject.toml` — backward-compatible minor version bump.
- `docs/broker/feishu-bitable.md` — Insert key-index contract, startup bound, reliable batches, ambiguity, single writer.
- `docs/broker/mysql.md` — durable cursor, contiguous/coalesced commits, logical-row retry.
- `docs/yaml-task-definition.md` — cross-plugin strict example and three distinct batch/concurrency knobs.
- `skills/onestep/references/connectors.md` — concise current connector wiring reference for this supported chain.
- `CHANGELOG.md` — compatibility and operational behavior for both plugin releases.
- `uv.lock` — regenerated plugin versions.

No core runtime source file changes. No relation code changes except preserving existing regression behavior. No captain application or deployment file changes.

## Dependency order and invariants

1. Feishu public configuration and index loading land before normal-path routing.
2. Reliable batch waiters land before MySQL cursor/retry changes; otherwise the cursor can still acknowledge a private buffer.
3. Ambiguous recovery lands before the chain test enables durable acknowledgement.
4. MySQL retry lands before cursor coalescing so tests can pin failed-gap behavior.
5. Documentation/release metadata land only after behavior and validation are green.

At every checkpoint, preserve these invariants:

- `Sink.send()` returns only for confirmed-created or confirmed-pre-existing input.
- No exact destination lookup occurs on the normal indexed path.
- No create follows an uncertain write until exact lookup confirms missing.
- A MySQL cursor moves only across a contiguous prefix of successful sink waiters.
- Logs contain counts/durations/outcomes, never credentials, payloads, keys, cursor values, or record IDs.

### Task 1: Add and validate the opt-in Insert index configuration

**Files:**
- Modify: `plugins/onestep-feishu-bitable/src/onestep_feishu_bitable/connector.py:119-177,763-817`
- Modify: `plugins/onestep-feishu-bitable/src/onestep_feishu_bitable/resources.py:20-105,143-220,260-300`
- Test: `plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py`
- Test: `plugins/onestep-feishu-bitable/tests/test_feishu_bitable_plugin.py`

- [ ] **Step 1: Write failing Python and strict YAML contract tests**

Add these complete tests:

```python
def test_feishu_insert_key_index_config_normalizes_python_api() -> None:
    connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")
    sink = connector.table_sink(
        app_token="app-token",
        table_id="tbl",
        mode="insert",
        match_fields=["编号"],
        batch_size=100,
        insert_key_index=True,
        insert_index_page_size=500,
        insert_index_max_pages=200,
        ambiguous_write_max_rounds=3,
    )
    assert sink.insert_key_index is True
    assert sink.insert_index_page_size == 500
    assert sink.insert_index_max_pages == 200
    assert sink.ambiguous_write_max_rounds == 3


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"mode": "upsert"}, "insert_key_index.*mode.*insert"),
        ({"match_fields": ["编号", "来源"]}, "exactly one match field"),
        ({"relations": {"关联": {"table_id": "rel", "key": "编号"}}}, "relations"),
        ({"insert_index_page_size": 0}, "insert_index_page_size.*>= 1"),
        ({"insert_index_max_pages": 0}, "insert_index_max_pages.*>= 1"),
        ({"ambiguous_write_max_rounds": 0}, "ambiguous_write_max_rounds.*>= 1"),
    ],
)
def test_feishu_insert_key_index_rejects_unsupported_config(
    overrides: dict[str, object], message: str
) -> None:
    connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")
    config = {
        "app_token": "app-token",
        "table_id": "tbl",
        "mode": "insert",
        "match_fields": ["编号"],
        "insert_key_index": True,
    }
    config.update(overrides)
    with pytest.raises((TypeError, ValueError), match=message):
        connector.table_sink(**config)


def test_yaml_builds_indexed_insert_sink_in_strict_mode() -> None:
    app = load_app_config(
        {
            "apiVersion": "onestep/v1alpha1",
            "kind": "App",
            "app": {"name": "follow-record-sync"},
            "resources": {
                "feishu": {"type": "feishu_bitable", "app_id": "id", "app_secret": "secret"},
                "sink": {
                    "type": "feishu_bitable_table_sink",
                    "connector": "feishu",
                    "app_token": "token",
                    "table_id": "table",
                    "mode": "insert",
                    "match_fields": ["编号"],
                    "batch_size": 100,
                    "insert_key_index": True,
                    "insert_index_page_size": 500,
                    "insert_index_max_pages": 200,
                    "ambiguous_write_max_rounds": 3,
                },
            },
            "tasks": [],
        },
        strict=True,
    )
    assert app.resources["sink"].insert_key_index is True
```

Extend the plugin catalog test to assert types/defaults for all four new fields.

- [ ] **Step 2: Run the contract tests and confirm the red phase**

Run:

```bash
uv run --all-packages pytest -q \
  plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py \
  plugins/onestep-feishu-bitable/tests/test_feishu_bitable_plugin.py \
  -k "insert_key_index_config or indexed_insert_sink or catalog"
```

Expected: FAIL with `TypeError: table_sink() got an unexpected keyword argument 'insert_key_index'` and strict YAML unknown-field errors.

- [ ] **Step 3: Implement the minimal configuration boundary**

Add constants and arguments exactly as follows:

```python
_DEFAULT_INSERT_INDEX_PAGE_SIZE = 500
_DEFAULT_INSERT_INDEX_MAX_PAGES = 200
_DEFAULT_AMBIGUOUS_WRITE_MAX_ROUNDS = 3

# FeishuBitableConnector.table_sink and FeishuBitableTableSink.__init__
insert_key_index: bool = False,
insert_index_page_size: int = _DEFAULT_INSERT_INDEX_PAGE_SIZE,
insert_index_max_pages: int = _DEFAULT_INSERT_INDEX_MAX_PAGES,
ambiguous_write_max_rounds: int = _DEFAULT_AMBIGUOUS_WRITE_MAX_ROUNDS,
```

Use `_normalize_positive_int(value, field, maximum=None)` for all three integers,
cap page size at `_MAX_PAGE_SIZE`, require an actual boolean for
`insert_key_index`, and reject enabled configurations unless mode is `insert`,
`match_fields` has length one, and relations is empty. Add the same fields to
`allowed_fields`, catalog, validation, and builder wiring in `resources.py`.

- [ ] **Step 4: Run focused tests**

Run the Step 2 command again.

Expected: PASS; the catalog exposes `boolean/integer` fields and existing strict YAML remains valid.

- [ ] **Step 5: Commit the configuration contract**

```bash
git add plugins/onestep-feishu-bitable/src/onestep_feishu_bitable/connector.py \
  plugins/onestep-feishu-bitable/src/onestep_feishu_bitable/resources.py \
  plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py \
  plugins/onestep-feishu-bitable/tests/test_feishu_bitable_plugin.py
git commit -m "feat(feishu): configure indexed insert mode"
```

### Task 2: Page the destination match field into a bounded startup index

**Files:**
- Modify: `plugins/onestep-feishu-bitable/src/onestep_feishu_bitable/connector.py:174-214,763-817`
- Test: `plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py`

- [ ] **Step 1: Write failing startup scan and canonical-key tests**

Add tests named:

```text
test_feishu_insert_index_open_pages_match_field_only
test_feishu_insert_index_open_rejects_truncated_scan
test_feishu_insert_index_open_rejects_duplicate_page_token
test_feishu_insert_index_canonicalizes_source_and_destination_keys
test_feishu_insert_index_counts_missing_and_duplicate_keys_without_logging_values
```

The main paging test uses a fake `search_records()` returning two pages and asserts:

```python
assert calls == [
    {"body": {"field_names": ["编号"]}, "page_size": 2, "page_token": None},
    {"body": {"field_names": ["编号"]}, "page_size": 2, "page_token": "p2"},
]
assert sink._insert_keys == {"A-1", "A-2", "A-3"}
```

The max-pages test configures `insert_index_max_pages=1`, returns `has_more=True`,
and expects a `ConnectorOperationError` containing `insert_index_max_pages=1` but no
key values or app token. The duplicate-token test proves the scan fails rather than
looping. Cover strings, finite numbers, booleans, empty strings, non-finite floats,
and mappings through one `_canonical_insert_key()` helper.

- [ ] **Step 2: Verify the scan tests fail**

```bash
uv run --all-packages pytest -q \
  plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py \
  -k "insert_index_open or canonicalizes_source"
```

Expected: FAIL because `FeishuBitableTableSink.open()` and `_insert_keys` do not exist.

- [ ] **Step 3: Implement the bounded scan**

Add:

```python
async def open(self) -> None:
    if not self.insert_key_index or self._index_loaded:
        return
    await self._load_insert_key_index()

async def _load_insert_key_index(self) -> None:
    page_token: str | None = None
    seen_tokens: set[str] = set()
    loaded: set[str] = set()
    for page_number in range(1, self.insert_index_max_pages + 1):
        data = await self.connector.search_records(
            app_token=self.app_token,
            table_id=self.table_id,
            body={"field_names": [self.match_fields[0]]},
            page_size=self.insert_index_page_size,
            page_token=page_token,
            user_id_type=self.user_id_type,
            operation=ConnectorOperation.OPEN,
            source_name=self.name,
            retry_delay_s=1.0,
        )
        raw_items = data.get("items", [])
        if not isinstance(raw_items, list):
            raise FeishuBitablePayloadError("insert index response items must be a list")
        for raw_item in raw_items:
            if not isinstance(raw_item, Mapping):
                raise FeishuBitablePayloadError("insert index item must be a mapping")
            raw_fields = raw_item.get("fields")
            if not isinstance(raw_fields, Mapping):
                missing_key_records += 1
                continue
            try:
                key = _canonical_insert_key(raw_fields.get(self.match_fields[0]))
            except FeishuBitablePayloadError:
                missing_key_records += 1
                continue
            if key in loaded:
                duplicate_keys += 1
            loaded.add(key)
        has_more = bool(data.get("has_more"))
        next_token = data.get("page_token")
        if not has_more:
            self._insert_keys = loaded
            self._index_loaded = True
            return
        if not isinstance(next_token, str) or not next_token or next_token in seen_tokens:
            raise FeishuBitablePayloadError("insert index pagination did not advance")
        seen_tokens.add(next_token)
        page_token = next_token
    raise FeishuBitablePayloadError(
        f"insert index exceeded insert_index_max_pages={self.insert_index_max_pages}"
    )
```

Store only canonical keys. Do not store records or IDs. Wrap payload/shape problems as
privacy-safe OPEN connector errors. Emit one completion/failure log with pages, keys,
missing-key count, duplicate count, duration, page size, and max pages.

- [ ] **Step 4: Run scan tests and all Feishu source paging regressions**

```bash
uv run --all-packages pytest -q \
  plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py \
  -k "insert_index_open or canonicalizes_source or incremental_source"
```

Expected: PASS; startup does not begin with a truncated set.

- [ ] **Step 5: Commit destination indexing**

```bash
git add plugins/onestep-feishu-bitable/src/onestep_feishu_bitable/connector.py \
  plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py
git commit -m "feat(feishu): preload insert destination keys"
```

### Task 3: Make buffered sends await their own batch outcome

**Files:**
- Modify: `plugins/onestep-feishu-bitable/src/onestep_feishu_bitable/connector.py:763-977,1145-1163`
- Test: `plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py:1500-1810`

- [ ] **Step 1: Write failing per-item completion tests**

Add tests named:

```text
test_feishu_insert_send_waits_for_its_batch_write
test_feishu_insert_100_concurrent_sends_form_one_batch
test_feishu_insert_index_hit_completes_without_search_or_write
test_feishu_insert_concurrent_duplicate_key_creates_once
test_feishu_insert_batch_failure_completes_every_member_with_error
test_feishu_insert_timer_flush_completes_partial_batch_waiters
test_feishu_insert_close_drains_partial_batch_and_every_waiter
test_feishu_insert_close_failure_fails_every_waiter
test_feishu_insert_cancelled_sender_leaves_no_waiter_after_close
```

The central test must block the connector write:

```python
send_tasks = [
    asyncio.create_task(sink.send(Envelope(body={"编号": f"K-{i:03d}"})))
    for i in range(100)
]
await asyncio.wait_for(write_started.wait(), timeout=1.0)
assert not any(task.done() for task in send_tasks)
release_write.set()
await asyncio.gather(*send_tasks)
assert len(created_batches) == 1
assert len(created_batches[0]) == 100
assert sink.inflight_waiter_count == 0
```

For batch failure, assert all gathered results are the same connector failure class,
not hung Futures, and `_pending_by_key` is empty.

- [ ] **Step 2: Run tests and verify premature completion**

```bash
uv run --all-packages pytest -q \
  plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py \
  -k "send_waits_for_its_batch or 100_concurrent or every_member or concurrent_duplicate or index_hit"
```

Expected: FAIL because below-threshold `send()` returns before the write and the
current bare `_buffer` cannot assign outcomes to members.

- [ ] **Step 3: Introduce localized pending-key groups**

Add only sink-private types:

```python
class _InsertState(str, Enum):
    BUFFERED = "buffered"
    WRITING = "writing"
    RECOVERING = "recovering"

@dataclass
class _PendingInsert:
    key: str
    fields: dict[str, Any]
    waiters: list[asyncio.Future[None]]
    buffered_at: float
    state: _InsertState = _InsertState.BUFFERED
```

Replace the indexed Insert path's bare buffer with:

```python
self._pending_by_key: dict[str, _PendingInsert] = {}
self._pending_order: deque[str] = deque()
self._flush_lock: asyncio.Lock | None = None
self._inflight_waiter_count = 0
```

Keep existing non-indexed and relation paths behaviorally compatible. Factor
`_complete_pending(pending, exc=None)` so every waiter is completed once and removed
from accounting. `send()` joins duplicate keys, elects a threshold flusher under the
lock, releases the lock, flushes if elected, and awaits its own Future.

- [ ] **Step 4: Rewrite threshold/timer/close flush around a sealed batch**

Implement:

```python
async def _flush_indexed_insert(self, *, reason: str) -> None:
    async with self._ensure_flush_lock():
        batch = self._seal_indexed_batch()
        if not batch:
            return
        await self._write_indexed_batch(batch, reason=reason)
```

A batch is at most `_batch_size` distinct keys. On confirmed success, validate response
cardinality, add keys to `_insert_keys`, then complete waiters. On definite failure,
fail every member and remove the batch. Timer identity cleanup must retain the 0.3.5
self-cancellation fix. Close rejects new sends, cancels only a separate timer, flushes
all remaining batches, and asserts no waiter remains.

- [ ] **Step 5: Run focused and existing batching tests**

```bash
uv run --all-packages pytest -q \
  plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py \
  -k "feishu_insert or batch_create or batch_upsert or automatically_flushes"
```

Expected: PASS. In particular, 100 concurrent sends produce one create and remain
pending until its result.

- [ ] **Step 6: Commit reliable batching**

```bash
git add plugins/onestep-feishu-bitable/src/onestep_feishu_bitable/connector.py \
  plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py
git commit -m "fix(feishu): await buffered insert outcomes"
```

### Task 4: Reconcile ambiguous writes before retrying creates

**Files:**
- Modify: `plugins/onestep-feishu-bitable/src/onestep_feishu_bitable/connector.py:923-977,1091-1143,1293-1311`
- Test: `plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py`

- [ ] **Step 1: Add failing uncertainty and partial-recovery tests**

Add tests named:

```text
test_feishu_insert_ambiguous_batch_reconciles_found_keys_without_repeat
test_feishu_insert_ambiguous_batch_creates_only_confirmed_missing_keys
test_feishu_insert_recovery_rejects_duplicate_match
test_feishu_insert_recovery_search_error_is_not_missing
test_feishu_insert_recovery_exhaustion_fails_only_unresolved_waiters
test_feishu_insert_retry_of_uncertain_key_searches_before_create
test_feishu_insert_short_success_response_enters_recovery
test_feishu_insert_http_disconnect_after_accept_does_not_duplicate_create
```

For a four-key batch where search finds K1/K3, assert the next create body contains
only K2/K4. For the real HTTP test, make the first `/batch_create` handler record keys
then close without a valid response; subsequent `/search` returns those accepted keys.
Assert there is exactly one create request.

- [ ] **Step 2: Verify the recovery tests fail**

```bash
uv run --all-packages pytest -q \
  plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py \
  -k "ambiguous_batch or recovery_ or uncertain_key or disconnect_after_accept or short_success"
```

Expected: FAIL because current flush propagates the write error or the executor repeats
`send()` without an uncertainty barrier.

- [ ] **Step 3: Implement exact-search recovery for affected keys only**

Add:

```python
self._uncertain_keys: set[str] = set()

async def _reconcile_ambiguous_insert_batch(
    self,
    batch: list[_PendingInsert],
    *,
    reason: str,
) -> None:
    unresolved = {item.key: item for item in batch}
    for round_number in range(1, self.ambiguous_write_max_rounds + 1):
        found, missing = await self._search_affected_insert_keys(unresolved.values())
        self._confirm_found(found)
        unresolved = {key: unresolved[key] for key in missing}
        if not unresolved:
            return
        try:
            await self._batch_create_pending(list(unresolved.values()))
        except ConnectorOperationError as exc:
            if _is_ambiguous_write_error(exc):
                continue
            self._fail_pending(unresolved.values(), exc)
            return
        self._confirm_created(unresolved.values())
        return
    self._fail_pending(
        unresolved.values(),
        ConnectorOperationError(
            backend="feishu_bitable",
            operation=ConnectorOperation.SEND,
            kind=ConnectorErrorKind.UNCERTAIN,
            source_name=self.name,
            message=(
                "feishu_bitable insert recovery exhausted "
                f"after {self.ambiguous_write_max_rounds} rounds"
            ),
        ),
    )
```

Use exact `_find_matches()` with `page_size=2` and semaphore 20. Treat
`UNCERTAIN`, `DISCONNECTED`, `TRANSIENT`, and `THROTTLED` write outcomes as ambiguous;
never turn search errors into misses. Before buffering a key present in
`_uncertain_keys`, run the same exact reconciliation gate. Remove uncertainty only
when the key is found or create is confirmed.

- [ ] **Step 4: Pass the focused recovery matrix**

Run the Step 2 command again.

Expected: PASS; no test issues a second create for a key that the recovery search found.

- [ ] **Step 5: Run all Feishu tests**

```bash
uv run --all-packages pytest -q plugins/onestep-feishu-bitable/tests
```

Expected: all tests pass, including relation and timer regressions.

- [ ] **Step 6: Commit ambiguity handling**

```bash
git add plugins/onestep-feishu-bitable/src/onestep_feishu_bitable/connector.py \
  plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py
git commit -m "fix(feishu): reconcile uncertain insert batches"
```

### Task 5: Retry the same MySQL logical row with bounded attempts

**Files:**
- Modify: `plugins/onestep-mysql/src/onestep_mysql/connector.py:638-760`
- Test: `plugins/onestep-mysql/tests/test_mysql_incremental.py`

- [ ] **Step 1: Write failing retry identity and gap tests**

Add tests named:

```text
test_mysql_incremental_retry_requeues_same_logical_row_with_incremented_attempt
test_mysql_incremental_retry_pauses_new_sql_fetches_until_gap_resolves
test_mysql_incremental_retry_does_not_duplicate_pending_cursor_token
test_mysql_incremental_max_attempts_runs_attempts_zero_one_two
test_mysql_incremental_exhaustion_blocks_source_before_failed_cursor
test_mysql_incremental_restart_replays_uncommitted_failed_row
```

Use a real SQLite incremental source with rows 1 and 2 and `DeliveryExecutor` plus a
sink that fails row 1. The MaxAttempts test must assert:

```python
assert seen == [(1, 0), (1, 1), (1, 2)]
assert await state.load("sync") is None
assert len(source._pending) == 1
```

The delayed-retry test starts `delivery.retry(delay_s=0.05)`, calls `fetch(10)` during
the delay, and asserts no SQL suffix row is returned. After the delay, the same row is
returned first with attempts incremented.

- [ ] **Step 2: Reproduce the current no-op retry defect**

```bash
uv run --all-packages pytest -q \
  plugins/onestep-mysql/tests/test_mysql_incremental.py \
  -k "retry_requeues or retry_pauses or max_attempts_runs or exhaustion_blocks or restart_replays"
```

Expected: FAIL because `IncrementalDelivery.retry()` only sleeps, does not increment
attempts, and `_fetched_cursor` remains beyond the failed row.

- [ ] **Step 3: Add a source-local retry state**

Add:

```python
@dataclass
class _IncrementalPendingRow:
    token: tuple[Any, ...]
    body: dict[str, Any]
    meta: dict[str, Any]
    retry_envelope: Envelope | None = None
    retry_ready_at: float | None = None
    retry_inflight: bool = False
    terminal_error: Exception | None = None
```

Replace `_pending: deque[tuple[Any, ...]]` with a deque of these rows and a token map.
`retry_token()` creates an envelope with copied body/meta and `attempts + 1`, marks the
head retrying before sleeping, then makes it ready. `fetch()` returns ready retries
first and returns `[]` without SQL while the head is delayed/in flight. It never
appends the same token twice.

`IncrementalDelivery.fail()` delegates to `fail_token()`, stores a permanent blocked
error, and subsequent fetch raises a redacted permanent FETCH error. `ack_token()`
removes the row only through the contiguous commit path. Do not put this behavior in
the handler or `DeliveryExecutor`.

- [ ] **Step 4: Run retry tests and existing cursor tests**

```bash
uv run --all-packages pytest -q \
  plugins/onestep-mysql/tests/test_mysql_incremental.py \
  -k "retry or exhaustion or cursor or pending_gap or tie_breaker"
```

Expected: PASS; attempts are 0/1/2 and no durable cursor crosses the failed row.

- [ ] **Step 5: Commit logical-row retry**

```bash
git add plugins/onestep-mysql/src/onestep_mysql/connector.py \
  plugins/onestep-mysql/tests/test_mysql_incremental.py
git commit -m "fix(mysql): retry incremental logical rows"
```

### Task 6: Coalesce contiguous cursor saves without weakening crash safety

**Files:**
- Modify: `plugins/onestep-mysql/src/onestep_mysql/connector.py:655-760`
- Test: `plugins/onestep-mysql/tests/test_mysql_incremental.py`

- [ ] **Step 1: Add failing commit-wave tests**

Add tests named:

```text
test_mysql_incremental_coalesces_100_contiguous_acks_into_one_save
test_mysql_incremental_out_of_order_gap_does_not_schedule_cursor_save
test_mysql_incremental_cursor_save_failure_keeps_prefix_pending
test_mysql_incremental_ack_waits_for_its_commit_wave
test_mysql_incremental_ack_arriving_during_save_gets_second_commit
test_mysql_incremental_close_drains_active_cursor_commit
```

Use a recording `CursorStore`:

```python
class RecordingCursorStore(InMemoryCursorStore):
    def __init__(self) -> None:
        super().__init__()
        self.saves: list[list[object]] = []
        self.release_save = asyncio.Event()

    async def save(self, key: str, value: object) -> None:
        self.saves.append(list(value))
        await super().save(key, value)
```

Fetch 100 rows, start 100 `ack()` tasks without awaiting them individually, then
`await asyncio.gather(*acks)`. Assert `state.saves == [[100, 100]]` for the chosen
cursor fixture. In the save-failure test, assert `_pending` and `_acked` still contain
the prefix and a subsequent ack/flush can save it.

- [ ] **Step 2: Confirm current write amplification**

```bash
uv run --all-packages pytest -q \
  plugins/onestep-mysql/tests/test_mysql_incremental.py \
  -k "coalesces_100 or commit_wave or save_failure or close_drains_active"
```

Expected: FAIL because current `ack_token()` calls `state.save()` synchronously for
each newly contiguous row.

- [ ] **Step 3: Implement the event-loop commit coordinator**

Add fields:

```python
self._commit_task: asyncio.Task[None] | None = None
self._commit_error: BaseException | None = None
```

Implement `_ensure_commit_task()` and `_flush_commits()` with this exact order:

1. `await asyncio.sleep(0)` to gather the current acknowledgement wave;
2. under `_runtime_commit_lock()`, find the highest contiguous acknowledged pending row;
3. release the lock and `await state.save(state_key, list(highest.token))`;
4. reacquire the lock, remove only the saved prefix, update `_committed_cursor`, and
   clear their acknowledged markers;
5. if another contiguous prefix appeared during save, loop and save it; otherwise exit;
6. clear `_commit_task` only if task identity still matches.

Every in-order ack captures and awaits the active task. On save failure, do not remove
pending rows or advance `_committed_cursor`; propagate to all ack callers waiting on
that wave. `close()` awaits the task and propagates `_commit_error`.

- [ ] **Step 4: Run coalescing tests**

Run the Step 2 command again.

Expected: PASS; one normal commit for 100 simultaneously released sink waiters, and a
second save only when an ack arrives after the first snapshot.

- [ ] **Step 5: Run the full MySQL plugin suite**

```bash
uv run --all-packages pytest -q -m "not integration" plugins/onestep-mysql/tests
```

Expected: all non-integration MySQL tests pass.

- [ ] **Step 6: Commit coalesced commits**

```bash
git add plugins/onestep-mysql/src/onestep_mysql/connector.py \
  plugins/onestep-mysql/tests/test_mysql_incremental.py
git commit -m "perf(mysql): coalesce incremental cursor commits"
```

### Task 7: Add privacy-safe operational telemetry

**Files:**
- Modify: `plugins/onestep-feishu-bitable/src/onestep_feishu_bitable/connector.py`
- Modify: `plugins/onestep-mysql/src/onestep_mysql/connector.py`
- Test: `plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py`
- Test: `plugins/onestep-mysql/tests/test_mysql_incremental.py`

- [ ] **Step 1: Write failing structured-log tests**

Add:

```text
test_feishu_insert_logs_index_batch_waiter_lookup_and_recovery_aggregates
test_feishu_insert_logs_never_include_secrets_payload_keys_cursors_or_record_ids
test_mysql_incremental_logs_fetch_retry_commit_and_cursor_lag_aggregates
test_mysql_incremental_logs_never_include_dsn_payload_key_or_cursor_values
```

Use `caplog` and sentinel secrets/values. Assert aggregate extras exist:

```python
assert {
    "scan_pages", "scan_keys", "duration_s", "page_size", "max_pages"
} <= index_record.__dict__.keys()
assert {
    "batch_size", "oldest_batch_age_s", "inflight_waiter_count", "flush_reason"
} <= batch_record.__dict__.keys()
```

Serialize `record.getMessage()` plus allowed extras and assert none of
`mysql://user:password@host/db`, `app-token-secret`, `union-key-secret`,
`record-id-secret`, payload field values, or cursor tuple values appears.

- [ ] **Step 2: Run telemetry tests and verify missing fields**

```bash
uv run --all-packages pytest -q \
  plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py \
  plugins/onestep-mysql/tests/test_mysql_incremental.py \
  -k "logs_index_batch or logs_fetch_retry or logs_never_include"
```

Expected: FAIL because these connector-stage aggregate records do not yet exist.

- [ ] **Step 3: Add module loggers and bounded aggregate fields**

Define one module logger in each connector. Emit exactly the events from the design:

```python
logger.info(
    "feishu insert batch write",
    extra={
        "event": "feishu_insert_batch_write",
        "batch_size": len(batch),
        "duration_s": duration,
        "outcome": outcome,
        "flush_reason": reason,
        "recovery_round": recovery_round,
        "inflight_waiter_count": self.inflight_waiter_count,
    },
)
```

and analogous `mysql_incremental_fetch`, `mysql_incremental_retry`, and
`mysql_incremental_cursor_commit` records. `fetched_cursor_lag_rows` is a count of
pending rows, never a cursor value. Include source fetch count/size/duration, index
pages/keys/duration, normal lookup avoided count, recovery lookup count, write
outcome/duration, batch age/size, waiter count, committed/fetched lag count, and retry
counts across the records.

- [ ] **Step 4: Run telemetry and connector suites**

```bash
uv run --all-packages pytest -q \
  plugins/onestep-feishu-bitable/tests \
  plugins/onestep-mysql/tests -m "not integration"
```

Expected: both plugin suites pass; privacy sentinel assertions pass.

- [ ] **Step 5: Commit observability**

```bash
git add plugins/onestep-feishu-bitable/src/onestep_feishu_bitable/connector.py \
  plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py \
  plugins/onestep-mysql/src/onestep_mysql/connector.py \
  plugins/onestep-mysql/tests/test_mysql_incremental.py
git commit -m "feat(connectors): expose insert sync telemetry"
```

### Task 8: Prove the chain and the 100k request-count model

**Files:**
- Create: `plugins/onestep-feishu-bitable/tests/test_feishu_insert_incremental_chain.py`

- [ ] **Step 1: Write the failing real-executor cursor-boundary test**

Create a SQLite `view_follow_record_sync` fixture with 100 rows, a recording cursor
store, an indexed Feishu sink whose batch write blocks, and a `OneStepApp` task with
`concurrency=100`. Drive fetched deliveries through `DeliveryExecutor.execute()`:

```python
executions = [asyncio.create_task(executor.execute(delivery)) for delivery in batch]
await asyncio.wait_for(write_started.wait(), timeout=1.0)
assert await state.load("follow-record-sync-v1") is None
assert not any(task.done() for task in executions)
release_write.set()
outcomes = await asyncio.gather(*executions)
assert all(outcome.completion == "succeeded" for outcome in outcomes)
assert await state.load("follow-record-sync-v1") == [100, "K-000100"]
assert state.save_count == 1
```

- [ ] **Step 2: Write the deterministic 100k synthetic request-count benchmark**

Add `test_indexed_insert_100k_request_count_benchmark`. It must use fakes only and:

```python
existing = {f"K-{i:06d}" for i in range(50_000)}
incoming = [f"K-{i:06d}" for i in range(100_000)]

async def run_benchmark(sink: FeishuBitableTableSink) -> None:
    await sink.open()
    for offset in range(0, len(incoming), 100):
        await asyncio.gather(*(
            sink.send(Envelope(body={"编号": key}))
            for key in incoming[offset:offset + 100]
        ))
    await sink.close()

assert fake.search_page_requests == 100
assert fake.normal_exact_search_requests == 0
assert fake.batch_create_requests == 500
assert fake.data_requests == 600
assert all(size == 100 for size in fake.create_batch_sizes)
assert sink.inflight_waiter_count == 0
assert not hasattr(sink, "record_ids")
```

Feed input in bounded waves of 100 concurrent sends so the test itself does not create
100,000 tasks simultaneously.

- [ ] **Step 3: Run chain tests and verify the first failure**

```bash
uv run --all-packages pytest -q \
  plugins/onestep-feishu-bitable/tests/test_feishu_insert_incremental_chain.py
```

Expected before Tasks 3–6: FAIL because sends complete before the blocked write, retry
does not preserve identity, and cursor saves are not coalesced. After those tasks:
PASS with the exact request counts above.

- [ ] **Step 4: Run three repetitions to catch waiter/timer races**

```bash
for run in 1 2 3; do
  uv run --all-packages pytest -q \
    plugins/onestep-feishu-bitable/tests/test_feishu_insert_incremental_chain.py || exit 1
done
```

Expected: all three runs pass with identical request counts and zero leaked waiters.

- [ ] **Step 5: Commit the chain proof**

```bash
git add plugins/onestep-feishu-bitable/tests/test_feishu_insert_incremental_chain.py
git commit -m "test(sync): prove indexed insert cursor boundary"
```

### Task 9: Document the supported workload and strict YAML

**Files:**
- Create: `example/mysql_feishu_insert.yaml`
- Modify: `docs/broker/feishu-bitable.md`
- Modify: `docs/broker/mysql.md`
- Modify: `docs/yaml-task-definition.md`
- Modify: `plugins/onestep-feishu-bitable/README.md`
- Modify: `plugins/onestep-mysql/README.md`
- Modify: `skills/onestep/references/connectors.md`

- [ ] **Step 1: Add the exact lowercase workload example**

Create `example/mysql_feishu_insert.yaml` using the full YAML from the design. Keep
these exact workload values:

```yaml
follow_records:
  type: mysql_incremental
  connector: mysql_source
  table: view_follow_record_sync
  key: unionKey
  cursor: [dataCreateTime, unionKey]
  batch_size: 1000
  state: mysql_cursors
  state_key: follow-record-sync-v1

follow_record_table:
  type: feishu_bitable_table_sink
  connector: feishu
  app_token: "${FEISHU_APP_TOKEN}"
  table_id: "${FEISHU_TABLE_ID}"
  mode: insert
  match_fields: [编号]
  batch_size: 100
  insert_key_index: true
  insert_index_page_size: 500
  insert_index_max_pages: 200
  ambiguous_write_max_rounds: 3
```

The task uses `concurrency: 100`, `max_attempts: 3`, and optional
`config.batch_size: 100` with a comment that it is handler-only.

- [ ] **Step 2: Add a tiny importable example handler for strict validation**

Because `onestep check --strict` resolves handler references, use YAML passthrough by
omitting `handler` and retaining `emit`; the source view must already expose destination
field names including `编号`. Document that captain applications with a mapping handler
should replace passthrough with their own `handler.ref` without moving retry or batching
logic into it.

- [ ] **Step 3: Update connector and YAML documentation**

Document all of these without adding broader sync promises:

- startup scan bounds and `D=50,000` / page-size-500 model;
- single active writer requirement and manual-write caveat;
- per-item completion and shutdown guarantee;
- exact-search recovery only after ambiguous writes;
- durable `mysql_cursor_store`, explicit `state_key`, contiguous prefix, and coalesced
  commit semantics;
- retry of the same logical row and restart attempts reset;
- migration from old in-memory cursor by full replay plus destination skip;
- no relation IDs, durable ledger, update/delete, CDC, or multi-writer guarantee;
- task `config.batch_size` is handler-only; source resource batch size, sink resource
  batch size, and task concurrency are independent.

- [ ] **Step 4: Validate strict YAML with dummy environment values**

```bash
MYSQL_DSN='sqlite://' \
FEISHU_APP_ID='app-id' \
FEISHU_APP_SECRET='secret' \
FEISHU_APP_TOKEN='app-token' \
FEISHU_TABLE_ID='table-id' \
uv run --all-packages onestep check --strict example/mysql_feishu_insert.yaml
```

Expected: exit 0 and output identifies app `follow-record-sync`; there are no unknown
fields or uppercase enum-value failures.

- [ ] **Step 5: Build the docs site**

```bash
pnpm --dir docs install --frozen-lockfile
pnpm --dir docs build
```

Expected: VitePress build exits 0. Existing non-blocking dead-link warnings may remain;
no new warning points at the modified pages.

- [ ] **Step 6: Commit documentation**

```bash
git add example/mysql_feishu_insert.yaml \
  docs/broker/feishu-bitable.md docs/broker/mysql.md docs/yaml-task-definition.md \
  plugins/onestep-feishu-bitable/README.md plugins/onestep-mysql/README.md \
  skills/onestep/references/connectors.md
git commit -m "docs(sync): describe reliable mysql to feishu insert"
```

### Task 10: Release metadata, compatibility gates, and packages

**Files:**
- Modify: `plugins/onestep-feishu-bitable/pyproject.toml`
- Modify: `plugins/onestep-mysql/pyproject.toml`
- Modify: `CHANGELOG.md`
- Modify: `uv.lock`

- [ ] **Step 1: Record compatibility impact in the changelog**

Add unreleased plugin entries stating:

```markdown
## onestep-feishu-bitable 0.4.0
- Adds opt-in, bounded destination-key preload for single-field Insert sinks.
- Makes buffered sends complete only after their item is confirmed created or pre-existing.
- Reconciles ambiguous batch writes by exact-searching only affected keys before creating confirmed misses.
- Requires one active writer per indexed destination table and retains no record IDs or durable ledger.

## onestep-mysql 0.5.0
- Retries the same incremental logical row with incremented delivery attempts.
- Coalesces contiguous cursor acknowledgements into event-loop commit waves without crossing failed gaps.
- Keeps the existing persisted cursor representation compatible with prior releases.
```

Use these minor versions unless `main` has advanced to an equal/newer release; in that
case choose the next minor version while preserving the same release semantics.

- [ ] **Step 2: Bump versions and regenerate the lock**

```bash
uv lock
```

Expected: `uv.lock` shows the chosen local versions for `onestep-feishu-bitable` and
`onestep-mysql`; unrelated dependency versions do not churn.

- [ ] **Step 3: Run focused plugin suites and strict YAML again**

```bash
uv run --all-packages pytest -q plugins/onestep-feishu-bitable/tests
uv run --all-packages pytest -q -m "not integration" plugins/onestep-mysql/tests
MYSQL_DSN='sqlite://' FEISHU_APP_ID='app-id' FEISHU_APP_SECRET='secret' \
FEISHU_APP_TOKEN='app-token' FEISHU_TABLE_ID='table-id' \
uv run --all-packages onestep check --strict example/mysql_feishu_insert.yaml
```

Expected: all tests pass and strict validation exits 0.

- [ ] **Step 4: Build and inspect both distributions**

```bash
rm -rf dist/feishu-plugin dist/mysql-plugin
uv build --package onestep-feishu-bitable --out-dir dist/feishu-plugin --sdist --wheel
uv build --package onestep-mysql --out-dir dist/mysql-plugin --sdist --wheel
uvx twine check dist/feishu-plugin/* dist/mysql-plugin/*
```

Expected: four artifacts build; twine reports every wheel/sdist `PASSED`.

- [ ] **Step 5: Commit releases**

```bash
git add plugins/onestep-feishu-bitable/pyproject.toml \
  plugins/onestep-mysql/pyproject.toml CHANGELOG.md uv.lock
git commit -m "chore(plugins): release reliable insert sync"
```

### Task 11: Full regression, self-review, and no-mistakes handoff

**Files:**
- Review: every file listed in the file map

- [ ] **Step 1: Run core and all plugin reliability suites**

```bash
uv run pytest -q -m "not integration"
ONESTEP_PYTHON_BIN="$(pwd)/.venv/bin/python" ./scripts/run-reliability-checks.sh
```

Expected: core non-integration tests and every runnable plugin suite pass; Kafka may be
reported skipped only when its documented Python/dependency condition applies.

- [ ] **Step 2: Run syntax, whitespace, and package checks**

```bash
uv run python -m compileall -q src \
  plugins/onestep-feishu-bitable/src plugins/onestep-mysql/src
git diff --check HEAD~7..HEAD
git status --short
```

Expected: compileall and diff check exit 0. Status contains only intentional files from
the file map; no credentials, generated caches, production files, or unrelated edits.

- [ ] **Step 3: Re-run the 100k benchmark and YAML/docs gates**

```bash
uv run --all-packages pytest -q \
  plugins/onestep-feishu-bitable/tests/test_feishu_insert_incremental_chain.py \
  -k "100k_request_count_benchmark or cursor_boundary"
MYSQL_DSN='sqlite://' FEISHU_APP_ID='app-id' FEISHU_APP_SECRET='secret' \
FEISHU_APP_TOKEN='app-token' FEISHU_TABLE_ID='table-id' \
uv run --all-packages onestep check --strict example/mysql_feishu_insert.yaml
pnpm --dir docs build
```

Expected: request counts are exactly 100 scans + 500 creates + 0 normal exact searches;
strict YAML and docs build pass.

- [ ] **Step 4: Perform the mandatory fresh-eyes self-review**

Check the final diff against every design section and record the result in the
implementation handoff:

```text
[ ] Scope is only Feishu indexed Insert batching and MySQL incremental durability/retry/coalescing.
[ ] No generic runtime batch-delivery API or core ordering change exists.
[ ] Every send waiter is completed once on success, pre-existing, failure, cancellation, or close.
[ ] Every ambiguous create path exact-searches affected keys before another create.
[ ] Cursor save occurs only after contiguous successful/pre-existing sink completion.
[ ] Retry attempts refer to the same logical row and stop at MaxAttempts.
[ ] Startup/round/page/concurrency/waiter bounds are enforced and tested.
[ ] No DSN, token, payload, key value, record ID, or cursor value enters logs/descriptors/errors.
[ ] Strict YAML uses lowercase enum values and explains all three batch/concurrency controls.
[ ] Placeholder scan reports no incomplete or defer-until-later instructions.
[ ] Type/function/property names are consistent across code, tests, docs, and catalog.
[ ] Existing relation behavior and non-indexed Feishu behavior remain green.
```

- [ ] **Step 5: Make one final checkpoint commit if validation caused changes**

```bash
git add -A
git diff --cached --quiet || git commit -m "test(sync): complete reliability validation"
```

Expected: either no staged changes or one small validation-only commit; never squash
away the earlier TDD checkpoints during implementation.

- [ ] **Step 6: Hand off to no-mistakes; do not push directly**

First read the complete current `/Users/miclon/.agents/skills/no-mistakes/SKILL.md`,
then run:

```bash
no-mistakes axi run --intent "Implement the approved single-writer MySQL incremental to Feishu Bitable Insert design: bounded destination key preload, per-item reliable batching, affected-key ambiguous recovery, same-row MaxAttempts retry, contiguous coalesced cursor commits, privacy-safe telemetry, strict YAML/docs, and plugin releases; do not add a generic runtime batching API or deploy production." --yes
```

Expected: drive every returned gate according to the skill until the tool reports
`outcome: checks-passed` or `outcome: passed`. Never restart/update the shared daemon.
On any daemon error, stop and report the exact blocker to the coordinator. The
no-mistakes executor—not the worker—owns any push, PR, and CI phases.

## Execution handoff

Plan complete and saved to
`docs/superpowers/plans/2026-08-14-feishu-insert-incremental-sync.md`.

Execution must use one of these modes:

1. **Subagent-Driven (recommended):** use `superpowers:subagent-driven-development`,
   dispatch a fresh worker per task, and perform specification then code-quality review
   at each checkpoint.
2. **Inline Execution:** use `superpowers:executing-plans`, execute tasks in order, and
   stop at each commit/validation checkpoint.

The executor must not mix modes mid-task, skip red-phase commands, push directly, open
a PR manually, or access production.
