"""Add task custom metric windows.

Revision ID: 202607180001
Revises: 202606190001
Create Date: 2026-07-18 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202607180001"
down_revision: str | None = "202606190001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if (
        _has_table("task_custom_metric_windows")
        or not _has_table("services")
        or not _has_table("instances")
    ):
        return
    op.create_table(
        "task_custom_metric_windows",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("service_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("instance_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("task_name", sa.String(length=255), nullable=False),
        sa.Column("window_id", sa.String(length=255), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metric_name", sa.String(length=255), nullable=False),
        sa.Column("metric_kind", sa.String(length=32), nullable=False),
        sa.Column("metric_value", sa.Float(), nullable=False),
        sa.Column("labels_hash", sa.String(length=64), nullable=False),
        sa.Column("labels_json", json_type, nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["instances.instance_id"],
            name=op.f("fk_task_custom_metric_windows_instance_id_instances"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_task_custom_metric_windows_service_id_services"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_custom_metric_windows")),
        sa.UniqueConstraint(
            "instance_id",
            "task_name",
            "window_id",
            "metric_name",
            "labels_hash",
            name="uq_task_custom_metric_windows_series_window",
        ),
    )
    op.create_index(
        "ix_task_custom_metric_windows_service_window",
        "task_custom_metric_windows",
        ["service_id", "window_ended_at"],
    )
    op.create_index(
        "ix_task_custom_metric_windows_metric_kind",
        "task_custom_metric_windows",
        ["metric_name", "metric_kind"],
    )


def downgrade() -> None:
    if not _has_table("task_custom_metric_windows"):
        return
    op.drop_index(
        "ix_task_custom_metric_windows_metric_kind",
        table_name="task_custom_metric_windows",
    )
    op.drop_index(
        "ix_task_custom_metric_windows_service_window",
        table_name="task_custom_metric_windows",
    )
    op.drop_table("task_custom_metric_windows")
