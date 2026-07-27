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
