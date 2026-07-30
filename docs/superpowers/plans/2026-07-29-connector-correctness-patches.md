# Connector Correctness Patches Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the currently confirmed Elasticsearch ambiguous-retry and MongoDB cursor/configuration correctness gaps before expanding the plugin surface.

**Architecture:** Keep changes inside the affected connector plugins. Elasticsearch request-level retries become conditional on replay safety, while item-level acknowledged rejections retain their existing retry behavior. MongoDB validates direct Python numeric options during construction, rejects polling projections that omit or rewrite effective cursor fields, and makes cursor persistence transactional from the tracker's point of view.

**Tech Stack:** Python 3.9+, asyncio, pytest, pytest-asyncio, httpx MockTransport, PyMongo BSON helpers, existing onestep connector contracts.

---

## Confirmed Main-Branch Baseline

The following minimal reproductions were run against `b31ed51` before writing
this plan:

```text
Elasticsearch auto-ID + request-level 504 + max_retries=2
=> {'calls': 3, 'kind': 'transient'}

MongoDB tracker: ACK token 'two', then invalidate/retry token 'one'
=> {'saved': ['two'], 'can_fetch': True}

MongoDBPollingSource(..., batch_size=0)
=> {'accepted_batch_size': 0}
```

These are implementation baselines, not hypothetical risks. The completion gate
at the end of this plan states the required replacement behavior.

## File Structure

- Modify `plugins/onestep-elasticsearch/src/onestep_elasticsearch/connector.py`: represent uncertain bulk outcomes and prevent unsafe internal request replay.
- Modify `plugins/onestep-elasticsearch/tests/test_elasticsearch_connector.py`: lock request-level retry behavior for auto-generated and stable IDs.
- Modify `plugins/onestep-elasticsearch/README.md`: document the request-level ambiguity rule.
- Modify `plugins/onestep-mongodb/src/onestep_mongodb/connector.py`: centralize direct Python option validation and make cursor persistence failure-safe.
- Modify `plugins/onestep-mongodb/tests/test_mongodb_polling.py`: cover invalid options, projection/cursor compatibility, and polling state-save failure.
- Modify `plugins/onestep-mongodb/tests/test_mongodb_change_stream.py`: cover change-stream state-save failure.
- Modify `plugins/onestep-mongodb/tests/test_mongodb_sink.py`: cover strict direct API sink option validation.
- Modify `plugins/onestep-mongodb/README.md`: document projection requirements and durable state failure behavior.
- Modify `CHANGELOG.md`: record unreleased plugin fixes without changing core version metadata.

## Task 1: Stop Unsafe Elasticsearch Request Replay

**Files:**
- Modify: `plugins/onestep-elasticsearch/src/onestep_elasticsearch/connector.py:49`
- Modify: `plugins/onestep-elasticsearch/src/onestep_elasticsearch/connector.py:397`
- Test: `plugins/onestep-elasticsearch/tests/test_elasticsearch_connector.py`

- [x] **Step 1: Write failing request-level ambiguity tests**

Add these tests next to the existing retry tests:

```python
@pytest.mark.asyncio
async def test_request_level_504_without_stable_ids_is_not_replayed() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(504, json={"error": {"reason": "gateway timeout"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = ElasticsearchConnector("http://search:9200", client=client).bulk_sink(
        index="events", max_retries=2
    )

    with pytest.raises(ConnectorOperationError) as captured:
        await sink.send(Envelope(body={"value": 1}))

    assert calls == 1
    assert captured.value.kind is ConnectorErrorKind.UNCERTAIN
    await client.aclose()


@pytest.mark.asyncio
async def test_request_level_504_with_stable_ids_retries() -> None:
    responses = [
        httpx.Response(504, json={"error": {"reason": "gateway timeout"}}),
        httpx.Response(
            200,
            json={"errors": False, "items": [{"index": {"status": 201}}]},
        ),
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = ElasticsearchConnector("http://search:9200", client=client).bulk_sink(
        index="events", id_field="id", max_retries=2
    )

    await sink.send(Envelope(body={"id": "evt-1", "value": 1}))

    assert responses == []
    await client.aclose()


@pytest.mark.asyncio
async def test_request_level_429_without_stable_ids_remains_retryable() -> None:
    responses = [
        httpx.Response(429, json={"error": {"reason": "rejected"}}),
        httpx.Response(
            200,
            json={"errors": False, "items": [{"index": {"status": 201}}]},
        ),
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = ElasticsearchConnector("http://search:9200", client=client).bulk_sink(
        index="events", max_retries=2
    )

    await sink.send(Envelope(body={"value": 1}))

    assert responses == []
    await client.aclose()
```

