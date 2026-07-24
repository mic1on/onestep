"""Add agent command lifecycle tracking.

Revision ID: 202603170003
Revises: 202603170002
Create Date: 2026-03-17 20:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "202603170003"
down_revision: str | None = "202603170002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if _has_table("agent_commands"):
        return

    op.create_table(
        "agent_commands",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.String(length=255), nullable=False),
        sa.Column("service_id", sa.Uuid(), nullable=False),
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("args_json", json_type, nullable=False),
        sa.Column("timeout_s", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("ack_status", sa.String(length=32), nullable=True),
        sa.Column("result_json", json_type, nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["instances.instance_id"],
            name=op.f("fk_agent_commands_instance_id_instances"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_agent_commands_service_id_services"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_commands")),
        sa.UniqueConstraint("command_id", name="uq_agent_commands_command_id"),
    )
    op.create_index(
        "ix_agent_commands_instance_id_status_created_at",
        "agent_commands",
        ["instance_id", "status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    if not _has_table("agent_commands"):
        return
    op.drop_index(
        "ix_agent_commands_instance_id_status_created_at",
        table_name="agent_commands",
    )
    op.drop_table("agent_commands")
