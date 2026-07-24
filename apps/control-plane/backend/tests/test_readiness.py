from collections.abc import Generator

import pytest
from fastapi import FastAPI
from onestep_control_plane_api.ops.readiness import (
    build_default_background_task_states,
    build_readiness_report,
    get_expected_migration_heads,
)
from onestep_control_plane_api.workers.notification_scanner import (
    NOTIFICATION_MISSED_START_SCANNER_NAME,
)
from onestep_control_plane_api.workers.retention_worker import RETENTION_WORKER_NAME
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


@pytest.fixture()
def sqlite_session_factory() -> Generator:
    head_revision = get_expected_migration_heads()[0]
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:version_num)"),
            {"version_num": head_revision},
        )

    def factory() -> Session:
        return Session(engine)

    yield factory
    engine.dispose()


def build_app(session_factory) -> FastAPI:
    app = FastAPI()
    app.state.session_factory = session_factory
    app.state.background_task_states = build_default_background_task_states()
    for state in app.state.background_task_states.values():
        state.mark_started()
        state.mark_leader("local")
        state.mark_success()
    app.state.background_task_refs = {name: object() for name in app.state.background_task_states}
    return app


def test_build_readiness_report_is_ready(sqlite_session_factory) -> None:
    app = build_app(sqlite_session_factory)

    report = build_readiness_report(app)

    assert report.ready is True
    assert report.database.ready is True
    assert report.migrations.ready is True
    assert report.background_tasks[NOTIFICATION_MISSED_START_SCANNER_NAME].ready is True
    assert report.background_tasks[RETENTION_WORKER_NAME].ready is True


def test_build_readiness_report_fails_when_schema_revision_is_behind(
    sqlite_session_factory,
) -> None:
    app = build_app(sqlite_session_factory)

    with sqlite_session_factory() as session:
        session.execute(text("DELETE FROM alembic_version"))
        session.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:version_num)"),
            {"version_num": "202603180001"},
        )
        session.commit()

    report = build_readiness_report(app)

    assert report.ready is False
    assert report.migrations.ready is False
    assert report.migrations.detail == "database schema revision is not at head"


def test_build_readiness_report_fails_without_session_factory() -> None:
    app = FastAPI()
    app.state.background_task_states = {}
    app.state.background_task_refs = {}

    report = build_readiness_report(app)

    assert report.ready is False
    assert report.database.ready is False
    assert report.database.detail == "session factory is not configured"


def test_build_readiness_report_marks_standby_worker_ready(sqlite_session_factory) -> None:
    app = build_app(sqlite_session_factory)
    state = app.state.background_task_states[RETENTION_WORKER_NAME]
    state.mark_standby("postgres_advisory_lock")

    report = build_readiness_report(app)
    result = report.background_tasks[RETENTION_WORKER_NAME]

    assert result.ready is True
    assert result.detail == "background task standing by for lease"
    assert result.meta["leadership_mode"] == "postgres_advisory_lock"
    assert result.meta["leadership_status"] == "standby"