- [x] **Step 2: Run the focused tests and verify the first test fails**

Run:

```bash
uv run --extra test --extra elasticsearch pytest -q \
  plugins/onestep-elasticsearch/tests/test_elasticsearch_connector.py \
  -k 'request_level_504 or request_level_429'
```

Expected: the auto-ID 504 case fails because the current loop issues three
requests and reports `TRANSIENT` rather than `UNCERTAIN`; the stable-ID and 429
cases pass.

- [x] **Step 3: Carry replay safety into the chunk retry loop**

Extend the bulk error with an explicit uncertainty flag:

```python
class ElasticsearchBulkError(Exception):
    def __init__(
        self,
        items: list[ElasticsearchBulkItemError],
        *,
        partial_success: bool = False,
        outcome_uncertain: bool = False,
    ) -> None:
        self.items = tuple(items)
        self.partial_success = partial_success
        self.outcome_uncertain = outcome_uncertain
        summary = ", ".join(
            f"item={item.action_index} status={item.status} reason={item.reason[:160]}"
            for item in self.items[:10]
        )
        super().__init__(f"Elasticsearch bulk request failed: {summary}")
```

Change the `_send_chunk()` signature to:

```python
async def _send_chunk(self, body: bytes, *, replay_safe: bool) -> None:
```

Then replace its current non-2xx status branch with:

```python
if status < 200 or status >= 300:
    failure = ElasticsearchBulkItemError(
        pending_indexes[0],
        self.operation,
        None,
        status,
        None,
        self._error_reason(payload.get("error", "request failed")),
    )
    request_rejected = status == 429
    request_ambiguous = status in {502, 503, 504}
    can_retry_request = request_rejected or (request_ambiguous and replay_safe)
    if can_retry_request and attempt < self.max_retries:
        await asyncio.sleep((0.05 * (2**attempt)) + random.uniform(0.0, 0.025))
        continue
    raise ElasticsearchBulkError(
        [failure],
        partial_success=partial_success,
        outcome_uncertain=request_ambiguous and not replay_safe,
    )
```

Pass the flag from `send()`:

```python
for chunk in chunks:
    await self._send_chunk(chunk, replay_safe=replay_safe)
    committed_chunks += 1
```

Classify an explicitly uncertain error before the existing partial-commit rule:

```python
kind = (
    ConnectorErrorKind.UNCERTAIN
    if exc.outcome_uncertain
    or ((committed_chunks or exc.partial_success) and not replay_safe)
    else base_kind
)
```

Keep item-level 429/502/503/504 retry selection unchanged because a valid bulk
response explicitly identifies those items as failed.

- [x] **Step 4: Run the Elasticsearch plugin suite**

Run:

```bash
uv run --extra test --extra elasticsearch pytest -q -m 'not integration' \
  plugins/onestep-elasticsearch/tests
```

Expected: PASS.

- [x] **Step 5: Commit the Elasticsearch fix**

```bash
git add \
  plugins/onestep-elasticsearch/src/onestep_elasticsearch/connector.py \
  plugins/onestep-elasticsearch/tests/test_elasticsearch_connector.py
git commit -m "fix(elasticsearch): avoid ambiguous auto-id retries"
```

## Task 2: Validate MongoDB Python Numeric Options and Cursor Projections

**Files:**
- Modify: `plugins/onestep-mongodb/src/onestep_mongodb/connector.py:147`
- Modify: `plugins/onestep-mongodb/src/onestep_mongodb/connector.py:279`
- Modify: `plugins/onestep-mongodb/src/onestep_mongodb/connector.py:454`
- Test: `plugins/onestep-mongodb/tests/test_mongodb_polling.py`
- Test: `plugins/onestep-mongodb/tests/test_mongodb_change_stream.py`
- Test: `plugins/onestep-mongodb/tests/test_mongodb_sink.py`

- [x] **Step 1: Write failing direct API validation tests**

Add:

