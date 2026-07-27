# MongoDB Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an independently testable `onestep-mongodb` plugin with deterministic polling, raw-event resumable change streams, and acknowledged insert/upsert sinks.

**Architecture:** Use one lazily owned PyMongo `AsyncMongoClient`, source-owned cursors/change streams, Extended JSON state encoding, and one shared contiguous acknowledgement/generation tracker for both sources. Polling and change streams may use in-memory state for development, while restart guarantees require an explicit cursor store; retry and release invalidate the current generation and replay only after every stale delivery terminates.

**Tech Stack:** Python `>=3.9`, `onestep>=1.7.1`, `pymongo>=4.13` native async API, BSON Extended JSON, Hatch, pytest, pytest-asyncio, MongoDB replica set.

---

## File Responsibility Map

- Create `plugins/onestep-mongodb/pyproject.toml`: independent package metadata, dependency floors, Hatch build, plugin-local onestep source, and entry point.
- Create `plugins/onestep-mongodb/README.md`: Python/YAML examples, replica-set and durable-state requirements, payload shape, polling limitations, reset runbook, and idempotency.
- Create `plugins/onestep-mongodb/src/onestep_mongodb/__init__.py`: approved exports, package version, and registration alias.
- Create `plugins/onestep-mongodb/src/onestep_mongodb/state_codec.py`: BSON cursor/resume-token Extended JSON conversion to plain JSON-compatible values.
- Create `plugins/onestep-mongodb/src/onestep_mongodb/connector.py`: connector lifecycle, shared generation/contiguous tracker, polling/change-stream deliveries and sources, and collection sink.
- Create `plugins/onestep-mongodb/src/onestep_mongodb/resources.py`: four resource catalogs, builders, durable-state references, and strict validation.
- Create `plugins/onestep-mongodb/src/onestep_mongodb/resilience.py`: PyMongo exception classification and redacted bulk-write causes.
- Create `plugins/onestep-mongodb/tests/test_mongodb_polling.py`: keyset query, Extended JSON, contiguous ack, retry/release generation replay, stale callbacks, and terminal fail.
- Create `plugins/onestep-mongodb/tests/test_mongodb_change_stream.py`: raw events, `updateLookup`, resume state, generation reset, history loss, and close.
- Create `plugins/onestep-mongodb/tests/test_mongodb_sink.py`: insert/upsert shapes, chunking, acknowledgement, write concern, item errors, and uncertainty.
- Create `plugins/onestep-mongodb/tests/test_mongodb_plugin.py`: exports, entry point, catalog, strict YAML, and state capability checks.
- Create `plugins/onestep-mongodb/tests/test_mongodb_resilience.py`: driver classification/redaction.
- Create `plugins/onestep-mongodb/tests/test_mongodb_runtime_contract.py`: unsafe-fetch release behavior during pause/drain/shutdown.
- Create `plugins/onestep-mongodb/tests/integration/test_mongodb_live.py`: polling restart, sink replay, raw insert/update/delete events, and resume after restart.

No task edits shared root files. In particular, do not add a MongoDB-backed implicit
state collection. Root workspace, lock, CI, Compose replica-set initialization,
docs, worker image, and release changes belong to the shared integration plan.

### Task 1: Establish Package Metadata And Approved Public Names

**Files:**
- Create: `plugins/onestep-mongodb/pyproject.toml`
- Create: `plugins/onestep-mongodb/README.md`
- Create: `plugins/onestep-mongodb/src/onestep_mongodb/__init__.py`
- Create: `plugins/onestep-mongodb/src/onestep_mongodb/connector.py`
- Create: `plugins/onestep-mongodb/src/onestep_mongodb/resources.py`
- Create: `plugins/onestep-mongodb/src/onestep_mongodb/resilience.py`
- Create: `plugins/onestep-mongodb/src/onestep_mongodb/state_codec.py`
- Create: `plugins/onestep-mongodb/tests/test_mongodb_plugin.py`

- [ ] **Step 1: Write the failing public-surface and entry-point test**

Create `test_mongodb_plugin.py`:

```python
from __future__ import annotations

from importlib import metadata as importlib_metadata

from onestep_mongodb import (
    MongoDBChangeStreamDelivery,
    MongoDBChangeStreamSource,
    MongoDBCollectionSink,
    MongoDBConnector,
    MongoDBPayloadError,
    MongoDBPollingDelivery,
    MongoDBPollingSource,
    register,
    register_resources,
)


def test_public_surface_and_entry_point() -> None:
    assert register is register_resources
    assert MongoDBConnector.__name__ == "MongoDBConnector"
    assert MongoDBPollingSource.__name__ == "MongoDBPollingSource"
    assert MongoDBPollingDelivery.__name__ == "MongoDBPollingDelivery"
    assert MongoDBChangeStreamSource.__name__ == "MongoDBChangeStreamSource"
    assert MongoDBChangeStreamDelivery.__name__ == "MongoDBChangeStreamDelivery"
    assert MongoDBCollectionSink.__name__ == "MongoDBCollectionSink"
    assert MongoDBPayloadError.__name__ == "MongoDBPayloadError"
    entries = importlib_metadata.entry_points()
    selected = entries.select(group="onestep.resources") if hasattr(entries, "select") else entries.get("onestep.resources", ())
    assert any(item.name == "mongodb" and item.value == "onestep_mongodb:register" for item in selected)
```

- [ ] **Step 2: Run and verify the project/package is absent**

```bash
uv run --project plugins/onestep-mongodb --extra test python -m pytest -q plugins/onestep-mongodb/tests/test_mongodb_plugin.py
```

Expected: FAIL because the project/package does not exist.

- [ ] **Step 3: Create exact package metadata**

Create `pyproject.toml`:

```toml
[project]
name = "onestep-mongodb"
version = "0.1.0"
description = "MongoDB connector plugin for onestep."
readme = "README.md"
requires-python = ">=3.9"
license = { text = "MIT" }
dependencies = ["onestep>=1.7.1", "pymongo>=4.13"]

[project.optional-dependencies]
test = ["pytest>=8.0.0", "pytest-asyncio>=0.23.0"]
dev = ["pytest>=8.0.0", "pytest-asyncio>=0.23.0"]

[project.entry-points."onestep.resources"]
mongodb = "onestep_mongodb:register"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/onestep_mongodb"]

[tool.uv.sources]
onestep = { path = "../..", editable = true }

[tool.pytest.ini_options]
asyncio_mode = "auto"
markers = ["integration: live external service tests"]
```

Create the package README required by that metadata at
`plugins/onestep-mongodb/README.md`:

```markdown
# onestep-mongodb

MongoDB connector plugin for onestep.
```

- [ ] **Step 4: Add importable public shells**

Create `connector.py`:

```python
from __future__ import annotations

from typing import Any

from onestep import Delivery, Sink, Source


class MongoDBPayloadError(ValueError):
    pass


class MongoDBConnector:
    def __init__(self, uri: str, *, database: str, client_options: dict[str, Any] | None = None, client: Any | None = None) -> None:
        self.uri = uri
        self.database_name = database
        self.client_options = dict(client_options or {})
        self._client = client


class MongoDBPollingSource(Source):
    async def fetch(self, limit: int) -> list[Delivery]:
        raise NotImplementedError


class MongoDBPollingDelivery(Delivery):
    async def ack(self) -> None: raise NotImplementedError
    async def retry(self, *, delay_s: float | None = None) -> None: raise NotImplementedError
    async def fail(self, exc: Exception | None = None) -> None: raise NotImplementedError


class MongoDBChangeStreamSource(Source):
    async def fetch(self, limit: int) -> list[Delivery]:
        raise NotImplementedError


class MongoDBChangeStreamDelivery(MongoDBPollingDelivery):
    pass


class MongoDBCollectionSink(Sink):
    async def send(self, envelope) -> None:
        raise NotImplementedError
```

Create `resources.py`:

```python
from __future__ import annotations

from onestep import ResourceRegistry


def register_resources(registry: ResourceRegistry) -> None:
    return None
```

Create `resilience.py`:

```python
from __future__ import annotations

from onestep import ConnectorErrorKind


def classify_mongodb_error(exc: BaseException, *, operation: str) -> ConnectorErrorKind | None:
    return None
```

Create `state_codec.py`:

```python
from __future__ import annotations
```

Create `__init__.py`:

```python
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _package_version

from .connector import MongoDBChangeStreamDelivery, MongoDBChangeStreamSource, MongoDBCollectionSink, MongoDBConnector, MongoDBPayloadError, MongoDBPollingDelivery, MongoDBPollingSource
from .resources import register_resources

try:
    __version__ = _package_version("onestep-mongodb")
except PackageNotFoundError:
    __version__ = "dev"

register = register_resources
__all__ = ["MongoDBChangeStreamDelivery", "MongoDBChangeStreamSource", "MongoDBCollectionSink", "MongoDBConnector", "MongoDBPayloadError", "MongoDBPollingDelivery", "MongoDBPollingSource", "__version__", "register", "register_resources"]
```

- [ ] **Step 5: Run the package test**

Run the Step 2 command.

Expected: `1 passed`.

- [ ] **Step 6: Commit package foundation**

```bash
git add plugins/onestep-mongodb
git commit -m "feat(mongodb): add plugin package foundation"
```

### Task 2: Encode BSON State And Track Contiguous Generations

**Files:**
- Modify: `plugins/onestep-mongodb/src/onestep_mongodb/state_codec.py`
- Modify: `plugins/onestep-mongodb/src/onestep_mongodb/connector.py`
- Create: `plugins/onestep-mongodb/tests/test_mongodb_polling.py`

- [ ] **Step 1: Write failing BSON round-trip and tracker tests**

Create `test_mongodb_polling.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId

from onestep_mongodb.connector import _ContiguousGenerationTracker
from onestep_mongodb.state_codec import decode_state, encode_state


def test_extended_json_round_trips_bson_values() -> None:
    value = [ObjectId("64b64c1234567890abcdef12"), datetime(2026, 7, 27, tzinfo=timezone.utc)]
    encoded = encode_state(value)
    assert isinstance(encoded, dict)
    assert decode_state(encoded) == value


@pytest.mark.asyncio
async def test_out_of_order_ack_does_not_cross_gap() -> None:
    saved: list[object] = []

    async def save(token): saved.append(token)

    tracker = _ContiguousGenerationTracker(save)
    first = tracker.add("one"); second = tracker.add("two")
    await tracker.complete(second, advance=True)
    assert saved == []
    await tracker.complete(first, advance=True)
    assert saved == ["two"]


@pytest.mark.asyncio
async def test_invalidated_generation_ignores_late_ack_and_blocks_reopen() -> None:
    saved: list[object] = []
    tracker = _ContiguousGenerationTracker(lambda token: _append(saved, token))
    first = tracker.add("one"); second = tracker.add("two")
    await tracker.invalidate(first.generation)
    assert tracker.can_fetch is False
    await tracker.complete(first, advance=True)
    assert saved == [] and tracker.can_fetch is False
    await tracker.complete(second, advance=False)
    assert tracker.can_fetch is True


async def _append(values, value):
    values.append(value)
```

