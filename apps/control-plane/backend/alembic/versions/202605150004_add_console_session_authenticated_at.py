"""Add authenticated timestamp to console sessions.

Revision ID: 202605150004
Revises: 202605150003
Create Date: 2026-05-15 20:15:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from onestep_control_plane_api.db.types import UTCDateTime

# revision identifiers, used by Alembic.
revision: str = "202605150004"
down_revision: str | None = "202605150003"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in set(inspector.get_table_names())


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_table("console_sessions") or _has_column("console_sessions", "authenticated_at"):
        return

    with op.batch_alter_table("console_sessions") as batch_op:
        batch_op.add_column(sa.Column("authenticated_at", UTCDateTime(), nullable=True))

    op.execute(
        sa.text(
            "UPDATE console_sessions "
            "SET authenticated_at = created_at "
            "WHERE authenticated_at IS NULL"
        )
    )

    with op.batch_alter_table("console_sessions") as batch_op:
        batch_op.alter_column(
            "authenticated_at",
            existing_type=UTCDateTime(),
            nullable=False,
        )


def downgrade() -> None:
    if not _has_table("console_sessions") or not _has_column(
        "console_sessions",
        "authenticated_at",
    ):
        return

    with op.batch_alter_table("console_sessions") as batch_op:
        batch_op.drop_column("authenticated_at")