```python
@pytest.mark.parametrize("batch_size", [0, -1, True, 1.5])
def test_polling_rejects_invalid_batch_size(batch_size) -> None:
    with pytest.raises((TypeError, ValueError), match="batch_size"):
        MongoDBConnector(
            "mongodb://local", database="app", client=object()
        ).poll_collection("events", batch_size=batch_size)


@pytest.mark.parametrize("poll_interval_s", [-0.1, True])
def test_polling_rejects_invalid_poll_interval(poll_interval_s) -> None:
    with pytest.raises((TypeError, ValueError), match="poll_interval_s"):
        MongoDBConnector(
            "mongodb://local", database="app", client=object()
        ).poll_collection("events", poll_interval_s=poll_interval_s)


@pytest.mark.parametrize(
    ("projection", "cursor"),
    [
        ({"updated_at": 0}, ("updated_at", "_id")),
        ({"value": 1}, ("updated_at", "_id")),
        ({"_id": 0}, ("updated_at", "_id")),
    ],
)
def test_polling_projection_must_preserve_effective_cursor(projection, cursor) -> None:
    with pytest.raises(ValueError, match="projection.*cursor"):
        MongoDBConnector(
            "mongodb://local", database="app", client=object()
        ).poll_collection("events", projection=projection, cursor=cursor)
```

In `test_mongodb_change_stream.py`, add invalid `batch_size`,
`max_await_time_ms`, and `poll_interval_s` constructor cases:

```python
@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("batch_size", 0),
        ("batch_size", True),
        ("max_await_time_ms", 0),
        ("max_await_time_ms", 1.5),
        ("poll_interval_s", -0.1),
        ("poll_interval_s", True),
    ],
)
def test_change_stream_rejects_invalid_numeric_options(option, value) -> None:
    with pytest.raises((TypeError, ValueError), match=option):
        MongoDBConnector(
            "mongodb://local", database="app", client=object()
        ).watch_collection("events", **{option: value})
```

In `test_mongodb_sink.py`, add:

```python
@pytest.mark.parametrize("batch_size", [0, -1, True, 1.5])
def test_sink_rejects_invalid_batch_size(batch_size) -> None:
    with pytest.raises((TypeError, ValueError), match="batch_size"):
        _connector(FakeSinkCollection()).collection_sink(
            "events", batch_size=batch_size
        )
```

- [x] **Step 2: Run focused tests and verify they fail**

Run:

```bash
uv run --extra test --extra mongodb pytest -q \
  plugins/onestep-mongodb/tests/test_mongodb_polling.py \
  plugins/onestep-mongodb/tests/test_mongodb_change_stream.py \
  plugins/onestep-mongodb/tests/test_mongodb_sink.py \
  -k 'invalid or projection or rejects'
```

Expected: the new polling and change-stream constructor cases fail because the
current Python API stores invalid values without validation.

- [x] **Step 3: Add small connector-local validators**

Add above `MongoDBConnector`:

```python
def _positive_integer(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    if value <= 0:
        raise ValueError(f"{field} must be positive")
    return value


def _non_negative_number(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a number")
    if value < 0:
        raise ValueError(f"{field} must be non-negative")
    return float(value)


def _validate_cursor_projection(
    projection: Mapping[str, Any] | None,
    cursor: Sequence[str],
) -> None:
    if not projection:
        return

    def excludes(value: Any) -> bool:
        return value is False or (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and value == 0
        )

    excluded = {key for key, value in projection.items() if excludes(value)}
    included = {
        key for key, value in projection.items()
        if key != "_id" and not excludes(value)
    }
    missing = [
        field
        for field in cursor
        if field in excluded
        or (included and field != "_id" and field not in projection)
    ]
    if "_id" in cursor and "_id" in excluded:
        missing.append("_id")
    if missing:
        fields = ", ".join(dict.fromkeys(missing))
        raise ValueError(f"projection must preserve cursor fields: {fields}")
```

Use the helpers in all three constructors:

```python
self.batch_size = _positive_integer(batch_size, field="batch_size")
self.poll_interval_s = _non_negative_number(
    poll_interval_s, field="poll_interval_s"
)
```

For change streams also validate `max_await_time_ms`; for the sink validate
`batch_size`. After computing the effective polling cursor, call:

```python
_validate_cursor_projection(self.projection, self.cursor)
```

- [x] **Step 4: Run MongoDB non-integration tests**

Run:

```bash
uv run --extra test --extra mongodb pytest -q -m 'not integration' \
  plugins/onestep-mongodb/tests
```

Expected: PASS.

- [x] **Step 5: Commit MongoDB constructor validation**

```bash
git add \
  plugins/onestep-mongodb/src/onestep_mongodb/connector.py \
  plugins/onestep-mongodb/tests/test_mongodb_polling.py \
  plugins/onestep-mongodb/tests/test_mongodb_change_stream.py \
  plugins/onestep-mongodb/tests/test_mongodb_sink.py
git commit -m "fix(mongodb): validate direct connector options"
```

