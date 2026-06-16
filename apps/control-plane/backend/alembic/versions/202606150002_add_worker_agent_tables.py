"""Add worker agent registry and deployment tables.

Revision ID: 202606150002
Revises: 202606150001
Create Date: 2026-06-16 08:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "202606150002"
down_revision: str | None = "202606150001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if not _has_table("worker_agents"):
        op.create_table(
            "worker_agents",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("worker_agent_id", sa.Uuid(), nullable=False),
            sa.Column("display_name", sa.String(length=255), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("execution_mode", sa.String(length=32), nullable=False),
            sa.Column("max_concurrent_deployments", sa.Integer(), nullable=False),
            sa.Column("used_slots", sa.Integer(), nullable=False),
            sa.Column("labels_json", json_type, nullable=False),
            sa.Column("capabilities_json", json_type, nullable=False),
            sa.Column("agent_version", sa.String(length=64), nullable=True),
            sa.Column("onestep_version", sa.String(length=64), nullable=True),
            sa.Column("python_version", sa.String(length=64), nullable=True),
            sa.Column("platform_json", json_type, nullable=False),
            sa.Column("connection_token_hash", sa.String(length=64), nullable=False),
            sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_worker_agents")),
            sa.UniqueConstraint(
                "worker_agent_id",
                name="uq_worker_agents_worker_agent_id",
            ),
        )
        op.create_index(
            "ix_worker_agents_status_last_seen_at",
            "worker_agents",
            ["status", "last_seen_at"],
            unique=False,
        )

    if not _has_table("worker_agent_sessions"):
        op.create_table(
            "worker_agent_sessions",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("session_id", sa.String(length=255), nullable=False),
            sa.Column("worker_agent_id", sa.Uuid(), nullable=False),
            sa.Column("protocol_version", sa.String(length=16), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("capabilities_json", json_type, nullable=False),
            sa.Column("accepted_capabilities_json", json_type, nullable=False),
            sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_hello_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["worker_agent_id"],
                ["worker_agents.worker_agent_id"],
                name=op.f("fk_worker_agent_sessions_worker_agent_id_worker_agents"),
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_worker_agent_sessions")),
            sa.UniqueConstraint("session_id", name="uq_worker_agent_sessions_session_id"),
        )
        op.create_index(
            "ix_worker_agent_sessions_agent_status_connected_at",
            "worker_agent_sessions",
            ["worker_agent_id", "status", "connected_at"],
            unique=False,
        )

    if not _has_table("workflow_packages"):
        op.create_table(
            "workflow_packages",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("package_id", sa.Uuid(), nullable=False),
            sa.Column("workflow_id", sa.Uuid(), nullable=False),
            sa.Column("version", sa.String(length=128), nullable=False),
            sa.Column("filename", sa.String(length=255), nullable=False),
            sa.Column("content_type", sa.String(length=128), nullable=False),
            sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("storage_path", sa.Text(), nullable=False),
            sa.Column("entrypoint", sa.String(length=255), nullable=False),
            sa.Column("metadata_json", json_type, nullable=False),
            sa.Column("created_by", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_packages")),
            sa.UniqueConstraint("package_id", name="uq_workflow_packages_package_id"),
        )
        op.create_index(
            "ix_workflow_packages_workflow_id_created_at",
            "workflow_packages",
            ["workflow_id", "created_at"],
            unique=False,
        )

    if not _has_table("worker_deployments"):
        op.create_table(
            "worker_deployments",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("deployment_id", sa.Uuid(), nullable=False),
            sa.Column("workflow_package_id", sa.Uuid(), nullable=False),
            sa.Column("worker_agent_id", sa.Uuid(), nullable=False),
            sa.Column("desired_status", sa.String(length=32), nullable=False),
            sa.Column("observed_status", sa.String(length=32), nullable=False),
            sa.Column("runtime_instance_id", sa.Uuid(), nullable=True),
            sa.Column("execution_mode", sa.String(length=32), nullable=False),
            sa.Column("params_json", json_type, nullable=False),
            sa.Column("env_json", json_type, nullable=False),
            sa.Column("credential_refs_json", json_type, nullable=False),
            sa.Column("package_checksum", sa.String(length=64), nullable=False),
            sa.Column("last_error_code", sa.String(length=128), nullable=True),
            sa.Column("last_error_message", sa.Text(), nullable=True),
            sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_by", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["workflow_package_id"],
                ["workflow_packages.package_id"],
                name=op.f("fk_worker_deployments_workflow_package_id_workflow_packages"),
                ondelete="RESTRICT",
            ),
            sa.ForeignKeyConstraint(
                ["worker_agent_id"],
                ["worker_agents.worker_agent_id"],
                name=op.f("fk_worker_deployments_worker_agent_id_worker_agents"),
                ondelete="RESTRICT",
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_worker_deployments")),
            sa.UniqueConstraint("deployment_id", name="uq_worker_deployments_deployment_id"),
        )
        op.create_index(
            "ix_worker_deployments_agent_observed_status",
            "worker_deployments",
            ["worker_agent_id", "observed_status"],
            unique=False,
        )

    if not _has_table("worker_agent_commands"):
        op.create_table(
            "worker_agent_commands",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("command_id", sa.Uuid(), nullable=False),
            sa.Column("worker_agent_id", sa.Uuid(), nullable=False),
            sa.Column("deployment_id", sa.Uuid(), nullable=True),
            sa.Column("session_id", sa.String(length=255), nullable=True),
            sa.Column("kind", sa.String(length=64), nullable=False),
            sa.Column("args_json", json_type, nullable=False),
            sa.Column("timeout_s", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("ack_status", sa.String(length=32), nullable=True),
            sa.Column("result_json", json_type, nullable=True),
            sa.Column("error_code", sa.String(length=128), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["worker_agent_id"],
                ["worker_agents.worker_agent_id"],
                name=op.f("fk_worker_agent_commands_worker_agent_id_worker_agents"),
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_worker_agent_commands")),
            sa.UniqueConstraint("command_id", name="uq_worker_agent_commands_command_id"),
        )
        op.create_index(
            "ix_worker_agent_commands_agent_status",
            "worker_agent_commands",
            ["worker_agent_id", "status"],
            unique=False,
        )

    if not _has_table("worker_deployment_events"):
        op.create_table(
            "worker_deployment_events",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("deployment_id", sa.Uuid(), nullable=False),
            sa.Column("worker_agent_id", sa.Uuid(), nullable=False),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("observed_status", sa.String(length=32), nullable=True),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("payload_json", json_type, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_worker_deployment_events")),
        )
        op.create_index(
            "ix_worker_deployment_events_deployment_created_at",
            "worker_deployment_events",
            ["deployment_id", "created_at"],
            unique=False,
        )


def downgrade() -> None:
    if _has_table("worker_deployment_events"):
        op.drop_index(
            "ix_worker_deployment_events_deployment_created_at",
            table_name="worker_deployment_events",
        )
        op.drop_table("worker_deployment_events")
    if _has_table("worker_agent_commands"):
        op.drop_index("ix_worker_agent_commands_agent_status", table_name="worker_agent_commands")
        op.drop_table("worker_agent_commands")
    if _has_table("worker_deployments"):
        op.drop_index(
            "ix_worker_deployments_agent_observed_status",
            table_name="worker_deployments",
        )
        op.drop_table("worker_deployments")
    if _has_table("workflow_packages"):
        op.drop_index(
            "ix_workflow_packages_workflow_id_created_at",
            table_name="workflow_packages",
        )
        op.drop_table("workflow_packages")
    if _has_table("worker_agent_sessions"):
        op.drop_index(
            "ix_worker_agent_sessions_agent_status_connected_at",
            table_name="worker_agent_sessions",
        )
        op.drop_table("worker_agent_sessions")
    if _has_table("worker_agents"):
        op.drop_index("ix_worker_agents_status_last_seen_at", table_name="worker_agents")
        op.drop_table("worker_agents")
