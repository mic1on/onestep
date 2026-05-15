"""Add retention cleanup indexes.

Revision ID: 202605150003
Revises: 202605150001
Create Date: 2026-05-15 18:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202605150003"
down_revision: str | None = "202605150001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in set(inspector.get_table_names())


def _has_index(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return index_name in {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    if _has_table("agent_commands") and not _has_index(
        "agent_commands",
        "ix_agent_commands_status_updated_at",
    ):
        op.create_index(
            "ix_agent_commands_status_updated_at",
            "agent_commands",
            ["status", "updated_at"],
            unique=False,
        )
    if _has_table("task_metric_windows") and not _has_index(
        "task_metric_windows",
        "ix_task_metric_windows_window_ended_at",
    ):
        op.create_index(
            "ix_task_metric_windows_window_ended_at",
            "task_metric_windows",
            ["window_ended_at"],
            unique=False,
        )
    if _has_table("task_events") and not _has_index(
        "task_events",
        "ix_task_events_occurred_at",
    ):
        op.create_index(
            "ix_task_events_occurred_at",
            "task_events",
            ["occurred_at"],
            unique=False,
        )


def downgrade() -> None:
    if _has_table("task_events") and _has_index("task_events", "ix_task_events_occurred_at"):
        op.drop_index("ix_task_events_occurred_at", table_name="task_events")
    if _has_table("task_metric_windows") and _has_index(
        "task_metric_windows",
        "ix_task_metric_windows_window_ended_at",
    ):
        op.drop_index("ix_task_metric_windows_window_ended_at", table_name="task_metric_windows")
    if _has_table("agent_commands") and _has_index(
        "agent_commands",
        "ix_agent_commands_status_updated_at",
    ):
        op.drop_index("ix_agent_commands_status_updated_at", table_name="agent_commands")
