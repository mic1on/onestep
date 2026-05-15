from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db.models import AgentCommand, TaskEvent, TaskMetricWindow

logger = logging.getLogger("onestep_control_plane_api.ops.retention")

RetentionMode = Literal["dry_run", "execute"]
TERMINAL_AGENT_COMMAND_STATUSES = (
    "expired",
    "rejected",
    "succeeded",
    "failed",
    "timeout",
    "cancelled",
)


@dataclass(frozen=True)
class RetentionPolicy:
    name: str
    model: type[AgentCommand] | type[TaskEvent] | type[TaskMetricWindow]
    id_column: object
    timestamp_column: object
    timestamp_column_name: str
    retention_days: int
    predicate: sa.ColumnElement[bool]


@dataclass(frozen=True)
class RetentionTableReport:
    table_name: str
    mode: RetentionMode
    timestamp_column: str
    retention_days: int
    cutoff_at: datetime
    matched_rows: int
    deleted_rows: int
    batches: int
    oldest_matched_at: datetime | None
    newest_matched_at: datetime | None

    def to_dict(self) -> dict[str, object]:
        return {
            "table_name": self.table_name,
            "mode": self.mode,
            "timestamp_column": self.timestamp_column,
            "retention_days": self.retention_days,
            "cutoff_at": self.cutoff_at.isoformat(),
            "matched_rows": self.matched_rows,
            "deleted_rows": self.deleted_rows,
            "batches": self.batches,
            "oldest_matched_at": (
                self.oldest_matched_at.isoformat() if self.oldest_matched_at is not None else None
            ),
            "newest_matched_at": (
                self.newest_matched_at.isoformat() if self.newest_matched_at is not None else None
            ),
        }


@dataclass(frozen=True)
class RetentionRunReport:
    mode: RetentionMode
    started_at: datetime
    finished_at: datetime
    batch_size: int
    tables: list[RetentionTableReport]

    @property
    def matched_rows(self) -> int:
        return sum(table.matched_rows for table in self.tables)

    @property
    def deleted_rows(self) -> int:
        return sum(table.deleted_rows for table in self.tables)

    @property
    def batches(self) -> int:
        return sum(table.batches for table in self.tables)

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "batch_size": self.batch_size,
            "matched_rows": self.matched_rows,
            "deleted_rows": self.deleted_rows,
            "batches": self.batches,
            "tables": [table.to_dict() for table in self.tables],
        }


def utcnow() -> datetime:
    return datetime.now(UTC)


def build_retention_policies(*, now: datetime | None = None) -> list[RetentionPolicy]:
    current_time = now or utcnow()
    return [
        RetentionPolicy(
            name="task_events",
            model=TaskEvent,
            id_column=TaskEvent.id,
            timestamp_column=TaskEvent.occurred_at,
            timestamp_column_name="occurred_at",
            retention_days=settings.retention_task_events_days,
            predicate=TaskEvent.occurred_at
            < current_time - timedelta(days=settings.retention_task_events_days),
        ),
        RetentionPolicy(
            name="task_metric_windows",
            model=TaskMetricWindow,
            id_column=TaskMetricWindow.id,
            timestamp_column=TaskMetricWindow.window_ended_at,
            timestamp_column_name="window_ended_at",
            retention_days=settings.retention_task_metric_windows_days,
            predicate=TaskMetricWindow.window_ended_at
            < current_time - timedelta(days=settings.retention_task_metric_windows_days),
        ),
        RetentionPolicy(
            name="agent_commands",
            model=AgentCommand,
            id_column=AgentCommand.id,
            timestamp_column=AgentCommand.updated_at,
            timestamp_column_name="updated_at",
            retention_days=settings.retention_agent_commands_days,
            predicate=sa.and_(
                AgentCommand.status.in_(TERMINAL_AGENT_COMMAND_STATUSES),
                AgentCommand.updated_at
                < current_time - timedelta(days=settings.retention_agent_commands_days),
            ),
        ),
    ]


def _normalize_batch_size(batch_size: int | None) -> int:
    return batch_size or settings.retention_delete_batch_size


def _summarize_policy(
    db: Session,
    *,
    policy: RetentionPolicy,
) -> tuple[int, datetime | None, datetime | None]:
    matched_rows, oldest_matched_at, newest_matched_at = db.execute(
        sa.select(
            sa.func.count(),
            sa.func.min(policy.timestamp_column),
            sa.func.max(policy.timestamp_column),
        ).where(policy.predicate)
    ).one()
    return int(matched_rows or 0), oldest_matched_at, newest_matched_at


def _delete_policy_rows(
    db: Session,
    *,
    policy: RetentionPolicy,
    batch_size: int,
) -> tuple[int, int]:
    deleted_rows = 0
    batches = 0
    while True:
        ids = db.scalars(
            sa.select(policy.id_column)
            .where(policy.predicate)
            .order_by(policy.timestamp_column.asc(), policy.id_column.asc())
            .limit(batch_size)
        ).all()
        if not ids:
            return deleted_rows, batches

        batches += 1
        result = db.execute(sa.delete(policy.model).where(policy.id_column.in_(ids)))
        deleted_rows += int(result.rowcount or len(ids))
        db.commit()


def run_retention(
    db: Session,
    *,
    execute: bool,
    now: datetime | None = None,
    batch_size: int | None = None,
) -> RetentionRunReport:
    current_time = now or utcnow()
    normalized_batch_size = _normalize_batch_size(batch_size)
    mode: RetentionMode = "execute" if execute else "dry_run"
    reports: list[RetentionTableReport] = []

    for policy in build_retention_policies(now=current_time):
        matched_rows, oldest_matched_at, newest_matched_at = _summarize_policy(db, policy=policy)
        deleted_rows = 0
        batches = 0
        if execute and matched_rows > 0:
            deleted_rows, batches = _delete_policy_rows(
                db,
                policy=policy,
                batch_size=normalized_batch_size,
            )

        report = RetentionTableReport(
            table_name=policy.name,
            mode=mode,
            timestamp_column=policy.timestamp_column_name,
            retention_days=policy.retention_days,
            cutoff_at=current_time - timedelta(days=policy.retention_days),
            matched_rows=matched_rows,
            deleted_rows=deleted_rows,
            batches=batches,
            oldest_matched_at=oldest_matched_at,
            newest_matched_at=newest_matched_at,
        )
        logger.info("retention processed table", extra=report.to_dict())
        reports.append(report)

    finished_at = utcnow()
    retention_report = RetentionRunReport(
        mode=mode,
        started_at=current_time,
        finished_at=finished_at,
        batch_size=normalized_batch_size,
        tables=reports,
    )
    logger.info("retention run completed", extra=retention_report.to_dict())
    return retention_report
