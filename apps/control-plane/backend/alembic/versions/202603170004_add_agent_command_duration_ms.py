"""Add duration_ms to agent commands.

Revision ID: 202603170004
Revises: 202603170003
Create Date: 2026-03-17 23:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202603170004"
down_revision: str | None = "202603170003"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if _has_column("agent_commands", "duration_ms"):
        return
    op.add_column("agent_commands", sa.Column("duration_ms", sa.Integer(), nullable=True))


def downgrade() -> None:
    if not _has_column("agent_commands", "duration_ms"):
        return
    op.drop_column("agent_commands", "duration_ms")