- [ ] **Step 2: Run and verify missing codec/tracker failures**

```bash
uv run --project plugins/onestep-mongodb --extra test python -m pytest -q plugins/onestep-mongodb/tests/test_mongodb_polling.py
```

Expected: FAIL during import because codec functions and tracker are absent.

- [ ] **Step 3: Implement Extended JSON conversion**

Replace `state_codec.py` with:

```python
from __future__ import annotations

import json
from typing import Any

from bson import json_util


def encode_state(value: Any) -> dict[str, Any]:
    return {"extended_json": json.loads(json_util.dumps(value, json_options=json_util.CANONICAL_JSON_OPTIONS))}


def decode_state(value: Any) -> Any:
    if not isinstance(value, dict) or "extended_json" not in value:
        raise ValueError("MongoDB state must contain extended_json")
    return json_util.loads(json.dumps(value["extended_json"]))
```

- [ ] **Step 4: Add the complete shared tracker to `connector.py`**

```python
import asyncio
from collections import deque
from dataclasses import dataclass
from collections.abc import Awaitable, Callable


@dataclass
class _TrackedToken:
    generation: int
    sequence: int
    token: Any
    completed: bool = False
    advances: bool = False


class _ContiguousGenerationTracker:
    def __init__(self, save: Callable[[Any], Awaitable[None]]) -> None:
        self._save = save
        self.generation = 0
        self._next_sequence = 0
        self._pending: deque[_TrackedToken] = deque()
        self._outstanding: dict[int, int] = {}
        self._invalidated: set[int] = set()
        self._lock = asyncio.Lock()

    @property
    def can_fetch(self) -> bool:
        return not any(
            generation in self._invalidated and count > 0
            for generation, count in self._outstanding.items()
        )

    def add(self, token: Any) -> _TrackedToken:
        tracked = _TrackedToken(self.generation, self._next_sequence, token)
        self._next_sequence += 1
        self._pending.append(tracked)
        self._outstanding[tracked.generation] = self._outstanding.get(tracked.generation, 0) + 1
        return tracked

    async def invalidate(self, generation: int) -> None:
        async with self._lock:
            if generation == self.generation:
                self._invalidated.add(generation)
                self.generation += 1

    async def complete(self, tracked: _TrackedToken, *, advance: bool) -> None:
        async with self._lock:
            stale = tracked.generation in self._invalidated
            tracked.completed = True
            tracked.advances = advance and not stale
            self._outstanding[tracked.generation] = max(0, self._outstanding.get(tracked.generation, 1) - 1)
            saved = None
            while self._pending and self._pending[0].completed:
                item = self._pending.popleft()
                if item.advances:
                    saved = item.token
            stale_generations = [item for item, count in self._outstanding.items() if count == 0]
            for item in stale_generations:
                self._outstanding.pop(item, None)
                self._invalidated.discard(item)
            if saved is not None:
                await self._save(saved)
```

- [ ] **Step 5: Run codec/tracker tests**

Run the Step 2 command.

Expected: `3 passed`.

- [ ] **Step 6: Commit state and acknowledgement primitives**

```bash
git add plugins/onestep-mongodb/src/onestep_mongodb/state_codec.py plugins/onestep-mongodb/src/onestep_mongodb/connector.py plugins/onestep-mongodb/tests/test_mongodb_polling.py
git commit -m "feat(mongodb): add BSON state and contiguous tracking"
```

### Task 3: Implement Connector Lifecycle And Deterministic Polling

**Files:**
- Modify: `plugins/onestep-mongodb/src/onestep_mongodb/connector.py`
- Modify: `plugins/onestep-mongodb/tests/test_mongodb_polling.py`

- [ ] **Step 1: Add fake collection, keyset, restart, and replay tests**

Append tests that assert these exact contracts:

```python
from onestep import InMemoryCursorStore
from onestep_mongodb import MongoDBConnector


def test_composite_keyset_query_is_lexicographic() -> None:
    source = MongoDBConnector("mongodb://local", database="app", client=object()).poll_collection("events", cursor=("updated_at", "_id"))
    query = source._cursor_query((10, ObjectId("64b64c1234567890abcdef12")))
    assert query == {"$or": [
        {"updated_at": {"$gt": 10}},
        {"updated_at": 10, "_id": {"$gt": ObjectId("64b64c1234567890abcdef12")}},
    ]}


def test_filter_combines_with_keyset_under_and() -> None:
    source = MongoDBConnector("mongodb://local", database="app", client=object()).poll_collection("events", cursor=("updated_at",), filter={"archived": False})
    query = source._query((10, ObjectId("64b64c1234567890abcdef12")))
    assert query == {"$and": [{"archived": False}, {"$or": [{"updated_at": {"$gt": 10}}, {"updated_at": 10, "_id": {"$gt": ObjectId("64b64c1234567890abcdef12")}}]}]}


def test_id_must_be_final_when_explicit() -> None:
    with pytest.raises(ValueError, match="final"):
        MongoDBConnector("mongodb://local", database="app", client=object()).poll_collection("events", cursor=("_id", "updated_at"))
```

Add these complete fakes and asynchronous tests below the query tests:

```python
class RecordingStore:
    def __init__(self, loaded=None) -> None:
        self.loaded = loaded
        self.saved: list[tuple[str, object]] = []

    async def load(self, key):
        return self.loaded

    async def save(self, key, value):
        self.saved.append((key, value))
        self.loaded = value


class FakeCursor:
    def __init__(self, documents) -> None:
        self.documents = list(documents)
        self.sort_value = None
        self.limit_value = None

    def sort(self, value):
        self.sort_value = value
        return self

    def limit(self, value):
        self.limit_value = value
        self.documents = self.documents[:value]
        return self

    def __aiter__(self):
        self._iterator = iter(self.documents)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration:
            raise StopAsyncIteration


class FakeCollection:
    def __init__(self, documents) -> None:
        self.documents = list(documents)
        self.find_calls: list[tuple[dict, object, FakeCursor]] = []

    def find(self, query, projection):
        cursor = FakeCursor(self.documents)
        self.find_calls.append((query, projection, cursor))
        return cursor


class FakeDatabase:
    def __init__(self, collection) -> None:
        self.collection = collection

    def __getitem__(self, name):
        return self.collection


class FakeClient:
    def __init__(self, collection) -> None:
        self.database = FakeDatabase(collection)

    def __getitem__(self, name):
        return self.database


@pytest.mark.asyncio
async def test_polling_persists_only_the_contiguous_ack_prefix() -> None:
    documents = [
        {"_id": ObjectId("64b64c1234567890abcdef12"), "updated_at": 1},
        {"_id": ObjectId("64b64c1234567890abcdef13"), "updated_at": 2},
    ]
    store = RecordingStore()
    collection = FakeCollection(documents)
    connector = MongoDBConnector("mongodb://local", database="app", client=FakeClient(collection))
    source = connector.poll_collection("events", cursor=("updated_at", "_id"), state=store)

    first, second = await source.fetch(2)
    await second.ack()
    assert store.saved == []
    await first.ack()
    assert decode_state(store.saved[-1][1]) == [2, documents[1]["_id"]]
    assert collection.find_calls[0][2].sort_value == [("updated_at", 1), ("_id", 1)]
    assert collection.find_calls[0][2].limit_value == 2


@pytest.mark.asyncio
async def test_retry_waits_for_stale_generation_then_replays_committed_state() -> None:
    documents = [
        {"_id": ObjectId("64b64c1234567890abcdef12"), "updated_at": 1},
        {"_id": ObjectId("64b64c1234567890abcdef13"), "updated_at": 2},
    ]
    store = RecordingStore()
    collection = FakeCollection(documents)
    source = MongoDBConnector("mongodb://local", database="app", client=FakeClient(collection)).poll_collection(
        "events", cursor=("updated_at", "_id"), state=store
    )

    first, second = await source.fetch(2)
    await first.retry()
    assert await source.fetch(2) == []
    await second.fail(RuntimeError("terminal stale handler"))
    await source.fetch(2)

    assert store.saved == []
    assert collection.find_calls[-1][0] == {}


@pytest.mark.asyncio
async def test_terminal_fail_advances_polling_cursor() -> None:
    document = {"_id": ObjectId("64b64c1234567890abcdef12"), "updated_at": 1}
    store = RecordingStore()
    source = MongoDBConnector("mongodb://local", database="app", client=FakeClient(FakeCollection([document]))).poll_collection(
        "events", cursor=("updated_at", "_id"), state=store
    )
    delivery = (await source.fetch(1))[0]
    await delivery.fail(RuntimeError("terminal handler failure"))
    assert decode_state(store.saved[-1][1]) == [1, document["_id"]]


@pytest.mark.asyncio
async def test_owned_client_is_lazy_and_closes_once(monkeypatch) -> None:
    import pymongo

    collection = FakeCollection([])

    class OwnedClient(FakeClient):
        def __init__(self):
            super().__init__(collection); self.close_calls = 0

        async def close(self):
            self.close_calls += 1

    client = OwnedClient()
    factory_calls = []

    def build_client(uri, **options):
        factory_calls.append((uri, options)); return client

    monkeypatch.setattr(pymongo, "AsyncMongoClient", build_client)
    connector = MongoDBConnector("mongodb://local", database="app")
    assert factory_calls == []
    assert connector.collection("events") is collection
    assert factory_calls == [("mongodb://local", {})]
    await connector.close(); await connector.close()
    assert client.close_calls == 1
```

- [ ] **Step 2: Run polling tests and verify factory/query methods fail**

```bash
uv run --project plugins/onestep-mongodb --extra test python -m pytest -q plugins/onestep-mongodb/tests/test_mongodb_polling.py
```

Expected: FAIL because `poll_collection`, `_cursor_query`, and source behavior are missing.

- [ ] **Step 3: Implement connector ownership and factories**

Replace the connector module import block with the complete runtime dependencies
used by Tasks 3-5:

```python
import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from onestep import ConnectorErrorKind, ConnectorOperation, ConnectorOperationError, CursorStore, Delivery, Envelope, InMemoryCursorStore, Sink, Source

from .state_codec import decode_state, encode_state
```

Replace the connector shell with:

