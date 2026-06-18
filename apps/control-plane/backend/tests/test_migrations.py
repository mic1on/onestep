from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

ROOT_DIR = Path(__file__).resolve().parents[2]
ALEMBIC_INI_PATH = ROOT_DIR / "alembic.ini"
INITIAL_REVISION = "202603080001"
HEAD_REVISION = "202606170003"


def make_alembic_config(database_url: str) -> Config:
    config = Config(str(ALEMBIC_INI_PATH))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_alembic_upgrade_head_creates_expected_schema(tmp_path) -> None:
    db_path = tmp_path / "control-plane.db"
    database_url = f"sqlite:///{db_path}"

    command.upgrade(make_alembic_config(database_url), "head")

    engine = create_engine(database_url)
    inspector = inspect(engine)

    assert set(inspector.get_table_names()) == {
        "agent_commands",
        "agent_sessions",
        "connectors",
        "instances",
        "services",
        "alembic_version",
        "console_sessions",
        "local_roles",
        "local_user_roles",
        "local_users",
        "notification_channels",
        "notification_deliveries",
        "notification_instance_states",
        "task_definitions",
        "task_events",
        "task_metric_windows",
        "worker_agent_commands",
        "worker_agent_sessions",
        "worker_agents",
        "workers",
        "worker_deployment_events",
        "worker_deployments",
        "workflow_packages",
    }

    assert {column["name"] for column in inspector.get_columns("services")} == {
        "id",
        "name",
        "environment",
        "latest_deployment_version",
        "latest_topology_hash",
        "latest_sync_at",
        "created_at",
        "updated_at",
    }
    assert {column["name"] for column in inspector.get_columns("instances")} == {
        "id",
        "service_id",
        "instance_id",
        "node_name",
        "hostname",
        "pid",
        "deployment_version",
        "onestep_version",
        "python_version",
        "started_at",
        "last_sync_at",
        "last_topology_hash",
        "app_snapshot_json",
        "last_sync_sent_at",
        "last_sync_sequence",
        "last_heartbeat_sent_at",
        "last_heartbeat_sequence",
        "last_seen_at",
        "status",
        "created_at",
        "updated_at",
    }
    assert {column["name"] for column in inspector.get_columns("agent_sessions")} == {
        "id",
        "session_id",
        "service_id",
        "instance_id",
        "protocol_version",
        "status",
        "capabilities_json",
        "accepted_capabilities_json",
        "connected_at",
        "last_hello_at",
        "last_message_at",
        "superseded_at",
        "disconnected_at",
        "created_at",
        "updated_at",
    }
    assert {column["name"] for column in inspector.get_columns("agent_commands")} == {
        "id",
        "command_id",
        "service_id",
        "instance_id",
        "session_id",
        "created_by",
        "reason",
        "source_surface",
        "kind",
        "args_json",
        "timeout_s",
        "status",
        "ack_status",
        "result_json",
        "duration_ms",
        "error_code",
        "error_message",
        "dispatched_at",
        "acked_at",
        "finished_at",
        "created_at",
        "updated_at",
    }
    assert {column["name"] for column in inspector.get_columns("notification_channels")} == {
        "id",
        "name",
        "provider",
        "webhook_url",
        "enabled",
        "service_scopes_json",
        "event_types_json",
        "missed_start_grace_seconds",
        "created_at",
        "updated_at",
    }
    assert {column["name"] for column in inspector.get_columns("notification_deliveries")} == {
        "id",
        "channel_id",
        "dedupe_key",
        "event_type",
        "service_name",
        "service_environment",
        "task_name",
        "task_event_id",
        "scheduled_at",
        "status",
        "request_payload_json",
        "response_status_code",
        "response_body",
        "error_message",
        "created_at",
        "sent_at",
    }
    assert {
        column["name"] for column in inspector.get_columns("notification_instance_states")
    } == {
        "id",
        "channel_id",
        "instance_id",
        "last_connectivity",
        "last_transition_at",
        "created_at",
        "updated_at",
    }
    assert {column["name"] for column in inspector.get_columns("worker_agents")} == {
        "id",
        "worker_agent_id",
        "display_name",
        "status",
        "execution_mode",
        "max_concurrent_deployments",
        "used_slots",
        "labels_json",
        "capabilities_json",
        "agent_version",
        "onestep_version",
        "python_version",
        "platform_json",
        "connection_token_hash",
        "registered_at",
        "last_seen_at",
        "created_at",
        "updated_at",
    }
    assert {column["name"] for column in inspector.get_columns("worker_agent_sessions")} == {
        "id",
        "session_id",
        "worker_agent_id",
        "protocol_version",
        "status",
        "capabilities_json",
        "accepted_capabilities_json",
        "connected_at",
        "last_hello_at",
        "last_message_at",
        "disconnected_at",
        "created_at",
        "updated_at",
    }
    assert {column["name"] for column in inspector.get_columns("workflow_packages")} == {
        "id",
        "package_id",
        "workflow_id",
        "version",
        "filename",
        "content_type",
        "checksum_sha256",
        "size_bytes",
        "storage_path",
        "entrypoint",
        "metadata_json",
        "created_by",
        "created_at",
    }
    assert {column["name"] for column in inspector.get_columns("worker_deployments")} == {
        "id",
        "deployment_id",
        "workflow_package_id",
        "worker_agent_id",
        "desired_status",
        "observed_status",
        "runtime_instance_id",
        "execution_mode",
        "params_json",
        "env_json",
        "credential_refs_json",
        "package_checksum",
        "last_error_code",
        "last_error_message",
        "assigned_at",
        "started_at",
        "finished_at",
        "created_by",
        "created_at",
        "updated_at",
    }
    assert {column["name"] for column in inspector.get_columns("worker_agent_commands")} == {
        "id",
        "command_id",
        "worker_agent_id",
        "deployment_id",
        "session_id",
        "kind",
        "args_json",
        "timeout_s",
        "status",
        "ack_status",
        "result_json",
        "error_code",
        "error_message",
        "created_at",
        "dispatched_at",
        "acked_at",
        "finished_at",
        "updated_at",
    }
    assert {column["name"] for column in inspector.get_columns("worker_deployment_events")} == {
        "id",
        "deployment_id",
        "worker_agent_id",
        "event_type",
        "observed_status",
        "message",
        "payload_json",
        "created_at",
    }
    assert {column["name"] for column in inspector.get_columns("local_roles")} == {
        "id",
        "name",
        "created_at",
    }
    assert {column["name"] for column in inspector.get_columns("local_users")} == {
        "id",
        "username",
        "password_hash",
        "is_active",
        "created_at",
        "updated_at",
    }
    assert {column["name"] for column in inspector.get_columns("local_user_roles")} == {
        "id",
        "user_id",
        "role_id",
        "created_at",
    }
    assert {column["name"] for column in inspector.get_columns("console_sessions")} == {
        "id",
        "user_id",
        "token_hash",
        "authenticated_at",
        "expires_at",
        "last_seen_at",
        "revoked_at",
        "created_at",
    }
    assert {column["name"] for column in inspector.get_columns("task_definitions")} == {
        "id",
        "service_id",
        "task_name",
        "description",
        "source_name",
        "source_kind",
        "source_config_json",
        "emit_json",
        "concurrency",
        "timeout_s",
        "retry_policy",
        "topology_hash",
        "updated_at",
    }
    task_definition_columns = {
        column["name"]: column for column in inspector.get_columns("task_definitions")
    }
    agent_session_columns = {
        column["name"]: column for column in inspector.get_columns("agent_sessions")
    }
    agent_command_columns = {
        column["name"]: column for column in inspector.get_columns("agent_commands")
    }
    notification_channel_columns = {
        column["name"]: column for column in inspector.get_columns("notification_channels")
    }
    notification_delivery_columns = {
        column["name"]: column for column in inspector.get_columns("notification_deliveries")
    }
    notification_instance_state_columns = {
        column["name"]: column
        for column in inspector.get_columns("notification_instance_states")
    }
    worker_agent_columns = {
        column["name"]: column for column in inspector.get_columns("worker_agents")
    }
    worker_agent_session_columns = {
        column["name"]: column for column in inspector.get_columns("worker_agent_sessions")
    }
    workflow_package_columns = {
        column["name"]: column for column in inspector.get_columns("workflow_packages")
    }
    worker_deployment_columns = {
        column["name"]: column for column in inspector.get_columns("worker_deployments")
    }
    worker_columns = {
        column["name"]: column for column in inspector.get_columns("workers")
    }
    worker_agent_command_columns = {
        column["name"]: column for column in inspector.get_columns("worker_agent_commands")
    }
    worker_deployment_event_columns = {
        column["name"]: column for column in inspector.get_columns("worker_deployment_events")
    }
    instance_columns = {column["name"]: column for column in inspector.get_columns("instances")}
    assert isinstance(instance_columns["app_snapshot_json"]["type"], sa.JSON)
    assert isinstance(agent_session_columns["capabilities_json"]["type"], sa.JSON)
    assert isinstance(agent_session_columns["accepted_capabilities_json"]["type"], sa.JSON)
    assert isinstance(agent_command_columns["args_json"]["type"], sa.JSON)
    assert isinstance(agent_command_columns["result_json"]["type"], sa.JSON)
    assert isinstance(agent_command_columns["duration_ms"]["type"], sa.Integer)
    assert isinstance(agent_command_columns["source_surface"]["type"], sa.String)
    assert isinstance(notification_channel_columns["service_scopes_json"]["type"], sa.JSON)
    assert isinstance(notification_channel_columns["event_types_json"]["type"], sa.JSON)
    assert isinstance(
        notification_channel_columns["missed_start_grace_seconds"]["type"],
        sa.Integer,
    )
    assert isinstance(notification_delivery_columns["request_payload_json"]["type"], sa.JSON)
    assert isinstance(
        notification_instance_state_columns["last_connectivity"]["type"],
        sa.String,
    )
    assert isinstance(worker_agent_columns["labels_json"]["type"], sa.JSON)
    assert isinstance(worker_agent_columns["capabilities_json"]["type"], sa.JSON)
    assert isinstance(worker_agent_columns["platform_json"]["type"], sa.JSON)
    assert isinstance(worker_agent_session_columns["capabilities_json"]["type"], sa.JSON)
    assert isinstance(worker_agent_session_columns["accepted_capabilities_json"]["type"], sa.JSON)
    assert isinstance(workflow_package_columns["metadata_json"]["type"], sa.JSON)
    assert isinstance(worker_deployment_columns["params_json"]["type"], sa.JSON)
    assert isinstance(worker_deployment_columns["env_json"]["type"], sa.JSON)
    assert isinstance(worker_deployment_columns["credential_refs_json"]["type"], sa.JSON)
    assert isinstance(worker_columns["env_json"]["type"], sa.JSON)
    assert isinstance(worker_agent_command_columns["args_json"]["type"], sa.JSON)
    assert isinstance(worker_agent_command_columns["result_json"]["type"], sa.JSON)
    assert isinstance(worker_deployment_event_columns["payload_json"]["type"], sa.JSON)
    assert isinstance(task_definition_columns["source_config_json"]["type"], sa.JSON)
    assert isinstance(task_definition_columns["emit_json"]["type"], sa.JSON)
    assert {column["name"] for column in inspector.get_columns("task_metric_windows")} == {
        "id",
        "service_id",
        "instance_id",
        "task_name",
        "window_id",
        "window_started_at",
        "window_ended_at",
        "fetched",
        "started",
        "succeeded",
        "retried",
        "failed",
        "dead_lettered",
        "cancelled",
        "timeouts",
        "inflight",
        "avg_duration_ms",
        "p95_duration_ms",
        "received_at",
        "created_at",
    }
    assert {column["name"] for column in inspector.get_columns("task_events")} == {
        "id",
        "event_id",
        "service_id",
        "instance_id",
        "task_name",
        "kind",
        "occurred_at",
        "attempts",
        "duration_ms",
        "failure_kind",
        "exception_type",
        "message",
        "traceback",
        "meta_json",
        "received_at",
        "created_at",
    }

    assert {index["name"] for index in inspector.get_indexes("services")} == {
        "ix_services_environment_name",
    }
    assert {index["name"] for index in inspector.get_indexes("instances")} == {
        "ix_instances_service_id_last_seen_at",
    }
    assert {index["name"] for index in inspector.get_indexes("agent_sessions")} == {
        "ix_agent_sessions_instance_id_status_connected_at",
    }
    assert {index["name"] for index in inspector.get_indexes("agent_commands")} == {
        "ix_agent_commands_instance_id_status_created_at",
        "ix_agent_commands_status_updated_at",
    }
    assert {index["name"] for index in inspector.get_indexes("notification_deliveries")} == {
        "ix_notification_deliveries_channel_id_created_at",
    }
    assert {index["name"] for index in inspector.get_indexes("worker_agents")} == {
        "ix_worker_agents_status_last_seen_at",
    }
    assert {index["name"] for index in inspector.get_indexes("worker_agent_sessions")} == {
        "ix_worker_agent_sessions_agent_status_connected_at",
    }
    assert {index["name"] for index in inspector.get_indexes("workflow_packages")} == {
        "ix_workflow_packages_workflow_id_created_at",
    }
    assert {index["name"] for index in inspector.get_indexes("worker_deployments")} == {
        "ix_worker_deployments_agent_observed_status",
    }
    assert {index["name"] for index in inspector.get_indexes("worker_agent_commands")} == {
        "ix_worker_agent_commands_agent_status",
    }
    assert {index["name"] for index in inspector.get_indexes("worker_deployment_events")} == {
        "ix_worker_deployment_events_deployment_created_at",
    }
    assert {index["name"] for index in inspector.get_indexes("local_users")} == {
        "ix_local_users_username",
    }
    assert {index["name"] for index in inspector.get_indexes("local_user_roles")} == {
        "ix_local_user_roles_role_id",
        "ix_local_user_roles_user_id",
    }
    assert {index["name"] for index in inspector.get_indexes("console_sessions")} == {
        "ix_console_sessions_user_id_expires_at",
    }
    assert {index["name"] for index in inspector.get_indexes("task_metric_windows")} == {
        "ix_task_metric_windows_service_id_task_name_window_ended_at",
        "ix_task_metric_windows_window_ended_at",
    }
    assert {index["name"] for index in inspector.get_indexes("task_events")} == {
        "ix_task_events_service_id_task_name_occurred_at",
        "ix_task_events_occurred_at",
    }

    with engine.connect() as connection:
        role_names = connection.execute(
            text("SELECT name FROM local_roles ORDER BY name")
        ).scalars().all()
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

    assert role_names == ["admin", "operator", "viewer"]
    assert version == HEAD_REVISION
    engine.dispose()


