"""Add audit metadata to agent commands.

Revision ID: 202603180001
Revises: 202603170004
Create Date: 2026-03-18 22:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202603180001"
down_revision: str | None = "202603170004"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_column("agent_commands", "created_by"):
        op.add_column(
            "agent_commands",
            sa.Column("created_by", sa.String(length=255), nullable=True),
        )
    if not _has_column("agent_commands", "reason"):
        op.add_column("agent_commands", sa.Column("reason", sa.Text(), nullable=True))
    if not _has_column("agent_commands", "source_surface"):
        op.add_column(
            "agent_commands",
            sa.Column(
                "source_surface",
                sa.String(length=64),
                nullable=False,
                server_default="unknown",
            ),
        )


def downgrade() -> None:
    if _has_column("agent_commands", "source_surface"):
        op.drop_column("agent_commands", "source_surface")
    if _has_column("agent_commands", "reason"):
        op.drop_column("agent_commands", "reason")
    if _has_column("agent_commands", "created_by"):
        op.drop_column("agent_commands", "created_by")
