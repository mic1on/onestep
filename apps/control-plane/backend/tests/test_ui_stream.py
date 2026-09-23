import asyncio
from datetime import UTC, datetime

import pytest
from onestep_control_plane_api.api.routers.ui_ws import ui_stream
from onestep_control_plane_api.api.schemas import UiStreamEvent
from onestep_control_plane_api.api.ui_event_stream import ui_event_stream_broker
from onestep_control_plane_api.auth.service import LocalAuthService
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
    with client.app.state.session_factory() as session:
        LocalAuthService(session).create_user(
            username="admin",
            password="secret-pass",
            role_names=["admin"],
        )

    response = client.get("/api/v1/ui/stream")

    assert response.status_code == 401
    assert response.json()["detail"] == "authentication required"


def test_ui_stream_disconnect_is_counted_when_the_client_goes_away(monkeypatch) -> None:
    """A completed stream teardown increments the disconnect counter.

    This is the emitting side of ``OneStepControlPlaneUiWsDisconnectSpike``
    (``increase(onestep_control_plane_ui_ws_disconnects_total[15m]) > 20``). The
    counter is incremented in the stream generator's ``finally``, which is the one
    place every exit path funnels through, so a client that simply goes away is
    counted rather than only a stream that raised.
    """

    from onestep_control_plane_api.ops import observability as obs

    class _DisconnectedRequest:
        async def is_disconnected(self) -> bool:
            return True

    async def _drive() -> None:
        response = await ui_stream(_DisconnectedRequest())
        try:
            async for _chunk in response.body_iterator:
                pass
        finally:
            await response.body_iterator.aclose()

    before = dict(
        obs.event_counter_snapshot()["onestep_control_plane_ui_ws_disconnects_total"]
    )

    asyncio.run(_drive())

    after = obs.event_counter_snapshot()[
        "onestep_control_plane_ui_ws_disconnects_total"
    ]
    assert after["client_closed"] == before["client_closed"] + 1


def test_ui_stream_disconnect_is_counted_as_error_when_the_stream_raises() -> None:
    """A stream that ends by raising is counted separately from a clean close.

    The reason label is what makes the alert diagnosable: a spike of ``error``
    teardowns points at the server or the broker, while ``client_closed`` points at
    clients or a proxy dropping idle connections. The failure is injected through
    the request object rather than by patching the recorder, so this exercises the
    production ``except`` branch instead of asserting on a stub.
    """

    from onestep_control_plane_api.ops import observability as obs

    class _ExplodingRequest:
        async def is_disconnected(self) -> bool:
            raise RuntimeError("transport gone")

    before = dict(
        obs.event_counter_snapshot()["onestep_control_plane_ui_ws_disconnects_total"]
    )

    async def _drive() -> None:
        response = await ui_stream(_ExplodingRequest())
        with pytest.raises(RuntimeError, match="transport gone"):
            await anext(response.body_iterator)
        # The generator is already closed by the raise; the ``finally`` ran with
        # reason="error" before the exception propagated.
        await response.body_iterator.aclose()

    asyncio.run(_drive())

    after = obs.event_counter_snapshot()["onestep_control_plane_ui_ws_disconnects_total"]
    assert after["error"] == before["error"] + 1
    assert after["client_closed"] == before["client_closed"]
