"""Authentication must finish its DB work before the long-lived socket opens."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack

import pytest
from onestep_control_plane_api.api.security import require_websocket_worker_agent_connection
from onestep_control_plane_api.db.session import get_db_session
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocket, WebSocketDisconnect
from test_worker_agent_ws import _hello_message, _register_worker_agent


def test_idle_sockets_release_auth_resources_before_hello(
    client, async_db, worker_agent_registration_token
):
    registration = _register_worker_agent(client, worker_agent_registration_token)
    previous = client.app.dependency_overrides[get_db_session]

    def forbid_sync_session():
        raise AssertionError("WebSocket authentication opened a lifetime sync session")
        yield  # make this a dependency with teardown, as in production

    client.app.dependency_overrides[get_db_session] = forbid_sync_session
    try:
        with ExitStack() as stack:
            sockets = [
                stack.enter_context(
                    client.websocket_connect(
                        "/api/v1/worker-agents/ws",
                        headers={"Authorization": f"Bearer {registration['connection_token']}"},
                    )
                )
                for _ in range(4)
            ]
            # No hello has been sent: this isolates authentication from message handling.
            assert async_db.checkouts >= 4
            assert async_db.open_sessions == []
            assert async_db.checkedout() == 0
            sockets[0].send_json(_hello_message(str(registration["worker_agent_id"])))
            assert sockets[0].receive_json()["type"] == "hello_ack"
        assert async_db.open_sessions == []
        assert async_db.checkedout() == 0
    finally:
        client.app.dependency_overrides[get_db_session] = previous


@pytest.mark.parametrize(
    "authorization,reason",
    [
        (None, "missing worker agent bearer token"),
        ("Bearer unknown", "invalid worker agent bearer token"),
    ],
)
def test_rejected_auth_releases_resources(client, async_db, authorization, reason):
    headers = {} if authorization is None else {"Authorization": authorization}
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/api/v1/worker-agents/ws", headers=headers):
            pytest.fail("unauthenticated WebSocket was accepted")
    assert exc.value.code == 1008
    assert exc.value.reason == reason
    assert async_db.open_sessions == []
    assert async_db.checkedout() == 0


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
def test_auth_failure_or_cancellation_releases_connection(async_db, monkeypatch, failure):
    original = AsyncSession.scalar

    async def fail_after_checkout(session, statement, *args, **kwargs):
        await original(session, statement, *args, **kwargs)
        raise failure("injected auth failure")

    monkeypatch.setattr(AsyncSession, "scalar", fail_after_checkout)

    async def unused(*args):
        raise AssertionError("authentication must not send or receive frames")

    websocket = WebSocket(
        {
            "type": "websocket",
            "headers": [(b"authorization", b"Bearer unknown")],
        },
        receive=unused,
        send=unused,
    )
    with pytest.raises(failure):
        asyncio.run(require_websocket_worker_agent_connection(websocket))
    assert async_db.checkouts > 0
    assert async_db.open_sessions == []
    assert async_db.checkedout() == 0
