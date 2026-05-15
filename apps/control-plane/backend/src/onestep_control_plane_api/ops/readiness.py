from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from onestep_control_plane_api.core.settings import settings

SessionFactory = Callable[[], Session]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
ALEMBIC_INI_PATH = PROJECT_ROOT / "alembic.ini"


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class BackgroundTaskReadinessState:
    name: str
    started_at: datetime | None = None
    last_tick_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_error: str | None = None

    def mark_started(self, when: datetime | None = None) -> None:
        timestamp = when or utcnow()
        self.started_at = timestamp
        self.last_tick_at = timestamp

    def mark_tick(self, when: datetime | None = None) -> None:
        self.last_tick_at = when or utcnow()

    def mark_success(self, when: datetime | None = None) -> None:
        timestamp = when or utcnow()
        self.last_tick_at = timestamp
        self.last_success_at = timestamp
        self.last_error = None

    def mark_failure(self, exc: Exception, when: datetime | None = None) -> None:
        timestamp = when or utcnow()
        self.last_tick_at = timestamp
        self.last_failure_at = timestamp
        self.last_error = str(exc)


@dataclass(frozen=True)
class CheckResult:
    ready: bool
    detail: str
    meta: dict[str, Any] = field(default_factory=dict)

    def to_response(self) -> dict[str, Any]:
        payload = {
            "ready": self.ready,
            "detail": self.detail,
        }
        if self.meta:
            payload["meta"] = self.meta
        return payload


@dataclass(frozen=True)
class ReadinessReport:
    ready: bool
    environment: str
    ingestion_auth_configured: bool
    database: CheckResult
    migrations: CheckResult
    background_tasks: dict[str, CheckResult]

    def to_response(self) -> dict[str, Any]:
        checks: dict[str, Any] = {
            "database": self.database.to_response(),
            "migrations": self.migrations.to_response(),
            "background_tasks": {
                name: result.to_response()
                for name, result in self.background_tasks.items()
            },
        }
        return {
            "status": "ready" if self.ready else "not_ready",
            "environment": self.environment,
            "ingestion_auth_configured": self.ingestion_auth_configured,
            "checks": checks,
        }


def create_alembic_config() -> Config:
    return Config(str(ALEMBIC_INI_PATH))


@lru_cache(maxsize=1)
def get_expected_migration_heads() -> tuple[str, ...]:
    script = ScriptDirectory.from_config(create_alembic_config())
    return tuple(sorted(script.get_heads()))


def check_database(session_factory: SessionFactory) -> CheckResult:
    try:
        with session_factory() as session:
            session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        return CheckResult(
            ready=False,
            detail="database connection failed",
            meta={"error": str(exc)},
        )
    except Exception as exc:
        return CheckResult(
            ready=False,
            detail="database readiness check failed",
            meta={"error": str(exc)},
        )
    return CheckResult(ready=True, detail="database connection ok")


def check_migrations(session_factory: SessionFactory) -> CheckResult:
    expected_heads = get_expected_migration_heads()
    if not expected_heads:
        return CheckResult(ready=False, detail="no alembic head revision found")

    try:
        with session_factory() as session:
            current_revisions = tuple(
                sorted(
                    row[0]
                    for row in session.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).all()
                )
            )
    except SQLAlchemyError as exc:
        return CheckResult(
            ready=False,
            detail="database migration check failed",
            meta={"error": str(exc)},
        )
    except Exception as exc:
        return CheckResult(
            ready=False,
            detail="database migration check failed",
            meta={"error": str(exc)},
        )

    if current_revisions != expected_heads:
        return CheckResult(
            ready=False,
            detail="database schema revision is not at head",
            meta={
                "current_revisions": list(current_revisions),
                "expected_heads": list(expected_heads),
            },
        )

    return CheckResult(
        ready=True,
        detail="database schema revision is at head",
        meta={"current_revisions": list(current_revisions)},
    )


def _check_background_task(
    *,
    name: str,
    state: BackgroundTaskReadinessState | None,
    task: Any,
) -> CheckResult:
    if state is None:
        return CheckResult(ready=False, detail="background task state is missing")
    if task is None:
        return CheckResult(ready=False, detail="background task is not registered")
    if getattr(task, "done", lambda: False)():
        return CheckResult(ready=False, detail="background task stopped unexpectedly")
    if state.started_at is None:
        return CheckResult(ready=False, detail="background task has not started")

    last_seen_at = state.last_tick_at or state.started_at
    stale_after_s = max(
        settings.readiness_task_stale_after_s,
        settings.notification_missed_start_scan_interval_s,
    )
    age_s = max(0.0, (utcnow() - last_seen_at).total_seconds())
    if age_s > stale_after_s:
        return CheckResult(
            ready=False,
            detail="background task is stalled",
            meta={
                "task": name,
                "age_s": round(age_s, 3),
                "stale_after_s": stale_after_s,
                "last_error": state.last_error,
            },
        )

    meta: dict[str, Any] = {
        "task": name,
        "last_seen_at": last_seen_at.isoformat(),
        "stale_after_s": stale_after_s,
    }
    if state.last_success_at is not None:
        meta["last_success_at"] = state.last_success_at.isoformat()
    if state.last_failure_at is not None:
        meta["last_failure_at"] = state.last_failure_at.isoformat()
    if state.last_error is not None:
        meta["last_error"] = state.last_error
    return CheckResult(ready=True, detail="background task running", meta=meta)


def check_background_tasks(app: FastAPI) -> dict[str, CheckResult]:
    states = getattr(app.state, "background_task_states", {})
    tasks = getattr(app.state, "background_task_refs", {})

    if not states:
        return {
            "background_tasks": CheckResult(
                ready=False,
                detail="no background task state registered",
            )
        }

    return {
        name: _check_background_task(
            name=name,
            state=state,
            task=tasks.get(name),
        )
        for name, state in states.items()
    }


def build_default_background_task_states() -> dict[str, BackgroundTaskReadinessState]:
    return {
        "notification_missed_start_scanner": BackgroundTaskReadinessState(
            name="notification_missed_start_scanner"
        )
    }


def build_readiness_report(app: FastAPI) -> ReadinessReport:
    session_factory = getattr(app.state, "session_factory", None)
    if session_factory is None:
        database = CheckResult(ready=False, detail="session factory is not configured")
        migrations = CheckResult(ready=False, detail="session factory is not configured")
    else:
        database = check_database(session_factory)
        migrations = (
            check_migrations(session_factory)
            if database.ready
            else CheckResult(ready=False, detail="database connection failed")
        )

    background_tasks = check_background_tasks(app)
    all_ready = (
        database.ready
        and migrations.ready
        and all(result.ready for result in background_tasks.values())
    )
    return ReadinessReport(
        ready=all_ready,
        environment=settings.app_env,
        ingestion_auth_configured=settings.ingest_auth_configured,
        database=database,
        migrations=migrations,
        background_tasks=background_tasks,
    )
