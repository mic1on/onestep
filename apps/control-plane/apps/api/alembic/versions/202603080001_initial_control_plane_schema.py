"""Initial control plane schema.

Revision ID: 202603080001
Revises:
Create Date: 2026-03-08 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "202603080001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "services",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("latest_deployment_version", sa.String(length=128), nullable=False),
        sa.Column("latest_topology_hash", sa.String(length=255), nullable=True),
        sa.Column("latest_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_services")),
        sa.UniqueConstraint("name", "environment", name="uq_services_name_environment"),
    )
    op.create_index(
        "ix_services_environment_name",
        "services",
        ["environment", "name"],
        unique=False,
    )

    op.create_table(
        "instances",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("service_id", sa.Uuid(), nullable=False),
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("node_name", sa.String(length=255), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=True),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("deployment_version", sa.String(length=128), nullable=False),
        sa.Column("onestep_version", sa.String(length=64), nullable=True),
        sa.Column("python_version", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_topology_hash", sa.String(length=255), nullable=True),
        sa.Column("app_snapshot_json", json_type, nullable=True),
        sa.Column("last_sync_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_sequence", sa.Integer(), nullable=True),
        sa.Column("last_heartbeat_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_sequence", sa.Integer(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_instances_service_id_services"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_instances")),
        sa.UniqueConstraint("instance_id", name="uq_instances_instance_id"),
    )
    op.create_index(
        "ix_instances_service_id_last_seen_at",
        "instances",
        ["service_id", "last_seen_at"],
        unique=False,
    )

    op.create_table(
        "task_definitions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("service_id", sa.Uuid(), nullable=False),
        sa.Column("task_name", sa.String(length=255), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=True),
        sa.Column("source_kind", sa.String(length=128), nullable=True),
        sa.Column("source_config_json", json_type, nullable=True),
        sa.Column("emit_json", json_type, nullable=True),
        sa.Column("concurrency", sa.Integer(), nullable=True),
        sa.Column("timeout_s", sa.Float(), nullable=True),
        sa.Column("retry_policy", json_type, nullable=True),
        sa.Column("topology_hash", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_task_definitions_service_id_services"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_definitions")),
        sa.UniqueConstraint(
            "service_id",
            "task_name",
            name="uq_task_definitions_service_id_task_name",
        ),
    )

    op.create_table(
        "task_metric_windows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("service_id", sa.Uuid(), nullable=False),
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("task_name", sa.String(length=255), nullable=False),
        sa.Column("window_id", sa.String(length=255), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched", sa.Integer(), nullable=False),
        sa.Column("started", sa.Integer(), nullable=False),
        sa.Column("succeeded", sa.Integer(), nullable=False),
        sa.Column("retried", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("dead_lettered", sa.Integer(), nullable=False),
        sa.Column("cancelled", sa.Integer(), nullable=False),
        sa.Column("timeouts", sa.Integer(), nullable=False),
        sa.Column("inflight", sa.Integer(), nullable=False),
        sa.Column("avg_duration_ms", sa.Float(), nullable=True),
        sa.Column("p95_duration_ms", sa.Float(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["instances.instance_id"],
            name=op.f("fk_task_metric_windows_instance_id_instances"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_task_metric_windows_service_id_services"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_metric_windows")),
        sa.UniqueConstraint(
            "instance_id",
            "task_name",
            "window_id",
            name="uq_task_metric_windows_instance_id_task_name_window_id",
        ),
    )
    op.create_index(
        "ix_task_metric_windows_service_id_task_name_window_ended_at",
        "task_metric_windows",
        ["service_id", "task_name", "window_ended_at"],
        unique=False,
    )

    op.create_table(
        "task_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("service_id", sa.Uuid(), nullable=False),
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("task_name", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("failure_kind", sa.String(length=64), nullable=True),
        sa.Column("exception_type", sa.String(length=255), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("meta_json", json_type, nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["instances.instance_id"],
            name=op.f("fk_task_events_instance_id_instances"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_task_events_service_id_services"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_events")),
        sa.UniqueConstraint("event_id", name="uq_task_events_event_id"),
    )
    op.create_index(
        "ix_task_events_service_id_task_name_occurred_at",
        "task_events",
        ["service_id", "task_name", "occurred_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_task_events_service_id_task_name_occurred_at", table_name="task_events")
    op.drop_table("task_events")

    op.drop_index(
        "ix_task_metric_windows_service_id_task_name_window_ended_at",
        table_name="task_metric_windows",
    )
    op.drop_table("task_metric_windows")

    op.drop_table("task_definitions")

    op.drop_index("ix_instances_service_id_last_seen_at", table_name="instances")
    op.drop_table("instances")

    op.drop_index("ix_services_environment_name", table_name="services")
    op.drop_table("services")