```python
class MongoDBConnector:
    def __init__(self, uri: str, *, database: str, client_options: Mapping[str, Any] | None = None, client: Any | None = None) -> None:
        if not uri or not database:
            raise ValueError("uri and database must not be empty")
        self.uri = uri; self.database_name = database
        self.client_options = dict(client_options or {})
        self._client = client; self._owns_client = client is None; self._closed = False

    def _get_client(self):
        if self._client is None:
            from pymongo import AsyncMongoClient
            self._client = AsyncMongoClient(self.uri, **self.client_options)
        return self._client

    def collection(self, name: str):
        return self._get_client()[self.database_name][name]

    def poll_collection(self, collection: str, **options: Any) -> "MongoDBPollingSource":
        return MongoDBPollingSource(connector=self, collection=collection, **options)

    def watch_collection(self, collection: str, **options: Any) -> "MongoDBChangeStreamSource":
        return MongoDBChangeStreamSource(connector=self, collection=collection, **options)

    def collection_sink(self, collection: str, **options: Any) -> "MongoDBCollectionSink":
        return MongoDBCollectionSink(connector=self, collection=collection, **options)

    async def close(self) -> None:
        if self._closed: return
        self._closed = True
        if self._owns_client and self._client is not None:
            result = self._client.close()
            if hasattr(result, "__await__"): await result
```

- [ ] **Step 4: Implement polling constructor and query generation**

Use `InMemoryCursorStore` when `state` is absent, append `_id` when absent, reject
an explicit non-final `_id`, and derive
`mongodb:{database}:{collection}:poll:{comma-separated-cursor}` when `state_key` is
absent. Add:

```python
class MongoDBPollingSource(Source):
    fetch_is_cancel_safe = False

    def __init__(self, *, connector: MongoDBConnector, collection: str, cursor: Sequence[str] = ("_id",), filter: Mapping[str, Any] | None = None, projection: Mapping[str, Any] | None = None, batch_size: int = 100, poll_interval_s: float = 1.0, state: CursorStore | None = None, state_key: str | None = None, initial_cursor: Sequence[Any] | None = None) -> None:
        super().__init__(f"mongodb.polling:{collection}")
        configured = tuple(cursor)
        if not configured or len(set(configured)) != len(configured): raise ValueError("cursor must be non-empty and unique")
        if "_id" in configured and configured[-1] != "_id": raise ValueError("_id must be the final cursor component")
        self.cursor = configured if configured[-1] == "_id" else (*configured, "_id")
        self.connector = connector; self.collection_name = collection
        self.filter = dict(filter or {}); self.projection = dict(projection or {}) or None
        self.batch_size = batch_size; self.poll_interval_s = poll_interval_s
        self.state = state or InMemoryCursorStore()
        self.state_key = state_key or f"mongodb:{connector.database_name}:{collection}:poll:{','.join(self.cursor)}"
        self.initial_cursor = tuple(decode_state({"extended_json": list(initial_cursor)})) if initial_cursor is not None else None
        self._committed = None; self._scan = None; self._loaded = False
        self._active_cursor = None
        self._tracker = _ContiguousGenerationTracker(self._save)

    def _cursor_query(self, token: Sequence[Any]) -> dict[str, Any]:
        branches = []
        for index, field in enumerate(self.cursor):
            branch = {self.cursor[prefix]: token[prefix] for prefix in range(index)}
            branch[field] = {"$gt": token[index]}
            branches.append(branch)
        return {"$or": branches}

    def _query(self, token: Sequence[Any] | None) -> dict[str, Any]:
        cursor_query = self._cursor_query(token) if token is not None else {}
        if self.filter and cursor_query: return {"$and": [self.filter, cursor_query]}
        return dict(self.filter or cursor_query)

    async def _save(self, token: Any) -> None:
        self._committed = tuple(token); await self.state.save(self.state_key, encode_state(list(token)))
```

- [ ] **Step 5: Implement polling delivery/fetch/generation methods**

Add concrete `MongoDBPollingDelivery` methods that call source `_complete`,
`_invalidate`, and `release_unstarted`, and implement source `open`, `fetch`, and
`close`. The fetch body must use:

```python
    async def open(self) -> None:
        if self._loaded: return
        loaded = await self.state.load(self.state_key)
        self._committed = tuple(decode_state(loaded)) if loaded is not None else self.initial_cursor
        self._scan = self._committed; self._loaded = True

    async def fetch(self, limit: int) -> list[Delivery]:
        await self.open()
        if not self._tracker.can_fetch: return []
        collection = self.connector.collection(self.collection_name)
        try:
            self._active_cursor = collection.find(self._query(self._scan), self.projection).sort([(field, 1) for field in self.cursor]).limit(min(limit, self.batch_size))
            documents = [document async for document in self._active_cursor]
        except Exception as exc:
            from .resilience import classify_mongodb_error
            kind = classify_mongodb_error(exc, operation="fetch")
            if kind is None: raise
            raise ConnectorOperationError(backend="mongodb", operation=ConnectorOperation.FETCH, kind=kind, source_name=self.name, retry_delay_s=self.poll_interval_s, cause=exc) from exc
        finally:
            cursor = self._active_cursor
            self._active_cursor = None
            if cursor is not None and hasattr(cursor, "close"):
                result = cursor.close()
                if hasattr(result, "__await__"): await result
        deliveries: list[Delivery] = []
        for document in documents:
            token = tuple(document[field] for field in self.cursor)
            self._scan = token
            tracked = self._tracker.add(token)
            deliveries.append(MongoDBPollingDelivery(self, Envelope(body=document, meta={"mongodb": {"database": self.connector.database_name, "collection": self.collection_name}}), tracked))
        return deliveries

    async def invalidate(self, tracked: _TrackedToken, *, delay_s: float | None = None) -> None:
        if delay_s: await asyncio.sleep(delay_s)
        await self._tracker.invalidate(tracked.generation)
        self._scan = self._committed

    async def close(self) -> None:
        cursor = self._active_cursor
        self._active_cursor = None
        if cursor is not None and hasattr(cursor, "close"):
            result = cursor.close()
            if hasattr(result, "__await__"): await result
```

Use this complete delivery implementation:

```python
class MongoDBPollingDelivery(Delivery):
    def __init__(self, source: MongoDBPollingSource, envelope: Envelope, tracked: _TrackedToken) -> None:
        super().__init__(envelope)
        self._source = source
        self._tracked = tracked
        self._terminal = False

    async def ack(self) -> None:
        if self._terminal: return
        self._terminal = True
        await self._source._tracker.complete(self._tracked, advance=True)

    async def retry(self, *, delay_s: float | None = None) -> None:
        if self._terminal: return
        self._terminal = True
        await self._source.invalidate(self._tracked, delay_s=delay_s)
        await self._source._tracker.complete(self._tracked, advance=False)

    async def fail(self, exc: Exception | None = None) -> None:
        if self._terminal: return
        self._terminal = True
        await self._source._tracker.complete(self._tracked, advance=True)

    async def release_unstarted(self) -> None:
        if self._terminal: return
        self._terminal = True
        await self._source.invalidate(self._tracked)
        await self._source._tracker.complete(self._tracked, advance=False)
```

- [ ] **Step 6: Run polling tests and commit**

```bash
uv run --project plugins/onestep-mongodb --extra test python -m pytest -q plugins/onestep-mongodb/tests/test_mongodb_polling.py
git add plugins/onestep-mongodb/src/onestep_mongodb plugins/onestep-mongodb/tests/test_mongodb_polling.py
git commit -m "feat(mongodb): add restart-safe collection polling"
```

Expected: all polling tests pass before the commit.

### Task 4: Implement Raw-Event Change Streams With Contiguous Resume

**Files:**
- Modify: `plugins/onestep-mongodb/src/onestep_mongodb/connector.py`
- Create: `plugins/onestep-mongodb/tests/test_mongodb_change_stream.py`

- [ ] **Step 1: Write failing raw-event/resume/generation tests**

Create `test_mongodb_change_stream.py` with these complete fakes and tests:

```python
from __future__ import annotations

import pytest
from bson import ObjectId

from onestep_mongodb import MongoDBConnector
from onestep_mongodb.state_codec import decode_state, encode_state


class RecordingStore:
    def __init__(self, loaded=None) -> None:
        self.loaded = loaded
        self.saved: list[tuple[str, object]] = []

    async def load(self, key): return self.loaded
    async def save(self, key, value): self.saved.append((key, value)); self.loaded = value


class FakeChangeStream:
    def __init__(self, events) -> None:
        self.events = list(events)
        self.closed = False

    async def try_next(self):
        return self.events.pop(0) if self.events else None

    async def close(self):
        self.closed = True


class FakeWatchCollection:
    def __init__(self, streams) -> None:
        self.streams = list(streams)
        self.watch_calls: list[dict] = []

    async def watch(self, pipeline, **options):
        self.watch_calls.append({"pipeline": pipeline, **options})
        return self.streams.pop(0)


class FakeWatchDatabase:
    def __init__(self, collection) -> None: self.collection = collection
    def __getitem__(self, name): return self.collection


class FakeWatchClient:
    def __init__(self, collection) -> None: self.database = FakeWatchDatabase(collection)
    def __getitem__(self, name): return self.database


def _event(hex_id: str, operation: str):
    object_id = ObjectId(hex_id)
    return {"_id": {"token": hex_id}, "operationType": operation, "documentKey": {"_id": object_id}}


@pytest.mark.asyncio
async def test_default_watch_emits_complete_raw_delete_event() -> None:
    raw_event = _event("64b64c1234567890abcdef12", "delete")
    stream = FakeChangeStream([raw_event])
    collection = FakeWatchCollection([stream])
    connector = MongoDBConnector("mongodb://local", database="app", client=FakeWatchClient(collection))
    source = connector.watch_collection("events")

    delivery = (await source.fetch(1))[0]

    assert collection.watch_calls[0]["full_document"] == "updateLookup"
    assert "resume_after" not in collection.watch_calls[0]
    assert delivery.envelope.body == raw_event
    assert delivery.envelope.body["operationType"] == "delete"
    assert delivery.envelope.body["documentKey"] == {"_id": raw_event["documentKey"]["_id"]}


@pytest.mark.asyncio
async def test_restart_passes_decoded_resume_token() -> None:
    token = {"token": "persisted"}
    store = RecordingStore(encode_state(token))
    collection = FakeWatchCollection([FakeChangeStream([])])
    source = MongoDBConnector("mongodb://local", database="app", client=FakeWatchClient(collection)).watch_collection("events", state=store)

    await source.fetch(1)

    assert collection.watch_calls[0]["resume_after"] == token


@pytest.mark.asyncio
async def test_change_tokens_persist_only_after_contiguous_ack() -> None:
    first_event = _event("64b64c1234567890abcdef12", "insert")
    second_event = _event("64b64c1234567890abcdef13", "update")
    store = RecordingStore()
    collection = FakeWatchCollection([FakeChangeStream([first_event, second_event])])
    source = MongoDBConnector("mongodb://local", database="app", client=FakeWatchClient(collection)).watch_collection("events", state=store)
    first, second = await source.fetch(2)

    await second.ack(); assert store.saved == []
    await first.ack()

    assert decode_state(store.saved[-1][1]) == second_event["_id"]


@pytest.mark.asyncio
async def test_retry_closes_stream_and_waits_for_stale_delivery() -> None:
    first_stream = FakeChangeStream([_event("64b64c1234567890abcdef12", "insert"), _event("64b64c1234567890abcdef13", "update")])
    replacement = FakeChangeStream([])
    collection = FakeWatchCollection([first_stream, replacement])
    store = RecordingStore()
    source = MongoDBConnector("mongodb://local", database="app", client=FakeWatchClient(collection)).watch_collection("events", state=store)
    first, second = await source.fetch(2)

    await first.retry()
    assert first_stream.closed is True
    assert await source.fetch(2) == []
    assert len(collection.watch_calls) == 1
    await second.release_unstarted()
    await source.fetch(2)

    assert store.saved == []
    assert len(collection.watch_calls) == 2
    assert "resume_after" not in collection.watch_calls[1]
```

