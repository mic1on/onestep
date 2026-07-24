"""Add traceback to task events.

Revision ID: 202603170001
Revises: 202603120001
Create Date: 2026-03-17 17:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202603170001"
down_revision: str | None = "202603120001"
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
    if _has_table("task_events") and not _has_column("task_events", "traceback"):
        op.add_column("task_events", sa.Column("traceback", sa.Text(), nullable=True))


def downgrade() -> None:
    return None
