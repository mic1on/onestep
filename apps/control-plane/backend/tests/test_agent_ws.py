import asyncio
from datetime import UTC, datetime
from uuid import UUID

from onestep_control_plane_api.api.agent_ingestion_service import ingest_heartbeat_request
from onestep_control_plane_api.api.schemas import HeartbeatIngestRequest
from onestep_control_plane_api.api.ui_event_stream import ui_event_stream_broker
from onestep_control_plane_api.db.models import (
    AgentCommand,
    AgentSession,
    Instance,
    Service,
    TaskDefinition,
)
from sqlalchemy import select


def _subscribe_ui_stream():
    subscriber_id = f"test_ui_stream_{len(ui_event_stream_broker._subscribers) + 1}"
    queue: asyncio.Queue = asyncio.Queue()
    ui_event_stream_broker._subscribers[subscriber_id] = queue
    return subscriber_id, queue


def _unsubscribe_ui_stream(subscriber_id: str) -> None:
    ui_event_stream_broker._subscribers.pop(subscriber_id, None)


def _drain_ui_stream_channels(queue: asyncio.Queue) -> list[str]:
    channels: list[str] = []
    while True:
        try:
            channels.append(queue.get_nowait().channel)
        except asyncio.QueueEmpty:
            return channels


def make_service_payload(
    instance_id: str = "8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df",
    node_name: str = "vm-prod-3",
) -> dict[str, str]:
    return {
        "name": "billing-sync",
        "environment": "prod",
        "node_name": node_name,
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


def _hello_message(
    *,
    capabilities: list[str] | None = None,
    instance_id: str = "8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df",
    node_name: str = "vm-prod-3",
) -> dict[str, object]:
    return {
        "type": "hello",
        "message_id": "msg_hello_1",
        "sent_at": "2026-03-17T12:00:00Z",
        "payload": {
            "protocol_version": "1",
            "capabilities": capabilities
            or [
                "telemetry.sync",
                "telemetry.heartbeat",
                "telemetry.metrics",
                "telemetry.events",
                "command.shutdown",
                "command.restart",
                "command.drain",
                "command.pause_task",
                "command.resume_task",
                "command.discard_dead_letters",
                "command.replay_dead_letters",
                "command.ping",
                "command.sync_now",
                "command.flush_metrics",
                "command.flush_events",
            ],
            "service": make_service_payload(instance_id=instance_id, node_name=node_name),
            "runtime": {
                "onestep_version": "1.0.0a0",
                "python_version": "3.11.14",
                "hostname": node_name,
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
        "command.restart",
        "command.drain",
        "command.pause_task",
        "command.resume_task",
        "command.discard_dead_letters",
        "command.replay_dead_letters",
        "command.ping",
        "command.sync_now",
        "command.flush_metrics",
        "command.flush_events",
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


def test_agent_ws_restart_reuses_instance_identity_and_accepts_continued_sequences(
    client,
    auth_headers,
    db_session,
) -> None:
    instance_id = "8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df"

    first_sync_body = make_sync_body(instance_id)
    first_sync_body["sequence"] = 1
    first_sync_body["sent_at"] = "2026-03-08T17:31:05Z"

    second_sync_body = make_sync_body(instance_id)
    second_sync_body["sequence"] = 2
    second_sync_body["runtime"]["pid"] = 18232
    second_sync_body["runtime"]["started_at"] = "2026-03-08T18:00:00Z"
    second_sync_body["sent_at"] = "2026-03-08T18:00:05Z"

    with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as websocket:
        websocket.send_json(_hello_message(instance_id=instance_id))
        first_hello_ack = websocket.receive_json()
        websocket.send_json(
            {
                "type": "telemetry",
                "message_id": "msg_restart_sync_1",
                "sent_at": "2026-03-17T12:00:01Z",
                "payload": {
                    "channel": "sync",
                    "body": first_sync_body,
                },
            }
        )
        websocket.send_json(
            {
                "type": "telemetry",
                "message_id": "msg_restart_heartbeat_1",
                "sent_at": "2026-03-17T12:00:02Z",
                "payload": {
                    "channel": "heartbeat",
                    "body": {
                        "service": make_service_payload(instance_id),
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
                        "sequence": 1,
                    },
                },
            }
        )

    with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as websocket:
        websocket.send_json(_hello_message(instance_id=instance_id))
        second_hello_ack = websocket.receive_json()
        websocket.send_json(
            {
                "type": "telemetry",
                "message_id": "msg_restart_sync_2",
                "sent_at": "2026-03-17T12:05:01Z",
                "payload": {
                    "channel": "sync",
                    "body": second_sync_body,
                },
            }
        )
        websocket.send_json(
            {
                "type": "telemetry",
                "message_id": "msg_restart_heartbeat_2",
                "sent_at": "2026-03-17T12:05:02Z",
                "payload": {
                    "channel": "heartbeat",
                    "body": {
                        "service": make_service_payload(instance_id),
                        "runtime": {
                            "onestep_version": "1.0.0a0",
                            "python_version": "3.11.14",
                            "hostname": "vm-prod-3",
                            "pid": 18232,
                            "started_at": "2026-03-08T18:00:00Z",
                        },
                        "health": {
                            "status": "ok",
                            "uptime_s": 10,
                            "inflight_tasks": 1,
                        },
                        "sent_at": "2026-03-08T18:00:10Z",
                        "sequence": 2,
                    },
                },
            }
        )

    db_session.expire_all()
    instances = db_session.scalars(
        select(Instance).where(Instance.instance_id == UUID(instance_id))
    ).all()
    sessions = db_session.scalars(
        select(AgentSession)
        .where(AgentSession.instance_id == UUID(instance_id))
        .order_by(AgentSession.connected_at.asc(), AgentSession.session_id.asc())
    ).all()

    assert len(instances) == 1
    assert len(sessions) == 2
    assert first_hello_ack["payload"]["session_id"] != second_hello_ack["payload"]["session_id"]

    instance = instances[0]
    assert instance.last_sync_sequence == 2
    assert instance.last_heartbeat_sequence == 2
    assert instance.pid == 18232
    assert instance.started_at == datetime(2026, 3, 8, 18, 0, 0, tzinfo=UTC)


def test_agent_ws_accepts_bearer_token_via_subprotocol(client) -> None:
    with client.websocket_connect(
        "/api/v1/agents/ws",
        subprotocols=["onestep-agent.v1", "bearer.test-ingest-token"],
    ) as websocket:
        websocket.send_json(_hello_message())
        response = websocket.receive_json()

    assert response["type"] == "hello_ack"


def test_agent_ws_accepts_worker_agent_connection_token(
    client,
    worker_agent_registration_token,
) -> None:
    registration = client.post(
        "/api/v1/worker-agents/register",
        json={
            "registration_token": worker_agent_registration_token,
            "display_name": "runtime-host",
            "execution_mode": "subprocess",
            "max_concurrent_deployments": 1,
            "capabilities": ["deployment.start"],
        },
    )
    assert registration.status_code == 200

    with client.websocket_connect(
        "/api/v1/agents/ws",
        headers={"Authorization": f"Bearer {registration.json()['connection_token']}"},
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
    assert command.duration_ms == 4
    assert command.dispatched_at is not None
    assert command.acked_at == datetime(2026, 3, 17, 12, 0, 3, tzinfo=UTC)
    assert command.finished_at == datetime(2026, 3, 17, 12, 0, 4, tzinfo=UTC)

    list_response = client.get(
        "/api/v1/instances/8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df/commands"
    )
    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["duration_ms"] == 4


def test_agent_ws_dispatches_restart_command_with_structured_result(
    client,
    auth_headers,
    db_session,
) -> None:
    with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as websocket:
        websocket.send_json(
            _hello_message(
                capabilities=[
                    "telemetry.sync",
                    "telemetry.heartbeat",
                    "command.restart",
                ]
            )
        )
        websocket.receive_json()

        create_response = client.post(
            "/api/v1/instances/8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df/commands",
            json={
                "kind": "restart",
                "args": {},
                "timeout_s": 30,
                "reason": "Rotate process after dependency deadlock",
            },
        )

        assert create_response.status_code == 200
        command_payload = websocket.receive_json()
        assert command_payload["type"] == "command"
        assert command_payload["payload"]["kind"] == "restart"
        command_id = command_payload["payload"]["command_id"]

        websocket.send_json(
            {
                "type": "command_ack",
                "message_id": "msg_ack_restart",
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
                "message_id": "msg_result_restart",
                "sent_at": "2026-03-17T12:00:04Z",
                "payload": {
                    "command_id": command_id,
                    "status": "succeeded",
                    "finished_at": "2026-03-17T12:00:04Z",
                    "result": {
                        "operation": "restart",
                        "requested": True,
                        "completion": "partial",
                        "restart_requested": True,
                        "shutdown_requested": True,
                        "supervisor_handoff_required": True,
                    },
                    "duration_ms": 5,
                },
            }
        )

    db_session.expire_all()
    command = db_session.scalar(
        select(AgentCommand).where(AgentCommand.command_id == command_id)
    )
    assert command is not None
    assert command.kind == "restart"
    assert command.status == "succeeded"
    assert command.result_json == {
        "operation": "restart",
        "requested": True,
        "completion": "partial",
        "restart_requested": True,
        "shutdown_requested": True,
        "supervisor_handoff_required": True,
    }


def test_agent_ws_dispatches_pause_task_command_with_structured_result(
    client,
    auth_headers,
    db_session,
) -> None:
    with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as websocket:
        websocket.send_json(
            _hello_message(
                capabilities=[
                    "telemetry.sync",
                    "telemetry.heartbeat",
                    "command.pause_task",
                ]
            )
        )
        websocket.receive_json()
        instance = db_session.scalar(
            select(Instance).where(
                Instance.instance_id == UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df")
            )
        )
        assert instance is not None
        instance.app_snapshot_json = {
            "name": "billing-sync",
            "task_control_states": [
                {
                    "task_name": "sync_users",
                    "supported_commands": ["pause_task"],
                    "pause_requested": False,
                    "paused": False,
                    "accepting_new_work": True,
                    "runner_count": 1,
                    "parked_runner_count": 0,
                    "fetching_runner_count": 1,
                    "inflight_task_count": 0,
                }
            ],
        }
        db_session.commit()

        create_response = client.post(
            "/api/v1/instances/8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df/commands",
            json={
                "kind": "pause_task",
                "args": {"task_name": "sync_users"},
                "timeout_s": 120,
                "reason": "Pause intake during upstream data correction",
            },
        )

        assert create_response.status_code == 200
        command_payload = websocket.receive_json()
        assert command_payload["type"] == "command"
        assert command_payload["payload"]["kind"] == "pause_task"
        assert command_payload["payload"]["args"] == {"task_name": "sync_users"}
        command_id = command_payload["payload"]["command_id"]

        websocket.send_json(
            {
                "type": "command_ack",
                "message_id": "msg_ack_pause_task",
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
                "message_id": "msg_result_pause_task",
                "sent_at": "2026-03-17T12:00:04Z",
                "payload": {
                    "command_id": command_id,
                    "status": "succeeded",
                    "finished_at": "2026-03-17T12:00:04Z",
                    "result": {
                        "operation": "pause_task",
                        "task_name": "sync_users",
                        "requested": True,
                        "completion": "complete",
                        "paused": True,
                        "accepting_new_work": False,
                        "runner_count": 1,
                        "parked_runner_count": 1,
                        "fetching_runner_count": 0,
                        "inflight_task_count": 0,
                    },
                    "duration_ms": 6,
                },
            }
        )

    db_session.expire_all()
    command = db_session.scalar(
        select(AgentCommand).where(AgentCommand.command_id == command_id)
    )
    assert command is not None
    assert command.kind == "pause_task"
    assert command.status == "succeeded"
    assert command.result_json == {
        "operation": "pause_task",
        "task_name": "sync_users",
        "requested": True,
        "completion": "complete",
        "paused": True,
        "accepting_new_work": False,
        "runner_count": 1,
        "parked_runner_count": 1,
        "fetching_runner_count": 0,
        "inflight_task_count": 0,
    }


def test_agent_ws_dispatches_replay_dead_letters_command_with_structured_result(
    client,
    auth_headers,
    db_session,
) -> None:
    with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as websocket:
        websocket.send_json(
            _hello_message(
                capabilities=[
                    "telemetry.sync",
                    "telemetry.heartbeat",
                    "command.replay_dead_letters",
                ]
            )
        )
        websocket.receive_json()
        instance = db_session.scalar(
            select(Instance).where(
                Instance.instance_id == UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df")
            )
        )
        assert instance is not None
        instance.app_snapshot_json = {
            "name": "billing-sync",
            "task_control_states": [
                {
                    "task_name": "sync_users",
                    "supported_commands": ["pause_task", "resume_task", "replay_dead_letters"],
                    "pause_requested": False,
                    "paused": False,
                    "accepting_new_work": True,
                    "runner_count": 1,
                    "parked_runner_count": 0,
                    "fetching_runner_count": 1,
                    "inflight_task_count": 0,
                }
            ],
        }
        db_session.commit()

        create_response = client.post(
            "/api/v1/instances/8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df/commands",
            json={
                "kind": "replay_dead_letters",
                "args": {"task_name": "sync_users", "limit": 5},
                "timeout_s": 120,
                "reason": "Replay dead-lettered work after upstream fix",
            },
        )

        assert create_response.status_code == 200
        command_payload = websocket.receive_json()
        assert command_payload["type"] == "command"
        assert command_payload["payload"]["kind"] == "replay_dead_letters"
        assert command_payload["payload"]["args"] == {"task_name": "sync_users", "limit": 5}
        command_id = command_payload["payload"]["command_id"]

        websocket.send_json(
            {
                "type": "command_ack",
                "message_id": "msg_ack_replay_dead_letters",
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
                "message_id": "msg_result_replay_dead_letters",
                "sent_at": "2026-03-17T12:00:04Z",
                "payload": {
                    "command_id": command_id,
                    "status": "succeeded",
                    "finished_at": "2026-03-17T12:00:04Z",
                    "result": {
                        "operation": "replay_dead_letters",
                        "task_name": "sync_users",
                        "requested": True,
                        "completion": "complete",
                        "requested_limit": 5,
                        "attempted_count": 2,
                        "replayed_count": 2,
                        "failed_count": 0,
                        "empty": False,
                    },
                    "duration_ms": 9,
                },
            }
        )

    db_session.expire_all()
    command = db_session.scalar(
        select(AgentCommand).where(AgentCommand.command_id == command_id)
    )
    assert command is not None
    assert command.kind == "replay_dead_letters"
    assert command.status == "succeeded"
    assert command.result_json == {
        "operation": "replay_dead_letters",
        "task_name": "sync_users",
        "requested": True,
        "completion": "complete",
        "requested_limit": 5,
        "attempted_count": 2,
        "replayed_count": 2,
        "failed_count": 0,
        "empty": False,
    }


def test_agent_ws_dispatches_discard_dead_letters_command_with_structured_result(
    client,
    auth_headers,
    db_session,
) -> None:
    with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as websocket:
        websocket.send_json(
            _hello_message(
                capabilities=[
                    "telemetry.sync",
                    "telemetry.heartbeat",
                    "command.discard_dead_letters",
                ]
            )
        )
        websocket.receive_json()
        instance = db_session.scalar(
            select(Instance).where(
                Instance.instance_id == UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df")
            )
        )
        assert instance is not None
        instance.app_snapshot_json = {
            "name": "billing-sync",
            "task_control_states": [
                {
                    "task_name": "sync_users",
                    "supported_commands": ["pause_task", "resume_task", "discard_dead_letters"],
                    "pause_requested": False,
                    "paused": False,
                    "accepting_new_work": True,
                    "runner_count": 1,
                    "parked_runner_count": 0,
                    "fetching_runner_count": 1,
                    "inflight_task_count": 0,
                }
            ],
        }
        db_session.commit()

        create_response = client.post(
            "/api/v1/instances/8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df/commands",
            json={
                "kind": "discard_dead_letters",
                "args": {"task_name": "sync_users", "limit": 5},
                "timeout_s": 120,
                "reason": "Drop invalid dead letters after operator review",
            },
        )

        assert create_response.status_code == 200
        command_payload = websocket.receive_json()
        assert command_payload["type"] == "command"
        assert command_payload["payload"]["kind"] == "discard_dead_letters"
        assert command_payload["payload"]["args"] == {"task_name": "sync_users", "limit": 5}
        command_id = command_payload["payload"]["command_id"]

        websocket.send_json(
            {
                "type": "command_ack",
                "message_id": "msg_ack_discard_dead_letters",
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
                "message_id": "msg_result_discard_dead_letters",
                "sent_at": "2026-03-17T12:00:04Z",
                "payload": {
                    "command_id": command_id,
                    "status": "succeeded",
                    "finished_at": "2026-03-17T12:00:04Z",
                    "result": {
                        "operation": "discard_dead_letters",
                        "task_name": "sync_users",
                        "requested": True,
                        "completion": "complete",
                        "requested_limit": 5,
                        "attempted_count": 2,
                        "discarded_count": 2,
                        "failed_count": 0,
                        "empty": False,
                    },
                    "duration_ms": 7,
                },
            }
        )

    db_session.expire_all()
    command = db_session.scalar(
        select(AgentCommand).where(AgentCommand.command_id == command_id)
    )
    assert command is not None
    assert command.kind == "discard_dead_letters"
    assert command.status == "succeeded"
    assert command.result_json == {
        "operation": "discard_dead_letters",
        "task_name": "sync_users",
        "requested": True,
        "completion": "complete",
        "requested_limit": 5,
        "attempted_count": 2,
        "discarded_count": 2,
        "failed_count": 0,
        "empty": False,
    }


def test_agent_ws_publishes_ui_stream_events_for_command_lifecycle(
    client,
    auth_headers,
) -> None:
    subscriber_id, queue = _subscribe_ui_stream()
    try:
        with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as websocket:
            websocket.send_json(_hello_message())
            websocket.receive_json()

            create_response = client.post(
                "/api/v1/instances/8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df/commands",
                json={"kind": "ping", "args": {"nonce": "stream"}, "timeout_s": 10},
            )
            assert create_response.status_code == 200
            command_payload = websocket.receive_json()
            command_id = command_payload["payload"]["command_id"]

            websocket.send_json(
                {
                    "type": "command_ack",
                    "message_id": "msg_ack_stream",
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
                    "message_id": "msg_result_stream",
                    "sent_at": "2026-03-17T12:00:04Z",
                    "payload": {
                        "command_id": command_id,
                        "status": "succeeded",
                        "finished_at": "2026-03-17T12:00:04Z",
                        "result": {"ok": True},
                        "duration_ms": 3,
                    },
                }
            )

        assert _drain_ui_stream_channels(queue) == [
            "sessions",
            "commands",
            "commands",
            "commands",
            "sessions",
        ]
    finally:
        _unsubscribe_ui_stream(subscriber_id)


def test_instance_command_requires_active_session(client, db_session) -> None:
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

    response = client.post(
        "/api/v1/instances/8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df/commands",
        json={"kind": "ping", "args": {}, "timeout_s": 10},
    )

    assert response.status_code == 409
    assert "no active control session" in response.json()["detail"]


def test_instance_command_requires_advertised_capability(
    client,
    auth_headers,
    db_session,
) -> None:
    with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as websocket:
        websocket.send_json(
            _hello_message(
                capabilities=[
                    "telemetry.sync",
                    "telemetry.heartbeat",
                    "command.sync_now",
                ]
            )
        )
        websocket.receive_json()

        response = client.post(
            "/api/v1/instances/8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df/commands",
            json={"kind": "ping", "args": {}, "timeout_s": 10},
        )

    assert response.status_code == 409
    assert "command.ping" in response.json()["detail"]
    assert db_session.query(AgentCommand).count() == 0


def test_agent_ws_redelivers_pending_commands_after_hello(
    client,
    auth_headers,
    db_session,
) -> None:
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

    pending_command = AgentCommand(
        command_id="cmd_pending_shutdown",
        service_id=db_session.scalar(
            select(Service.id).where(Service.name == "billing-sync", Service.environment == "prod")
        ),
        instance_id=UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df"),
        kind="shutdown",
        args_json={},
        timeout_s=15,
        status="pending",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(pending_command)
    db_session.commit()
    command_id = pending_command.command_id

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


def test_agent_ws_redelivers_queued_instance_command_after_reconnect(
    client,
    auth_headers,
    db_session,
) -> None:
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
    db_session.add(
        AgentSession(
            session_id="sess_disconnected_sync",
            service_id=db_session.scalar(
                select(Service.id).where(
                    Service.name == "billing-sync",
                    Service.environment == "prod",
                )
            ),
            instance_id=UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df"),
            protocol_version="1",
            status="disconnected",
            capabilities_json=["telemetry.sync", "command.sync_now"],
            accepted_capabilities_json=["telemetry.sync", "command.sync_now"],
            connected_at=datetime(2026, 3, 17, 11, 55, 0, tzinfo=UTC),
            last_hello_at=datetime(2026, 3, 17, 11, 55, 0, tzinfo=UTC),
            last_message_at=datetime(2026, 3, 17, 11, 56, 0, tzinfo=UTC),
            disconnected_at=datetime(2026, 3, 17, 11, 56, 0, tzinfo=UTC),
            created_at=datetime(2026, 3, 17, 11, 55, 0, tzinfo=UTC),
            updated_at=datetime(2026, 3, 17, 11, 56, 0, tzinfo=UTC),
        )
    )
    db_session.commit()

    create_response = client.post(
        "/api/v1/instances/8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df/commands",
        json={
            "kind": "sync_now",
            "args": {},
            "timeout_s": 15,
            "delivery_mode": "queue_until_reconnect",
        },
    )
    assert create_response.status_code == 200
    command_id = create_response.json()["command_id"]
    assert create_response.json()["status"] == "pending"

    with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as websocket:
        websocket.send_json(
            _hello_message(
                capabilities=[
                    "telemetry.sync",
                    "telemetry.heartbeat",
                    "command.sync_now",
                ]
            )
        )
        websocket.receive_json()
        command_payload = websocket.receive_json()

    assert command_payload["type"] == "command"
    assert command_payload["payload"]["command_id"] == command_id
    assert command_payload["payload"]["kind"] == "sync_now"

    db_session.expire_all()
    command = db_session.scalar(
        select(AgentCommand).where(AgentCommand.command_id == command_id)
    )
    assert command is not None
    assert command.status == "dispatched"
    assert command.dispatched_at is not None


def test_agent_ws_rejects_queued_command_when_reconnect_lacks_capability(
    client,
    auth_headers,
    db_session,
) -> None:
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

    pending_command = AgentCommand(
        command_id="cmd_pending_sync_now",
        service_id=db_session.scalar(
            select(Service.id).where(Service.name == "billing-sync", Service.environment == "prod")
        ),
        instance_id=UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df"),
        kind="sync_now",
        args_json={},
        timeout_s=15,
        status="pending",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(pending_command)
    db_session.commit()

    with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as websocket:
        websocket.send_json(
            _hello_message(
                capabilities=[
                    "telemetry.sync",
                    "telemetry.heartbeat",
                    "command.ping",
                ]
            )
        )
        websocket.receive_json()

    db_session.expire_all()
    command = db_session.scalar(
        select(AgentCommand).where(AgentCommand.command_id == "cmd_pending_sync_now")
    )
    assert command is not None
    assert command.status == "rejected"
    assert command.error_code == "command_capability_missing_on_reconnect"
    assert "command.sync_now" in (command.error_message or "")


def test_agent_ws_does_not_redeliver_accepted_commands_after_reconnect(
    client,
    auth_headers,
    db_session,
) -> None:
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
    with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as websocket:
        websocket.send_json(_hello_message())
        websocket.receive_json()
        create_response = client.post(
            "/api/v1/instances/8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df/commands",
            json={"kind": "sync_now", "args": {}, "timeout_s": 15},
        )
        assert create_response.status_code == 200
        command_id = create_response.json()["command_id"]
        command_payload = websocket.receive_json()
        assert command_payload["payload"]["command_id"] == command_id
        websocket.send_json(
            {
                "type": "command_ack",
                "message_id": "msg_ack_once",
                "sent_at": "2026-03-17T12:00:03Z",
                "payload": {
                    "command_id": command_id,
                    "status": "accepted",
                    "received_at": "2026-03-17T12:00:03Z",
                },
            }
        )

    with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as websocket:
        websocket.send_json(_hello_message())
        websocket.receive_json()

    db_session.expire_all()
    command = db_session.scalar(
        select(AgentCommand).where(AgentCommand.command_id == command_id)
    )
    assert command is not None
    assert command.status == "accepted"
    assert command.ack_status == "accepted"


def test_service_command_fanout_dispatches_online_targets_and_skips_instances_without_session(
    client,
    auth_headers,
    db_session,
) -> None:
    ingest_heartbeat_request(
        db_session,
        HeartbeatIngestRequest.model_validate(
            {
                "service": make_service_payload(
                    instance_id="aaaaaaaa-4b4a-4a58-8a6f-52d6735f44df",
                    node_name="vm-prod-4",
                ),
                "runtime": {
                    "onestep_version": "1.0.0a0",
                    "python_version": "3.11.14",
                    "hostname": "vm-prod-4",
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

    with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as websocket:
        websocket.send_json(_hello_message())
        websocket.receive_json()

        response = client.post(
            "/api/v1/services/billing-sync/commands",
            params={"environment": "prod"},
            json={
                "kind": "sync_now",
                "reason": "Refresh all online replicas after drift check",
                "target_mode": "all_online",
                "offline_behavior": "skip",
                "timeout_s": 20,
            },
        )

        assert response.status_code == 200
        payload = response.json()
        command_payload = websocket.receive_json()

    assert command_payload["type"] == "command"
    assert command_payload["payload"]["kind"] == "sync_now"
    assert payload["counts"] == {
        "dispatched": 1,
        "queued": 0,
        "skipped": 1,
        "rejected": 0,
        "total": 2,
    }
    assert payload["dispatched"][0]["instance_id"] == "8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df"
    assert payload["skipped"][0]["instance_id"] == "aaaaaaaa-4b4a-4a58-8a6f-52d6735f44df"
    assert payload["skipped"][0]["reason_code"] == "no_active_session"


def test_list_instance_commands_expires_stale_pending_command(
    client,
    db_session,
) -> None:
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
        json={"kind": "ping", "args": {}, "timeout_s": 1},
    )
    assert create_response.status_code == 409
    assert "no active control session" in create_response.json()["detail"]
    pending_command = AgentCommand(
        command_id="cmd_expire_ping",
        service_id=db_session.scalar(
            select(Service.id).where(Service.name == "billing-sync", Service.environment == "prod")
        ),
        instance_id=UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df"),
        kind="ping",
        args_json={},
        timeout_s=1,
        status="pending",
        created_at=datetime(2026, 3, 17, 12, 0, 0, tzinfo=UTC),
        updated_at=datetime(2026, 3, 17, 12, 0, 0, tzinfo=UTC),
    )
    db_session.add(pending_command)
    db_session.commit()
    command_id = pending_command.command_id
    command = db_session.scalar(
        select(AgentCommand).where(AgentCommand.command_id == command_id)
    )
    assert command is not None

    response = client.get(
        "/api/v1/instances/8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df/commands"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["command_id"] == command_id
    assert payload["items"][0]["status"] == "expired"
    assert payload["items"][0]["error_code"] == "command_delivery_expired"
