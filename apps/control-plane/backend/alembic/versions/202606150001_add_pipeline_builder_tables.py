"""Add pipeline builder tables.

Revision ID: 202606150001
Revises: 202605270001
Create Date: 2026-06-15 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from onestep_control_plane_api.db.types import UTCDateTime

revision: str = "202606150001"
down_revision: str | None = "202605270001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if not _has_table("pipelines"):
        op.create_table(
            "pipelines",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("graph_json", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("created_at", UTCDateTime(), nullable=False),
            sa.Column("updated_at", UTCDateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_pipelines")),
        )
        op.create_index("ix_pipelines_updated_at", "pipelines", ["updated_at"])
        op.create_index(
            "ix_pipelines_status_updated_at",
            "pipelines",
            ["status", "updated_at"],
        )
    if not _has_table("pipeline_credentials"):
        op.create_table(
            "pipeline_credentials",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("connector_type", sa.String(length=128), nullable=False),
            sa.Column("config_encrypted", sa.Text(), nullable=False),
            sa.Column("env_vars_encrypted", sa.Text(), nullable=False),
            sa.Column("created_at", UTCDateTime(), nullable=False),
            sa.Column("updated_at", UTCDateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_pipeline_credentials")),
            sa.UniqueConstraint("name", name="uq_pipeline_credentials_name"),
        )
        op.create_index(
            "ix_pipeline_credentials_connector_type_name",
            "pipeline_credentials",
            ["connector_type", "name"],
        )


def downgrade() -> None:
    if _has_table("pipeline_credentials"):
        op.drop_index(
            "ix_pipeline_credentials_connector_type_name",
            table_name="pipeline_credentials",
        )
        op.drop_table("pipeline_credentials")
    if _has_table("pipelines"):
        op.drop_index("ix_pipelines_status_updated_at", table_name="pipelines")
        op.drop_index("ix_pipelines_updated_at", table_name="pipelines")
        op.drop_table("pipelines")
