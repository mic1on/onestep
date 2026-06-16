from __future__ import annotations

from uuid import UUID

from onestep_worker_agent.client import (
    _worker_agent_ws_url,
    build_heartbeat_message,
    build_hello_message,
)


def test_build_hello_message() -> None:
    message = build_hello_message(
        worker_agent_id=UUID("11111111-1111-4111-8111-111111111111"),
        max_concurrent_deployments=2,
        used_slots=1,
        running_deployments=["deployment-1"],
    )

    assert message["type"] == "hello"
    assert message["payload"]["protocol_version"] == "1"
    assert message["payload"]["worker_agent_id"] == "11111111-1111-4111-8111-111111111111"
    assert message["payload"]["used_slots"] == 1


def test_build_heartbeat_message() -> None:
    message = build_heartbeat_message(
        worker_agent_id=UUID("11111111-1111-4111-8111-111111111111"),
        used_slots=0,
        running_deployments=[],
    )

    assert message["type"] == "heartbeat"
    assert message["payload"]["running_deployments"] == []


def test_worker_agent_ws_url_uses_plane_host() -> None:
    assert _worker_agent_ws_url("http://localhost:8000") == (
        "ws://localhost:8000/api/v1/worker-agents/ws"
    )
    assert _worker_agent_ws_url("https://cp.example.com/base") == (
        "wss://cp.example.com/api/v1/worker-agents/ws"
    )
