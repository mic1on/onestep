from __future__ import annotations

from typing import Any, Protocol


class CursorStore(Protocol):
    async def load(self, key: str) -> Any | None: ...

    async def save(self, key: str, value: Any) -> None: ...


class StateStore(CursorStore, Protocol):
    async def delete(self, key: str) -> None: ...


class InMemoryStateStore:
    def __init__(self) -> None:
        self._values: dict[str, Any] = {}

    async def load(self, key: str) -> Any | None:
        return self._values.get(key)

    async def save(self, key: str, value: Any) -> None:
        self._values[key] = value

    async def delete(self, key: str) -> None:
        self._values.pop(key, None)


class InMemoryCursorStore(InMemoryStateStore):
    pass


class ScopedState:
    def __init__(self, store: StateStore, namespace: str) -> None:
        self._store = store
        self._namespace = namespace

    def scope(self, namespace: str) -> "ScopedState":
        return ScopedState(self._store, self._qualify(namespace))

    async def get(self, key: str, default: Any | None = None) -> Any | None:
        value = await self.load(key)
        if value is None:
            return default
        return value

    async def load(self, key: str) -> Any | None:
        return await self._store.load(self._qualify(key))

    async def set(self, key: str, value: Any) -> None:
        await self.save(key, value)

    async def save(self, key: str, value: Any) -> None:
        await self._store.save(self._qualify(key), value)

    async def delete(self, key: str) -> None:
        await self._store.delete(self._qualify(key))

    def _qualify(self, key: str) -> str:
        if not key:
            raise ValueError("state key must not be empty")
        return f"{self._namespace}:{key}"
