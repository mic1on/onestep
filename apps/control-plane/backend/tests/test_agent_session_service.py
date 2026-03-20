from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from onestep_control_plane_api.api.agent_session_service import disconnect_active_sessions
from onestep_control_plane_api.db.models import AgentSession, Instance, Service


def test_disconnect_active_sessions_marks_only_active_sessions_disconnected(db_session) -> None:
    connected_at = datetime(2026, 3, 18, 0, 0, tzinfo=UTC)
    disconnected_at = datetime(2026, 3, 18, 0, 5, tzinfo=UTC)
    marker = datetime(2026, 3, 18, 0, 10, tzinfo=UTC)

    service = Service(
        name="billing-sync",
        environment="prod",
        latest_deployment_version="1.0.0",
    )
    instance = Instance(
        service=service,
        instance_id=UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df"),
        node_name="vm-prod-3",
        deployment_version="1.0.0",
        status="ok",
        last_seen_at=connected_at,
    )
    active_session = AgentSession(
        session_id="sess_active",
        service=service,
        instance=instance,
        instance_id=instance.instance_id,
        protocol_version="1",
        status="active",
        capabilities_json=["telemetry.sync"],
        accepted_capabilities_json=["telemetry.sync"],
        connected_at=connected_at,
        last_hello_at=connected_at,
        last_message_at=connected_at,
    )
    disconnected_session = AgentSession(
        session_id="sess_disconnected",
        service=service,
        instance=instance,
        instance_id=instance.instance_id,
        protocol_version="1",
        status="disconnected",
        capabilities_json=["telemetry.sync"],
        accepted_capabilities_json=["telemetry.sync"],
        connected_at=connected_at,
        last_hello_at=connected_at,
        last_message_at=connected_at,
        disconnected_at=disconnected_at,
    )
    db_session.add_all([service, instance, active_session, disconnected_session])
    db_session.commit()

    updated_count = disconnect_active_sessions(db_session, disconnected_at=marker)

    assert updated_count == 1
    db_session.expire_all()
    sessions = {
        session.session_id: session
        for session in db_session.scalars(select(AgentSession)).all()
    }
    assert sessions["sess_active"].status == "disconnected"
    assert sessions["sess_active"].disconnected_at == marker
    assert sessions["sess_disconnected"].status == "disconnected"
    assert sessions["sess_disconnected"].disconnected_at == disconnected_at
