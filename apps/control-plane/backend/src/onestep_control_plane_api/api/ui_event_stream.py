from __future__ import annotations

import asyncio
from uuid import uuid4

from onestep_control_plane_api.api.common import utcnow
from onestep_control_plane_api.api.schemas import UiStreamChannel, UiStreamEvent


class UiEventStreamBroker:
    def __init__(self) -> None:
        self._subscribers: dict[str, asyncio.Queue[UiStreamEvent]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self) -> tuple[str, asyncio.Queue[UiStreamEvent]]:
        subscriber_id = f"ui_stream_{uuid4().hex}"
        queue: asyncio.Queue[UiStreamEvent] = asyncio.Queue(maxsize=20)
        async with self._lock:
            self._subscribers[subscriber_id] = queue
        return subscriber_id, queue

    async def unsubscribe(self, subscriber_id: str) -> None:
        async with self._lock:
            self._subscribers.pop(subscriber_id, None)

    def publish(self, channel: UiStreamChannel) -> None:
        if not self._subscribers:
            return
        event = UiStreamEvent(channel=channel, published_at=utcnow())
        for queue in list(self._subscribers.values()):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                continue


ui_event_stream_broker = UiEventStreamBroker()


def publish_ui_stream_event(channel: UiStreamChannel) -> None:
    ui_event_stream_broker.publish(channel)
