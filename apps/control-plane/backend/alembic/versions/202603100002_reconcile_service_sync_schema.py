"""Reconcile schema drift for service sync metadata.

Revision ID: 202603100002
Revises: 202603100001
Create Date: 2026-03-10 17:15:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202603100002"
down_revision: str | None = "202603100001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return False

    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_column("services", "latest_topology_hash"):
        op.add_column(
            "services",
            sa.Column("latest_topology_hash", sa.String(length=255), nullable=True),
        )

    if not _has_column("services", "latest_sync_at"):
        op.add_column(
            "services",
            sa.Column("latest_sync_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    # This reconciliation migration only repairs drifted databases.
    return None
