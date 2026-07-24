"""Add notification webhook configuration tables.

Revision ID: 202604300001
Revises: 202603180001
Create Date: 2026-04-30 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "202604300001"
down_revision: str | None = "202603180001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in set(inspector.get_table_names())


def _has_index(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return index_name in {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    if not _has_table("notification_channels"):
        op.create_table(
            "notification_channels",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("webhook_url", sa.Text(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("service_scopes_json", json_type, nullable=False),
            sa.Column("event_types_json", json_type, nullable=False),
            sa.Column(
                "missed_start_grace_seconds",
                sa.Integer(),
                nullable=False,
                server_default="300",
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_channels")),
            sa.UniqueConstraint("name", name="uq_notification_channels_name"),
        )

    if not _has_table("notification_deliveries"):
        op.create_table(
            "notification_deliveries",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("channel_id", sa.Uuid(), nullable=False),
            sa.Column("dedupe_key", sa.String(length=512), nullable=False),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("service_name", sa.String(length=255), nullable=True),
            sa.Column("service_environment", sa.String(length=32), nullable=True),
            sa.Column("task_name", sa.String(length=255), nullable=True),
            sa.Column("task_event_id", sa.String(length=255), nullable=True),
            sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("request_payload_json", json_type, nullable=True),
            sa.Column("response_status_code", sa.Integer(), nullable=True),
            sa.Column("response_body", sa.Text(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["channel_id"],
                ["notification_channels.id"],
                name=op.f("fk_notification_deliveries_channel_id_notification_channels"),
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_deliveries")),
            sa.UniqueConstraint("dedupe_key", name="uq_notification_deliveries_dedupe_key"),
        )

    if not _has_index(
        "notification_deliveries",
        "ix_notification_deliveries_channel_id_created_at",
    ):
        op.create_index(
            "ix_notification_deliveries_channel_id_created_at",
            "notification_deliveries",
            ["channel_id", "created_at"],
            unique=False,
        )


def downgrade() -> None:
    if _has_index("notification_deliveries", "ix_notification_deliveries_channel_id_created_at"):
        op.drop_index(
            "ix_notification_deliveries_channel_id_created_at",
            table_name="notification_deliveries",
        )
    if _has_table("notification_deliveries"):
        op.drop_table("notification_deliveries")
    if _has_table("notification_channels"):
        op.drop_table("notification_channels")
