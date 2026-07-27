from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from onestep import Delivery, Sink, Source


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
    def __init__(self, uri: str, *, database: str, client_options: dict[str, Any] | None = None, client: Any | None = None) -> None:
        self.uri = uri
        self.database_name = database
        self.client_options = dict(client_options or {})
        self._client = client


class MongoDBPollingSource(Source):
    async def fetch(self, limit: int) -> list[Delivery]:
        raise NotImplementedError


class MongoDBPollingDelivery(Delivery):
    async def ack(self) -> None:
        raise NotImplementedError

    async def retry(self, *, delay_s: float | None = None) -> None:
        raise NotImplementedError

    async def fail(self, exc: Exception | None = None) -> None:
        raise NotImplementedError


class MongoDBChangeStreamSource(Source):
    async def fetch(self, limit: int) -> list[Delivery]:
        raise NotImplementedError


class MongoDBChangeStreamDelivery(MongoDBPollingDelivery):
    pass


class MongoDBCollectionSink(Sink):
    async def send(self, envelope) -> None:
        raise NotImplementedError
