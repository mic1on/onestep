from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from onestep_control_plane_api.api.agent_connection_registry import agent_connection_registry
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db.models import (
    AgentCommand,
    AgentSession,
    Instance,
    Service,
    TaskDefinition,
    TaskEvent,
    TaskMetricWindow,
)
from sqlalchemy import select


@pytest.fixture(autouse=True)
def restore_console_auth_settings() -> None:
    original_username = settings.console_auth_username
    original_password = settings.console_auth_password
    try:
        yield
    finally:
        settings.console_auth_username = original_username
        settings.console_auth_password = original_password


def seed_service(
    db_session,
    *,
    name: str = "billing-sync",
    environment: str = "prod",
    latest_deployment_version: str = "1.0.0a0+c435c99",
    latest_topology_hash: str | None = None,
    latest_sync_at: datetime | None = None,
) -> Service:
    service = Service(
        name=name,
        environment=environment,
        latest_deployment_version=latest_deployment_version,
        latest_topology_hash=latest_topology_hash,
        latest_sync_at=latest_sync_at,
    )
    db_session.add(service)
    db_session.flush()
    return service


def seed_instance(
    db_session,
    service: Service,
    *,
    instance_id: UUID | None = None,
    node_name: str,
    status: str,
    last_seen_at: datetime | None,
    deployment_version: str = "1.0.0a0+c435c99",
    hostname: str | None = None,
    last_sync_at: datetime | None = None,
    last_topology_hash: str | None = None,
    app_snapshot_json: dict[str, object] | None = None,
) -> Instance:
    instance = Instance(
        service=service,
        instance_id=instance_id or uuid4(),
        node_name=node_name,
        hostname=hostname or node_name,
        pid=18231,
        deployment_version=deployment_version,
        onestep_version="1.0.0a0",
        python_version="3.11.14",
        started_at=datetime(2026, 3, 8, 17, 29, 50, tzinfo=UTC),
        last_sync_at=last_sync_at,
        last_topology_hash=last_topology_hash,
        app_snapshot_json=app_snapshot_json,
        last_heartbeat_sent_at=last_seen_at,
        last_heartbeat_sequence=1 if last_seen_at is not None else None,
        last_seen_at=last_seen_at,
        status=status,
    )
    db_session.add(instance)
    db_session.flush()
    return instance


def seed_metric_window(
    db_session,
    service: Service,
    instance: Instance,
    *,
    task_name: str,
    window_id: str,
    window_started_at: datetime,
    window_ended_at: datetime,
    succeeded: int,
) -> TaskMetricWindow:
    metric_window = TaskMetricWindow(
        service=service,
        instance_id=instance.instance_id,
        task_name=task_name,
        window_id=window_id,
        window_started_at=window_started_at,
        window_ended_at=window_ended_at,
        fetched=succeeded,
        started=succeeded,
        succeeded=succeeded,
        retried=0,
        failed=0,
        dead_lettered=0,
        cancelled=0,
        timeouts=0,
        inflight=0,
        avg_duration_ms=123.4,
        p95_duration_ms=220.0,
        received_at=window_ended_at,
    )
    db_session.add(metric_window)
    db_session.flush()
    return metric_window


def seed_agent_session(
    db_session,
    service: Service,
    instance: Instance,
    *,
    status: str,
    connected_at: datetime,
    last_message_at: datetime | None = None,
    session_id: str = "sess_test_active",
    accepted_capabilities_json: list[str] | None = None,
) -> AgentSession:
    resolved_last_message_at = last_message_at or connected_at
    accepted_capabilities = accepted_capabilities_json or [
        "telemetry.sync",
        "command.sync_now",
        "command.flush_metrics",
    ]
    session = AgentSession(
        session_id=session_id,
        service=service,
        instance_id=instance.instance_id,
        protocol_version="1",
        status=status,
        capabilities_json=accepted_capabilities,
        accepted_capabilities_json=accepted_capabilities,
        connected_at=connected_at,
        last_hello_at=connected_at,
        last_message_at=resolved_last_message_at,
        superseded_at=resolved_last_message_at if status == "superseded" else None,
        disconnected_at=resolved_last_message_at if status != "active" else None,
        created_at=connected_at,
        updated_at=resolved_last_message_at,
    )
    db_session.add(session)
    db_session.flush()
    return session


def seed_agent_command(
    db_session,
    service: Service,
    instance: Instance,
    *,
    command_id: str,
    kind: str,
    status: str,
    created_at: datetime,
    timeout_s: int = 15,
    session_id: str | None = None,
    ack_status: str | None = None,
    dispatched_at: datetime | None = None,
    acked_at: datetime | None = None,
    finished_at: datetime | None = None,
    result_json: dict[str, object] | None = None,
    duration_ms: int | None = None,
    created_by: str | None = None,
    reason: str | None = None,
    source_surface: str = "unknown",
    error_code: str | None = None,
    error_message: str | None = None,
) -> AgentCommand:
    command = AgentCommand(
        command_id=command_id,
        service=service,
        instance_id=instance.instance_id,
        session_id=session_id,
        created_by=created_by,
        reason=reason,
        source_surface=source_surface,
        kind=kind,
        args_json={},
        timeout_s=timeout_s,
        status=status,
        ack_status=ack_status,
        result_json=result_json,
        duration_ms=duration_ms,
        error_code=error_code,
        error_message=error_message,
        dispatched_at=dispatched_at,
        acked_at=acked_at,
        finished_at=finished_at,
        created_at=created_at,
        updated_at=finished_at or acked_at or dispatched_at or created_at,
    )
    db_session.add(command)
    db_session.flush()
    return command


def seed_task_event(
    db_session,
    service: Service,
    instance: Instance,
    *,
    event_id: str,
    task_name: str,
    kind: str,
    occurred_at: datetime,
) -> TaskEvent:
    task_event = TaskEvent(
        event_id=event_id,
        service=service,
        instance_id=instance.instance_id,
        task_name=task_name,
        kind=kind,
        occurred_at=occurred_at,
        attempts=3,
        duration_ms=30012,
        failure_kind="timeout" if kind == "failed" else None,
        exception_type="TimeoutError" if kind == "failed" else None,
        message="task exceeded timeout" if kind == "failed" else None,
        traceback=(
            "Traceback (most recent call last):\nTimeoutError: task exceeded timeout\n"
            if kind == "failed"
            else None
        ),
        meta_json={"source": "interval:3600s"},
        received_at=occurred_at + timedelta(seconds=1),
    )
    db_session.add(task_event)
    db_session.flush()
    return task_event


def login_console_admin(client) -> None:
    settings.console_auth_username = "admin"
    settings.console_auth_password = "secret-pass"
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "secret-pass"},
    )
    assert response.status_code == 200


def seed_task_definition(
    db_session,
    service: Service,
    *,
    task_name: str,
    source_name: str,
    source_kind: str,
    description: str | None = None,
    source_config_json: dict[str, object] | None = None,
    emit_json: list[dict[str, object]] | None = None,
    concurrency: int | None = None,
    timeout_s: float | None = None,
    retry_policy: dict[str, object] | None = None,
    topology_hash: str = "sha256:topology-a",
) -> TaskDefinition:
    task_definition = TaskDefinition(
        service=service,
        task_name=task_name,
        description=description,
        source_name=source_name,
        source_kind=source_kind,
        source_config_json=source_config_json,
        emit_json=emit_json,
        concurrency=concurrency,
        timeout_s=timeout_s,
        retry_policy=retry_policy,
        topology_hash=topology_hash,
    )
    db_session.add(task_definition)
    db_session.flush()
    return task_definition


def test_list_services_and_get_service_summary(client, db_session) -> None:
    now = datetime.now(UTC)
    prod_service = seed_service(db_session, name="billing-sync", environment="prod")
    staging_service = seed_service(db_session, name="billing-sync", environment="staging")
    seed_instance(
        db_session,
        prod_service,
        node_name="vm-prod-online",
        status="ok",
        last_seen_at=now - timedelta(seconds=30),
    )
    seed_instance(
        db_session,
        prod_service,
        node_name="vm-prod-offline",
        status="degraded",
        last_seen_at=now - timedelta(minutes=5),
    )
    seed_instance(
        db_session,
        staging_service,
        node_name="vm-staging-online",
        status="ok",
        last_seen_at=now - timedelta(seconds=20),
    )
    db_session.commit()

    response = client.get("/api/v1/services", params={"environment": "prod"})
    assert response.status_code == 200

    payload = response.json()
    assert payload["total"] == 1
    assert payload["limit"] == 50
    assert payload["offset"] == 0
    assert len(payload["items"]) == 1
    assert payload["items"][0]["name"] == "billing-sync"
    assert payload["items"][0]["environment"] == "prod"
    assert payload["items"][0]["instance_count"] == 2
    assert payload["items"][0]["online_instance_count"] == 1

    detail = client.get("/api/v1/services/billing-sync", params={"environment": "prod"})
    assert detail.status_code == 200
    assert detail.json()["instance_count"] == 2
    assert detail.json()["online_instance_count"] == 1