- [ ] **Step 2: Run and verify change-stream construction fails**

```bash
uv run --project plugins/onestep-mongodb --extra test python -m pytest -q plugins/onestep-mongodb/tests/test_mongodb_change_stream.py
```

Expected: FAIL because `MongoDBChangeStreamSource` is still abstract/incomplete.

- [ ] **Step 3: Implement the change-stream source constructor and state**

```python
class MongoDBChangeStreamSource(Source):
    fetch_is_cancel_safe = False

    def __init__(self, *, connector: MongoDBConnector, collection: str, pipeline: Sequence[Mapping[str, Any]] | None = None, full_document: str = "updateLookup", max_await_time_ms: int = 1000, batch_size: int = 100, poll_interval_s: float = 0.1, state: CursorStore | None = None, state_key: str | None = None) -> None:
        super().__init__(f"mongodb.change_stream:{collection}")
        self.connector = connector; self.collection_name = collection
        self.pipeline = [dict(stage) for stage in (pipeline or [])]
        self.full_document = full_document; self.max_await_time_ms = max_await_time_ms
        self.batch_size = batch_size; self.poll_interval_s = poll_interval_s
        self.state = state or InMemoryCursorStore()
        self.state_key = state_key or f"mongodb:{connector.database_name}:{collection}:change-stream"
        self._resume_token = None; self._loaded = False; self._stream = None
        self._tracker = _ContiguousGenerationTracker(self._save)

    async def _save(self, token: Any) -> None:
        self._resume_token = token
        await self.state.save(self.state_key, encode_state(token))

    async def open(self) -> None:
        if not self._loaded:
            loaded = await self.state.load(self.state_key)
            self._resume_token = decode_state(loaded) if loaded is not None else None
            self._loaded = True
        if self._stream is None and self._tracker.can_fetch:
            options = {"full_document": self.full_document, "max_await_time_ms": self.max_await_time_ms}
            if self._resume_token is not None: options["resume_after"] = self._resume_token
            self._stream = await self.connector.collection(self.collection_name).watch(self.pipeline, **options)
```

- [ ] **Step 4: Implement raw-event fetch, invalidation, and close**

```python
    async def fetch(self, limit: int) -> list[Delivery]:
        await self.open()
        if self._stream is None or not self._tracker.can_fetch: return []
        deliveries: list[Delivery] = []
        for _ in range(min(limit, self.batch_size)):
            event = await self._stream.try_next()
            if event is None: break
            token = event["_id"]
            tracked = self._tracker.add(token)
            meta = {"mongodb": {"database": self.connector.database_name, "collection": self.collection_name, "operation_type": event.get("operationType"), "document_key": event.get("documentKey")}}
            deliveries.append(MongoDBChangeStreamDelivery(self, Envelope(body=event, meta=meta), tracked))
        return deliveries

    async def invalidate(self, tracked: _TrackedToken, *, delay_s: float | None = None) -> None:
        if delay_s: await asyncio.sleep(delay_s)
        await self._tracker.invalidate(tracked.generation)
        if self._stream is not None:
            await self._stream.close(); self._stream = None

    async def close(self) -> None:
        if self._stream is not None:
            await self._stream.close(); self._stream = None
```

Implement the change-stream delivery explicitly:

```python
class MongoDBChangeStreamDelivery(Delivery):
    def __init__(self, source: MongoDBChangeStreamSource, envelope: Envelope, tracked: _TrackedToken) -> None:
        super().__init__(envelope)
        self._source = source; self._tracked = tracked; self._terminal = False

    async def ack(self) -> None:
        if self._terminal: return
        self._terminal = True
        await self._source._tracker.complete(self._tracked, advance=True)

    async def retry(self, *, delay_s: float | None = None) -> None:
        if self._terminal: return
        self._terminal = True
        await self._source.invalidate(self._tracked, delay_s=delay_s)
        await self._source._tracker.complete(self._tracked, advance=False)

    async def fail(self, exc: Exception | None = None) -> None:
        if self._terminal: return
        self._terminal = True
        await self._source._tracker.complete(self._tracked, advance=True)

    async def release_unstarted(self) -> None:
        if self._terminal: return
        self._terminal = True
        await self._source.invalidate(self._tracked)
        await self._source._tracker.complete(self._tracked, advance=False)
```

- [ ] **Step 5: Add history-loss and resumable-reopen tests**

Append this exact test:

```python
from pymongo.errors import OperationFailure
from onestep import ConnectorErrorKind, ConnectorOperationError


@pytest.mark.asyncio
async def test_history_lost_is_permanent_and_never_falls_back_to_now() -> None:
    class HistoryLostStream(FakeChangeStream):
        async def try_next(self):
            raise OperationFailure("resume history lost", code=286)

    collection = FakeWatchCollection([HistoryLostStream([])])
    store = RecordingStore(encode_state({"token": "old"}))
    source = MongoDBConnector("mongodb://local", database="app", client=FakeWatchClient(collection)).watch_collection("events", state=store)

    with pytest.raises(ConnectorOperationError) as captured:
        await source.fetch(1)

    assert captured.value.kind is ConnectorErrorKind.PERMANENT
    assert "reset durable resume state" in str(captured.value)
    assert len(collection.watch_calls) == 1
    assert collection.watch_calls[0]["resume_after"] == {"token": "old"}


@pytest.mark.asyncio
async def test_resumable_stream_failure_reopens_from_committed_token() -> None:
    class ResumableStream(FakeChangeStream):
        async def try_next(self):
            raise OperationFailure(
                "temporary stream failure",
                details={"errorLabels": ["ResumableChangeStreamError"]},
            )

    token = {"token": "persisted"}
    collection = FakeWatchCollection([ResumableStream([]), FakeChangeStream([])])
    source = MongoDBConnector("mongodb://local", database="app", client=FakeWatchClient(collection)).watch_collection(
        "events", state=RecordingStore(encode_state(token))
    )

    with pytest.raises(ConnectorOperationError) as captured:
        await source.fetch(1)
    assert captured.value.kind is ConnectorErrorKind.TRANSIENT

    assert await source.fetch(1) == []
    assert len(collection.watch_calls) == 2
    assert collection.watch_calls[1]["resume_after"] == token
```

- [ ] **Step 6: Convert driver fetch failures without token fallback**

Replace change-stream `fetch()` with the complete method below. It buffers raw
events until the fetch call succeeds, so an exception cannot strand tracker entries
for deliveries that were never returned. On failure it invalidates the active
generation and closes the stream so a retry reopens from the last contiguously
committed token. History-loss codes are classified directly here so this task does
not depend on Task 5's complete general classifier.

```python
    async def fetch(self, limit: int) -> list[Delivery]:
        await self.open()
        if self._stream is None or not self._tracker.can_fetch:
            return []
        events: list[Mapping[str, Any]] = []
        try:
            for _ in range(min(limit, self.batch_size)):
                event = await self._stream.try_next()
                if event is None:
                    break
                events.append(event)
        except Exception as exc:
            from .resilience import classify_mongodb_error

            await self._tracker.invalidate(self._tracker.generation)
            stream = self._stream
            self._stream = None
            if stream is not None:
                await stream.close()
            history_lost = getattr(exc, "code", None) in {280, 286}
            has_resumable_label = getattr(exc, "has_error_label", lambda label: False)(
                "ResumableChangeStreamError"
            )
            kind = (
                ConnectorErrorKind.PERMANENT
                if history_lost
                else (
                    ConnectorErrorKind.TRANSIENT
                    if has_resumable_label
                    else classify_mongodb_error(exc, operation="fetch")
                )
            )
            if kind is None:
                raise
            message = (
                "MongoDB change-stream history is unavailable; reset durable "
                "resume state deliberately before restarting"
                if history_lost
                else None
            )
            raise ConnectorOperationError(
                backend="mongodb",
                operation=ConnectorOperation.FETCH,
                kind=kind,
                source_name=self.name,
                cause=exc,
                message=message,
            ) from exc

        deliveries: list[Delivery] = []
        for event in events:
            token = event["_id"]
            tracked = self._tracker.add(token)
            meta = {
                "mongodb": {
                    "database": self.connector.database_name,
                    "collection": self.collection_name,
                    "operation_type": event.get("operationType"),
                    "document_key": event.get("documentKey"),
                }
            }
            deliveries.append(
                MongoDBChangeStreamDelivery(
                    self,
                    Envelope(body=event, meta=meta),
                    tracked,
                )
            )
        return deliveries
```

The module import block from Task 3 already supplies `Mapping`, `Any`,
`ConnectorErrorKind`, `ConnectorOperation`, and `ConnectorOperationError`.
`asyncio.CancelledError` is not caught because it derives from `BaseException` on
the supported Python versions.

- [ ] **Step 7: Run and commit change-stream behavior**

```bash
uv run --project plugins/onestep-mongodb --extra test python -m pytest -q plugins/onestep-mongodb/tests/test_mongodb_change_stream.py
git add plugins/onestep-mongodb/src/onestep_mongodb/connector.py plugins/onestep-mongodb/tests/test_mongodb_change_stream.py
git commit -m "feat(mongodb): add resumable raw change streams"
```

Expected: all change-stream tests pass before commit.

### Task 5: Implement Insert/Upsert Sink And Driver Error Semantics

**Files:**
- Modify: `plugins/onestep-mongodb/src/onestep_mongodb/connector.py`
- Modify: `plugins/onestep-mongodb/src/onestep_mongodb/resilience.py`
- Create: `plugins/onestep-mongodb/tests/test_mongodb_sink.py`
- Create: `plugins/onestep-mongodb/tests/test_mongodb_resilience.py`

