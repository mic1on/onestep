"""Add persistent console login throttles.

Revision ID: 202607230001
Revises: 202607200001
Create Date: 2026-07-23 00:01:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from onestep_control_plane_api.db.types import UTCDateTime

revision: str = "202607230001"
down_revision: str | None = "202607200001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in set(inspector.get_table_names())


def upgrade() -> None:
    if _has_table("console_login_throttles"):
        return
    op.create_table(
        "console_login_throttles",
        sa.Column("username", sa.String(length=255), primary_key=True),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("window_started_at", UTCDateTime(), nullable=False),
        sa.Column("locked_until", UTCDateTime(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
    )
    op.create_index(
        "ix_console_login_throttles_locked_until",
        "console_login_throttles",
        ["locked_until"],
    )


def downgrade() -> None:
    if _has_table("console_login_throttles"):
        op.drop_table("console_login_throttles")
