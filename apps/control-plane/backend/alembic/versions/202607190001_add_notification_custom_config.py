"""Add custom notification channel config.

Revision ID: 202607190001
Revises: 202607180002
Create Date: 2026-07-19 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202607190001"
down_revision: str | None = "202607180002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in set(inspector.get_table_names())


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_table("notification_channels"):
        return
    if not _has_column("notification_channels", "custom_config_json"):
        op.add_column(
            "notification_channels",
            sa.Column("custom_config_json", json_type, nullable=True),
        )


def downgrade() -> None:
    if not _has_table("notification_channels"):
        return
    if _has_column("notification_channels", "custom_config_json"):
        op.drop_column("notification_channels", "custom_config_json")
