"""Add description to task definitions.

Revision ID: 202603120001
Revises: 202603100002
Create Date: 2026-03-12 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202603120001"
down_revision: str | None = "202603100002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not _has_table(table_name):
        return False

    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if _has_table("task_definitions") and not _has_column("task_definitions", "description"):
        op.add_column(
            "task_definitions",
            sa.Column("description", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    # This reconciliation migration only repairs databases that predate task descriptions.
    return None