- [ ] **Step 1: Write failing sink-shape tests**

Create `test_mongodb_sink.py`:

```python
from __future__ import annotations

import pytest
from pymongo.errors import BulkWriteError

from onestep import ConnectorErrorKind, ConnectorOperationError, Envelope
from onestep_mongodb import MongoDBConnector, MongoDBPayloadError


class FakeWriteConcern:
    def __init__(self, acknowledged=True) -> None: self.acknowledged = acknowledged


class FakeSinkCollection:
    def __init__(self, *, acknowledged=True) -> None:
        self.write_concern = FakeWriteConcern(acknowledged)
        self.insert_one_calls = []
        self.insert_many_calls = []
        self.bulk_calls = []

    async def insert_one(self, document): self.insert_one_calls.append(document); return object()
    async def insert_many(self, documents, *, ordered): self.insert_many_calls.append((documents, ordered)); return object()
    async def bulk_write(self, operations, *, ordered): self.bulk_calls.append((operations, ordered)); return object()


class FakeSinkDatabase:
    def __init__(self, collection) -> None: self.collection = collection
    def __getitem__(self, name): return self.collection


class FakeSinkClient:
    def __init__(self, collection) -> None: self.database = FakeSinkDatabase(collection)
    def __getitem__(self, name): return self.database


def _connector(collection):
    return MongoDBConnector("mongodb://local", database="app", client=FakeSinkClient(collection))


@pytest.mark.asyncio
async def test_insert_mapping_and_chunked_sequence() -> None:
    collection = FakeSinkCollection()
    sink = _connector(collection).collection_sink("events", batch_size=2, ordered=True)
    await sink.send(Envelope(body={"_id": "one"}))
    await sink.send(Envelope(body=[{"_id": "two"}, {"_id": "three"}, {"_id": "four"}]))
    assert collection.insert_one_calls == [{"_id": "one"}]
    assert collection.insert_many_calls == [
        ([{"_id": "two"}, {"_id": "three"}], True),
        ([{"_id": "four"}], True),
    ]


@pytest.mark.asyncio
async def test_upsert_uses_all_keys_and_excludes_keys_and_id_from_set() -> None:
    collection = FakeSinkCollection()
    sink = _connector(collection).collection_sink("events", mode="upsert", keys=("tenant", "event_id"), ordered=False)
    await sink.send(Envelope(body={"_id": "mongo-id", "tenant": "a", "event_id": "1", "value": 3}))
    request = collection.bulk_calls[0][0][0]
    assert request._filter == {"tenant": "a", "event_id": "1"}
    assert request._doc == {"$set": {"value": 3}}
    assert collection.bulk_calls[0][1] is False


@pytest.mark.parametrize("body", [[], "text", [1], [{"event_id": "one"}]])
@pytest.mark.asyncio
async def test_invalid_upsert_payloads_fail_before_client_call(body) -> None:
    collection = FakeSinkCollection()
    sink = _connector(collection).collection_sink("events", mode="upsert", keys=("tenant", "event_id"))
    with pytest.raises((MongoDBPayloadError, TypeError, ValueError)):
        await sink.send(Envelope(body=body))
    assert collection.bulk_calls == []


@pytest.mark.asyncio
async def test_unacknowledged_write_concern_is_rejected() -> None:
    collection = FakeSinkCollection(acknowledged=False)
    sink = _connector(collection).collection_sink("events")
    with pytest.raises(ValueError, match="acknowledged"):
        await sink.send(Envelope(body={"_id": "one"}))


@pytest.mark.asyncio
async def test_partial_insert_bulk_error_is_uncertain_and_redacted() -> None:
    class PartialCollection(FakeSinkCollection):
        async def insert_many(self, documents, *, ordered):
            raise BulkWriteError({"writeErrors": [{"index": 1, "code": 11000, "errmsg": "duplicate"}], "nInserted": 1})

    sink = _connector(PartialCollection()).collection_sink("events")
    with pytest.raises(ConnectorOperationError) as captured:
        await sink.send(Envelope(body=[{"_id": "one"}, {"_id": "one"}]))
    assert captured.value.kind is ConnectorErrorKind.UNCERTAIN
    assert captured.value.cause.failed_indexes == (1,)
    assert captured.value.cause.codes == (11000,)
    assert captured.value.cause.committed_count == 1
    assert "documents" not in str(captured.value)
```

- [ ] **Step 2: Write failing resilience tests**

Create `test_mongodb_resilience.py`:

```python
from __future__ import annotations

from pymongo.errors import AutoReconnect, BulkWriteError, DuplicateKeyError, OperationFailure

from onestep import ConnectorErrorKind
from onestep_mongodb.resilience import classify_mongodb_error


def test_driver_error_classes() -> None:
    assert classify_mongodb_error(AutoReconnect("lost after submit"), operation="send") is ConnectorErrorKind.UNCERTAIN
    assert classify_mongodb_error(AutoReconnect("selection"), operation="fetch") is ConnectorErrorKind.DISCONNECTED
    assert classify_mongodb_error(DuplicateKeyError("duplicate"), operation="send") is ConnectorErrorKind.PERMANENT
    assert classify_mongodb_error(OperationFailure("auth", code=18), operation="open") is ConnectorErrorKind.MISCONFIGURED
    assert classify_mongodb_error(OperationFailure("history", code=286), operation="fetch") is ConnectorErrorKind.PERMANENT
    assert classify_mongodb_error(BulkWriteError({"writeErrors": [{"index": 0, "code": 16500, "errmsg": "busy"}]}), operation="send") is ConnectorErrorKind.THROTTLED
```

- [ ] **Step 3: Run tests and verify sink/factory is incomplete**

```bash
uv run --project plugins/onestep-mongodb --extra test python -m pytest -q plugins/onestep-mongodb/tests/test_mongodb_sink.py plugins/onestep-mongodb/tests/test_mongodb_resilience.py
```

Expected: FAIL because collection sink and classifiers are not implemented.

- [ ] **Step 4: Implement sink normalization and acknowledged writes**

Implement `MongoDBCollectionSink` with this public constructor and core send:

```python
class MongoDBCollectionSink(Sink):
    def __init__(self, *, connector: MongoDBConnector, collection: str, mode: str = "insert", keys: Sequence[str] = (), ordered: bool = True, batch_size: int = 1000) -> None:
        super().__init__(f"mongodb.collection:{collection}")
        if mode not in {"insert", "upsert"}: raise ValueError("mode must be insert or upsert")
        if mode == "upsert" and not keys: raise ValueError("upsert mode requires keys")
        if batch_size <= 0: raise ValueError("batch_size must be positive")
        self.connector = connector; self.collection_name = collection; self.mode = mode
        self.keys = tuple(keys); self.ordered = ordered; self.batch_size = batch_size

    def _documents(self, body: Any) -> list[dict[str, Any]]:
        if isinstance(body, Mapping): return [dict(body)]
        if not isinstance(body, Sequence) or isinstance(body, (str, bytes, bytearray)) or not body: raise MongoDBPayloadError("payload must be a mapping or non-empty sequence")
        if any(not isinstance(item, Mapping) for item in body): raise MongoDBPayloadError("every payload item must be a mapping")
        return [dict(item) for item in body]

    async def send(self, envelope: Envelope) -> None:
        from pymongo import UpdateOne
        collection = self.connector.collection(self.collection_name)
        if not collection.write_concern.acknowledged:
            raise ValueError("MongoDB sink requires acknowledged write concern")
        try:
            single_document = isinstance(envelope.body, Mapping)
            documents = self._documents(envelope.body)
            if self.mode == "upsert":
                for index, document in enumerate(documents):
                    missing = [key for key in self.keys if key not in document]
                    if missing:
                        raise MongoDBPayloadError(f"document {index} missing upsert keys {missing}")
        except MongoDBPayloadError as exc:
            raise ConnectorOperationError(backend="mongodb", operation=ConnectorOperation.SEND, kind=ConnectorErrorKind.PERMANENT, source_name=self.name, cause=exc) from exc
        committed = 0
        try:
            for start in range(0, len(documents), self.batch_size):
                chunk = documents[start : start + self.batch_size]
                if self.mode == "insert":
                    if single_document: await collection.insert_one(chunk[0])
                    else: await collection.insert_many(chunk, ordered=self.ordered)
                else:
                    operations = []
                    for index, document in enumerate(chunk):
                        selector = {key: document[key] for key in self.keys}
                        update = {key: value for key, value in document.items() if key not in self.keys and key != "_id"}
                        operations.append(UpdateOne(selector, {"$set": update}, upsert=True))
                    await collection.bulk_write(operations, ordered=self.ordered)
                committed += 1
        except Exception as exc:
            from .resilience import classify_mongodb_error, redacted_mongodb_cause
            kind = classify_mongodb_error(exc, operation="send")
            if kind is None: raise
            replay_safe = self.mode == "upsert" and bool(self.keys)
            partial = committed > 0 or redacted_mongodb_cause(exc).committed_count > 0
            if partial and not replay_safe: kind = ConnectorErrorKind.UNCERTAIN
            raise ConnectorOperationError(backend="mongodb", operation=ConnectorOperation.SEND, kind=kind, source_name=self.name, cause=redacted_mongodb_cause(exc)) from exc
```

- [ ] **Step 5: Implement complete PyMongo classification and redaction**