## Task 3: Make MongoDB Cursor Generations And Persistence Failure-Safe

**Files:**
- Modify: `plugins/onestep-mongodb/src/onestep_mongodb/connector.py:33`
- Modify: `plugins/onestep-mongodb/src/onestep_mongodb/connector.py:187`
- Modify: `plugins/onestep-mongodb/src/onestep_mongodb/connector.py:245`
- Modify: `plugins/onestep-mongodb/src/onestep_mongodb/connector.py:298`
- Modify: `plugins/onestep-mongodb/src/onestep_mongodb/connector.py:420`
- Test: `plugins/onestep-mongodb/tests/test_mongodb_polling.py`
- Test: `plugins/onestep-mongodb/tests/test_mongodb_change_stream.py`

- [x] **Step 1: Lock the later-ACK then earlier-retry regression**

Add this pure tracker test to `test_mongodb_polling.py`:

```python
@pytest.mark.asyncio
async def test_later_ack_then_earlier_retry_does_not_cross_generation_gap() -> None:
    saved: list[object] = []
    tracker = _ContiguousGenerationTracker(lambda token: _append(saved, token))
    first = tracker.add("one")
    second = tracker.add("two")

    await tracker.complete(second, advance=True)
    await tracker.invalidate(first.generation)
    await tracker.complete(first, advance=False)

    assert saved == []
    assert tracker.can_fetch is True
```

Add the source-level version next to
`test_retry_waits_for_stale_generation_then_replays_committed_state`:

```python
@pytest.mark.asyncio
async def test_later_ack_then_earlier_retry_replays_from_committed_state() -> None:
    documents = [
        {"_id": ObjectId("64b64c1234567890abcdef12"), "updated_at": 1},
        {"_id": ObjectId("64b64c1234567890abcdef13"), "updated_at": 2},
    ]
    store = RecordingStore()
    collection = FakeCollection(documents)
    source = MongoDBConnector(
        "mongodb://local", database="app", client=FakeClient(collection)
    ).poll_collection("events", cursor=("updated_at", "_id"), state=store)
    first, second = await source.fetch(2)

    await second.ack()
    await first.retry()
    replayed = await source.fetch(2)

    assert store.saved == []
    assert [item.payload["updated_at"] for item in replayed] == [1, 2]
```

- [x] **Step 2: Run the generation-gap tests and verify they fail**

Run:

```bash
uv run --extra test --extra mongodb pytest -q \
  plugins/onestep-mongodb/tests/test_mongodb_polling.py \
  -k 'later_ack_then_earlier_retry'
```

Expected: FAIL because the later completed token still has `advances=True` when
the earlier retry invalidates the generation, so the current prefix loop saves
the later token.

- [x] **Step 3: Clear advancement for the complete invalidated generation**

Update `_ContiguousGenerationTracker.invalidate()`:

```python
async def invalidate(self, generation: int) -> None:
    async with self._lock:
        if generation == self.generation:
            self._invalidated.add(generation)
            for item in self._pending:
                if item.generation == generation:
                    item.advances = False
            self.generation += 1
```

This invalidates both unfinished and already-completed tokens from the same
fetch generation. Do not clear `completed`; those delivery callbacks have already
finished and still count toward releasing the stale generation.

- [x] **Step 4: Add a state store that fails one save**

Add this helper directly after `RecordingStore` in both
`test_mongodb_polling.py` and `test_mongodb_change_stream.py`:

```python
class FailOnceStore(RecordingStore):
    def __init__(self, loaded=None) -> None:
        super().__init__(loaded)
        self.save_calls = 0

    async def save(self, key, value):
        self.save_calls += 1
        if self.save_calls == 1:
            raise RuntimeError("state unavailable")
        await super().save(key, value)
```

- [x] **Step 5: Write failing polling persistence tests**

Add:

```python
@pytest.mark.asyncio
async def test_polling_state_save_failure_does_not_advance_or_drop_prefix() -> None:
    documents = [
        {"_id": ObjectId("64b64c1234567890abcdef12"), "updated_at": 1},
        {"_id": ObjectId("64b64c1234567890abcdef13"), "updated_at": 2},
    ]
    store = FailOnceStore()
    collection = FakeCollection(documents)
    source = MongoDBConnector(
        "mongodb://local", database="app", client=FakeClient(collection)
    ).poll_collection("events", cursor=("updated_at", "_id"), state=store)
    first, second = await source.fetch(2)

    await second.ack()
    with pytest.raises(RuntimeError, match="state unavailable"):
        await first.ack()

    assert source._committed is None
    assert store.saved == []

    await first.retry()
    replayed = await source.fetch(2)
    assert [item.payload["updated_at"] for item in replayed] == [1, 2]
```

