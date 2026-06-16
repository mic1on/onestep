from __future__ import annotations

from uuid import UUID, uuid4

from onestep_control_plane_api.db.models import (
    WorkerAgent,
    WorkerAgentCommand,
    WorkerAgentSession,
)
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


def _upload_workflow_package(client) -> dict[str, object]:
    response = client.post(
        "/api/v1/workflow-packages",
        params={
            "workflow_id": str(uuid4()),
            "version": "2026.06.16",
            "filename": "workflow.zip",
            "entrypoint": "worker.yaml",
        },
        headers={"content-type": "application/zip"},
        content=b"PK\x03\x04fake workflow package",
    )
    assert response.status_code == 200
    return response.json()


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
        assert unsupported["payload"]["code"] == "invalid_command_ack"

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


def test_worker_agent_ws_receives_start_deployment_and_records_ack(
    client,
    db_session,
    worker_agent_registration_token,
) -> None:
    registration = _register_worker_agent(client, worker_agent_registration_token)
    package = _upload_workflow_package(client)
    worker_agent_id = registration["worker_agent_id"]

    with client.websocket_connect(
        "/api/v1/worker-agents/ws",
        headers={"Authorization": f"Bearer {registration['connection_token']}"},
    ) as websocket:
        websocket.send_json(_hello_message(worker_agent_id))
        websocket.receive_json()

        deployment_response = client.post(
            "/api/v1/worker-deployments",
            json={
                "workflow_package_id": package["package_id"],
                "worker_agent_id": worker_agent_id,
            },
        )
        assert deployment_response.status_code == 200
        deployment = deployment_response.json()

        command_message = websocket.receive_json()
        assert command_message["type"] == "command"
        assert command_message["payload"]["kind"] == "start_deployment"
        assert command_message["payload"]["deployment_id"] == deployment["deployment_id"]
        assert command_message["payload"]["args"]["package_id"] == package["package_id"]
        assert command_message["payload"]["args"]["download_url"] == (
            f"/api/v1/workflow-packages/{package['package_id']}/download"
        )

        command_id = command_message["payload"]["command_id"]
        websocket.send_json(
            {
                "type": "command_ack",
                "message_id": "msg_ack_1",
                "sent_at": "2026-06-16T09:00:10Z",
                "payload": {
                    "command_id": command_id,
                    "status": "rejected",
                    "error_code": "unsupported_command",
                    "error_message": "not implemented yet",
                },
            }
        )
        websocket.send_json(
            {
                "type": "command_result",
                "message_id": "msg_result_unknown",
                "sent_at": "2026-06-16T09:00:11Z",
                "payload": {
                    "command_id": str(uuid4()),
                    "status": "failed",
                    "error_code": "unknown",
                    "error_message": "unknown",
                    "finished_at": "2026-06-16T09:00:11Z",
                },
            }
        )
        error = websocket.receive_json()
        assert error["payload"]["code"] == "unknown_command"

        db_session.expire_all()
        command = db_session.scalar(
            select(WorkerAgentCommand).where(
                WorkerAgentCommand.command_id == UUID(command_id)
            )
        )
        assert command is not None
        assert command.status == "rejected"
        assert command.ack_status == "rejected"
        assert command.error_code == "unsupported_command"
        assert command.deployment_id == UUID(deployment["deployment_id"])


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
