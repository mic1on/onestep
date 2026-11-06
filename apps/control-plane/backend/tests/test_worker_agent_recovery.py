from uuid import UUID, uuid4

import pytest
from onestep_control_plane_api.db.models import (
    WorkerAgentCommand,
    WorkerDeployment,
    WorkerDeploymentEvent,
)
from sqlalchemy import select
from test_worker_agent_ws import (
    _ack_barrier,
    _hello_message,
    _register_worker_agent,
    _send_command_result,
    _upload_workflow_package,
)


@pytest.mark.parametrize("recovery", [False, True])
def test_accepted_redelivery_requires_recovery_capability(
    client,
    db_session,
    worker_agent_registration_token,
    recovery,
):
    registration = _register_worker_agent(client, worker_agent_registration_token)
    package = _upload_workflow_package(client)
    deployment = client.post(
        "/api/v1/worker-deployments",
        json={
            "workflow_package_id": package["package_id"],
            "worker_agent_id": registration["worker_agent_id"],
        },
    ).json()
    command = db_session.scalar(
        select(WorkerAgentCommand).where(
            WorkerAgentCommand.deployment_id == UUID(deployment["deployment_id"])
        )
    )
    command.status = "accepted"
    command.ack_status = "accepted"
    command_id = str(command.command_id)
    db_session.commit()
    hello = _hello_message(registration["worker_agent_id"])
    if recovery:
        hello["payload"]["capabilities"].append("agent.command_recovery")
    with client.websocket_connect(
        "/api/v1/worker-agents/ws",
        headers={"Authorization": f"Bearer {registration['connection_token']}"},
    ) as websocket:
        websocket.send_json(hello)
        assert websocket.receive_json()["type"] == "hello_ack"
        if recovery:
            assert websocket.receive_json()["payload"]["command_id"] == command_id
        websocket.send_json(_ack_barrier())
        assert websocket.receive_json()["type"] == "error"


def test_exit_ack_deduplication_and_late_start_result_preserve_terminal_state(
    client,
    db_session,
    worker_agent_registration_token,
):
    registration = _register_worker_agent(client, worker_agent_registration_token)
    package = _upload_workflow_package(client)
    deployment = client.post(
        "/api/v1/worker-deployments",
        json={
            "workflow_package_id": package["package_id"],
            "worker_agent_id": registration["worker_agent_id"],
        },
    ).json()
    runtime_id, event_id = str(uuid4()), str(uuid4())
    with client.websocket_connect(
        "/api/v1/worker-agents/ws",
        headers={"Authorization": f"Bearer {registration['connection_token']}"},
    ) as websocket:
        websocket.send_json(_hello_message(registration["worker_agent_id"]))
        websocket.receive_json()
        command = websocket.receive_json()
        event = {
            "type": "deployment_event",
            "message_id": "exit",
            "sent_at": "2026-06-16T09:00:10Z",
            "payload": {
                "deployment_id": deployment["deployment_id"],
                "event_type": "exited",
                "observed_status": "failed",
                "message": "exit 7",
                "payload": {"agent_event_id": event_id, "runtime_instance_id": runtime_id},
            },
        }
        for _ in range(2):
            websocket.send_json(event)
            ack = websocket.receive_json()
            assert ack == {"type": "deployment_event_ack", "payload": {"event_id": event_id}}
        _send_command_result(
            websocket,
            command_id=command["payload"]["command_id"],
            status="succeeded",
            result={"runtime_instance_id": runtime_id},
        )
        websocket.send_json(_ack_barrier())
        websocket.receive_json()
        db_session.expire_all()
        row = db_session.scalar(
            select(WorkerDeployment).where(
                WorkerDeployment.deployment_id == UUID(deployment["deployment_id"])
            )
        )
        assert row.observed_status == "failed"
        events = db_session.scalars(
            select(WorkerDeploymentEvent).where(WorkerDeploymentEvent.event_type == "exited")
        ).all()
        assert len(events) == 1
