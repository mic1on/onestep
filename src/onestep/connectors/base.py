from __future__ import annotations

import abc
from collections.abc import Mapping
from typing import Any

from onestep.envelope import Envelope


class Delivery(abc.ABC):
    def __init__(self, envelope: Envelope) -> None:
        self.envelope = envelope

    @property
    def payload(self) -> Any:
        return self.envelope.body

    async def start_processing(self) -> None:
        return None

    @abc.abstractmethod
    async def ack(self) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    async def retry(self, *, delay_s: float | None = None) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    async def fail(self, exc: Exception | None = None) -> None:
        raise NotImplementedError


class Source(abc.ABC):
    batch_size: int = 1
    poll_interval_s: float = 1.0

    def __init__(self, name: str) -> None:
        self.name = name

    async def open(self) -> None:
        return None

    @abc.abstractmethod
    async def fetch(self, limit: int) -> list[Delivery]:
        raise NotImplementedError

    async def close(self) -> None:
        return None


class Sink(abc.ABC):
    def __init__(self, name: str) -> None:
        self.name = name

    async def open(self) -> None:
        return None

    @abc.abstractmethod
    async def send(self, envelope: Envelope) -> None:
        raise NotImplementedError

    async def publish(
        self,
        body: Any,
        *,
        meta: Mapping[str, Any] | None = None,
        attempts: int = 0,
    ) -> None:
        await self.send(Envelope(body=body, meta=dict(meta or {}), attempts=attempts))

    async def close(self) -> None:
        return None