def test_alembic_upgrade_head_reconciles_legacy_services_schema(tmp_path) -> None:
    db_path = tmp_path / "legacy-control-plane.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url)

    metadata = sa.MetaData()
    sa.Table(
        "services",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("latest_deployment_version", sa.String(length=128), nullable=False),
        sa.Column("latest_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "alembic_version",
        metadata,
        sa.Column("version_num", sa.String(length=32), nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:version_num)"),
            {"version_num": INITIAL_REVISION},
        )
    engine.dispose()

    command.upgrade(make_alembic_config(database_url), "head")

    upgraded_engine = create_engine(database_url)
    inspector = inspect(upgraded_engine)

    assert "latest_topology_hash" in {
        column["name"] for column in inspector.get_columns("services")
    }

    with upgraded_engine.connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

    assert version == HEAD_REVISION
    upgraded_engine.dispose()


def test_alembic_upgrade_head_reconciles_missing_service_sync_column(tmp_path) -> None:
    db_path = tmp_path / "missing-service-sync.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url)

    metadata = sa.MetaData()
    sa.Table(
        "services",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("latest_deployment_version", sa.String(length=128), nullable=False),
        sa.Column("latest_topology_hash", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "alembic_version",
        metadata,
        sa.Column("version_num", sa.String(length=32), nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:version_num)"),
            {"version_num": "202603100001"},
        )
    engine.dispose()

    command.upgrade(make_alembic_config(database_url), "head")

    upgraded_engine = create_engine(database_url)
    inspector = inspect(upgraded_engine)

    assert "latest_sync_at" in {column["name"] for column in inspector.get_columns("services")}

    with upgraded_engine.connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

    assert version == HEAD_REVISION
    upgraded_engine.dispose()


def test_alembic_upgrade_head_reconciles_missing_task_description_column(tmp_path) -> None:
    db_path = tmp_path / "missing-task-description.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url)

    metadata = sa.MetaData()
    sa.Table(
        "task_definitions",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("service_id", sa.Uuid(), nullable=False),
        sa.Column("task_name", sa.String(length=255), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=True),
        sa.Column("source_kind", sa.String(length=128), nullable=True),
        sa.Column("source_config_json", sa.JSON(), nullable=True),
        sa.Column("emit_json", sa.JSON(), nullable=True),
        sa.Column("concurrency", sa.Integer(), nullable=True),
        sa.Column("timeout_s", sa.Float(), nullable=True),
        sa.Column("retry_policy", sa.JSON(), nullable=True),
        sa.Column("topology_hash", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "alembic_version",
        metadata,
        sa.Column("version_num", sa.String(length=32), nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:version_num)"),
            {"version_num": "202603100002"},
        )
    engine.dispose()

    command.upgrade(make_alembic_config(database_url), "head")

    upgraded_engine = create_engine(database_url)
    inspector = inspect(upgraded_engine)

    assert "description" in {
        column["name"] for column in inspector.get_columns("task_definitions")
    }

    with upgraded_engine.connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

    assert version == HEAD_REVISION
    upgraded_engine.dispose()
