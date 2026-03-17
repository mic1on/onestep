"""Add agent WS session tracking.

Revision ID: 202603170002
Revises: 202603170001
Create Date: 2026-03-17 18:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "202603170002"
down_revision: str | None = "202603170001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if _has_table("agent_sessions"):
        return

    op.create_table(
        "agent_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("service_id", sa.Uuid(), nullable=False),
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("protocol_version", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("capabilities_json", json_type, nullable=False),
        sa.Column("accepted_capabilities_json", json_type, nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_hello_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["instances.instance_id"],
            name=op.f("fk_agent_sessions_instance_id_instances"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_agent_sessions_service_id_services"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_sessions")),
        sa.UniqueConstraint("session_id", name="uq_agent_sessions_session_id"),
    )
    op.create_index(
        "ix_agent_sessions_instance_id_status_connected_at",
        "agent_sessions",
        ["instance_id", "status", "connected_at"],
        unique=False,
    )


def downgrade() -> None:
    if not _has_table("agent_sessions"):
        return
    op.drop_index(
        "ix_agent_sessions_instance_id_status_connected_at",
        table_name="agent_sessions",
    )
    op.drop_table("agent_sessions")
