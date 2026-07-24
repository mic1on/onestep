"""Add worker reporting configuration.

Revision ID: 202606190001
Revises: 202606170003
Create Date: 2026-06-19 14:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202606190001"
down_revision: str | None = "202606170003"
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
    if not _has_table("workers"):
        return
    if not _has_column("workers", "reporting_enabled"):
        op.add_column(
            "workers",
            sa.Column("reporting_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        )
    if not _has_column("workers", "reporting_config_json"):
        op.add_column(
            "workers",
            sa.Column(
                "reporting_config_json",
                json_type,
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
        )
    if not _has_column("workers", "reporting_secret_encrypted"):
        op.add_column(
            "workers",
            sa.Column("reporting_secret_encrypted", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    if not _has_table("workers"):
        return
    if _has_column("workers", "reporting_secret_encrypted"):
        op.drop_column("workers", "reporting_secret_encrypted")
    if _has_column("workers", "reporting_config_json"):
        op.drop_column("workers", "reporting_config_json")
    if _has_column("workers", "reporting_enabled"):
        op.drop_column("workers", "reporting_enabled")
