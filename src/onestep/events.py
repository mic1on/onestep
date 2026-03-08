from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import logging
from typing import Any

from .retry import FailureInfo, FailureKind


class TaskEventKind(str, Enum):
    FETCHED = "fetched"
    STARTED = "started"
    SUCCEEDED = "succeeded"
    RETRIED = "retried"
    FAILED = "failed"
    DEAD_LETTERED = "dead_lettered"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class TaskEvent:
    kind: TaskEventKind
    app: str
    task: str
    source: str | None
    attempts: int
    emitted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration_s: float | None = None
    failure: FailureInfo | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class InMemoryMetrics:
    def __init__(self) -> None:
        self._kind_counts: Counter[TaskEventKind] = Counter()
        self._task_counts: Counter[tuple[str, TaskEventKind]] = Counter()
        self._failure_counts: Counter[tuple[TaskEventKind, FailureKind]] = Counter()

    def __call__(self, event: TaskEvent) -> None:
        self._kind_counts[event.kind] += 1
        self._task_counts[(event.task, event.kind)] += 1
        if event.failure is not None:
            self._failure_counts[(event.kind, event.failure.kind)] += 1

    def count(
        self,
        kind: TaskEventKind,
        *,
        task: str | None = None,
        failure_kind: FailureKind | None = None,
    ) -> int:
        if task is not None:
            return self._task_counts[(task, kind)]
        if failure_kind is not None:
            return self._failure_counts[(kind, failure_kind)]
        return self._kind_counts[kind]

    def snapshot(self) -> dict[str, Any]:
        return {
            "kinds": {kind.value: count for kind, count in self._kind_counts.items()},
            "tasks": {
                f"{task}:{kind.value}": count
                for (task, kind), count in self._task_counts.items()
            },
            "failures": {
                f"{kind.value}:{failure_kind.value}": count
                for (kind, failure_kind), count in self._failure_counts.items()
            },
        }


class StructuredEventLogger:
    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        logger_name: str = "onestep.events",
        level_by_kind: dict[TaskEventKind, int] | None = None,
    ) -> None:
        self.logger = logger or logging.getLogger(logger_name)
        self.level_by_kind = {
            TaskEventKind.FETCHED: logging.DEBUG,
            TaskEventKind.STARTED: logging.DEBUG,
            TaskEventKind.SUCCEEDED: logging.INFO,
            TaskEventKind.RETRIED: logging.WARNING,
            TaskEventKind.FAILED: logging.ERROR,
            TaskEventKind.DEAD_LETTERED: logging.ERROR,
            TaskEventKind.CANCELLED: logging.WARNING,
            **(level_by_kind or {}),
        }

    def __call__(self, event: TaskEvent) -> None:
        failure = event.failure
        self.logger.log(
            self.level_by_kind[event.kind],
            f"task {event.kind.value}",
            extra={
                "event_kind": event.kind.value,
                "app_name": event.app,
                "task_name": event.task,
                "source_name": event.source,
                "attempts": event.attempts,
                "duration_s": event.duration_s,
                "emitted_at": event.emitted_at.isoformat(),
                "failure_kind": failure.kind.value if failure is not None else None,
                "failure_exception_type": failure.exception_type if failure is not None else None,
                "failure_message": failure.message if failure is not None else None,
                "task_event_meta": dict(event.meta),
            },
        )


__all__ = [
    "InMemoryMetrics",
    "StructuredEventLogger",
    "TaskEvent",
    "TaskEventKind",
]
