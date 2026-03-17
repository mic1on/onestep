from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from onestep_control_plane_api.api.agent_ingestion_service import ingest_heartbeat_request
from onestep_control_plane_api.api.schemas import HeartbeatIngestRequest
from onestep_control_plane_api.db.models import AgentCommand, AgentSession, Instance, Service, TaskDefinition


def make_service_payload(
    instance_id: str = "8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df",
) -> dict[str, str]:
    return {
        "name": "billing-sync",
        "environment": "prod",
        "node_name": "vm-prod-3",
        "instance_id": instance_id,
        "deployment_version": "1.0.0a0+c435c99",
    }


def make_sync_body(
    instance_id: str = "8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df",
) -> dict[str, object]:
    return {
        "service": make_service_payload(instance_id),
        "runtime": {
            "onestep_version": "1.0.0a0",
            "python_version": "3.11.14",
            "hostname": "vm-prod-3",
            "pid": 18231,
            "started_at": "2026-03-08T17:29:50Z",
        },
        "app": {
            "name": "billing-sync",
            "shutdown_timeout_s": 30.0,
            "topology_hash": "sha256:6d5b0d1f",
            "tasks": [
                {
                    "name": "sync_users",
                    "description": "Sync billing users into the warehouse.",
                    "source": {
                        "kind": "interval",
                        "name": "interval:3600s",
                        "config": {
                            "seconds": 3600,
                            "immediate": False,
                            "overlap": "skip",
                        },
                    },
                    "emit": [],
                    "concurrency": 16,
                    "timeout_s": 30.0,
                    "retry": {
                        "kind": "max_attempts",
                        "config": {
                            "max_attempts": 5,
                            "delay_s": 10,
                        },
                    },
                }
            ],
        },
        "sent_at": "2026-03-08T17:31:05Z",
        "sequence": 5,
    }


def _hello_message() -> dict[str, object]:
    return {
        "type": "hello",
        "message_id": "msg_hello_1",
        "sent_at": "2026-03-17T12:00:00Z",
        "payload": {
            "protocol_version": "1",
            "capabilities": [
                "telemetry.sync",
                "telemetry.heartbeat",
                "telemetry.metrics",
                "telemetry.events",
                "command.shutdown",
            ],
            "service": make_service_payload(),
            "runtime": {
                "onestep_version": "1.0.0a0",
                "python_version": "3.11.14",
                "hostname": "vm-prod-3",
                "pid": 18231,
                "started_at": "2026-03-08T17:29:50Z",
            },
        },
    }


def test_agent_ws_hello_establishes_session_and_returns_hello_ack(
    client,
    auth_headers,
    db_session,
) -> None:
    with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as websocket:
        websocket.send_json(_hello_message())
        response = websocket.receive_json()

    assert response["type"] == "hello_ack"
    assert response["payload"]["protocol_version"] == "1"
    assert response["payload"]["session_id"].startswith("sess_")
    assert response["payload"]["accepted_capabilities"] == [
        "telemetry.sync",
        "telemetry.heartbeat",
        "telemetry.metrics",
        "telemetry.events",
        "command.shutdown",
    ]

    db_session.expire_all()
    service = db_session.scalar(
        select(Service).where(Service.name == "billing-sync", Service.environment == "prod")
    )
    instance = db_session.scalar(
        select(Instance).where(
            Instance.instance_id == UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df")
        )
    )
    session = db_session.scalar(
        select(AgentSession).where(AgentSession.session_id == response["payload"]["session_id"])
    )

    assert service is not None
    assert instance is not None
    assert session is not None
    assert session.status == "disconnected"
    assert session.protocol_version == "1"
    assert session.capabilities_json == _hello_message()["payload"]["capabilities"]
    assert instance.onestep_version == "1.0.0a0"
    assert instance.started_at == datetime(2026, 3, 8, 17, 29, 50, tzinfo=UTC)


def test_agent_ws_telemetry_reuses_ingestion_logic(
    client,
    auth_headers,
    db_session,
) -> None:
    with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as websocket:
        websocket.send_json(_hello_message())
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "telemetry",
                "message_id": "msg_sync_1",
                "sent_at": "2026-03-17T12:00:01Z",
                "payload": {
                    "channel": "sync",
                    "body": make_sync_body(),
                },
            }
        )
        websocket.send_json(
            {
                "type": "telemetry",
                "message_id": "msg_heartbeat_1",
                "sent_at": "2026-03-17T12:00:02Z",
                "payload": {
                    "channel": "heartbeat",
                    "body": {
                        "service": make_service_payload(),
                        "runtime": {
                            "onestep_version": "1.0.0a0",
                            "python_version": "3.11.14",
                            "hostname": "vm-prod-3",
                            "pid": 18231,
                            "started_at": "2026-03-08T17:29:50Z",
                        },
                        "health": {
                            "status": "ok",
                            "uptime_s": 70,
                            "inflight_tasks": 3,
                        },
                        "sent_at": "2026-03-08T17:31:10Z",
                        "sequence": 6,
                    },
                },
            }
        )

    db_session.expire_all()
    instance = db_session.scalar(
        select(Instance).where(
            Instance.instance_id == UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df")
        )
    )
    task_definition = db_session.scalar(
        select(TaskDefinition).where(TaskDefinition.task_name == "sync_users")
    )

    assert instance is not None
    assert task_definition is not None
    assert instance.last_sync_sequence == 5
    assert instance.last_heartbeat_sequence == 6
    assert instance.last_topology_hash == "sha256:6d5b0d1f"
    assert instance.status == "ok"
    assert task_definition.topology_hash == "sha256:6d5b0d1f"
    assert task_definition.description == "Sync billing users into the warehouse."


