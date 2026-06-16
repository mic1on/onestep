from __future__ import annotations

from uuid import UUID

from onestep_control_plane_api.db.models import WorkerAgent, WorkerAgentSession
from sqlalchemy import select


def _register_worker_agent(client, worker_agent_registration_token) -> dict[str, object]:
    response = client.post(
        "/api/v1/worker-agents/register",
        json={
            "registration_token": worker_agent_registration_token,
            "display_name": "ws-agent",
            "execution_mode": "subprocess",
            "max_concurrent_deployments": 2,
            "capabilities": ["deployment.start", "deployment.stop"],
        },
    )
    assert response.status_code == 200
    return response.json()


def _hello_message(worker_agent_id: str) -> dict[str, object]:
    return {
        "type": "hello",
        "message_id": "msg_hello_1",
        "sent_at": "2026-06-16T09:00:00Z",
        "payload": {
            "protocol_version": "1",
            "worker_agent_id": worker_agent_id,
            "capabilities": ["deployment.start", "deployment.stop", "unknown.future"],
            "max_concurrent_deployments": 2,
            "used_slots": 0,
            "running_deployments": [],
        },
    }


def _heartbeat_message(worker_agent_id: str) -> dict[str, object]:
    return {
        "type": "heartbeat",
        "message_id": "msg_heartbeat_1",
        "sent_at": "2026-06-16T09:00:05Z",
        "payload": {
            "worker_agent_id": worker_agent_id,
            "used_slots": 1,
            "running_deployments": [],
            "recent_errors": [],
        },
    }


def test_worker_agent_ws_hello_heartbeat_and_disconnect(
    client,
    db_session,
    worker_agent_registration_token,
) -> None:
    registration = _register_worker_agent(client, worker_agent_registration_token)
    worker_agent_id = registration["worker_agent_id"]

    with client.websocket_connect(
        "/api/v1/worker-agents/ws",
        headers={"Authorization": f"Bearer {registration['connection_token']}"},
    ) as websocket:
        websocket.send_json(_hello_message(worker_agent_id))
        hello_ack = websocket.receive_json()

        assert hello_ack["type"] == "hello_ack"
        assert hello_ack["payload"]["session_id"].startswith("worker_sess_")
        assert hello_ack["payload"]["accepted_capabilities"] == [
            "deployment.start",
            "deployment.stop",
        ]

        websocket.send_json(_heartbeat_message(worker_agent_id))
        websocket.send_json(
            {
                "type": "command_ack",
                "message_id": "msg_ack_1",
                "sent_at": "2026-06-16T09:00:06Z",
                "payload": {"command_id": "unused"},
            }
        )
        unsupported = websocket.receive_json()
        assert unsupported["payload"]["code"] == "unsupported_message_type"

        db_session.expire_all()
        worker_agent = db_session.scalar(
            select(WorkerAgent).where(
                WorkerAgent.worker_agent_id == UUID(worker_agent_id)
            )
        )
        session = db_session.scalar(
            select(WorkerAgentSession).where(
                WorkerAgentSession.session_id == hello_ack["payload"]["session_id"]
            )
        )
        assert worker_agent is not None
        assert session is not None
        assert worker_agent.status == "online"
        assert worker_agent.used_slots == 1
        assert session.status == "active"

    db_session.expire_all()
    worker_agent = db_session.scalar(
        select(WorkerAgent).where(WorkerAgent.worker_agent_id == UUID(worker_agent_id))
    )
    session = db_session.scalar(
        select(WorkerAgentSession).where(
            WorkerAgentSession.session_id == hello_ack["payload"]["session_id"]
        )
    )
    assert worker_agent is not None
    assert session is not None
    assert worker_agent.status == "offline"
    assert session.status == "disconnected"


def test_worker_agent_ws_requires_hello_first(
    client,
    worker_agent_registration_token,
) -> None:
    registration = _register_worker_agent(client, worker_agent_registration_token)

    with client.websocket_connect(
        "/api/v1/worker-agents/ws",
        headers={"Authorization": f"Bearer {registration['connection_token']}"},
    ) as websocket:
        websocket.send_json(_heartbeat_message(registration["worker_agent_id"]))
        error = websocket.receive_json()

    assert error["type"] == "error"
    assert error["payload"]["code"] == "hello_required"
    assert error["payload"]["close_connection"] is True