- [x] **Step 6: Write the change-stream persistence test**

Add:

```python
@pytest.mark.asyncio
async def test_change_stream_state_save_failure_reopens_without_lost_token() -> None:
    first_event = _event("64b64c1234567890abcdef12", "insert")
    second_event = _event("64b64c1234567890abcdef13", "update")
    first_stream = FakeChangeStream([first_event, second_event])
    replacement = FakeChangeStream([])
    collection = FakeWatchCollection([first_stream, replacement])
    store = FailOnceStore()
    source = MongoDBConnector(
        "mongodb://local", database="app", client=FakeWatchClient(collection)
    ).watch_collection("events", state=store)
    first, second = await source.fetch(2)

    await second.ack()
    with pytest.raises(RuntimeError, match="state unavailable"):
        await first.ack()

    assert source._resume_token is None
    assert store.saved == []

    await first.retry()
    await source.fetch(2)
    assert len(collection.watch_calls) == 2
    assert "resume_after" not in collection.watch_calls[1]
```

- [x] **Step 7: Run the new tests and verify they fail**

Run:

```bash
uv run --extra test --extra mongodb pytest -q \
  plugins/onestep-mongodb/tests/test_mongodb_polling.py \
  plugins/onestep-mongodb/tests/test_mongodb_change_stream.py \
  -k 'state_save_failure'
```

Expected: FAIL because `_save()` mutates the in-memory committed token before
durable persistence, `complete()` removes the contiguous prefix before save, and
the delivery marks itself terminal before the failing operation returns.

- [x] **Step 8: Persist before mutating the tracker**

Change both source `_save()` methods so durable persistence precedes in-memory
state:

```python
async def _save(self, token: Any) -> None:
    await self.state.save(self.state_key, encode_state(list(token)))
    self._committed = tuple(token)
```

```python
async def _save(self, token: Any) -> None:
    await self.state.save(self.state_key, encode_state(token))
    self._resume_token = token
```

Refactor `_ContiguousGenerationTracker.complete()` to inspect the contiguous
completed prefix without removing it, call `_save()` first, and only then remove
the prefix and decrement the completing delivery's outstanding count. If `_save()`
raises, restore the completing token's prior `completed` and `advances` values and
leave `_pending`, `_outstanding`, and `_invalidated` unchanged.

Use this transaction shape:

```python
async def complete(self, tracked: _TrackedToken, *, advance: bool) -> None:
    async with self._lock:
        previous = (tracked.completed, tracked.advances)
        stale = tracked.generation in self._invalidated
        tracked.completed = True
        tracked.advances = advance and not stale

        prefix: list[_TrackedToken] = []
        saved = None
        for item in self._pending:
            if not item.completed:
                break
            prefix.append(item)
            if item.advances:
                saved = item.token

        try:
            if saved is not None:
                await self._save(saved)
        except Exception:
            tracked.completed, tracked.advances = previous
            raise

        if not previous[0]:
            self._outstanding[tracked.generation] = max(
                0, self._outstanding.get(tracked.generation, 1) - 1
            )
        for _ in prefix:
            self._pending.popleft()
        self._discard_settled_generations()
```

Extract the existing zero-count cleanup loop into
`_discard_settled_generations()` so both behavior and ordering stay explicit.

- [x] **Step 9: Mark delivery terminal only after its callback succeeds**

For polling and change-stream deliveries, move `_terminal = True` after the
awaited operation in `ack()`, `retry()`, `fail()`, and `release_unstarted()`:

```python
async def ack(self) -> None:
    if self._terminal:
        return
    await self._source._tracker.complete(self._tracked, advance=True)
    self._terminal = True
```

For multi-step callbacks, keep the delivery non-terminal until invalidation and
tracker completion both succeed. This lets the runtime call `retry()` when an ACK
fails instead of turning that retry into a no-op.

- [x] **Step 10: Run MongoDB tests**

Run:

```bash
uv run --extra test --extra mongodb pytest -q -m 'not integration' \
  plugins/onestep-mongodb/tests
```

Expected: PASS, including out-of-order ACK, retry-gap, stop-control, and the new
state-save failure cases.