Replace `resilience.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass

from pymongo.errors import AutoReconnect, BulkWriteError, ConfigurationError, DuplicateKeyError, ExecutionTimeout, InvalidURI, NetworkTimeout, OperationFailure, ServerSelectionTimeoutError

from onestep import ConnectorErrorKind


@dataclass(frozen=True)
class MongoDBErrorCause(Exception):
    code: int | None
    failed_indexes: tuple[int, ...]
    codes: tuple[int | None, ...]
    committed_count: int
    message: str

    def __str__(self) -> str:
        return f"MongoDB error code={self.code} failed_indexes={self.failed_indexes} codes={self.codes}: {self.message}"


def redacted_mongodb_cause(exc: BaseException) -> MongoDBErrorCause:
    details = exc.details if isinstance(exc, BulkWriteError) else {}
    write_errors = details.get("writeErrors", []) if isinstance(details, dict) else []
    failed_indexes = tuple(int(item["index"]) for item in write_errors if isinstance(item, dict) and "index" in item)
    codes = tuple(item.get("code") for item in write_errors if isinstance(item, dict) and "index" in item)
    committed_count = sum(int(details.get(key, 0) or 0) for key in ("nInserted", "nUpserted", "nMatched")) if isinstance(details, dict) else 0
    code = getattr(exc, "code", None)
    if code is None and write_errors:
        code = write_errors[0].get("code")
    if isinstance(exc, BulkWriteError):
        message = "; ".join(
            str(item.get("errmsg", "write error"))[:160]
            for item in write_errors
            if isinstance(item, dict)
        )[:500]
    else:
        message = str(exc)[:500]
    return MongoDBErrorCause(code, failed_indexes, codes, committed_count, message)


def classify_mongodb_error(exc: BaseException, *, operation: str) -> ConnectorErrorKind | None:
    if isinstance(exc, BulkWriteError):
        details = exc.details if isinstance(exc.details, dict) else {}
        codes = {
            item.get("code")
            for key in ("writeErrors", "writeConcernErrors")
            for item in details.get(key, [])
            if isinstance(item, dict)
        }
        if codes & {13, 18}: return ConnectorErrorKind.MISCONFIGURED
        if codes & {50, 16500}: return ConnectorErrorKind.THROTTLED
        return ConnectorErrorKind.PERMANENT
    if isinstance(exc, (ConfigurationError, InvalidURI)): return ConnectorErrorKind.MISCONFIGURED
    if isinstance(exc, DuplicateKeyError): return ConnectorErrorKind.PERMANENT
    if isinstance(exc, OperationFailure):
        if exc.code in {13, 18}: return ConnectorErrorKind.MISCONFIGURED
        if exc.code in {286, 280}: return ConnectorErrorKind.PERMANENT
        if exc.code in {50, 16500}: return ConnectorErrorKind.THROTTLED
        if exc.has_error_label("ResumableChangeStreamError"): return ConnectorErrorKind.TRANSIENT
        return ConnectorErrorKind.PERMANENT
    if isinstance(exc, AutoReconnect): return ConnectorErrorKind.UNCERTAIN if operation == "send" else ConnectorErrorKind.DISCONNECTED
    if isinstance(exc, (NetworkTimeout, ExecutionTimeout)): return ConnectorErrorKind.UNCERTAIN if operation == "send" else ConnectorErrorKind.TRANSIENT
    if isinstance(exc, ServerSelectionTimeoutError): return ConnectorErrorKind.DISCONNECTED
    return None
```

The redacted cause stores only codes, integer indexes, counts, and a capped driver
message; it never retains documents or the connector URI.

- [ ] **Step 6: Run sink/resilience tests and commit**

```bash
uv run --project plugins/onestep-mongodb --extra test python -m pytest -q plugins/onestep-mongodb/tests/test_mongodb_sink.py plugins/onestep-mongodb/tests/test_mongodb_resilience.py
git add plugins/onestep-mongodb/src/onestep_mongodb plugins/onestep-mongodb/tests/test_mongodb_sink.py plugins/onestep-mongodb/tests/test_mongodb_resilience.py
git commit -m "feat(mongodb): add acknowledged insert and upsert sinks"
```

Expected: all sink/resilience tests pass; partial inserts surface `UNCERTAIN`, and
stable-key upserts retain their backend error class.

### Task 6: Register Four Strict YAML Resources

**Files:**
- Modify: `plugins/onestep-mongodb/src/onestep_mongodb/resources.py`
- Modify: `plugins/onestep-mongodb/tests/test_mongodb_plugin.py`

- [ ] **Step 1: Add failing catalog and strict-construction tests**

Append these exact tests to `test_mongodb_plugin.py`:

```python
import pytest

from onestep import InMemoryCursorStore, ResourceBuildContext, ResourceRegistry, load_app_config
from onestep_mongodb.resources import _build_polling


def _config(resources):
    return {"apiVersion": "onestep/v1alpha1", "kind": "App", "app": {"name": "mongo"}, "resources": resources, "tasks": []}


def test_catalog_roles_and_topology_are_exact() -> None:
    registry = ResourceRegistry(); register(registry)
    catalog = {entry.type: entry for entry in registry.catalog_entries()}
    assert catalog["mongodb"].roles == ("connector",)
    assert catalog["mongodb_polling"].topology_fields == ("collection", "cursor", "batch_size", "poll_interval_s")
    assert catalog["mongodb_change_stream"].topology_fields == ("collection", "full_document", "batch_size", "max_await_time_ms")
    assert catalog["mongodb_collection_sink"].topology_fields == ("collection", "mode", "keys", "batch_size")


def test_strict_yaml_builds_all_resource_roles() -> None:
    app = load_app_config(_config({
        "mongo": {"type": "mongodb", "uri": "mongodb://localhost/app?replicaSet=rs0", "database": "app"},
        "poll": {"type": "mongodb_polling", "connector": "mongo", "collection": "events", "cursor": ["updated_at", "_id"]},
        "changes": {"type": "mongodb_change_stream", "connector": "mongo", "collection": "events", "full_document": "updateLookup"},
        "sink": {"type": "mongodb_collection_sink", "connector": "mongo", "collection": "archive", "mode": "upsert", "keys": ["event_id"]},
    }), strict=True)
    assert isinstance(app.resources["mongo"], MongoDBConnector)
    assert isinstance(app.resources["poll"], MongoDBPollingSource)
    assert isinstance(app.resources["changes"], MongoDBChangeStreamSource)
    assert isinstance(app.resources["sink"], MongoDBCollectionSink)


def test_polling_builder_accepts_cursor_store_capability() -> None:
    connector = MongoDBConnector("mongodb://localhost", database="app", client=object())
    store = InMemoryCursorStore()
    values = {"mongo": connector, "state": store}
    ctx = ResourceBuildContext(name="poll", type="mongodb_polling", field="resources.poll", _resolve=values.__getitem__)
    source = _build_polling(ctx, {"type": "mongodb_polling", "connector": "mongo", "collection": "events", "state": "state"})
    assert source.state is store


@pytest.mark.parametrize("resource", [
    {"type": "mongodb", "uri": "mongodb://localhost/app?w=0", "database": "app"},
    {"type": "mongodb_polling", "connector": "mongo", "collection": "events", "cursor": ["updated_at", "updated_at"]},
    {"type": "mongodb_polling", "connector": "mongo", "collection": "events", "cursor": ["_id", "updated_at"]},
    {"type": "mongodb_change_stream", "connector": "mongo", "collection": "events", "pipeline": {}},
    {"type": "mongodb_change_stream", "connector": "mongo", "collection": "events", "full_document": "lookup"},
    {"type": "mongodb_collection_sink", "connector": "mongo", "collection": "events", "mode": "upsert"},
    {"type": "mongodb_collection_sink", "connector": "mongo", "collection": "events", "unknown": True},
])
def test_strict_yaml_rejects_invalid_resource(resource) -> None:
    resources = {"mongo": {"type": "mongodb", "uri": "mongodb://localhost", "database": "app"}, "target": resource}
    if resource["type"] == "mongodb": resources = {"mongo": resource}
    with pytest.raises((TypeError, ValueError)):
        load_app_config(_config(resources), strict=True)
```

- [ ] **Step 2: Run strict tests and verify missing handlers fail**

```bash
uv run --project plugins/onestep-mongodb --extra test python -m pytest -q plugins/onestep-mongodb/tests/test_mongodb_plugin.py -k "catalog or strict"
```

Expected: FAIL because the registry has no MongoDB handlers.

- [ ] **Step 3: Define exact allowed fields and catalogs**

Replace `resources.py` with this complete module; Step 4 continues the same code
block with builders and registration:

```python
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import parse_qs, urlparse

from onestep import ResourceBuildContext, ResourceCatalogEntry, ResourceCatalogField, ResourceRegistry, ResourceSpecHandler, ResourceValidationContext

from .connector import MongoDBConnector

CONNECTOR_FIELDS = frozenset({"type", "uri", "database", "client_options"})
POLLING_FIELDS = frozenset({"type", "connector", "collection", "cursor", "filter", "projection", "batch_size", "poll_interval_s", "state", "state_key", "initial_cursor"})
CHANGE_FIELDS = frozenset({"type", "connector", "collection", "pipeline", "full_document", "max_await_time_ms", "batch_size", "poll_interval_s", "state", "state_key"})
SINK_FIELDS = frozenset({"type", "connector", "collection", "mode", "keys", "ordered", "batch_size"})

CONNECTOR_CATALOG = ResourceCatalogEntry(
    type="mongodb", roles=("connector",), label="MongoDB",
    fields=(
        ResourceCatalogField("uri", "string", required=True, secret=True),
        ResourceCatalogField("database", "string", required=True),
        ResourceCatalogField("client_options", "mapping", secret=True),
    ),
)
POLLING_CATALOG = ResourceCatalogEntry(
    type="mongodb_polling", roles=("source",), label="MongoDB Polling", connector_types=("mongodb",),
    fields=(
        ResourceCatalogField("connector", "ref", required=True), ResourceCatalogField("collection", "string", required=True),
        ResourceCatalogField("cursor", "string_list", default=["_id"]), ResourceCatalogField("filter", "mapping"),
        ResourceCatalogField("projection", "mapping"), ResourceCatalogField("batch_size", "integer", default=100),
        ResourceCatalogField("poll_interval_s", "number", default=1.0), ResourceCatalogField("state", "ref"),
        ResourceCatalogField("state_key", "string"), ResourceCatalogField("initial_cursor", "json"),
    ), topology_fields=("collection", "cursor", "batch_size", "poll_interval_s"),
)
CHANGE_CATALOG = ResourceCatalogEntry(
    type="mongodb_change_stream", roles=("source",), label="MongoDB Change Stream", connector_types=("mongodb",),
    fields=(
        ResourceCatalogField("connector", "ref", required=True), ResourceCatalogField("collection", "string", required=True),
        ResourceCatalogField("pipeline", "json"), ResourceCatalogField("full_document", "string", default="updateLookup", options=("default", "updateLookup", "whenAvailable", "required")),
        ResourceCatalogField("max_await_time_ms", "integer", default=1000), ResourceCatalogField("batch_size", "integer", default=100),
        ResourceCatalogField("poll_interval_s", "number", default=0.1), ResourceCatalogField("state", "ref"),
        ResourceCatalogField("state_key", "string"),
    ), topology_fields=("collection", "full_document", "batch_size", "max_await_time_ms"),
)
SINK_CATALOG = ResourceCatalogEntry(
    type="mongodb_collection_sink", roles=("sink",), label="MongoDB Collection Sink", connector_types=("mongodb",),
    fields=(
        ResourceCatalogField("connector", "ref", required=True), ResourceCatalogField("collection", "string", required=True),
        ResourceCatalogField("mode", "string", default="insert", options=("insert", "upsert")), ResourceCatalogField("keys", "string_list"),
        ResourceCatalogField("ordered", "boolean", default=True), ResourceCatalogField("batch_size", "integer", default=1000),
    ), topology_fields=("collection", "mode", "keys", "batch_size"),
)


def _unique_strings(ctx: ResourceValidationContext, spec: Mapping[str, Any], key: str, *, default: Sequence[str] | None = None) -> list[str]:
    if key not in spec and default is not None:
        return list(default)
    return ctx.require_non_empty_string_list(spec, key, field=f"{ctx.field}.{key}")


def _validate_connector(ctx: ResourceValidationContext, spec: Mapping[str, Any]) -> None:
    uri = ctx.require_string(spec, "uri")
    ctx.require_string(spec, "database")
    if urlparse(uri).scheme not in {"mongodb", "mongodb+srv"}:
        raise ValueError(f"'{ctx.field}.uri' must be a MongoDB URI")
    options = spec.get("client_options", {})
    if not isinstance(options, Mapping):
        raise TypeError(f"'{ctx.field}.client_options' must be a mapping")
    query = parse_qs(urlparse(uri).query)
    if options.get("w") in {0, "0"} or query.get("w") == ["0"]:
        raise ValueError(f"'{ctx.field}' requires acknowledged write concern")


def _validate_polling(ctx: ResourceValidationContext, spec: Mapping[str, Any]) -> None:
    ctx.require_string(spec, "connector"); ctx.require_string(spec, "collection")
    cursor = _unique_strings(ctx, spec, "cursor", default=("_id",))
    if "_id" in cursor and cursor[-1] != "_id":
        raise ValueError(f"'{ctx.field}.cursor' requires _id as the final component")
    for key in ("filter", "projection"):
        if key in spec and not isinstance(spec.get(key), Mapping):
            raise TypeError(f"'{ctx.field}.{key}' must be a mapping")
    ctx.validate_positive_integer(spec.get("batch_size"), field=f"{ctx.field}.batch_size")
    ctx.validate_non_negative_number(spec.get("poll_interval_s"), field=f"{ctx.field}.poll_interval_s")
    initial = spec.get("initial_cursor")
    effective_length = len(cursor) if cursor[-1] == "_id" else len(cursor) + 1
    if initial is not None and (not isinstance(initial, Sequence) or isinstance(initial, (str, bytes)) or len(initial) != effective_length):
        raise ValueError(f"'{ctx.field}.initial_cursor' must match the effective cursor length")


def _validate_change(ctx: ResourceValidationContext, spec: Mapping[str, Any]) -> None:
    ctx.require_string(spec, "connector"); ctx.require_string(spec, "collection")
    pipeline = spec.get("pipeline", [])
    if not isinstance(pipeline, Sequence) or isinstance(pipeline, (str, bytes)) or any(not isinstance(stage, Mapping) for stage in pipeline):
        raise TypeError(f"'{ctx.field}.pipeline' must be a list of mappings")
    if spec.get("full_document", "updateLookup") not in {"default", "updateLookup", "whenAvailable", "required"}:
        raise ValueError(f"'{ctx.field}.full_document' is invalid")
    ctx.validate_positive_integer(spec.get("max_await_time_ms"), field=f"{ctx.field}.max_await_time_ms")
    ctx.validate_positive_integer(spec.get("batch_size"), field=f"{ctx.field}.batch_size")
    ctx.validate_non_negative_number(spec.get("poll_interval_s"), field=f"{ctx.field}.poll_interval_s")


def _validate_sink(ctx: ResourceValidationContext, spec: Mapping[str, Any]) -> None:
    ctx.require_string(spec, "connector"); ctx.require_string(spec, "collection")
    mode = spec.get("mode", "insert")
    if mode not in {"insert", "upsert"}: raise ValueError(f"'{ctx.field}.mode' is invalid")
    if mode == "upsert": _unique_strings(ctx, spec, "keys")
    elif "keys" in spec: _unique_strings(ctx, spec, "keys")
    ctx.validate_positive_integer(spec.get("batch_size"), field=f"{ctx.field}.batch_size")
```

- [ ] **Step 4: Implement builders with exact type/capability checks**

Append these exact builders and registration to `resources.py`:

```python
def _connector(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> MongoDBConnector:
    value = ctx.resolve_dependency(spec, "connector")
    if not isinstance(value, MongoDBConnector):
        raise TypeError(f"resource {spec['connector']!r} is not a MongoDBConnector")
    return value


def _state(ctx: ResourceBuildContext, spec: Mapping[str, Any]):
    name = spec.get("state")
    if name is None: return None
    resolved_name = ctx.string_value(name, field=f"{ctx.field}.state")
    value = ctx.resolve(resolved_name)
    if not ctx.is_cursor_store(value):
        raise TypeError(f"resource {resolved_name!r} is not a cursor store")
    return value


def _build_connector(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> MongoDBConnector:
    return MongoDBConnector(ctx.require_string(spec, "uri"), database=ctx.require_string(spec, "database"), client_options=ctx.mapping_value(spec.get("client_options"), field=f"{ctx.field}.client_options"))


def _build_polling(ctx: ResourceBuildContext, spec: Mapping[str, Any]):
    return _connector(ctx, spec).poll_collection(
        ctx.require_string(spec, "collection"), cursor=tuple(ctx.string_list(spec.get("cursor", ["_id"]), field=f"{ctx.field}.cursor")),
        filter=ctx.mapping_value(spec.get("filter"), field=f"{ctx.field}.filter"), projection=ctx.mapping_value(spec.get("projection"), field=f"{ctx.field}.projection"),
        batch_size=spec.get("batch_size", 100), poll_interval_s=spec.get("poll_interval_s", 1.0), state=_state(ctx, spec), state_key=spec.get("state_key"), initial_cursor=spec.get("initial_cursor"),
    )


def _build_change(ctx: ResourceBuildContext, spec: Mapping[str, Any]):
    pipeline = spec.get("pipeline", [])
    return _connector(ctx, spec).watch_collection(
        ctx.require_string(spec, "collection"), pipeline=[dict(stage) for stage in pipeline], full_document=spec.get("full_document", "updateLookup"),
        max_await_time_ms=spec.get("max_await_time_ms", 1000), batch_size=spec.get("batch_size", 100), poll_interval_s=spec.get("poll_interval_s", 0.1),
        state=_state(ctx, spec), state_key=spec.get("state_key"),
    )


def _build_sink(ctx: ResourceBuildContext, spec: Mapping[str, Any]):
    keys = tuple(ctx.string_list(spec.get("keys"), field=f"{ctx.field}.keys")) if spec.get("keys") is not None else ()
    return _connector(ctx, spec).collection_sink(ctx.require_string(spec, "collection"), mode=spec.get("mode", "insert"), keys=keys, ordered=spec.get("ordered", True), batch_size=spec.get("batch_size", 1000))


def register_resources(registry: ResourceRegistry) -> None:
    registry.register_resource_type(ResourceSpecHandler(type="mongodb", catalog=CONNECTOR_CATALOG, allowed_fields=CONNECTOR_FIELDS, build=_build_connector, validate=_validate_connector))
    registry.register_resource_type(ResourceSpecHandler(type="mongodb_polling", catalog=POLLING_CATALOG, allowed_fields=POLLING_FIELDS, build=_build_polling, validate=_validate_polling))
    registry.register_resource_type(ResourceSpecHandler(type="mongodb_change_stream", catalog=CHANGE_CATALOG, allowed_fields=CHANGE_FIELDS, build=_build_change, validate=_validate_change))
    registry.register_resource_type(ResourceSpecHandler(type="mongodb_collection_sink", catalog=SINK_CATALOG, allowed_fields=SINK_FIELDS, build=_build_sink, validate=_validate_sink))
```

- [ ] **Step 5: Run complete plugin unit tests**

```bash
uv run --project plugins/onestep-mongodb --extra test python -m pytest -q plugins/onestep-mongodb/tests -m "not integration"
```

Expected: all plugin tests pass.

- [ ] **Step 6: Commit strict resources**

```bash
git add plugins/onestep-mongodb/src/onestep_mongodb/resources.py plugins/onestep-mongodb/tests/test_mongodb_plugin.py
git commit -m "feat(mongodb): register strict YAML resources"
```

### Task 7: Prove Unsafe-Fetch Runtime Behavior And Add Live Coverage

**Files:**
- Create: `plugins/onestep-mongodb/tests/test_mongodb_runtime_contract.py`
- Create: `plugins/onestep-mongodb/tests/integration/test_mongodb_live.py`
- Modify: `plugins/onestep-mongodb/README.md`

- [ ] **Step 1: Add unsafe-fetch runtime contract tests**

Create `test_mongodb_runtime_contract.py`:

```python
from __future__ import annotations

import asyncio

import pytest
from bson import ObjectId

from onestep import Delivery, Envelope, OneStepApp, Source
from onestep_mongodb import MongoDBConnector


class Store:
    def __init__(self) -> None: self.saved = []
    async def load(self, key): return None
    async def save(self, key, value): self.saved.append((key, value))


class BlockingCursor:
    def __init__(self, document, started, release) -> None:
        self.document = document; self.started = started; self.release = release; self.yielded = False
    def sort(self, value): return self
    def limit(self, value): return self
    def __aiter__(self): return self
    async def __anext__(self):
        if self.yielded: raise StopAsyncIteration
        self.started.set(); await self.release.wait(); self.yielded = True
        return self.document


class BlockingStream:
    def __init__(self, event, started, release) -> None:
        self.event = event; self.started = started; self.release = release; self.returned = False; self.closed = False
    async def try_next(self):
        self.started.set(); await self.release.wait()
        if self.returned: return None
        self.returned = True; return self.event
    async def close(self): self.closed = True


class RuntimeCollection:
    def __init__(self, *, document=None, event=None, started, release) -> None:
        self.document = document; self.event = event; self.started = started; self.release = release
    def find(self, query, projection): return BlockingCursor(self.document, self.started, self.release)
    async def watch(self, pipeline, **options): return BlockingStream(self.event, self.started, self.release)


class Database:
    def __init__(self, collection) -> None: self.collection = collection
    def __getitem__(self, name): return self.collection


class Client:
    def __init__(self, collection) -> None: self.database = Database(collection)
    def __getitem__(self, name): return self.database


async def _request_stop(app, action):
    if action == "shutdown": app.request_shutdown(); return
    if action == "drain": app.request_drain(); await app.wait_for_drain(); app.request_shutdown(); return
    app.request_task_pause("consume"); await app.wait_for_task_pause("consume"); app.request_shutdown()


@pytest.mark.parametrize("source_kind", ["polling", "change_stream"])
@pytest.mark.parametrize("action", ["shutdown", "drain", "pause"])
@pytest.mark.asyncio
async def test_stop_controls_release_unstarted_without_committing(source_kind, action) -> None:
    started = asyncio.Event(); release = asyncio.Event(); store = Store()
    object_id = ObjectId("64b64c1234567890abcdef12")
    collection = RuntimeCollection(
        document={"_id": object_id, "updated_at": 1},
        event={"_id": {"token": "one"}, "operationType": "insert", "documentKey": {"_id": object_id}},
        started=started,
        release=release,
    )
    connector = MongoDBConnector("mongodb://local", database="app", client=Client(collection))
    source = connector.poll_collection("events", cursor=("updated_at", "_id"), state=store) if source_kind == "polling" else connector.watch_collection("events", state=store)
    assert source.fetch_is_cancel_safe is False
    handled = []
    app = OneStepApp(f"mongo-{source_kind}-{action}", shutdown_timeout_s=1.0)

    @app.task(source=source, name="consume", concurrency=1)
    async def consume(ctx, item): handled.append(item)

    serving = asyncio.create_task(app.serve())
    await asyncio.wait_for(started.wait(), timeout=1.0)
    stopping = asyncio.create_task(_request_stop(app, action))
    await asyncio.sleep(0)
    release.set()
    await asyncio.wait_for(stopping, timeout=2.0)
    await asyncio.wait_for(serving, timeout=2.0)

    assert handled == []
    assert store.saved == []
    assert source._tracker.can_fetch is True


class AckRecordingDelivery(Delivery):
    def __init__(self, envelope: Envelope) -> None:
        super().__init__(envelope)
        self.acked = False

    async def ack(self) -> None: self.acked = True
    async def retry(self, *, delay_s=None) -> None: raise AssertionError("must not retry")
    async def fail(self, exc=None) -> None: raise AssertionError(f"unexpected failure: {exc}")


class OneShotSource(Source):
    poll_interval_s = 0.01

    def __init__(self, delivery) -> None:
        super().__init__("one-shot"); self.delivery = delivery; self.sent = False

    async def fetch(self, limit):
        if self.sent: return []
        self.sent = True; return [self.delivery]


class BlockingSinkCollection:
    class WriteConcern:
        acknowledged = True

    write_concern = WriteConcern()

    def __init__(self, entered, release) -> None:
        self.entered = entered; self.release = release

    async def insert_one(self, document):
        self.entered.set(); await self.release.wait(); return object()


@pytest.mark.asyncio
async def test_runtime_ack_follows_mongodb_write_acknowledgement() -> None:
    entered = asyncio.Event(); release = asyncio.Event()
    collection = BlockingSinkCollection(entered, release)
    connector = MongoDBConnector("mongodb://local", database="app", client=Client(collection))
    sink = connector.collection_sink("events")
    delivery = AckRecordingDelivery(Envelope(body={"_id": "one"}))
    app = OneStepApp("mongodb-sink-runtime-order", shutdown_timeout_s=1.0)

    @app.task(source=OneShotSource(delivery), emit=sink, concurrency=1)
    async def forward(ctx, item):
        ctx.app.request_shutdown(); return item

    serving = asyncio.create_task(app.serve())
    await entered.wait()
    assert delivery.acked is False
    release.set()
    await asyncio.wait_for(serving, timeout=2.0)
    assert delivery.acked is True
```

- [ ] **Step 2: Run runtime contract tests**

```bash
uv run --project plugins/onestep-mongodb --extra test python -m pytest -q plugins/onestep-mongodb/tests/test_mongodb_runtime_contract.py
```

Expected: PASS. Stop controls release unstarted source deliveries without advancing
state, and the sink delivery is acknowledged only after MongoDB acknowledges the
write. If cancellation commits state or opens a replacement generation early, fix
the source/tracker rather than weakening the assertions.

- [ ] **Step 3: Add live replica-set coverage**

Create `test_mongodb_live.py`:

```python
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from pymongo import AsyncMongoClient

from onestep import Envelope
from onestep_mongodb import MongoDBConnector

URI = os.getenv("ONESTEP_MONGODB_URI")
pytestmark = [pytest.mark.integration, pytest.mark.skipif(not URI, reason="ONESTEP_MONGODB_URI is not configured")]


class DurableTestStore:
    def __init__(self) -> None: self.values = {}
    async def load(self, key): return self.values.get(key)
    async def save(self, key, value): self.values[key] = value


async def _next_delivery(source, *, attempts=20):
    for _ in range(attempts):
        deliveries = await source.fetch(10)
        if deliveries: return deliveries[0]
        await asyncio.sleep(0.05)
    raise AssertionError("change stream produced no delivery")


@pytest.mark.asyncio
async def test_polling_restart_reads_only_later_documents() -> None:
    name = f"poll_{uuid.uuid4().hex}"; client = AsyncMongoClient(URI); database = client.get_default_database()
    collection = database[name]; store = DurableTestStore()
    await collection.insert_many([{"event_id": "one", "updated_at": 1}, {"event_id": "two", "updated_at": 2}])
    connector = MongoDBConnector(URI, database=database.name, client=client)
    first_source = connector.poll_collection(name, cursor=("updated_at", "_id"), state=store, state_key=name)
    try:
        deliveries = await first_source.fetch(10)
        for delivery in deliveries: await delivery.ack()
        await collection.insert_one({"event_id": "three", "updated_at": 3})
        second_source = connector.poll_collection(name, cursor=("updated_at", "_id"), state=store, state_key=name)
        restarted = await second_source.fetch(10)
        assert [item.payload["event_id"] for item in restarted] == ["three"]
    finally:
        await collection.drop(); await client.close()


@pytest.mark.asyncio
async def test_insert_and_stable_key_upsert_are_acknowledged() -> None:
    name = f"sink_{uuid.uuid4().hex}"; client = AsyncMongoClient(URI); database = client.get_default_database(); collection = database[name]
    connector = MongoDBConnector(URI, database=database.name, client=client)
    try:
        await connector.collection_sink(name).send(Envelope(body={"_id": "stable", "value": 1}))
        upsert = connector.collection_sink(name, mode="upsert", keys=("event_id",))
        await upsert.send(Envelope(body={"event_id": "evt", "value": 2}))
        await upsert.send(Envelope(body={"event_id": "evt", "value": 3}))
        assert (await collection.find_one({"_id": "stable"}))["value"] == 1
        assert (await collection.find_one({"event_id": "evt"}))["value"] == 3
    finally:
        await collection.drop(); await client.close()


@pytest.mark.asyncio
async def test_raw_change_events_include_update_lookup_and_delete_key() -> None:
    name = f"changes_{uuid.uuid4().hex}"; client = AsyncMongoClient(URI); database = client.get_default_database(); collection = database[name]
    connector = MongoDBConnector(URI, database=database.name, client=client)
    source = connector.watch_collection(name)
    await source.open()
    try:
        inserted = await collection.insert_one({"value": 1}); insert_event = await _next_delivery(source); await insert_event.ack()
        await collection.update_one({"_id": inserted.inserted_id}, {"$set": {"value": 2}}); update_event = await _next_delivery(source); await update_event.ack()
        await collection.delete_one({"_id": inserted.inserted_id}); delete_event = await _next_delivery(source); await delete_event.ack()
        assert insert_event.payload["operationType"] == "insert" and "_id" in insert_event.payload
        assert update_event.payload["operationType"] == "update" and update_event.payload["fullDocument"]["value"] == 2
        assert delete_event.payload["operationType"] == "delete"
        assert delete_event.payload["documentKey"] == {"_id": inserted.inserted_id}
    finally:
        await source.close(); await collection.drop(); await client.close()


@pytest.mark.asyncio
async def test_change_stream_resumes_after_acknowledged_token() -> None:
    name = f"resume_{uuid.uuid4().hex}"; state_key = f"state-{name}"; store = DurableTestStore()
    client = AsyncMongoClient(URI); database = client.get_default_database(); collection = database[name]
    connector = MongoDBConnector(URI, database=database.name, client=client)
    first_source = connector.watch_collection(name, state=store, state_key=state_key); await first_source.open()
    try:
        await collection.insert_one({"sequence": 1}); first = await _next_delivery(first_source); await first.ack(); await first_source.close()
        second_source = connector.watch_collection(name, state=store, state_key=state_key); await second_source.open()
        await collection.insert_one({"sequence": 2}); second = await _next_delivery(second_source)
        assert second.payload["fullDocument"]["sequence"] == 2
        await second.ack(); await second_source.close()
    finally:
        await collection.drop(); await client.close()
```

The shared end-to-end harness separately proves compatibility with a real durable
`postgres_cursor_store`; this plugin-local store deliberately survives source
recreation only within the test process.

- [ ] **Step 4: Write the operational README**

Include install, approved Python and strict production YAML examples, explicit
development-without-state behavior, production durable-state requirement, raw
change event example, `updateLookup`, Extended JSON, replica-set requirement,
polling delete/update limitations, acknowledged write concern, generation replay,
resume-token reset steps, insert `_id` idempotency, stable-key upsert semantics,
ordered/unordered partial writes, and all deferred features.

Include these statements verbatim:

```markdown
Polling and change streams can run with in-memory state for development, but that
state is lost on restart. Production restart guarantees require an explicit durable
`state` cursor-store resource. Without polling state, scanning starts from the
beginning; without a change-stream resume token, watching starts at the current
server position.

Change-stream deliveries contain the complete raw MongoDB change event. Update
streams request `full_document: updateLookup` by default. Project or reduce the
event in the application handler, not in YAML.
```

The reset runbook must tell operators to stop the worker, inspect the permanent
history-lost error, deliberately delete/reset only that source's `state_key`, and
restart knowing the stream begins at the current server position.

- [ ] **Step 5: Run all plugin-local validation and build**

```bash
uv run --project plugins/onestep-mongodb --extra test python -m pytest -q plugins/onestep-mongodb/tests -m "not integration"
uv build plugins/onestep-mongodb --out-dir /tmp/onestep-mongodb-dist --sdist --wheel --clear
uvx twine check /tmp/onestep-mongodb-dist/*
git diff --check
```

Expected: unit/runtime tests pass; wheel/sdist build; both artifacts pass metadata
checks; no whitespace errors.

- [ ] **Step 6: Run live tests when a replica set is available**

```bash
ONESTEP_MONGODB_URI='mongodb://127.0.0.1:27017/onestep?replicaSet=rs0' uv run --project plugins/onestep-mongodb --extra test python -m pytest -q plugins/onestep-mongodb/tests/integration -m integration
```

Expected: PASS against a replica set. A standalone MongoDB server is an invalid
test target. Forced primary stepdown remains a separate optional resilience profile.

- [ ] **Step 7: Commit runtime, live, and docs coverage**

```bash
git add plugins/onestep-mongodb
git commit -m "test(mongodb): add runtime and replica-set coverage"
```

## Plan Completion Gate

Run `git status --short` and `git log --oneline --max-count=8`.

Expected: this plan changed only `plugins/onestep-mongodb/**`; polling and change
streams share one tested generation/contiguous tracker; no implicit durable state
or shared root changes exist. Hand the stable package to the integration plan.
