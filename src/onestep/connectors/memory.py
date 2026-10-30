from __future__ import annotations

import asyncio
from asyncio import Queue, QueueEmpty
from collections import deque

from onestep.envelope import Envelope

from .base import Delivery, Sink, Source


class _MemoryBuffer(Queue[Envelope]):
    def __init__(self, maxsize: int) -> None:
        self._returned: deque[Envelope] = deque()
        super().__init__(maxsize=maxsize)

    def qsize(self) -> int:
        return len(self._returned) + super().qsize()

    def empty(self) -> bool:
        return not self._returned and super().empty()

    def _get(self) -> Envelope:
        if self._returned:
            return self._returned.popleft()
        return super()._get()

    def return_unstarted(self, envelope: Envelope) -> None:
        # Producers may have filled the freed slots already. Returning owned
        # work must not wait for capacity while all consumers are stopping.
        # qsize includes these items, so subsequent puts retain backpressure.
        self._returned.append(envelope)
        self._wakeup_next(self._getters)


class MemoryDelivery(Delivery):
    def __init__(self, queue: _MemoryBuffer, envelope: Envelope) -> None:
        super().__init__(envelope)
        self._queue = queue
        self._started = False
        self._released = False

    async def start_processing(self) -> None:
        self._started = True

    async def release_unstarted(self) -> None:
        if self._started or self._released:
            return
        self._queue.return_unstarted(self.envelope)
        self._released = True

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
        self._queue: _MemoryBuffer | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.batch_size = batch_size
        self.poll_interval_s = poll_interval_s

    def _ensure_queue(self) -> _MemoryBuffer:
        current_loop = asyncio.get_running_loop()
        if self._queue is None or self._loop is not current_loop:
            self._queue = _MemoryBuffer(maxsize=self._maxsize)
            self._loop = current_loop
        return self._queue

    async def send(self, envelope: Envelope) -> None:
        queue = self._ensure_queue()
        await queue.put(envelope)

    async def fetch(self, limit: int) -> list[Delivery]:
        queue = self._ensure_queue()
        fetch_limit = max(1, min(limit, self.batch_size))
        get_task = asyncio.create_task(queue.get())
        try:
            # Own cancellation explicitly: on Python 3.12 wait_for directly
            # awaits its input, so cancelling fetch can otherwise cancel a get
            # that has already removed an envelope but has not returned yet.
            first = await asyncio.wait_for(
                asyncio.shield(get_task), timeout=self.poll_interval_s
            )
        except (asyncio.CancelledError, asyncio.TimeoutError) as exc:
            get_task.cancel()
            await asyncio.gather(get_task, return_exceptions=True)
            # get can finish before cancellation or return a result despite it.
            # Settle it before returning so no later consumer loses that item.
            if not get_task.cancelled():
                queue.return_unstarted(get_task.result())
            if isinstance(exc, asyncio.CancelledError):
                raise
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
