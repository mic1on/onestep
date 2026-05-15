from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from onestep_control_plane_api.api.agent_connection_registry import agent_connection_registry
from onestep_control_plane_api.auth.service import LocalAuthService
from onestep_control_plane_api.db.models import AgentCommand, AgentSession, Instance, Service
from sqlalchemy import select


def seed_service_and_instance(db_session) -> Instance:
    service = Service(
        name="billing-sync",
        environment="prod",
        latest_deployment_version="1.0.0",
    )
    db_session.add(service)
    db_session.flush()

    instance = Instance(
        service=service,
        instance_id=uuid4(),
        node_name="vm-prod-1",
        hostname="vm-prod-1",
        pid=1234,
        deployment_version="1.0.0",
        onestep_version="1.0.0",
        python_version="3.11.14",
        started_at=datetime(2026, 5, 15, 12, 0, tzinfo=UTC),
        last_seen_at=datetime(2026, 5, 15, 12, 1, tzinfo=UTC),
        status="ok",
    )
    db_session.add(instance)
    db_session.flush()

    session = AgentSession(
        session_id="sess_active_1",
        service=service,
        instance_id=instance.instance_id,
        protocol_version="1",
        status="active",
        capabilities_json=[
            "command.ping",
            "command.restart",
            "command.drain",
            "command.shutdown",
            "command.pause_task",
        ],
        accepted_capabilities_json=[
            "command.ping",
            "command.restart",
            "command.drain",
            "command.shutdown",
            "command.pause_task",
        ],
        connected_at=datetime(2026, 5, 15, 12, 0, tzinfo=UTC),
        last_hello_at=datetime(2026, 5, 15, 12, 0, tzinfo=UTC),
        last_message_at=datetime(2026, 5, 15, 12, 1, tzinfo=UTC),
        created_at=datetime(2026, 5, 15, 12, 0, tzinfo=UTC),
        updated_at=datetime(2026, 5, 15, 12, 1, tzinfo=UTC),
    )
    db_session.add(session)
    db_session.commit()
    return instance


def login_role(client, db_session, *, username: str, role: str):
    LocalAuthService(db_session).create_user(
        username=username,
        password="secret-pass",
        role_names=[role],
    )
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "secret-pass"},
    )
    assert response.status_code == 200


async def _register_live_connection(instance_id):
    import asyncio

    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    return await agent_connection_registry.register(
        instance_id=instance_id,
        session_id="sess_active_1",
        send_queue=queue,
    )


def test_viewer_cannot_create_instance_command(client, db_session) -> None:
    instance = seed_service_and_instance(db_session)
    login_role(client, db_session, username="viewer", role="viewer")

    response = client.post(
        f"/api/v1/instances/{instance.instance_id}/commands",
        json={"kind": "ping", "args": {"nonce": "abc"}, "timeout_s": 10},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "insufficient role for command execution"


def test_operator_can_run_non_destructive_instance_command(client, db_session) -> None:
    import asyncio

    instance = seed_service_and_instance(db_session)
    login_role(client, db_session, username="operator", role="operator")
    connection = asyncio.run(_register_live_connection(instance.instance_id))

    try:
        response = client.post(
            f"/api/v1/instances/{instance.instance_id}/commands",
            json={"kind": "ping", "args": {"nonce": "abc"}, "timeout_s": 10},
        )
    finally:
        asyncio.run(
            agent_connection_registry.unregister(
                instance_id=instance.instance_id,
                session_id=connection.session_id,
            )
        )

    assert response.status_code == 200
    command = db_session.scalar(
        select(AgentCommand).where(AgentCommand.instance_id == instance.instance_id)
    )
    assert command is not None
    assert command.created_by == "operator"
    assert command.kind == "ping"


def test_operator_cannot_run_destructive_instance_command(client, db_session) -> None:
    instance = seed_service_and_instance(db_session)
    login_role(client, db_session, username="operator", role="operator")

    response = client.post(
        f"/api/v1/instances/{instance.instance_id}/commands",
        json={"kind": "restart", "args": {}, "timeout_s": 10, "reason": "maintenance"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "admin role required for destructive commands"


def test_admin_can_run_destructive_instance_command(client, db_session) -> None:
    import asyncio

    instance = seed_service_and_instance(db_session)
    login_role(client, db_session, username="admin", role="admin")
    connection = asyncio.run(_register_live_connection(instance.instance_id))

    try:
        response = client.post(
            f"/api/v1/instances/{instance.instance_id}/commands",
            json={"kind": "restart", "args": {}, "timeout_s": 10, "reason": "maintenance"},
        )
    finally:
        asyncio.run(
            agent_connection_registry.unregister(
                instance_id=instance.instance_id,
                session_id=connection.session_id,
            )
        )

    assert response.status_code == 200
    command = db_session.scalar(
        select(AgentCommand).where(AgentCommand.instance_id == instance.instance_id)
    )
    assert command is not None
    assert command.created_by == "admin"
    assert command.kind == "restart"
