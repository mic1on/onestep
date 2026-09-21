"""Add instance connectivity stable-confirmation and flap-damping state.

Issue #196. ``scan_and_dispatch_instance_connectivity_notifications`` previously
notified on every observed connectivity flip, so a briefly flapping instance
produced a notification storm. Damping needs state that survives a process
restart and a leader switch, which is why it is persisted here rather than held
in memory:

* ``pending_connectivity`` / ``pending_since`` — a flip that has been observed
  but not yet held for the stable-confirmation window. A flip that heals inside
  the window is cancelled and never notified.
* ``flap_episode_*`` / ``flap_suppressed_count`` — a run of confirmed flips
  closer together than the flap window is one episode; flips beyond the
  configured maximum are counted rather than sent, and one summary reports the
  suppressed count when the episode goes quiet.

All columns are nullable or defaulted, so an existing deployment upgrades
without rewriting rows. Existing rows simply have no pending flip and no open
episode, which is the correct "nothing anomalous in flight" starting state.

Revision ID: 202607250001
Revises: 202607240001
Create Date: 2026-07-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "202607250001"
down_revision: str | None = "202607240001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notification_instance_states",
        sa.Column("pending_connectivity", sa.String(32), nullable=True),
    )
    op.add_column(
        "notification_instance_states",
        sa.Column("pending_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "notification_instance_states",
        sa.Column("flap_episode_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "notification_instance_states",
        sa.Column("flap_episode_last_flip_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "notification_instance_states",
        sa.Column("flap_episode_flips", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "notification_instance_states",
        sa.Column("flap_suppressed_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("notification_instance_states", "flap_suppressed_count")
    op.drop_column("notification_instance_states", "flap_episode_flips")
    op.drop_column("notification_instance_states", "flap_episode_last_flip_at")
    op.drop_column("notification_instance_states", "flap_episode_started_at")
    op.drop_column("notification_instance_states", "pending_since")
    op.drop_column("notification_instance_states", "pending_connectivity")