- [x] **Step 11: Commit MongoDB generation and persistence semantics**

```bash
git add \
  plugins/onestep-mongodb/src/onestep_mongodb/connector.py \
  plugins/onestep-mongodb/tests/test_mongodb_polling.py \
  plugins/onestep-mongodb/tests/test_mongodb_change_stream.py
git commit -m "fix(mongodb): preserve cursor generation gaps"
```

## Task 4: Document, Verify, And Prepare Patch Releases

**Files:**
- Modify: `plugins/onestep-elasticsearch/README.md`
- Modify: `plugins/onestep-mongodb/README.md`
- Modify: `CHANGELOG.md`

- [x] **Step 1: Document Elasticsearch request ambiguity**

Append to `Delivery semantics`:

```markdown
Request-level 502, 503, and 504 responses are ambiguous after the request body
has been sent. The sink retries them internally only when `operation: index` and
a present `id_field` make replay convergent. A request-level 429 is an explicit
rejection and remains retryable without stable IDs.
```

- [x] **Step 2: Document MongoDB projection and state behavior**

Append to `Sources`:

```markdown
A polling projection must retain every effective cursor field, including the
implicit `_id` tie-breaker. Invalid projections fail during source construction.
Cursor state is updated in memory only after the configured cursor store confirms
the save; a save failure leaves the generation replayable from the last durable
cursor. If any delivery retries, every token in that fetch generation is prevented
from advancing the cursor, including later deliveries that already acknowledged.
```

- [x] **Step 3: Add unreleased changelog entries**

Under `## Unreleased`, add:

```markdown
- Prevents the Elasticsearch/OpenSearch bulk sink from internally replaying
  ambiguous request-level gateway failures when documents lack stable IDs.
- Rejects invalid MongoDB direct Python numeric options during construction and
  polling projections that omit or rewrite effective cursor fields.
- Prevents a later MongoDB acknowledgement from advancing the cursor after an
  earlier delivery invalidates the same generation for retry.
- Keeps MongoDB cursor generations replayable when durable state persistence
  fails.
```

- [x] **Step 4: Run format and focused compatibility checks**

Run:

```bash
uv run ruff check \
  plugins/onestep-elasticsearch/src \
  plugins/onestep-elasticsearch/tests \
  plugins/onestep-mongodb/src \
  plugins/onestep-mongodb/tests
uv run --extra test --extra elasticsearch --extra mongodb pytest -q \
  -m 'not integration' \
  plugins/onestep-elasticsearch/tests
uv run --extra test --extra mongodb pytest -q -m 'not integration' \
  plugins/onestep-mongodb/tests
```

Expected: all commands exit 0.

Actual: both affected plugin suites pass. Runtime-critical Ruff rules pass; the
unfiltered Ruff command still reports 12 MongoDB import/style findings, including
pre-existing plugin-wide style debt unrelated to these correctness semantics.

- [x] **Step 5: Run the repository reliability gate**

Run:

```bash
./scripts/run-reliability-checks.sh
```

Expected: core and every installed official plugin non-integration suite pass in
isolated pytest processes.

- [x] **Step 6: Commit documentation**

```bash
git add \
  CHANGELOG.md \
  plugins/onestep-elasticsearch/README.md \
  plugins/onestep-mongodb/README.md
git commit -m "docs: clarify connector replay guarantees"
```

- [x] **Step 7: Stop before release metadata**

Do not bump versions, update `uv.lock`, tag, push, or publish as part of this
implementation plan. When the user requests release, follow repository release
rules in a separate release step: bump both affected plugin patch versions,
update `uv.lock`, preserve `onestep>=1.7.1` because core did not change, update
the changelog headings, then publish the new immutable package versions.

## Completion Gate

The increment is complete only when:

- auto-ID Elasticsearch writes issue exactly one request after an ambiguous
  request-level 502/503/504 and return `UNCERTAIN`;
- stable-ID index writes and explicit request-level 429 rejections retain bounded
  internal retry;
- invalid MongoDB direct Python source/sink options fail during construction;
- MongoDB polling projections cannot remove effective cursor fields;
- a later MongoDB ACK followed by an earlier retry does not persist a token from
  the invalidated generation;
- failed MongoDB cursor-store saves do not advance in-memory state, discard the
  contiguous pending prefix, or suppress the runtime retry callback;
- both plugin suites and the repository reliability gate pass;
- no runtime reporter or control-plane protocol field changes.
