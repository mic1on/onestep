"""Add per-channel instance connectivity notification states.

Revision ID: 202605270001
Revises: 202605150004
Create Date: 2026-05-27 11:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from onestep_control_plane_api.db.types import UTCDateTime

# revision identifiers, used by Alembic.
revision: str = "202605270001"
down_revision: str | None = "202605150004"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in set(inspector.get_table_names())


def upgrade() -> None:
    if _has_table("notification_instance_states"):
        return

    op.create_table(
        "notification_instance_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("last_connectivity", sa.String(length=32), nullable=False),
        sa.Column("last_transition_at", UTCDateTime(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["notification_channels.id"],
            name=(
                "fk_notification_instance_states_channel_id_notification_channels"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["instances.instance_id"],
            name="fk_notification_instance_states_instance_id_instances",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_instance_states")),
        sa.UniqueConstraint(
            "channel_id",
            "instance_id",
            name="uq_notification_instance_states_channel_id_instance_id",
        ),
    )


def downgrade() -> None:
    if _has_table("notification_instance_states"):
        op.drop_table("notification_instance_states")
