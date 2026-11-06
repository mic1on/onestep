"""Add task-failure burst damping state.

Connectivity flips are damped by ``notification_instance_states`` (#196), but task
lifecycle events had no damping at all. One root cause -- a database outage, a bad
deploy -- fails every task on every instance of a service at once, and each failure
produced its own webhook. ``notification_task_failure_bursts`` bounds that burst per
(channel, service): the first ``task_failure_burst_max_notifications`` failures of a
burst are sent, later ones are counted rather than sent, and one summary reports the
withheld count when the burst goes quiet.

Persisted rather than held in memory for the same reason as the connectivity state:
it must survive a process restart and a leader switch, otherwise a restart mid
incident resets the counter and the storm resumes.

Revision ID: 202609230001
Revises: 202607250001
Create Date: 2026-09-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "202609230001"
down_revision: str | None = "202607250001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_task_failure_bursts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "channel_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("notification_channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "service_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("services.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("burst_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("suppressed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "channel_id",
            "service_id",
            name="uq_notification_task_failure_bursts_channel_id_service_id",
        ),
    )


def downgrade() -> None:
    op.drop_table("notification_task_failure_bursts")
