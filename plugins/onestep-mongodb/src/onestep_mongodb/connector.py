from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from onestep import (
    ConnectorErrorKind,
    ConnectorOperation,
    ConnectorOperationError,
    CursorStore,
    Delivery,
    Envelope,
    InMemoryCursorStore,
    Sink,
    Source,
)

from .state_codec import decode_state, encode_state


def _positive_integer(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    if value <= 0:
        raise ValueError(f"{field} must be positive")
    return value


def _non_negative_number(value: Any, *, field: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a number")
    if value < 0:
        raise ValueError(f"{field} must be non-negative")
    return value


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

    def includes_unchanged(value: Any) -> bool:
        return value is True or (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and value == 1
        )

    excluded = {key for key, value in projection.items() if excludes(value)}
    has_inclusion = any(not excludes(value) for value in projection.values())
    missing = [
        field
        for field in cursor
        if field in excluded
        or (
            has_inclusion
            and field != "_id"
            and not includes_unchanged(projection.get(field))
        )
        or (
            has_inclusion
            and field == "_id"
            and field in projection
            and not includes_unchanged(projection[field])
        )
    ]
    if missing:
        fields = ", ".join(dict.fromkeys(missing))
        raise ValueError(f"projection must preserve cursor fields: {fields}")


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
                for item in self._pending:
                    if item.generation == generation:
                        item.advances = False
                self.generation += 1

    def _discard_settled_generations(self) -> None:
        settled = [
            generation
            for generation, count in self._outstanding.items()
            if count == 0
        ]
        for generation in settled:
            self._outstanding.pop(generation, None)
            self._invalidated.discard(generation)

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
            except BaseException:
                tracked.completed, tracked.advances = previous
                raise

            if not previous[0]:
                self._outstanding[tracked.generation] = max(
                    0, self._outstanding.get(tracked.generation, 1) - 1
                )
            for _ in prefix:
                self._pending.popleft()
            self._discard_settled_generations()


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

    def _secret_tokens(self) -> list[str]:
        """Secret-bearing config tokens used to scrub error messages."""
        from .resilience import collect_sensitive_tokens
        return collect_sensitive_tokens(self.uri, self.client_options)

    def _get_client(self):
        if self._client is None:
            from pymongo import AsyncMongoClient

            self._client = AsyncMongoClient(self.uri, **self.client_options)
        return self._client

    def collection(self, name: str):
        try:
            return self._get_client()[self.database_name][name]
        except ConnectorOperationError:
            raise
        except Exception as exc:
            from .resilience import classify_mongodb_error, redacted_mongodb_cause

            kind = classify_mongodb_error(exc, operation="open")
            if kind is None:
                raise
            cause = redacted_mongodb_cause(exc, secrets=self._secret_tokens())
            raise ConnectorOperationError(
                backend="mongodb",
                operation=ConnectorOperation.OPEN,
                kind=kind,
                cause=cause,
            ) from None

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
        _validate_cursor_projection(self.projection, self.cursor)
        self.batch_size = _positive_integer(batch_size, field="batch_size")
        self.poll_interval_s = _non_negative_number(
            poll_interval_s, field="poll_interval_s"
        )
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
        await self.state.save(self.state_key, encode_state(list(token)))
        self._committed = tuple(token)

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
            from .resilience import classify_mongodb_error, redacted_mongodb_cause

            kind = classify_mongodb_error(exc, operation="fetch")
            if kind is None:
                raise
            cause = redacted_mongodb_cause(exc, secrets=self.connector._secret_tokens())
            raise ConnectorOperationError(backend="mongodb", operation=ConnectorOperation.FETCH, kind=kind, source_name=self.name, retry_delay_s=self.poll_interval_s, cause=cause) from None
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
        await self._source._tracker.complete(self._tracked, advance=True)
        self._terminal = True

    async def retry(self, *, delay_s: float | None = None) -> None:
        if self._terminal:
            return
        await self._source.invalidate(self._tracked, delay_s=delay_s)
        await self._source._tracker.complete(self._tracked, advance=False)
        self._terminal = True

    async def fail(self, exc: Exception | None = None) -> None:
        if self._terminal:
            return
        await self._source._tracker.complete(self._tracked, advance=True)
        self._terminal = True

    async def release_unstarted(self) -> None:
        if self._terminal:
            return
        await self._source.invalidate(self._tracked)
        await self._source._tracker.complete(self._tracked, advance=False)
        self._terminal = True


class MongoDBChangeStreamSource(Source):
    fetch_is_cancel_safe = False

    def __init__(self, *, connector: MongoDBConnector, collection: str, pipeline: Sequence[Mapping[str, Any]] | None = None, full_document: str = "updateLookup", max_await_time_ms: int = 1000, batch_size: int = 100, poll_interval_s: float = 0.1, state: CursorStore | None = None, state_key: str | None = None) -> None:
        super().__init__(f"mongodb.change_stream:{collection}")
        self.connector = connector
        self.collection_name = collection
        self.pipeline = [dict(stage) for stage in (pipeline or [])]
        self.full_document = full_document
        self.max_await_time_ms = _positive_integer(
            max_await_time_ms, field="max_await_time_ms"
        )
        self.batch_size = _positive_integer(batch_size, field="batch_size")
        self.poll_interval_s = _non_negative_number(
            poll_interval_s, field="poll_interval_s"
        )
        self.state = state or InMemoryCursorStore()
        self.state_key = state_key or f"mongodb:{connector.database_name}:{collection}:change-stream"
        self._resume_token = None
        self._loaded = False
        self._stream = None
        self._tracker = _ContiguousGenerationTracker(self._save)

    async def _save(self, token: Any) -> None:
        await self.state.save(self.state_key, encode_state(token))
        self._resume_token = token

    async def open(self) -> None:
        if not self._loaded:
            loaded = await self.state.load(self.state_key)
            self._resume_token = decode_state(loaded) if loaded is not None else None
            self._loaded = True
        if self._stream is None and self._tracker.can_fetch:
            options = {"full_document": self.full_document, "max_await_time_ms": self.max_await_time_ms}
            if self._resume_token is not None:
                options["resume_after"] = self._resume_token
            try:
                self._stream = await self.connector.collection(self.collection_name).watch(self.pipeline, **options)
            except ConnectorOperationError:
                raise
            except Exception as exc:
                from .resilience import classify_mongodb_error, redacted_mongodb_cause

                kind = classify_mongodb_error(exc, operation="open")
                if kind is None:
                    raise
                cause = redacted_mongodb_cause(exc, secrets=self.connector._secret_tokens())
                raise ConnectorOperationError(
                    backend="mongodb",
                    operation=ConnectorOperation.OPEN,
                    kind=kind,
                    source_name=self.name,
                    cause=cause,
                ) from None

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
            from .resilience import classify_mongodb_error, redacted_mongodb_cause

            await self._tracker.invalidate(self._tracker.generation)
            stream = self._stream
            self._stream = None
            if stream is not None:
                # Keep a classified close failure from replacing the primary error.
                try:
                    await stream.close()
                except Exception as close_exc:
                    if classify_mongodb_error(close_exc, operation="close") is None:
                        raise
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
            cause = redacted_mongodb_cause(exc, secrets=self.connector._secret_tokens())
            raise ConnectorOperationError(
                backend="mongodb",
                operation=ConnectorOperation.FETCH,
                kind=kind,
                source_name=self.name,
                cause=cause,
                message=message,
            ) from None

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

    async def invalidate(self, tracked: _TrackedToken, *, delay_s: float | None = None) -> None:
        if delay_s:
            await asyncio.sleep(delay_s)
        await self._tracker.invalidate(tracked.generation)
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                await stream.close()
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

    async def close(self) -> None:
        if self._stream is not None:
            await self._stream.close()
            self._stream = None


class MongoDBChangeStreamDelivery(Delivery):
    def __init__(self, source: MongoDBChangeStreamSource, envelope: Envelope, tracked: _TrackedToken) -> None:
        super().__init__(envelope)
        self._source = source
        self._tracked = tracked
        self._terminal = False

    async def ack(self) -> None:
        if self._terminal:
            return
        await self._source._tracker.complete(self._tracked, advance=True)
        self._terminal = True

    async def retry(self, *, delay_s: float | None = None) -> None:
        if self._terminal:
            return
        await self._source.invalidate(self._tracked, delay_s=delay_s)
        await self._source._tracker.complete(self._tracked, advance=False)
        self._terminal = True

    async def fail(self, exc: Exception | None = None) -> None:
        if self._terminal:
            return
        await self._source._tracker.complete(self._tracked, advance=True)
        self._terminal = True

    async def release_unstarted(self) -> None:
        if self._terminal:
            return
        await self._source.invalidate(self._tracked)
        await self._source._tracker.complete(self._tracked, advance=False)
        self._terminal = True


class MongoDBCollectionSink(Sink):
    def __init__(self, *, connector: MongoDBConnector, collection: str, mode: str = "insert", keys: Sequence[str] = (), ordered: bool = True, batch_size: int = 1000) -> None:
        super().__init__(f"mongodb.collection:{collection}")
        if mode not in {"insert", "upsert"}:
            raise ValueError("mode must be insert or upsert")
        if mode == "upsert" and not keys:
            raise ValueError("upsert mode requires keys")
        self.connector = connector
        self.collection_name = collection
        self.mode = mode
        self.keys = tuple(keys)
        self.ordered = ordered
        self.batch_size = _positive_integer(batch_size, field="batch_size")

    def _documents(self, body: Any) -> list[dict[str, Any]]:
        if isinstance(body, Mapping):
            return [dict(body)]
        if not isinstance(body, Sequence) or isinstance(body, (str, bytes, bytearray)) or not body:
            raise MongoDBPayloadError("payload must be a mapping or non-empty sequence")
        if any(not isinstance(item, Mapping) for item in body):
            raise MongoDBPayloadError("every payload item must be a mapping")
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
            raise ConnectorOperationError(backend="mongodb", operation=ConnectorOperation.SEND, kind=ConnectorErrorKind.PERMANENT, source_name=self.name, cause=exc) from None
        committed = 0
        try:
            for start in range(0, len(documents), self.batch_size):
                chunk = documents[start : start + self.batch_size]
                if self.mode == "insert":
                    if single_document:
                        await collection.insert_one(chunk[0])
                    else:
                        await collection.insert_many(chunk, ordered=self.ordered)
                else:
                    operations = []
                    for document in chunk:
                        selector = {key: document[key] for key in self.keys}
                        update = {key: value for key, value in document.items() if key not in self.keys and key != "_id"}
                        operations.append(UpdateOne(selector, {"$set": update}, upsert=True))
                    await collection.bulk_write(operations, ordered=self.ordered)
                committed += 1
        except Exception as exc:
            from .resilience import classify_mongodb_error, redacted_mongodb_cause

            kind = classify_mongodb_error(exc, operation="send")
            if kind is None:
                raise
            cause = redacted_mongodb_cause(exc, secrets=self.connector._secret_tokens())
            replay_safe = self.mode == "upsert" and bool(self.keys)
            partial = committed > 0 or cause.committed_count > 0
            if partial and not replay_safe:
                kind = ConnectorErrorKind.UNCERTAIN
            raise ConnectorOperationError(backend="mongodb", operation=ConnectorOperation.SEND, kind=kind, source_name=self.name, cause=cause) from None