def test_agent_ws_accepts_bearer_token_via_subprotocol(client) -> None:
    with client.websocket_connect(
        "/api/v1/agents/ws",
        subprotocols=["onestep-agent.v1", "bearer.test-ingest-token"],
    ) as websocket:
        websocket.send_json(_hello_message())
        response = websocket.receive_json()

    assert response["type"] == "hello_ack"


def test_agent_ws_dispatches_created_command_and_tracks_ack_result(
    client,
    auth_headers,
    db_session,
) -> None:
    with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as websocket:
        websocket.send_json(_hello_message())
        websocket.receive_json()

        create_response = client.post(
            "/api/v1/instances/8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df/commands",
            json={"kind": "ping", "args": {"nonce": "abc"}, "timeout_s": 10},
        )

        assert create_response.status_code == 200
        command_payload = websocket.receive_json()
        assert command_payload["type"] == "command"
        assert command_payload["payload"]["kind"] == "ping"
        command_id = command_payload["payload"]["command_id"]

        websocket.send_json(
            {
                "type": "command_ack",
                "message_id": "msg_ack_1",
                "sent_at": "2026-03-17T12:00:03Z",
                "payload": {
                    "command_id": command_id,
                    "status": "accepted",
                    "received_at": "2026-03-17T12:00:03Z",
                },
            }
        )
        websocket.send_json(
            {
                "type": "command_result",
                "message_id": "msg_result_1",
                "sent_at": "2026-03-17T12:00:04Z",
                "payload": {
                    "command_id": command_id,
                    "status": "succeeded",
                    "finished_at": "2026-03-17T12:00:04Z",
                    "result": {"ok": True},
                    "duration_ms": 4,
                },
            }
        )

    db_session.expire_all()
    command = db_session.scalar(
        select(AgentCommand).where(AgentCommand.command_id == command_id)
    )
    assert command is not None
    assert command.status == "succeeded"
    assert command.ack_status == "accepted"
    assert command.result_json == {"ok": True}
    assert command.dispatched_at is not None
    assert command.acked_at == datetime(2026, 3, 17, 12, 0, 3, tzinfo=UTC)
    assert command.finished_at == datetime(2026, 3, 17, 12, 0, 4, tzinfo=UTC)


def test_agent_ws_redelivers_pending_commands_after_hello(
    client,
    auth_headers,
    db_session,
) -> None:
    create_response = client.post(
        "/api/v1/instances/8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df/commands",
        json={"kind": "shutdown", "args": {}, "timeout_s": 15},
    )
    assert create_response.status_code == 404

    ingest_heartbeat_request(
        db_session,
        HeartbeatIngestRequest.model_validate(
            {
                "service": make_service_payload(),
                "runtime": {
                    "onestep_version": "1.0.0a0",
                    "python_version": "3.11.14",
                    "hostname": "vm-prod-3",
                    "pid": 18231,
                    "started_at": "2026-03-08T17:29:50Z",
                },
                "health": {
                    "status": "ok",
                    "uptime_s": 70,
                    "inflight_tasks": 3,
                },
                "sent_at": "2026-03-08T17:31:00Z",
                "sequence": 2,
            }
        ),
    )

    create_response = client.post(
        "/api/v1/instances/8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df/commands",
        json={"kind": "shutdown", "args": {}, "timeout_s": 15},
    )
    assert create_response.status_code == 200
    command_id = create_response.json()["command_id"]
    assert create_response.json()["status"] == "pending"

    with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as websocket:
        websocket.send_json(_hello_message())
        websocket.receive_json()
        command_payload = websocket.receive_json()

    assert command_payload["type"] == "command"
    assert command_payload["payload"]["command_id"] == command_id

    db_session.expire_all()
    command = db_session.scalar(
        select(AgentCommand).where(AgentCommand.command_id == command_id)
    )
    assert command is not None
    assert command.status == "dispatched"
    assert command.dispatched_at is not None
