from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from onestep_control_plane_api.api.security import require_console_auth
from onestep_control_plane_api.api.ui_event_stream import ui_event_stream_broker

router = APIRouter(
    prefix="/api/v1/ui",
    tags=["ui-stream"],
    dependencies=[Depends(require_console_auth)],
)


@router.get("/stream")
async def ui_stream(request: Request) -> StreamingResponse:
    subscriber_id, queue = await ui_event_stream_broker.subscribe()

    async def event_source():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {event.model_dump_json()}\n\n"
        finally:
            await ui_event_stream_broker.unsubscribe(subscriber_id)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
