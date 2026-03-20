import asyncio
from datetime import UTC, datetime

import pytest
from onestep_control_plane_api.api.routers.ui_ws import ui_stream
from onestep_control_plane_api.api.schemas import UiStreamEvent
from onestep_control_plane_api.api.ui_event_stream import ui_event_stream_broker
from onestep_control_plane_api.core.settings import settings


@pytest.fixture(autouse=True)
def restore_console_auth_settings():
    original_username = settings.console_auth_username
    original_password = settings.console_auth_password
    try:
        yield
    finally:
        settings.console_auth_username = original_username
        settings.console_auth_password = original_password


class _ConnectedRequest:
    async def is_disconnected(self) -> bool:
        return False


async def _read_first_stream_chunk() -> str:
    response = await ui_stream(_ConnectedRequest())
    try:
        chunk = await anext(response.body_iterator)
    finally:
        await response.body_iterator.aclose()
    return chunk.decode() if isinstance(chunk, bytes) else chunk


def test_ui_stream_emits_sse_data_frame(monkeypatch) -> None:
    queue: asyncio.Queue[UiStreamEvent] = asyncio.Queue()
    event = UiStreamEvent(
        channel="commands",
        published_at=datetime(2026, 3, 18, 9, 30, tzinfo=UTC),
    )
    queue.put_nowait(event)
    unsubscribed: list[str] = []

    async def fake_subscribe():
        return "ui-stream-test", queue

    async def fake_unsubscribe(subscriber_id: str) -> None:
        unsubscribed.append(subscriber_id)

    monkeypatch.setattr(ui_event_stream_broker, "subscribe", fake_subscribe)
    monkeypatch.setattr(ui_event_stream_broker, "unsubscribe", fake_unsubscribe)

    line = asyncio.run(_read_first_stream_chunk())

    assert line == f"data: {event.model_dump_json()}\n\n"
    assert unsubscribed == ["ui-stream-test"]


def test_ui_stream_requires_console_auth_when_configured(client) -> None:
    settings.console_auth_username = "admin"
    settings.console_auth_password = "secret-pass"

    response = client.get("/api/v1/ui/stream")

    assert response.status_code == 401
    assert response.json()["detail"] == "authentication required"
