"""Add the notification outbox queue table.

Revision ID: 202607240001
Revises: 202607230001
Create Date: 2026-07-24 00:01:00.000000

Webhook delivery was previously performed inline on the asyncio event loop
(``notification_service._post_webhook``), so a single slow/hung webhook could
stall the entire control plane (agent telemetry, commands, API). Notifications
are now enqueued into ``notification_outbox`` (a fast DB insert on the receive
path) and drained off the event loop by the leader-gated
``notification_outbox_worker``, which performs the HTTP POSTs in a worker
thread. Delivery is at-least-once with bounded retry/backoff.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from onestep_control_plane_api.db.types import UTCDateTime

# revision identifiers, used by Alembic.
revision: str = "202607240001"
down_revision: str | None = "202607230001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in set(inspector.get_table_names())


def upgrade() -> None:
    if _has_table("notification_outbox"):
        return

    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("delivery_id", sa.Uuid(), nullable=False),
        sa.Column("webhook_url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", UTCDateTime(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_response_status_code", sa.Integer(), nullable=True),
        sa.Column("last_response_body", sa.Text(), nullable=True),
        sa.Column("last_attempt_at", UTCDateTime(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["delivery_id"],
            ["notification_deliveries.id"],
            name="fk_notification_outbox_delivery_id_notification_deliveries",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_outbox")),
        sa.UniqueConstraint(
            "delivery_id",
            name="uq_notification_outbox_delivery_id",
        ),
    )
    op.create_index(
        "ix_notification_outbox_status_next_attempt_at",
        "notification_outbox",
        ["status", "next_attempt_at"],
        unique=False,
    )


def downgrade() -> None:
    if _has_table("notification_outbox"):
        op.drop_table("notification_outbox")
