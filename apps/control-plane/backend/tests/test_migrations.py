from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

ROOT_DIR = Path(__file__).resolve().parents[2]
ALEMBIC_INI_PATH = ROOT_DIR / "alembic.ini"
INITIAL_REVISION = "202603080001"


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
        "instances",
        "services",
        "alembic_version",
        "task_definitions",
        "task_events",
        "task_metric_windows",
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
    assert {column["name"] for column in inspector.get_columns("task_definitions")} == {
        "id",
        "service_id",
        "task_name",
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
    instance_columns = {column["name"]: column for column in inspector.get_columns("instances")}
    assert isinstance(instance_columns["app_snapshot_json"]["type"], sa.JSON)
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
    assert {index["name"] for index in inspector.get_indexes("task_metric_windows")} == {
        "ix_task_metric_windows_service_id_task_name_window_ended_at",
    }
    assert {index["name"] for index in inspector.get_indexes("task_events")} == {
        "ix_task_events_service_id_task_name_occurred_at",
    }

    with engine.connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

    assert version == INITIAL_REVISION
    engine.dispose()
