from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from onestep import ConnectorErrorKind, ConnectorOperation, ConnectorOperationError, CursorStore, Delivery, Envelope, InMemoryCursorStore, Sink, Source

from .state_codec import decode_state, encode_state


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


class MongoDBPayloadError(ValueError):
    pass


class MongoDBConnector:
    def __init__(self, uri: str, *, database: str, client_options: Mapping[str, Any] | None = None, client: Any | None = None) -> None:
        if not uri or not database:
            raise ValueError("uri and database must not be empty")
        self.uri = uri
        self.database_name = database
        self.client_options = dict(client_options or {})
        self._client = client
        self._owns_client = client is None
        self._closed = False

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
        if self._closed:
            return
        self._closed = True
        if self._owns_client and self._client is not None:
            result = self._client.close()
            if hasattr(result, "__await__"):
                await result


class MongoDBPollingSource(Source):
    fetch_is_cancel_safe = False

    def __init__(self, *, connector: MongoDBConnector, collection: str, cursor: Sequence[str] = ("_id",), filter: Mapping[str, Any] | None = None, projection: Mapping[str, Any] | None = None, batch_size: int = 100, poll_interval_s: float = 1.0, state: CursorStore | None = None, state_key: str | None = None, initial_cursor: Sequence[Any] | None = None) -> None:
        super().__init__(f"mongodb.polling:{collection}")
        configured = tuple(cursor)
        if not configured or len(set(configured)) != len(configured):
            raise ValueError("cursor must be non-empty and unique")
        if "_id" in configured and configured[-1] != "_id":
            raise ValueError("_id must be the final cursor component")
        self.cursor = configured if configured[-1] == "_id" else (*configured, "_id")
        self.connector = connector
        self.collection_name = collection
        self.filter = dict(filter or {})
        self.projection = dict(projection or {}) or None
        self.batch_size = batch_size
        self.poll_interval_s = poll_interval_s
        self.state = state or InMemoryCursorStore()
        self.state_key = state_key or f"mongodb:{connector.database_name}:{collection}:poll:{','.join(self.cursor)}"
        self.initial_cursor = tuple(decode_state({"extended_json": list(initial_cursor)})) if initial_cursor is not None else None
        self._committed = None
        self._scan = None
        self._loaded = False
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
        if self.filter and cursor_query:
            return {"$and": [self.filter, cursor_query]}
        return dict(self.filter or cursor_query)

    async def _save(self, token: Any) -> None:
        self._committed = tuple(token)
        await self.state.save(self.state_key, encode_state(list(token)))

    async def open(self) -> None:
        if self._loaded:
            return
        loaded = await self.state.load(self.state_key)
        self._committed = tuple(decode_state(loaded)) if loaded is not None else self.initial_cursor
        self._scan = self._committed
        self._loaded = True

    async def fetch(self, limit: int) -> list[Delivery]:
        await self.open()
        if not self._tracker.can_fetch:
            return []
        collection = self.connector.collection(self.collection_name)
        try:
            self._active_cursor = collection.find(self._query(self._scan), self.projection).sort([(field, 1) for field in self.cursor]).limit(min(limit, self.batch_size))
            documents = [document async for document in self._active_cursor]
        except Exception as exc:
            from .resilience import classify_mongodb_error

            kind = classify_mongodb_error(exc, operation="fetch")
            if kind is None:
                raise
            raise ConnectorOperationError(backend="mongodb", operation=ConnectorOperation.FETCH, kind=kind, source_name=self.name, retry_delay_s=self.poll_interval_s, cause=exc) from exc
        finally:
            cursor = self._active_cursor
            self._active_cursor = None
            if cursor is not None and hasattr(cursor, "close"):
                result = cursor.close()
                if hasattr(result, "__await__"):
                    await result
        deliveries: list[Delivery] = []
        for document in documents:
            token = tuple(document[field] for field in self.cursor)
            self._scan = token
            tracked = self._tracker.add(token)
            deliveries.append(MongoDBPollingDelivery(self, Envelope(body=document, meta={"mongodb": {"database": self.connector.database_name, "collection": self.collection_name}}), tracked))
        return deliveries

    async def invalidate(self, tracked: _TrackedToken, *, delay_s: float | None = None) -> None:
        if delay_s:
            await asyncio.sleep(delay_s)
        await self._tracker.invalidate(tracked.generation)
        self._scan = self._committed

    async def close(self) -> None:
        cursor = self._active_cursor
        self._active_cursor = None
        if cursor is not None and hasattr(cursor, "close"):
            result = cursor.close()
            if hasattr(result, "__await__"):
                await result


class MongoDBPollingDelivery(Delivery):
    def __init__(self, source: MongoDBPollingSource, envelope: Envelope, tracked: _TrackedToken) -> None:
        super().__init__(envelope)
        self._source = source
        self._tracked = tracked
        self._terminal = False

    async def ack(self) -> None:
        if self._terminal:
            return
        self._terminal = True
        await self._source._tracker.complete(self._tracked, advance=True)

    async def retry(self, *, delay_s: float | None = None) -> None:
        if self._terminal:
            return
        self._terminal = True
        await self._source.invalidate(self._tracked, delay_s=delay_s)
        await self._source._tracker.complete(self._tracked, advance=False)

    async def fail(self, exc: Exception | None = None) -> None:
        if self._terminal:
            return
        self._terminal = True
        await self._source._tracker.complete(self._tracked, advance=True)

    async def release_unstarted(self) -> None:
        if self._terminal:
            return
        self._terminal = True
        await self._source.invalidate(self._tracked)
        await self._source._tracker.complete(self._tracked, advance=False)


class MongoDBChangeStreamSource(Source):
    async def fetch(self, limit: int) -> list[Delivery]:
        raise NotImplementedError


class MongoDBChangeStreamDelivery(MongoDBPollingDelivery):
    pass


class MongoDBCollectionSink(Sink):
    async def send(self, envelope) -> None:
        raise NotImplementedError
