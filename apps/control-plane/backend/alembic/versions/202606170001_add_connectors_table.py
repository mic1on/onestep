"""Add connectors table.

Revision ID: 202606170001
Revises: 202606150002
Create Date: 2026-06-17 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "202606170001"
down_revision: str | None = "202606150002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if not _has_table("connectors"):
        op.create_table(
            "connectors",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("type", sa.String(length=64), nullable=False),
            sa.Column("config_json", json_type, nullable=False),
            sa.Column("secret_encrypted", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("name", name="uq_connectors_name"),
        )
        op.create_index("ix_connectors_type", "connectors", ["type"])


def downgrade() -> None:
    if _has_table("connectors"):
        op.drop_index("ix_connectors_type", table_name="connectors")
        op.drop_table("connectors")