def test_list_service_instances_returns_connectivity_and_filters(client, db_session) -> None:
    now = datetime.now(UTC)
    service = seed_service(db_session)
    online_instance = seed_instance(
        db_session,
        service,
        node_name="vm-online",
        status="ok",
        last_seen_at=now - timedelta(seconds=30),
    )
    seed_instance(
        db_session,
        service,
        node_name="vm-offline",
        status="degraded",
        last_seen_at=now - timedelta(minutes=5),
    )
    seed_instance(
        db_session,
        service,
        node_name="vm-stub",
        status="unknown",
        last_seen_at=None,
    )
    db_session.commit()

    response = client.get("/api/v1/services/billing-sync/instances", params={"environment": "prod"})
    assert response.status_code == 200

    payload = response.json()
    assert payload["total"] == 3
    by_node_name = {item["node_name"]: item for item in payload["items"]}
    assert by_node_name["vm-online"]["connectivity"] == "online"
    assert by_node_name["vm-offline"]["connectivity"] == "offline"
    assert by_node_name["vm-stub"]["connectivity"] == "never_reported"

    filtered = client.get(
        "/api/v1/services/billing-sync/instances",
        params={"environment": "prod", "connectivity": "online"},
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["instance_id"] == str(online_instance.instance_id)


def test_list_service_metric_windows_filters_by_task_and_instance(client, db_session) -> None:
    service = seed_service(db_session, name="billing-sync", environment="prod")
    other_service = seed_service(db_session, name="audit-sync", environment="prod")
    first_instance = seed_instance(
        db_session,
        service,
        node_name="vm-prod-1",
        status="ok",
        last_seen_at=datetime.now(UTC),
    )
    second_instance = seed_instance(
        db_session,
        service,
        node_name="vm-prod-2",
        status="ok",
        last_seen_at=datetime.now(UTC),
    )
    other_instance = seed_instance(
        db_session,
        other_service,
        node_name="vm-audit-1",
        status="ok",
        last_seen_at=datetime.now(UTC),
    )

    seed_metric_window(
        db_session,
        service,
        first_instance,
        task_name="sync_users",
        window_id="sync_users:2026-03-08T17:30:30Z:2026-03-08T17:31:00Z",
        window_started_at=datetime(2026, 3, 8, 17, 30, 30, tzinfo=UTC),
        window_ended_at=datetime(2026, 3, 8, 17, 31, 0, tzinfo=UTC),
        succeeded=118,
    )
    seed_metric_window(
        db_session,
        service,
        second_instance,
        task_name="sync_users",
        window_id="sync_users:2026-03-08T17:31:00Z:2026-03-08T17:31:30Z",
        window_started_at=datetime(2026, 3, 8, 17, 31, 0, tzinfo=UTC),
        window_ended_at=datetime(2026, 3, 8, 17, 31, 30, tzinfo=UTC),
        succeeded=121,
    )
    seed_metric_window(
        db_session,
        service,
        first_instance,
        task_name="cleanup_orphans",
        window_id="cleanup_orphans:2026-03-08T17:31:00Z:2026-03-08T17:31:30Z",
        window_started_at=datetime(2026, 3, 8, 17, 31, 0, tzinfo=UTC),
        window_ended_at=datetime(2026, 3, 8, 17, 31, 30, tzinfo=UTC),
        succeeded=42,
    )
    seed_metric_window(
        db_session,
        other_service,
        other_instance,
        task_name="sync_users",
        window_id="sync_users:2026-03-08T17:31:30Z:2026-03-08T17:32:00Z",
        window_started_at=datetime(2026, 3, 8, 17, 31, 30, tzinfo=UTC),
        window_ended_at=datetime(2026, 3, 8, 17, 32, 0, tzinfo=UTC),
        succeeded=99,
    )
    db_session.commit()

    response = client.get(
        "/api/v1/services/billing-sync/metric-windows",
        params={"environment": "prod", "task_name": "sync_users"},
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["total"] == 2
    assert [item["window_id"] for item in payload["items"]] == [
        "sync_users:2026-03-08T17:31:00Z:2026-03-08T17:31:30Z",
        "sync_users:2026-03-08T17:30:30Z:2026-03-08T17:31:00Z",
    ]

    filtered = client.get(
        "/api/v1/services/billing-sync/metric-windows",
        params={"environment": "prod", "instance_id": str(first_instance.instance_id)},
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 2
    assert all(
        item["instance_id"] == str(first_instance.instance_id)
        for item in filtered.json()["items"]
    )


def test_list_service_events_filters_by_kind_and_time_order(client, db_session) -> None:
    service = seed_service(db_session, name="billing-sync", environment="prod")
    other_service = seed_service(db_session, name="audit-sync", environment="prod")
    instance = seed_instance(
        db_session,
        service,
        node_name="vm-prod-1",
        status="ok",
        last_seen_at=datetime.now(UTC),
    )
    other_instance = seed_instance(
        db_session,
        other_service,
        node_name="vm-audit-1",
        status="ok",
        last_seen_at=datetime.now(UTC),
    )

    seed_task_event(
        db_session,
        service,
        instance,
        event_id="evt_01JNXFAILED",
        task_name="sync_users",
        kind="failed",
        occurred_at=datetime(2026, 3, 8, 17, 30, 58, tzinfo=UTC),
    )
    seed_task_event(
        db_session,
        service,
        instance,
        event_id="evt_01JNXSUCCEEDED",
        task_name="sync_users",
        kind="succeeded",
        occurred_at=datetime(2026, 3, 8, 17, 30, 59, tzinfo=UTC),
    )
    seed_task_event(
        db_session,
        other_service,
        other_instance,
        event_id="evt_01JNXOTHER",
        task_name="sync_users",
        kind="failed",
        occurred_at=datetime(2026, 3, 8, 17, 31, 0, tzinfo=UTC),
    )
    db_session.commit()

    response = client.get(
        "/api/v1/services/billing-sync/events",
        params={"environment": "prod", "kind": "failed"},
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["event_id"] == "evt_01JNXFAILED"
    assert payload["items"][0]["failure_kind"] == "timeout"
    assert payload["items"][0]["traceback"] == (
        "Traceback (most recent call last):\n"
        "TimeoutError: task exceeded timeout\n"
    )

    ordered = client.get("/api/v1/services/billing-sync/events", params={"environment": "prod"})
    assert ordered.status_code == 200
    assert [item["event_id"] for item in ordered.json()["items"]] == [
        "evt_01JNXSUCCEEDED",
        "evt_01JNXFAILED",
    ]


def test_service_dashboard_returns_instance_and_task_overview(client, db_session) -> None:
    now = datetime.now(UTC)
    service = seed_service(
        db_session,
        name="billing-sync",
        environment="prod",
        latest_topology_hash="sha256:topology-b",
        latest_sync_at=now - timedelta(seconds=15),
    )
    online_instance = seed_instance(
        db_session,
        service,
        node_name="vm-online",
        status="ok",
        last_seen_at=now - timedelta(seconds=20),
        last_sync_at=now - timedelta(seconds=15),
        last_topology_hash="sha256:topology-a",
    )
    seed_instance(
        db_session,
        service,
        node_name="vm-offline",
        status="degraded",
        last_seen_at=now - timedelta(minutes=10),
        last_sync_at=now - timedelta(minutes=2),
        last_topology_hash="sha256:topology-b",
    )
    seed_instance(
        db_session,
        service,
        node_name="vm-stub",
        status="unknown",
        last_seen_at=None,
    )

    seed_metric_window(
        db_session,
        service,
        online_instance,
        task_name="sync_users",
        window_id="sync_users:2026-03-08T17:45:00Z:2026-03-08T17:46:00Z",
        window_started_at=now - timedelta(minutes=2),
        window_ended_at=now - timedelta(minutes=1),
        succeeded=12,
    )
    seed_metric_window(
        db_session,
        service,
        online_instance,
        task_name="cleanup_orphans",
        window_id="cleanup_orphans:2026-03-08T17:44:00Z:2026-03-08T17:45:00Z",
        window_started_at=now - timedelta(minutes=3),
        window_ended_at=now - timedelta(minutes=2),
        succeeded=4,
    )
    seed_task_event(
        db_session,
        service,
        online_instance,
        event_id="evt_dashboard_failed",
        task_name="sync_users",
        kind="failed",
        occurred_at=now - timedelta(seconds=50),
    )
    seed_task_event(
        db_session,
        service,
        online_instance,
        event_id="evt_dashboard_succeeded",
        task_name="cleanup_orphans",
        kind="succeeded",
        occurred_at=now - timedelta(seconds=40),
    )
    seed_task_event(
        db_session,
        service,
        online_instance,
        event_id="evt_dashboard_old",
        task_name="sync_users",
        kind="failed",
        occurred_at=now - timedelta(minutes=45),
    )
    db_session.commit()

    response = client.get(
        "/api/v1/services/billing-sync/dashboard",
        params={"environment": "prod", "lookback_minutes": 15, "recent_event_limit": 3},
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["service"]["instance_count"] == 3
    assert payload["service"]["latest_topology_hash"] == "sha256:topology-b"
    assert payload["instance_connectivity"] == {
        "total": 3,
        "online": 1,
        "offline": 1,
        "never_reported": 1,
    }
    assert payload["instance_statuses"] == {
        "ok": 1,
        "degraded": 1,
        "error": 0,
        "starting": 0,
        "unknown": 1,
    }
    assert payload["task_count"] == 2
    assert payload["failing_task_count"] == 1
    assert payload["topology_hashes"] == ["sha256:topology-a"]
    assert payload["topology_consistent"] is True
    assert [event["event_id"] for event in payload["recent_events"]] == [
        "evt_dashboard_succeeded",
        "evt_dashboard_failed",
    ]


def test_service_dashboard_marks_topology_drift_when_online_instances_disagree(
    client, db_session
) -> None:
    now = datetime.now(UTC)
    service = seed_service(
        db_session,
        name="billing-sync",
        environment="prod",
        latest_topology_hash="sha256:topology-b",
        latest_sync_at=now - timedelta(seconds=10),
    )
    seed_instance(
        db_session,
        service,
        node_name="vm-online-a",
        status="ok",
        last_seen_at=now - timedelta(seconds=20),
        last_sync_at=now - timedelta(seconds=15),
        last_topology_hash="sha256:topology-a",
    )
    seed_instance(
        db_session,
        service,
        node_name="vm-online-b",
        status="ok",
        last_seen_at=now - timedelta(seconds=25),
        last_sync_at=now - timedelta(seconds=12),
        last_topology_hash="sha256:topology-b",
    )
    seed_instance(
        db_session,
        service,
        node_name="vm-offline",
        status="degraded",
        last_seen_at=now - timedelta(minutes=10),
        last_sync_at=now - timedelta(minutes=2),
        last_topology_hash="sha256:topology-c",
    )
    db_session.commit()

    response = client.get(
        "/api/v1/services/billing-sync/dashboard",
        params={"environment": "prod"},
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["topology_hashes"] == ["sha256:topology-a", "sha256:topology-b"]
    assert payload["topology_consistent"] is False


def test_service_dashboard_and_instance_detail_include_command_overview_and_active_session(
    client,
    db_session,
) -> None:
    now = datetime.now(UTC)
    service = seed_service(db_session, name="billing-sync", environment="prod")
    instance = seed_instance(
        db_session,
        service,
        node_name="vm-prod-1",
        status="ok",
        last_seen_at=now - timedelta(seconds=10),
    )
    active_session = seed_agent_session(
        db_session,
        service,
        instance,
        status="active",
        connected_at=now - timedelta(minutes=2),
        last_message_at=now - timedelta(seconds=6),
        session_id="sess_live_vm_prod_1",
    )
    seed_agent_command(
        db_session,
        service,
        instance,
        command_id="cmd_sync_now",
        kind="sync_now",
        status="accepted",
        created_at=now - timedelta(seconds=40),
        timeout_s=300,
        session_id=active_session.session_id,
        ack_status="accepted",
        dispatched_at=now - timedelta(seconds=39),
        acked_at=now - timedelta(seconds=38),
    )
    seed_agent_command(
        db_session,
        service,
        instance,
        command_id="cmd_ping",
        kind="ping",
        status="succeeded",
        created_at=now - timedelta(minutes=4),
        session_id=active_session.session_id,
        ack_status="accepted",
        dispatched_at=now - timedelta(minutes=4) + timedelta(seconds=1),
        acked_at=now - timedelta(minutes=4) + timedelta(seconds=2),
        finished_at=now - timedelta(minutes=4) + timedelta(seconds=3),
        result_json={"ok": True},
        duration_ms=1000,
    )
    db_session.commit()

    dashboard_response = client.get(
        "/api/v1/services/billing-sync/dashboard",
        params={"environment": "prod"},
    )
    assert dashboard_response.status_code == 200
    dashboard_payload = dashboard_response.json()
    assert dashboard_payload["command_overview"]["active_session_count"] == 1
    assert dashboard_payload["command_overview"]["statuses"]["accepted"] == 1
    assert dashboard_payload["command_overview"]["statuses"]["succeeded"] == 1
    assert dashboard_payload["command_overview"]["statuses"]["in_flight"] == 1
    assert dashboard_payload["command_overview"]["statuses"]["total"] == 2

    instance_response = client.get(
        f"/api/v1/services/billing-sync/instances/{instance.instance_id}",
        params={"environment": "prod"},
    )
    assert instance_response.status_code == 200
    instance_payload = instance_response.json()
    assert instance_payload["instance"]["active_session"]["session_id"] == "sess_live_vm_prod_1"
    assert instance_payload["instance"]["active_session"]["status"] == "active"
    assert instance_payload["instance"]["active_session"]["accepted_capabilities"] == [
        "telemetry.sync",
        "command.sync_now",
        "command.flush_metrics",
    ]


def test_list_service_commands_and_sessions_filters(client, db_session) -> None:
    now = datetime.now(UTC)
    service = seed_service(db_session, name="billing-sync", environment="prod")
    first_instance = seed_instance(
        db_session,
        service,
        node_name="vm-prod-1",
        status="ok",
        last_seen_at=now - timedelta(seconds=5),
    )
    second_instance = seed_instance(
        db_session,
        service,
        node_name="vm-prod-2",
        status="degraded",
        last_seen_at=now - timedelta(minutes=3),
    )
    seed_agent_session(
        db_session,
        service,
        first_instance,
        status="active",
        connected_at=now - timedelta(minutes=3),
        session_id="sess_active",
    )
    seed_agent_session(
        db_session,
        service,
        second_instance,
        status="disconnected",
        connected_at=now - timedelta(minutes=10),
        last_message_at=now - timedelta(minutes=7),
        session_id="sess_disconnected",
    )
    seed_agent_command(
        db_session,
        service,
        first_instance,
        command_id="cmd_instance_one_sync",
        kind="sync_now",
        status="accepted",
        created_at=now - timedelta(minutes=1),
        timeout_s=300,
        session_id="sess_active",
        ack_status="accepted",
        dispatched_at=now - timedelta(minutes=1) + timedelta(seconds=1),
        acked_at=now - timedelta(minutes=1) + timedelta(seconds=2),
    )
    seed_agent_command(
        db_session,
        service,
        second_instance,
        command_id="cmd_instance_two_flush",
        kind="flush_metrics",
        status="failed",
        created_at=now - timedelta(minutes=2),
        session_id="sess_disconnected",
        ack_status="accepted",
        dispatched_at=now - timedelta(minutes=2) + timedelta(seconds=1),
        acked_at=now - timedelta(minutes=2) + timedelta(seconds=2),
        finished_at=now - timedelta(minutes=2) + timedelta(seconds=6),
        error_code="runtime_error",
        error_message="metric exporter is unavailable",
    )
    db_session.commit()

    commands_response = client.get(
        "/api/v1/services/billing-sync/commands",
        params={"environment": "prod", "status": "failed"},
    )
    assert commands_response.status_code == 200
    commands_payload = commands_response.json()
    assert commands_payload["total"] == 1
    assert commands_payload["items"][0]["command_id"] == "cmd_instance_two_flush"
    assert commands_payload["items"][0]["node_name"] == "vm-prod-2"

    filtered_by_instance = client.get(
        "/api/v1/services/billing-sync/commands",
        params={"environment": "prod", "instance_id": str(first_instance.instance_id)},
    )
    assert filtered_by_instance.status_code == 200
    assert filtered_by_instance.json()["total"] == 1
    assert filtered_by_instance.json()["items"][0]["command_id"] == "cmd_instance_one_sync"

    sessions_response = client.get(
        "/api/v1/services/billing-sync/sessions",
        params={"environment": "prod", "status": "active"},
    )
    assert sessions_response.status_code == 200
    sessions_payload = sessions_response.json()
    assert sessions_payload["total"] == 1
    assert sessions_payload["items"][0]["session_id"] == "sess_active"
    assert sessions_payload["items"][0]["node_name"] == "vm-prod-1"


def test_command_audit_metadata_requires_reason_and_records_actor(client, db_session) -> None:
    now = datetime.now(UTC)
    service = seed_service(db_session, name="billing-sync", environment="prod")
    instance = seed_instance(
        db_session,
        service,
        node_name="vm-prod-1",
        status="ok",
        last_seen_at=now - timedelta(seconds=5),
    )
    seed_agent_session(
        db_session,
        service,
        instance,
        status="active",
        connected_at=now - timedelta(minutes=1),
        session_id="sess_active",
        accepted_capabilities_json=[
            "telemetry.sync",
            "command.shutdown",
            "command.sync_now",
        ],
    )
    db_session.commit()
    login_console_admin(client)

    missing_reason = client.post(
        f"/api/v1/instances/{instance.instance_id}/commands",
        json={"kind": "shutdown", "args": {}, "timeout_s": 30},
    )
    assert missing_reason.status_code == 422
    assert "reason is required when kind=shutdown" in missing_reason.text

    create_response = client.post(
        f"/api/v1/instances/{instance.instance_id}/commands",
        json={
            "kind": "sync_now",
            "args": {},
            "timeout_s": 30,
            "delivery_mode": "queue_until_reconnect",
            "reason": "Drain node before deploy rollback",
        },
    )
    assert create_response.status_code == 200
    payload = create_response.json()
    assert payload["created_by"] == "admin"
    assert payload["reason"] == "Drain node before deploy rollback"
    assert payload["source_surface"] == "instance_detail"

    command = db_session.scalar(
        select(AgentCommand).where(AgentCommand.command_id == payload["command_id"])
    )
    assert command is not None
    assert command.status == "pending"
    assert command.created_by == "admin"
    assert command.reason == "Drain node before deploy rollback"
    assert command.source_surface == "instance_detail"

    commands_response = client.get(
        "/api/v1/services/billing-sync/commands",
        params={"environment": "prod"},
    )
    assert commands_response.status_code == 200
    command_payload = commands_response.json()["items"][0]
    assert command_payload["created_by"] == "admin"
    assert command_payload["reason"] == "Drain node before deploy rollback"
    assert command_payload["source_surface"] == "instance_detail"


def test_instance_command_queue_until_reconnect_uses_latest_compatible_session(
    client,
    db_session,
) -> None:
    now = datetime.now(UTC)
    service = seed_service(db_session, name="billing-sync", environment="prod")
    instance = seed_instance(
        db_session,
        service,
        node_name="vm-queueable",
        status="ok",
        last_seen_at=now - timedelta(minutes=6),
    )
    seed_agent_session(
        db_session,
        service,
        instance,
        status="disconnected",
        connected_at=now - timedelta(minutes=15),
        last_message_at=now - timedelta(minutes=7),
        session_id="sess_last_known",
        accepted_capabilities_json=[
            "telemetry.sync",
            "command.sync_now",
            "command.flush_metrics",
        ],
    )
    db_session.commit()

    response = client.post(
        f"/api/v1/instances/{instance.instance_id}/commands",
        json={
            "kind": "sync_now",
            "args": {},
            "timeout_s": 20,
            "delivery_mode": "queue_until_reconnect",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "pending"
    assert payload["session_id"] is None

    command = db_session.scalar(
        select(AgentCommand).where(AgentCommand.command_id == payload["command_id"])
    )
    assert command is not None
    assert command.status == "pending"
    assert command.kind == "sync_now"

    detail_response = client.get(
        f"/api/v1/services/billing-sync/instances/{instance.instance_id}",
        params={"environment": "prod"},
    )
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["latest_session"]["session_id"] == "sess_last_known"
    assert detail_payload["latest_session"]["status"] == "disconnected"


def test_instance_command_dispatch_now_only_requires_live_connection(client, db_session) -> None:
    now = datetime.now(UTC)
    service = seed_service(db_session, name="billing-sync", environment="prod")
    instance = seed_instance(
        db_session,
        service,
        node_name="vm-live-gap",
        status="ok",
        last_seen_at=now - timedelta(seconds=20),
    )
    seed_agent_session(
        db_session,
        service,
        instance,
        status="active",
        connected_at=now - timedelta(minutes=1),
        session_id="sess_without_registry",
        accepted_capabilities_json=[
            "telemetry.sync",
            "command.sync_now",
        ],
    )
    db_session.commit()

    response = client.post(
        f"/api/v1/instances/{instance.instance_id}/commands",
        json={
            "kind": "sync_now",
            "args": {},
            "timeout_s": 20,
        },
    )
    assert response.status_code == 409
    assert "no live control connection" in response.json()["detail"]
    assert db_session.query(AgentCommand).count() == 0


def test_maintenance_commands_require_reason_and_live_delivery_mode(client, db_session) -> None:
    now = datetime.now(UTC)
    service = seed_service(db_session, name="billing-sync", environment="prod")
    instance = seed_instance(
        db_session,
        service,
        node_name="vm-maintenance",
        status="ok",
        last_seen_at=now - timedelta(seconds=20),
    )
    seed_agent_session(
        db_session,
        service,
        instance,
        status="active",
        connected_at=now - timedelta(minutes=1),
        session_id="sess_maintenance",
        accepted_capabilities_json=[
            "telemetry.sync",
            "command.restart",
            "command.drain",
            "command.pause_task",
        ],
    )
    db_session.commit()

    missing_reason = client.post(
        f"/api/v1/instances/{instance.instance_id}/commands",
        json={
            "kind": "restart",
            "args": {},
            "timeout_s": 30,
        },
    )
    assert missing_reason.status_code == 422
    assert "reason is required when kind=restart" in missing_reason.text

    invalid_queue_mode = client.post(
        f"/api/v1/instances/{instance.instance_id}/commands",
        json={
            "kind": "drain",
            "args": {},
            "timeout_s": 30,
            "reason": "Quiesce intake before connector maintenance",
            "delivery_mode": "queue_until_reconnect",
        },
    )
    assert invalid_queue_mode.status_code == 422
    assert (
        "delivery_mode=queue_until_reconnect is not supported when kind=drain"
        in invalid_queue_mode.text
    )

    missing_task_reason = client.post(
        f"/api/v1/instances/{instance.instance_id}/commands",
        json={
            "kind": "pause_task",
            "args": {"task_name": "sync_users"},
            "timeout_s": 120,
        },
    )
    assert missing_task_reason.status_code == 422
    assert "reason is required when kind=pause_task" in missing_task_reason.text


def test_service_command_fanout_queues_supported_offline_targets_and_rejects_capability_mismatches(
    client,
    db_session,
) -> None:
    now = datetime.now(UTC)
    service = seed_service(db_session, name="billing-sync", environment="prod")
    queueable_instance = seed_instance(
        db_session,
        service,
        node_name="vm-queueable",
        status="ok",
        last_seen_at=now - timedelta(minutes=5),
    )
    rejected_instance = seed_instance(
        db_session,
        service,
        node_name="vm-rejected",
        status="ok",
        last_seen_at=now - timedelta(minutes=8),
    )
    seed_agent_session(
        db_session,
        service,
        queueable_instance,
        status="disconnected",
        connected_at=now - timedelta(minutes=15),
        last_message_at=now - timedelta(minutes=6),
        session_id="sess_queueable",
    )
    rejected_session = seed_agent_session(
        db_session,
        service,
        rejected_instance,
        status="disconnected",
        connected_at=now - timedelta(minutes=12),
        last_message_at=now - timedelta(minutes=9),
        session_id="sess_rejected",
    )
    rejected_session.capabilities_json = ["telemetry.sync", "command.flush_metrics"]
    rejected_session.accepted_capabilities_json = ["telemetry.sync", "command.flush_metrics"]
    db_session.commit()

    missing_reason = client.post(
        "/api/v1/services/billing-sync/commands",
        params={"environment": "prod"},
        json={
            "kind": "sync_now",
            "target_mode": "selected_instances",
            "target_instance_ids": [
                str(queueable_instance.instance_id),
                str(rejected_instance.instance_id),
            ],
            "offline_behavior": "queue",
            "timeout_s": 30,
        },
    )
    assert missing_reason.status_code == 422
    assert "reason" in missing_reason.text

    response = client.post(
        "/api/v1/services/billing-sync/commands",
        params={"environment": "prod"},
        json={
            "kind": "sync_now",
            "reason": "Reconcile queue consumers after incident review",
            "target_mode": "selected_instances",
            "target_instance_ids": [
                str(queueable_instance.instance_id),
                str(rejected_instance.instance_id),
            ],
            "offline_behavior": "queue",
            "timeout_s": 30,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["counts"] == {
        "dispatched": 0,
        "queued": 1,
        "skipped": 0,
        "rejected": 1,
        "total": 2,
    }
    assert payload["queued"][0]["instance_id"] == str(queueable_instance.instance_id)
    assert payload["queued"][0]["reason_code"] == "no_active_session"
    assert payload["queued"][0]["command_id"] is not None
    assert payload["rejected"][0]["instance_id"] == str(rejected_instance.instance_id)
    assert payload["rejected"][0]["reason_code"] == "missing_capability"

    commands = db_session.scalars(
        select(AgentCommand).where(AgentCommand.service_id == service.id)
    ).all()
    assert len(commands) == 1
    assert commands[0].instance_id == queueable_instance.instance_id
    assert commands[0].status == "pending"
    assert commands[0].kind == "sync_now"
    assert commands[0].reason == "Reconcile queue consumers after incident review"
    assert commands[0].source_surface == "service_detail_fanout"


def test_service_command_fanout_rejects_queue_mode_for_maintenance_commands(
    client,
    db_session,
) -> None:
    now = datetime.now(UTC)
    service = seed_service(db_session, name="billing-sync", environment="prod")
    instance = seed_instance(
        db_session,
        service,
        node_name="vm-maintenance",
        status="ok",
        last_seen_at=now - timedelta(minutes=6),
    )
    seed_agent_session(
        db_session,
        service,
        instance,
        status="disconnected",
        connected_at=now - timedelta(minutes=10),
        last_message_at=now - timedelta(minutes=7),
        session_id="sess_restart_last_known",
        accepted_capabilities_json=[
            "telemetry.sync",
            "command.restart",
        ],
    )
    db_session.commit()

    response = client.post(
        "/api/v1/services/billing-sync/commands",
        params={"environment": "prod"},
        json={
            "kind": "restart",
            "reason": "Cycle process after runtime patch",
            "target_mode": "selected_instances",
            "target_instance_ids": [str(instance.instance_id)],
            "offline_behavior": "queue",
            "timeout_s": 30,
        },
    )
    assert response.status_code == 422
    assert "offline_behavior=queue is not supported when kind=restart" in response.text


def test_task_command_fanout_dispatches_and_records_task_detail_source_surface(
    client,
    db_session,
) -> None:
    now = datetime.now(UTC)
    service = seed_service(db_session, name="billing-sync", environment="prod")
    instance = seed_instance(
        db_session,
        service,
        node_name="vm-task-control",
        status="ok",
        last_seen_at=now - timedelta(seconds=10),
        app_snapshot_json={
            "name": "billing-sync",
            "task_control_states": [
                {
                    "task_name": "sync_users",
                    "supported_commands": ["pause_task", "resume_task"],
                    "pause_requested": False,
                    "paused": False,
                    "accepting_new_work": True,
                    "runner_count": 1,
                    "parked_runner_count": 0,
                    "fetching_runner_count": 1,
                    "inflight_task_count": 0,
                }
            ],
        },
    )
    session = seed_agent_session(
        db_session,
        service,
        instance,
        status="active",
        connected_at=now - timedelta(minutes=1),
        session_id="sess_task_control",
        accepted_capabilities_json=[
            "telemetry.sync",
            "command.pause_task",
            "command.resume_task",
        ],
    )
    db_session.commit()

    send_queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    asyncio.run(
        agent_connection_registry.register(
            instance_id=instance.instance_id,
            session_id=session.session_id,
            send_queue=send_queue,
        )
    )
    try:
        response = client.post(
            "/api/v1/services/billing-sync/tasks/sync_users/commands",
            params={"environment": "prod"},
            json={
                "kind": "pause_task",
                "reason": "Pause this task during upstream reconciliation",
                "target_mode": "all_online",
                "offline_behavior": "skip",
                "timeout_s": 120,
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["counts"] == {
            "dispatched": 1,
            "queued": 0,
            "skipped": 0,
            "rejected": 0,
            "total": 1,
        }
        assert payload["dispatched"][0]["instance_id"] == str(instance.instance_id)
        assert payload["kind"] == "pause_task"

        command_message = send_queue.get_nowait()
        assert command_message["payload"]["kind"] == "pause_task"
        assert command_message["payload"]["args"] == {"task_name": "sync_users"}

        command = db_session.scalar(
            select(AgentCommand).where(
                AgentCommand.command_id == payload["dispatched"][0]["command_id"]
            )
        )
        assert command is not None
        assert command.kind == "pause_task"
        assert command.reason == "Pause this task during upstream reconciliation"
        assert command.source_surface == "task_detail"
    finally:
        asyncio.run(
            agent_connection_registry.unregister(
                instance_id=instance.instance_id,
                session_id=session.session_id,
            )
        )


def test_task_command_fanout_rejects_queue_mode(client, db_session) -> None:
    now = datetime.now(UTC)
    service = seed_service(db_session, name="billing-sync", environment="prod")
    instance = seed_instance(
        db_session,
        service,
        node_name="vm-task-control",
        status="ok",
        last_seen_at=now - timedelta(minutes=6),
        app_snapshot_json={
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
        },
    )
    seed_agent_session(
        db_session,
        service,
        instance,
        status="disconnected",
        connected_at=now - timedelta(minutes=10),
        last_message_at=now - timedelta(minutes=7),
        session_id="sess_task_pause_last_known",
        accepted_capabilities_json=[
            "telemetry.sync",
            "command.pause_task",
        ],
    )
    db_session.commit()

    response = client.post(
        "/api/v1/services/billing-sync/tasks/sync_users/commands",
        params={"environment": "prod"},
        json={
            "kind": "pause_task",
            "reason": "Queueing task pause is not supported",
            "target_mode": "selected_instances",
            "target_instance_ids": [str(instance.instance_id)],
            "offline_behavior": "queue",
            "timeout_s": 120,
        },
    )
    assert response.status_code == 422
    assert "offline_behavior=queue is not supported when kind=pause_task" in response.text


def test_task_command_fanout_targets_selected_instances_only(
    client,
    db_session,
) -> None:
    now = datetime.now(UTC)
    service = seed_service(db_session, name="billing-sync", environment="prod")
    selected_a = seed_instance(
        db_session,
        service,
        node_name="vm-task-a",
        status="ok",
        last_seen_at=now - timedelta(seconds=10),
        app_snapshot_json={
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
        },
    )
    skipped_instance = seed_instance(
        db_session,
        service,
        node_name="vm-task-b",
        status="ok",
        last_seen_at=now - timedelta(seconds=9),
        app_snapshot_json={
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
        },
    )
    selected_c = seed_instance(
        db_session,
        service,
        node_name="vm-task-c",
        status="ok",
        last_seen_at=now - timedelta(seconds=8),
        app_snapshot_json={
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
        },
    )
    session_a = seed_agent_session(
        db_session,
        service,
        selected_a,
        status="active",
        connected_at=now - timedelta(minutes=2),
        session_id="sess_task_a",
        accepted_capabilities_json=["telemetry.sync", "command.pause_task"],
    )
    session_b = seed_agent_session(
        db_session,
        service,
        skipped_instance,
        status="active",
        connected_at=now - timedelta(minutes=2),
        session_id="sess_task_b",
        accepted_capabilities_json=["telemetry.sync", "command.pause_task"],
    )
    session_c = seed_agent_session(
        db_session,
        service,
        selected_c,
        status="active",
        connected_at=now - timedelta(minutes=2),
        session_id="sess_task_c",
        accepted_capabilities_json=["telemetry.sync", "command.pause_task"],
    )
    db_session.commit()

    queue_a: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    queue_b: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    queue_c: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    asyncio.run(
        agent_connection_registry.register(
            instance_id=selected_a.instance_id,
            session_id=session_a.session_id,
            send_queue=queue_a,
        )
    )
    asyncio.run(
        agent_connection_registry.register(
            instance_id=skipped_instance.instance_id,
            session_id=session_b.session_id,
            send_queue=queue_b,
        )
    )
    asyncio.run(
        agent_connection_registry.register(
            instance_id=selected_c.instance_id,
            session_id=session_c.session_id,
            send_queue=queue_c,
        )
    )
    try:
        response = client.post(
            "/api/v1/services/billing-sync/tasks/sync_users/commands",
            params={"environment": "prod"},
            json={
                "kind": "pause_task",
                "reason": "Pause only the selected task executors",
                "target_mode": "selected_instances",
                "target_instance_ids": [
                    str(selected_a.instance_id),
                    str(selected_c.instance_id),
                ],
                "offline_behavior": "skip",
                "timeout_s": 120,
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["counts"] == {
            "dispatched": 2,
            "queued": 0,
            "skipped": 0,
            "rejected": 0,
            "total": 2,
        }
        assert {item["instance_id"] for item in payload["dispatched"]} == {
            str(selected_a.instance_id),
            str(selected_c.instance_id),
        }
        assert queue_a.get_nowait()["payload"]["args"] == {"task_name": "sync_users"}
        assert queue_c.get_nowait()["payload"]["args"] == {"task_name": "sync_users"}
        assert queue_b.empty()
    finally:
        asyncio.run(
            agent_connection_registry.unregister(
                instance_id=selected_a.instance_id,
                session_id=session_a.session_id,
            )
        )
        asyncio.run(
            agent_connection_registry.unregister(
                instance_id=skipped_instance.instance_id,
                session_id=session_b.session_id,
            )
        )
        asyncio.run(
            agent_connection_registry.unregister(
                instance_id=selected_c.instance_id,
                session_id=session_c.session_id,
            )
        )


def test_task_command_fanout_dispatches_replay_dead_letters_with_limit(
    client,
    db_session,
) -> None:
    now = datetime.now(UTC)
    service = seed_service(db_session, name="billing-sync", environment="prod")
    instance = seed_instance(
        db_session,
        service,
        node_name="vm-task-replay",
        status="ok",
        last_seen_at=now - timedelta(seconds=10),
        app_snapshot_json={
            "name": "billing-sync",
            "task_control_states": [
                {
                    "task_name": "sync_users",
                    "supported_commands": [
                        "pause_task",
                        "resume_task",
                        "discard_dead_letters",
                        "replay_dead_letters",
                    ],
                    "pause_requested": False,
                    "paused": False,
                    "accepting_new_work": True,
                    "runner_count": 1,
                    "parked_runner_count": 0,
                    "fetching_runner_count": 1,
                    "inflight_task_count": 0,
                }
            ],
        },
    )
    session = seed_agent_session(
        db_session,
        service,
        instance,
        status="active",
        connected_at=now - timedelta(minutes=1),
        session_id="sess_task_replay",
        accepted_capabilities_json=[
            "telemetry.sync",
            "command.replay_dead_letters",
        ],
    )
    db_session.commit()

    send_queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    asyncio.run(
        agent_connection_registry.register(
            instance_id=instance.instance_id,
            session_id=session.session_id,
            send_queue=send_queue,
        )
    )
    try:
        response = client.post(
            "/api/v1/services/billing-sync/tasks/sync_users/commands",
            params={"environment": "prod"},
            json={
                "kind": "replay_dead_letters",
                "args": {"limit": 5},
                "reason": "Replay dead letters after upstream fix",
                "target_mode": "all_online",
                "offline_behavior": "skip",
                "timeout_s": 120,
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["counts"] == {
            "dispatched": 1,
            "queued": 0,
            "skipped": 0,
            "rejected": 0,
            "total": 1,
        }
        assert payload["kind"] == "replay_dead_letters"
        command_message = send_queue.get_nowait()
        assert command_message["payload"]["kind"] == "replay_dead_letters"
        assert command_message["payload"]["args"] == {"task_name": "sync_users", "limit": 5}
    finally:
        asyncio.run(
            agent_connection_registry.unregister(
                instance_id=instance.instance_id,
                session_id=session.session_id,
            )
        )


def test_task_command_fanout_dispatches_discard_dead_letters_with_limit(
    client,
    db_session,
) -> None:
    now = datetime.now(UTC)
    service = seed_service(db_session, name="billing-sync", environment="prod")
    instance = seed_instance(
        db_session,
        service,
        node_name="vm-task-discard",
        status="ok",
        last_seen_at=now - timedelta(seconds=10),
        app_snapshot_json={
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
        },
    )
    session = seed_agent_session(
        db_session,
        service,
        instance,
        status="active",
        connected_at=now - timedelta(minutes=1),
        session_id="sess_task_discard",
        accepted_capabilities_json=[
            "telemetry.sync",
            "command.discard_dead_letters",
        ],
    )
    db_session.commit()

    send_queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    asyncio.run(
        agent_connection_registry.register(
            instance_id=instance.instance_id,
            session_id=session.session_id,
            send_queue=send_queue,
        )
    )
    try:
        response = client.post(
            "/api/v1/services/billing-sync/tasks/sync_users/commands",
            params={"environment": "prod"},
            json={
                "kind": "discard_dead_letters",
                "args": {"limit": 5},
                "reason": "Discard known-bad dead letters after review",
                "target_mode": "all_online",
                "offline_behavior": "skip",
                "timeout_s": 120,
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["counts"] == {
            "dispatched": 1,
            "queued": 0,
            "skipped": 0,
            "rejected": 0,
            "total": 1,
        }
        assert payload["kind"] == "discard_dead_letters"
        command_message = send_queue.get_nowait()
        assert command_message["payload"]["kind"] == "discard_dead_letters"
        assert command_message["payload"]["args"] == {"task_name": "sync_users", "limit": 5}
    finally:
        asyncio.run(
            agent_connection_registry.unregister(
                instance_id=instance.instance_id,
                session_id=session.session_id,
            )
        )


def test_task_command_fanout_rejects_when_task_snapshot_lacks_discard_support(
    client,
    db_session,
) -> None:
    now = datetime.now(UTC)
    service = seed_service(db_session, name="billing-sync", environment="prod")
    instance = seed_instance(
        db_session,
        service,
        node_name="vm-task-discard-unsupported",
        status="ok",
        last_seen_at=now - timedelta(seconds=10),
        app_snapshot_json={
            "name": "billing-sync",
            "task_control_states": [
                {
                    "task_name": "sync_users",
                    "supported_commands": ["pause_task", "resume_task"],
                    "pause_requested": False,
                    "paused": False,
                    "accepting_new_work": True,
                    "runner_count": 1,
                    "parked_runner_count": 0,
                    "fetching_runner_count": 1,
                    "inflight_task_count": 0,
                }
            ],
        },
    )
    seed_agent_session(
        db_session,
        service,
        instance,
        status="active",
        connected_at=now - timedelta(minutes=1),
        session_id="sess_task_discard_unsupported",
        accepted_capabilities_json=[
            "telemetry.sync",
            "command.discard_dead_letters",
        ],
    )
    db_session.commit()

    response = client.post(
        "/api/v1/services/billing-sync/tasks/sync_users/commands",
        params={"environment": "prod"},
        json={
            "kind": "discard_dead_letters",
            "args": {"limit": 3},
            "reason": "Discard should be blocked for unsupported tasks",
            "target_mode": "all_online",
            "offline_behavior": "skip",
            "timeout_s": 120,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["counts"] == {
        "dispatched": 0,
        "queued": 0,
        "skipped": 0,
        "rejected": 1,
        "total": 1,
    }
    assert payload["rejected"][0]["reason_code"] == "task_command_unsupported"
    assert "does not advertise discard_dead_letters" in payload["rejected"][0]["reason_message"]


def test_task_command_fanout_rejects_when_task_snapshot_lacks_replay_support(
    client,
    db_session,
) -> None:
    now = datetime.now(UTC)
    service = seed_service(db_session, name="billing-sync", environment="prod")
    instance = seed_instance(
        db_session,
        service,
        node_name="vm-task-replay-unsupported",
        status="ok",
        last_seen_at=now - timedelta(seconds=10),
        app_snapshot_json={
            "name": "billing-sync",
            "task_control_states": [
                {
                    "task_name": "sync_users",
                    "supported_commands": ["pause_task", "resume_task"],
                    "pause_requested": False,
                    "paused": False,
                    "accepting_new_work": True,
                    "runner_count": 1,
                    "parked_runner_count": 0,
                    "fetching_runner_count": 1,
                    "inflight_task_count": 0,
                }
            ],
        },
    )
    seed_agent_session(
        db_session,
        service,
        instance,
        status="active",
        connected_at=now - timedelta(minutes=1),
        session_id="sess_task_replay_unsupported",
        accepted_capabilities_json=[
            "telemetry.sync",
            "command.replay_dead_letters",
        ],
    )
    db_session.commit()

    response = client.post(
        "/api/v1/services/billing-sync/tasks/sync_users/commands",
        params={"environment": "prod"},
        json={
            "kind": "replay_dead_letters",
            "args": {"limit": 3},
            "reason": "Replay should be blocked for unsupported tasks",
            "target_mode": "all_online",
            "offline_behavior": "skip",
            "timeout_s": 120,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["counts"] == {
        "dispatched": 0,
        "queued": 0,
        "skipped": 0,
        "rejected": 1,
        "total": 1,
    }
    assert payload["rejected"][0]["reason_code"] == "task_command_unsupported"
    assert "does not advertise replay_dead_letters" in payload["rejected"][0]["reason_message"]


def test_service_command_queries_reconcile_expired_status(client, db_session) -> None:
    now = datetime.now(UTC)
    service = seed_service(db_session, name="billing-sync", environment="prod")
    instance = seed_instance(
        db_session,
        service,
        node_name="vm-prod-1",
        status="ok",
        last_seen_at=now - timedelta(seconds=5),
    )
    seed_agent_command(
        db_session,
        service,
        instance,
        command_id="cmd_expired_ping",
        kind="ping",
        status="pending",
        created_at=now - timedelta(seconds=20),
    )
    db_session.commit()

    dashboard_response = client.get(
        "/api/v1/services/billing-sync/dashboard",
        params={"environment": "prod"},
    )
    assert dashboard_response.status_code == 200
    dashboard_payload = dashboard_response.json()
    assert dashboard_payload["command_overview"]["statuses"]["expired"] == 1
    assert dashboard_payload["command_overview"]["statuses"]["in_flight"] == 0

    commands_response = client.get(
        "/api/v1/services/billing-sync/commands",
        params={"environment": "prod", "status": "expired"},
    )
    assert commands_response.status_code == 200
    commands_payload = commands_response.json()
    assert commands_payload["total"] == 1
    assert commands_payload["items"][0]["status"] == "expired"


def test_list_service_tasks_aggregates_metrics_and_events(client, db_session) -> None:
    now = datetime.now(UTC)
    service = seed_service(
        db_session,
        name="billing-sync",
        environment="prod",
        latest_topology_hash="sha256:topology-a",
        latest_sync_at=now - timedelta(minutes=1),
    )
    instance = seed_instance(
        db_session,
        service,
        node_name="vm-online",
        status="ok",
        last_seen_at=now - timedelta(seconds=15),
        last_sync_at=now - timedelta(minutes=1),
        last_topology_hash="sha256:topology-a",
        app_snapshot_json={"name": "billing-sync", "topology_hash": "sha256:topology-a"},
    )
    seed_task_definition(
        db_session,
        service,
        task_name="sync_users",
        description="Continuously reconcile billing users into the downstream warehouse.",
        source_name="interval:3600s",
        source_kind="interval",
        source_config_json={"seconds": 3600, "immediate": False, "overlap": "skip"},
        emit_json=[
            {
                "kind": "mysql_table_sink",
                "name": "mysql:dw_users",
                "config": {"table": "dw_users", "mode": "upsert", "keys": ["id"]},
            }
        ],
        concurrency=16,
        timeout_s=30.0,
        retry_policy={"kind": "max_attempts", "config": {"max_attempts": 5, "delay_s": 10}},
        topology_hash="sha256:topology-a",
    )
    seed_task_definition(
        db_session,
        service,
        task_name="cleanup_orphans",
        description="Handle orphan cleanup tasks that still emit lifecycle events.",
        source_name="rabbitmq:orders",
        source_kind="rabbitmq_queue",
        source_config_json={"queue": "orders", "prefetch": 100},
        emit_json=[],
        concurrency=32,
        timeout_s=None,
        retry_policy={"kind": "no_retry", "config": {}},
        topology_hash="sha256:topology-a",
    )
    seed_task_definition(
        db_session,
        service,
        task_name="nightly_reconcile",
        description="Run the full nightly reconciliation pass for dormant records.",
        source_name="interval:86400s",
        source_kind="interval",
        source_config_json={"seconds": 86400},
        emit_json=[],
        concurrency=1,
        timeout_s=120.0,
        retry_policy={"kind": "no_retry", "config": {}},
        topology_hash="sha256:topology-a",
    )

    first_started = now - timedelta(minutes=10)
    first_ended = now - timedelta(minutes=9)
    second_started = now - timedelta(minutes=4)
    second_ended = now - timedelta(minutes=3)
    old_started = now - timedelta(minutes=80)
    old_ended = now - timedelta(minutes=79)

    first_window = seed_metric_window(
        db_session,
        service,
        instance,
        task_name="sync_users",
        window_id="sync_users:first",
        window_started_at=first_started,
        window_ended_at=first_ended,
        succeeded=10,
    )
    first_window.fetched = 12
    first_window.started = 10
    first_window.retried = 1
    first_window.failed = 1
    first_window.avg_duration_ms = 100.0
    first_window.p95_duration_ms = 180.0

    second_window = seed_metric_window(
        db_session,
        service,
        instance,
        task_name="sync_users",
        window_id="sync_users:second",
        window_started_at=second_started,
        window_ended_at=second_ended,
        succeeded=30,
    )
    second_window.fetched = 33
    second_window.started = 30
    second_window.retried = 2
    second_window.failed = 3
    second_window.dead_lettered = 1
    second_window.timeouts = 1
    second_window.avg_duration_ms = 200.0
    second_window.p95_duration_ms = 300.0

    seed_metric_window(
        db_session,
        service,
        instance,
        task_name="old_task",
        window_id="old_task:stale",
        window_started_at=old_started,
        window_ended_at=old_ended,
        succeeded=99,
    )

    seed_task_event(
        db_session,
        service,
        instance,
        event_id="evt_task_failed",
        task_name="sync_users",
        kind="failed",
        occurred_at=now - timedelta(minutes=2),
    )
    seed_task_event(
        db_session,
        service,
        instance,
        event_id="evt_task_retried",
        task_name="sync_users",
        kind="retried",
        occurred_at=now - timedelta(minutes=1),
    )
    seed_task_event(
        db_session,
        service,
        instance,
        event_id="evt_task_event_only",
        task_name="cleanup_orphans",
        kind="succeeded",
        occurred_at=now - timedelta(minutes=5),
    )
    seed_task_event(
        db_session,
        service,
        instance,
        event_id="evt_task_old",
        task_name="old_task",
        kind="failed",
        occurred_at=now - timedelta(minutes=70),
    )
    db_session.commit()

    response = client.get(
        "/api/v1/services/billing-sync/tasks",
        params={"environment": "prod", "lookback_minutes": 60},
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["total"] == 3
    by_task_name = {item["task_name"]: item for item in payload["items"]}

    sync_users = by_task_name["sync_users"]
    assert (
        sync_users["description"]
        == "Continuously reconcile billing users into the downstream warehouse."
    )
    assert sync_users["source_name"] == "interval:3600s"
    assert sync_users["source_kind"] == "interval"
    assert sync_users["source_config"] == {
        "seconds": 3600,
        "immediate": False,
        "overlap": "skip",
    }
    assert sync_users["emit"] == [
        {
            "kind": "mysql_table_sink",
            "name": "mysql:dw_users",
            "config": {"table": "dw_users", "mode": "upsert", "keys": ["id"]},
        }
    ]
    assert sync_users["concurrency"] == 16
    assert sync_users["timeout_s"] == 30.0
    assert sync_users["retry_policy"] == {
        "kind": "max_attempts",
        "config": {"max_attempts": 5, "delay_s": 10},
    }
    assert sync_users["topology_hash"] == "sha256:topology-a"
    assert sync_users["metric_window_count"] == 2
    assert sync_users["fetched"] == 45
    assert sync_users["started"] == 40
    assert sync_users["succeeded"] == 40
    assert sync_users["retried"] == 3
    assert sync_users["failed"] == 4
    assert sync_users["dead_lettered"] == 1
    assert sync_users["timeouts"] == 1
    assert sync_users["weighted_avg_duration_ms"] == 175.0
    assert sync_users["max_p95_duration_ms"] == 300.0
    assert sync_users["event_counts"] == {
        "failed": 1,
        "retried": 1,
        "dead_lettered": 0,
        "cancelled": 0,
        "succeeded": 0,
    }

    cleanup_orphans = by_task_name["cleanup_orphans"]
    assert (
        cleanup_orphans["description"]
        == "Handle orphan cleanup tasks that still emit lifecycle events."
    )
    assert cleanup_orphans["source_kind"] == "rabbitmq_queue"
    assert cleanup_orphans["emit"] == []
    assert cleanup_orphans["retry_policy"] == {"kind": "no_retry", "config": {}}
    assert cleanup_orphans["metric_window_count"] == 0
    assert cleanup_orphans["last_event_at"] is not None
    assert cleanup_orphans["event_counts"] == {
        "failed": 0,
        "retried": 0,
        "dead_lettered": 0,
        "cancelled": 0,
        "succeeded": 1,
    }

    nightly_reconcile = by_task_name["nightly_reconcile"]
    assert nightly_reconcile["metric_window_count"] == 0
    assert (
        nightly_reconcile["description"]
        == "Run the full nightly reconciliation pass for dormant records."
    )
    assert nightly_reconcile["event_counts"] == {
        "failed": 0,
        "retried": 0,
        "dead_lettered": 0,
        "cancelled": 0,
        "succeeded": 0,
    }
    assert nightly_reconcile["source_name"] == "interval:86400s"
    assert nightly_reconcile["timeout_s"] == 120.0

    filtered = client.get(
        "/api/v1/services/billing-sync/tasks",
        params={
            "environment": "prod",
            "lookback_minutes": 60,
            "task_name": "cleanup_orphans",
        },
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["task_name"] == "cleanup_orphans"


def test_get_service_task_detail_returns_summary_windows_and_events(client, db_session) -> None:
    now = datetime.now(UTC)
    service = seed_service(db_session, name="billing-sync", environment="prod")
    instance = seed_instance(
        db_session,
        service,
        node_name="vm-online",
        status="ok",
        last_seen_at=now - timedelta(seconds=10),
    )
    seed_task_definition(
        db_session,
        service,
        task_name="sync_users",
        description="Continuously reconcile billing users into the downstream warehouse.",
        source_name="interval:3600s",
        source_kind="interval",
        source_config_json={"seconds": 3600},
        emit_json=[
            {
                "kind": "mysql_table_sink",
                "name": "mysql:dw_users",
                "config": {"table": "dw_users", "mode": "upsert", "keys": ["id"]},
            }
        ],
        concurrency=16,
        timeout_s=30.0,
        retry_policy={"kind": "max_attempts", "config": {"max_attempts": 5, "delay_s": 10}},
        topology_hash="sha256:topology-a",
    )

    first_window = seed_metric_window(
        db_session,
        service,
        instance,
        task_name="sync_users",
        window_id="sync_users:one",
        window_started_at=now - timedelta(minutes=6),
        window_ended_at=now - timedelta(minutes=5),
        succeeded=20,
    )
    first_window.fetched = 22
    first_window.started = 20
    first_window.retried = 1
    first_window.avg_duration_ms = 120.0
    first_window.p95_duration_ms = 200.0

    second_window = seed_metric_window(
        db_session,
        service,
        instance,
        task_name="sync_users",
        window_id="sync_users:two",
        window_started_at=now - timedelta(minutes=3),
        window_ended_at=now - timedelta(minutes=2),
        succeeded=40,
    )
    second_window.fetched = 44
    second_window.started = 40
    second_window.failed = 2
    second_window.timeouts = 1
    second_window.avg_duration_ms = 180.0
    second_window.p95_duration_ms = 320.0

    seed_task_event(
        db_session,
        service,
        instance,
        event_id="evt_task_detail_failed",
        task_name="sync_users",
        kind="failed",
        occurred_at=now - timedelta(seconds=90),
    )
    seed_task_event(
        db_session,
        service,
        instance,
        event_id="evt_task_detail_retried",
        task_name="sync_users",
        kind="retried",
        occurred_at=now - timedelta(seconds=30),
    )
    db_session.commit()

    response = client.get(
        "/api/v1/services/billing-sync/tasks/sync_users",
        params={
            "environment": "prod",
            "lookback_minutes": 15,
            "metric_window_limit": 5,
            "event_limit": 5,
        },
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["task_name"] == "sync_users"
    assert payload["service"]["name"] == "billing-sync"
    assert payload["summary"]["metric_window_count"] == 2
    assert payload["summary"]["fetched"] == 66
    assert payload["summary"]["started"] == 60
    assert payload["summary"]["succeeded"] == 60
    assert payload["summary"]["failed"] == 2
    assert payload["summary"]["retried"] == 1
    assert payload["summary"]["timeouts"] == 1
    assert payload["summary"]["weighted_avg_duration_ms"] == 160.0
    assert payload["summary"]["max_p95_duration_ms"] == 320.0
    assert (
        payload["summary"]["description"]
        == "Continuously reconcile billing users into the downstream warehouse."
    )
    assert payload["summary"]["source_name"] == "interval:3600s"
    assert payload["summary"]["emit"] == [
        {
            "kind": "mysql_table_sink",
            "name": "mysql:dw_users",
            "config": {"table": "dw_users", "mode": "upsert", "keys": ["id"]},
        }
    ]
    assert payload["summary"]["event_counts"] == {
        "failed": 1,
        "retried": 1,
        "dead_lettered": 0,
        "cancelled": 0,
        "succeeded": 0,
    }
    assert [window["window_id"] for window in payload["recent_metric_windows"]] == [
        "sync_users:two",
        "sync_users:one",
    ]
    assert [event["event_id"] for event in payload["recent_events"]] == [
        "evt_task_detail_retried",
        "evt_task_detail_failed",
    ]
    assert payload["recent_events"][1]["traceback"] == (
        "Traceback (most recent call last):\nTimeoutError: task exceeded timeout\n"
    )


def test_get_service_task_detail_returns_404_for_unknown_task(client, db_session) -> None:
    service = seed_service(db_session, name="billing-sync", environment="prod")
    instance = seed_instance(
        db_session,
        service,
        node_name="vm-online",
        status="ok",
        last_seen_at=datetime.now(UTC),
    )
    seed_metric_window(
        db_session,
        service,
        instance,
        task_name="sync_users",
        window_id="sync_users:one",
        window_started_at=datetime.now(UTC) - timedelta(minutes=2),
        window_ended_at=datetime.now(UTC) - timedelta(minutes=1),
        succeeded=10,
    )
    db_session.commit()

    response = client.get(
        "/api/v1/services/billing-sync/tasks/cleanup_orphans",
        params={"environment": "prod"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == (
        "task cleanup_orphans was not found for service billing-sync/prod"
    )


def test_get_service_task_detail_includes_task_control_state(client, db_session) -> None:
    now = datetime.now(UTC)
    service = seed_service(db_session, name="billing-sync", environment="prod")
    paused_instance = seed_instance(
        db_session,
        service,
        node_name="vm-prod-1",
        status="ok",
        last_seen_at=now - timedelta(seconds=15),
        app_snapshot_json={
            "name": "billing-sync",
            "topology_hash": "sha256:topology-a",
            "task_control_states": [
                {
                    "task_name": "sync_users",
                    "supported_commands": [
                        "pause_task",
                        "resume_task",
                        "discard_dead_letters",
                        "replay_dead_letters",
                    ],
                    "pause_requested": True,
                    "paused": True,
                    "accepting_new_work": False,
                    "runner_count": 2,
                    "parked_runner_count": 2,
                    "fetching_runner_count": 0,
                    "inflight_task_count": 0,
                }
            ],
        },
    )
    running_instance = seed_instance(
        db_session,
        service,
        node_name="vm-prod-2",
        status="ok",
        last_seen_at=now - timedelta(seconds=25),
        app_snapshot_json={
            "name": "billing-sync",
            "topology_hash": "sha256:topology-a",
            "task_control_states": [
                {
                    "task_name": "sync_users",
                    "supported_commands": ["pause_task", "resume_task"],
                    "pause_requested": False,
                    "paused": False,
                    "accepting_new_work": True,
                    "runner_count": 1,
                    "parked_runner_count": 0,
                    "fetching_runner_count": 1,
                    "inflight_task_count": 0,
                }
            ],
        },
    )
    unsupported_instance = seed_instance(
        db_session,
        service,
        node_name="vm-prod-3",
        status="degraded",
        last_seen_at=now - timedelta(seconds=35),
        app_snapshot_json={"name": "billing-sync", "topology_hash": "sha256:topology-a"},
    )
    seed_agent_session(
        db_session,
        service,
        paused_instance,
        status="active",
        connected_at=now - timedelta(minutes=5),
        accepted_capabilities_json=[
            "command.pause_task",
            "command.resume_task",
            "command.discard_dead_letters",
            "command.replay_dead_letters",
        ],
        session_id="sess_paused",
    )
    seed_agent_session(
        db_session,
        service,
        running_instance,
        status="active",
        connected_at=now - timedelta(minutes=4),
        accepted_capabilities_json=["command.pause_task", "command.resume_task"],
        session_id="sess_running",
    )
    seed_metric_window(
        db_session,
        service,
        paused_instance,
        task_name="sync_users",
        window_id="sync_users:control",
        window_started_at=now - timedelta(minutes=3),
        window_ended_at=now - timedelta(minutes=2),
        succeeded=10,
    )
    db_session.commit()

    response = client.get(
        "/api/v1/services/billing-sync/tasks/sync_users",
        params={"environment": "prod"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_control"]["task_name"] == "sync_users"
    assert payload["task_control"]["instances"] == [
        {
            "instance_id": str(paused_instance.instance_id),
            "node_name": "vm-prod-1",
            "connectivity": "online",
            "status": "ok",
            "last_seen_at": payload["task_control"]["instances"][0]["last_seen_at"],
            "supported_commands": [
                "pause_task",
                "resume_task",
                "discard_dead_letters",
                "replay_dead_letters",
            ],
            "state_known": True,
            "pause_requested": True,
            "paused": True,
            "accepting_new_work": False,
            "runner_count": 2,
            "parked_runner_count": 2,
            "fetching_runner_count": 0,
            "inflight_task_count": 0,
        },
        {
            "instance_id": str(running_instance.instance_id),
            "node_name": "vm-prod-2",
            "connectivity": "online",
            "status": "ok",
            "last_seen_at": payload["task_control"]["instances"][1]["last_seen_at"],
            "supported_commands": ["pause_task", "resume_task"],
            "state_known": True,
            "pause_requested": False,
            "paused": False,
            "accepting_new_work": True,
            "runner_count": 1,
            "parked_runner_count": 0,
            "fetching_runner_count": 1,
            "inflight_task_count": 0,
        },
        {
            "instance_id": str(unsupported_instance.instance_id),
            "node_name": "vm-prod-3",
            "connectivity": "online",
            "status": "degraded",
            "last_seen_at": payload["task_control"]["instances"][2]["last_seen_at"],
            "supported_commands": [],
            "state_known": False,
            "pause_requested": None,
            "paused": None,
            "accepting_new_work": None,
            "runner_count": None,
            "parked_runner_count": None,
            "fetching_runner_count": None,
            "inflight_task_count": None,
        },
    ]


def test_get_service_instance_detail_returns_snapshot_and_activity(client, db_session) -> None:
    now = datetime.now(UTC)
    service = seed_service(db_session, name="billing-sync", environment="prod")
    target_instance = seed_instance(
        db_session,
        service,
        node_name="vm-prod-1",
        status="ok",
        last_seen_at=now - timedelta(seconds=20),
        last_sync_at=now - timedelta(seconds=15),
        last_topology_hash="sha256:topology-a",
        app_snapshot_json={
            "name": "billing-sync",
            "shutdown_timeout_s": 30.0,
            "topology_hash": "sha256:topology-a",
            "tasks": [{"name": "sync_users"}],
        },
    )
    other_instance = seed_instance(
        db_session,
        service,
        node_name="vm-prod-2",
        status="degraded",
        last_seen_at=now - timedelta(minutes=5),
    )

    seed_metric_window(
        db_session,
        service,
        target_instance,
        task_name="sync_users",
        window_id="sync_users:instance-one",
        window_started_at=now - timedelta(minutes=4),
        window_ended_at=now - timedelta(minutes=3),
        succeeded=20,
    )
    seed_metric_window(
        db_session,
        service,
        target_instance,
        task_name="cleanup_orphans",
        window_id="cleanup_orphans:instance-two",
        window_started_at=now - timedelta(minutes=2),
        window_ended_at=now - timedelta(minutes=1),
        succeeded=8,
    )
    seed_metric_window(
        db_session,
        service,
        other_instance,
        task_name="sync_users",
        window_id="sync_users:other-instance",
        window_started_at=now - timedelta(minutes=2),
        window_ended_at=now - timedelta(minutes=1),
        succeeded=99,
    )

    seed_task_event(
        db_session,
        service,
        target_instance,
        event_id="evt_instance_detail_failed",
        task_name="sync_users",
        kind="failed",
        occurred_at=now - timedelta(seconds=50),
    )
    seed_task_event(
        db_session,
        service,
        target_instance,
        event_id="evt_instance_detail_succeeded",
        task_name="cleanup_orphans",
        kind="succeeded",
        occurred_at=now - timedelta(seconds=30),
    )
    seed_task_event(
        db_session,
        service,
        other_instance,
        event_id="evt_other_instance",
        task_name="sync_users",
        kind="retried",
        occurred_at=now - timedelta(seconds=10),
    )
    db_session.commit()

    response = client.get(
        f"/api/v1/services/billing-sync/instances/{target_instance.instance_id}",
        params={
            "environment": "prod",
            "lookback_minutes": 15,
            "metric_window_limit": 5,
            "event_limit": 5,
        },
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["instance"]["instance_id"] == str(target_instance.instance_id)
    assert payload["instance"]["node_name"] == "vm-prod-1"
    assert payload["instance"]["connectivity"] == "online"
    assert payload["instance"]["last_topology_hash"] == "sha256:topology-a"
    assert payload["app_snapshot"]["topology_hash"] == "sha256:topology-a"
    assert [window["window_id"] for window in payload["recent_metric_windows"]] == [
        "cleanup_orphans:instance-two",
        "sync_users:instance-one",
    ]
    assert [event["event_id"] for event in payload["recent_events"]] == [
        "evt_instance_detail_succeeded",
        "evt_instance_detail_failed",
    ]


def test_get_service_instance_detail_returns_404_for_unknown_instance(client, db_session) -> None:
    service = seed_service(db_session, name="billing-sync", environment="prod")
    seed_instance(
        db_session,
        service,
        node_name="vm-prod-1",
        status="ok",
        last_seen_at=datetime.now(UTC),
    )
    db_session.commit()

    unknown_instance_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    response = client.get(
        f"/api/v1/services/billing-sync/instances/{unknown_instance_id}",
        params={"environment": "prod"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == (
        f"instance {unknown_instance_id} was not found for service billing-sync/prod"
    )


def test_openapi_contains_query_paths(client) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    payload = response.json()
    assert "/api/v1/services" in payload["paths"]
    assert "/api/v1/services/{service_name}" in payload["paths"]
    assert "/api/v1/services/{service_name}/dashboard" in payload["paths"]
    assert "/api/v1/services/{service_name}/instances" in payload["paths"]
    assert "/api/v1/services/{service_name}/instances/{instance_id}" in payload["paths"]
    assert "/api/v1/services/{service_name}/commands" in payload["paths"]
    assert "/api/v1/services/{service_name}/sessions" in payload["paths"]
    assert "/api/v1/services/{service_name}/tasks" in payload["paths"]
    assert "/api/v1/services/{service_name}/tasks/{task_name}" in payload["paths"]
    assert "/api/v1/services/{service_name}/metric-windows" in payload["paths"]
    assert "/api/v1/services/{service_name}/events" in payload["paths"]
