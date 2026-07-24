"""Add description to services.

Revision ID: 202607200001
Revises: 202607190001
Create Date: 2026-07-20 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607200001"
down_revision: str | None = "202607190001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in set(inspector.get_table_names())


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not _has_table(table_name):
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if _has_table("services") and not _has_column("services", "description"):
        op.add_column("services", sa.Column("description", sa.Text(), nullable=True))


def downgrade() -> None:
    if _has_table("services") and _has_column("services", "description"):
        op.drop_column("services", "description")
