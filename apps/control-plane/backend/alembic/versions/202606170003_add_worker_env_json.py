"""Add worker environment variables.

Revision ID: 202606170003
Revises: 202606170002
Create Date: 2026-06-17 22:50:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202606170003"
down_revision: str | None = "202606170002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if _has_table("workers") and not _has_column("workers", "env_json"):
        op.add_column(
            "workers",
            sa.Column("env_json", json_type, nullable=False, server_default=sa.text("'{}'")),
        )


def downgrade() -> None:
    if _has_table("workers") and _has_column("workers", "env_json"):
        op.drop_column("workers", "env_json")
