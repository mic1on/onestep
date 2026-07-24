"""Include custom metric kind in unique key.

Revision ID: 202607180002
Revises: 202607180001
Create Date: 2026-07-18 21:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607180002"
down_revision: str | None = "202607180001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

CONSTRAINT_NAME = "uq_task_custom_metric_windows_series_window"
OLD_COLUMNS = [
    "instance_id",
    "task_name",
    "window_id",
    "metric_name",
    "labels_hash",
]
NEW_COLUMNS = [
    "instance_id",
    "task_name",
    "window_id",
    "metric_name",
    "metric_kind",
    "labels_hash",
]


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if (
        not _has_table("task_custom_metric_windows")
        or not _has_table("services")
        or not _has_table("instances")
    ):
        return
    with op.batch_alter_table("task_custom_metric_windows") as batch_op:
        batch_op.drop_constraint(CONSTRAINT_NAME, type_="unique")
        batch_op.create_unique_constraint(CONSTRAINT_NAME, NEW_COLUMNS)


def downgrade() -> None:
    if (
        not _has_table("task_custom_metric_windows")
        or not _has_table("services")
        or not _has_table("instances")
    ):
        return
    with op.batch_alter_table("task_custom_metric_windows") as batch_op:
        batch_op.drop_constraint(CONSTRAINT_NAME, type_="unique")
        batch_op.create_unique_constraint(CONSTRAINT_NAME, OLD_COLUMNS)
