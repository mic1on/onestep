from __future__ import annotations

import asyncio
from asyncio import Queue, QueueEmpty

from onestep.envelope import Envelope

from .base import Delivery, Sink, Source


class MemoryDelivery(Delivery):
    def __init__(self, queue: Queue, envelope: Envelope) -> None:
        super().__init__(envelope)
        self._queue = queue

    async def ack(self) -> None:
        return None

    async def retry(self, *, delay_s: float | None = None) -> None:
        if delay_s:
            await asyncio.sleep(delay_s)
        await self._queue.put(
            Envelope(
                body=self.envelope.body,
                meta=dict(self.envelope.meta),
                attempts=self.envelope.attempts + 1,
            )
        )

    async def fail(self, exc: Exception | None = None) -> None:
        return None


class MemoryQueue(Source, Sink):
    supports_manual_run = True

    def __init__(
        self,
        name: str,
        *,
        maxsize: int = 0,
        batch_size: int = 100,
        poll_interval_s: float = 0.1,
    ) -> None:
        Source.__init__(self, name)
        Sink.__init__(self, name)
        self._maxsize = maxsize
        self._queue: Queue | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.batch_size = batch_size
        self.poll_interval_s = poll_interval_s

    def _ensure_queue(self) -> Queue:
        current_loop = asyncio.get_running_loop()
        if self._queue is None or self._loop is not current_loop:
            self._queue = Queue(maxsize=self._maxsize)
            self._loop = current_loop
        return self._queue

    async def send(self, envelope: Envelope) -> None:
        queue = self._ensure_queue()
        await queue.put(envelope)

    async def fetch(self, limit: int) -> list[Delivery]:
        queue = self._ensure_queue()
        fetch_limit = max(1, min(limit, self.batch_size))
        try:
            first = await asyncio.wait_for(queue.get(), timeout=self.poll_interval_s)
        except asyncio.TimeoutError:
            return []

        deliveries: list[Delivery] = [MemoryDelivery(queue, first)]
        while len(deliveries) < fetch_limit:
            try:
                envelope = queue.get_nowait()
            except QueueEmpty:
                break
            deliveries.append(MemoryDelivery(queue, envelope))
        return deliveries

    def size(self) -> int:
        if self._queue is None:
            return 0
        return self._queue.qsize()
