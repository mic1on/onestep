"""Tests for the native-async database foundation in ``db.session``.

Covers the happy path, the failure path, the cancellation path and the
concurrency contract, plus the documented database support range. The
synchronous API is exercised as well, because this commit must not disturb it.

These tests deliberately import ``aiosqlite`` and ``greenlet`` at module level.
SQLAlchemy's own greenlet requirement is marker-gated on ``platform_machine``
values such as ``aarch64``/``x86_64``; macOS arm64 reports ``arm64``, so the
marker does not match and the dependency is never installed locally unless the
project declares it explicitly. A missing driver must fail loudly here rather
than degrade silently, so collection failing is the intended regression signal.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import os
import typing
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import aiosqlite  # noqa: F401  # regression guard: async test driver must be installed
import greenlet  # noqa: F401  # regression guard: see test_greenlet_is_importable
import pytest
import sqlalchemy as sa
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from onestep_control_plane_api.db import session as db_session
from onestep_control_plane_api.db.base import Base
from onestep_control_plane_api.db.models import Service
from onestep_control_plane_api.db.session import (
    SUPPORTED_ASYNC_DIALECTS,
    AsyncSessionLocal,
    SessionLocal,
    UnsupportedAsyncDatabaseError,
    async_engine,
    create_async_engine_from_url,
    create_async_session_factory,
    engine,
    get_async_session,
    get_db_session,
    init_db,
    resolve_async_database_url,
    session_scope,
)
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import DetachedInstanceError

#: Real PostgreSQL for the production-path test. Opt in, so a machine without a
#: database keeps a green suite; CI is expected to set it when it has one.
POSTGRES_URL_ENV = "ONESTEP_CP_TEST_ASYNC_DATABASE_URL"

_INSERT_SERVICE = text(
    "INSERT INTO services (id, name, environment, latest_deployment_version, "
    "created_at, updated_at) VALUES (:id, :name, :environment, '1', "
    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
)


class PoolProbe:
    """Counts pool checkouts/checkins so connection leaks are observable.

    ``StaticPool`` has no ``checkedout()``, and the test path may use either
    pool class, so this tracks engine-level checkout/checkin events and only
    falls back to ``checkedout()`` for a real pool object.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self.checkouts = 0
        self.checkins = 0
        event.listen(engine.sync_engine, "checkout", self._on_checkout)
        event.listen(engine.sync_engine, "checkin", self._on_checkin)

    def _on_checkout(self, *_args: object, **_kwargs: object) -> None:
        self.checkouts += 1

    def _on_checkin(self, *_args: object, **_kwargs: object) -> None:
        self.checkins += 1

    @property
    def leaked(self) -> int:
        return self.checkouts - self.checkins

    def checkedout(self) -> int:
        pool = self._engine.pool
        checkedout = getattr(pool, "checkedout", None)
        if callable(checkedout):
            return int(checkedout())
        return self.leaked


class AsyncTestDatabase:
    """A file-backed sqlite+aiosqlite engine installed as the module singleton."""

    def __init__(self, path: Path) -> None:
        self.url = f"sqlite+aiosqlite:///{path}"
        self.engine = create_async_engine_from_url(self.url)
        self.probe = PoolProbe(self.engine)
        self.factory: async_sessionmaker[AsyncSession] = create_async_session_factory(self.engine)

    async def create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def count_services(self) -> int:
        async with self.factory() as session:
            result = await session.execute(text("SELECT count(*) FROM services"))
            return int(result.scalar_one())

    async def dispose(self) -> None:
        await self.engine.dispose()


def install_recording_factory(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record ``commit``/``rollback``/``close`` calls made by the session scope.

    End-state assertions alone cannot distinguish "rolled back" from "closed
    without committing", because ``AsyncSession.close()`` also discards an open
    transaction. Recording the calls proves which operation actually ran.
    """

    calls: list[str] = []
    engine = db_session.get_async_engine()

    class RecordingSession(AsyncSession):
        async def commit(self) -> None:
            calls.append("commit")
            await super().commit()

        async def rollback(self) -> None:
            calls.append("rollback")
            await super().rollback()

        async def close(self) -> None:
            calls.append("close")
            await super().close()

    monkeypatch.setattr(
        db_session,
        "AsyncSessionLocal",
        async_sessionmaker(
            bind=engine,
            autoflush=False,
            expire_on_commit=False,
            class_=RecordingSession,
        ),
    )
    return calls


@pytest.fixture()
def async_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AsyncTestDatabase]:
    """Install a real sqlite+aiosqlite engine as the module's async singletons."""

    database = AsyncTestDatabase(tmp_path / "async-session.db")
    asyncio.run(database.create_schema())
    monkeypatch.setattr(db_session, "async_engine", database.engine)
    monkeypatch.setattr(db_session, "AsyncSessionLocal", database.factory)
    try:
        yield database
    finally:
        asyncio.run(database.dispose())


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Exported interface
# --------------------------------------------------------------------------- #


def test_module_exposes_async_engine_and_session_factory() -> None:
    assert isinstance(async_engine, AsyncEngine)
    assert isinstance(AsyncSessionLocal, async_sessionmaker)
    assert AsyncSessionLocal.class_ is AsyncSession
    assert AsyncSessionLocal.kw["autoflush"] is False
    assert AsyncSessionLocal.kw["expire_on_commit"] is False


def test_get_async_session_is_a_fastapi_async_dependency() -> None:
    # FastAPI resolves async generator dependencies by iterating them.
    assert inspect.isasyncgenfunction(get_async_session)


def test_session_scope_is_an_async_context_manager() -> None:
    """``@asynccontextmanager`` keeps the signature but yields a real CM."""

    assert inspect.isasyncgenfunction(session_scope.__wrapped__)

    async def scenario() -> None:
        manager = session_scope()
        assert hasattr(manager, "__aenter__")
        assert hasattr(manager, "__aexit__")
        session = await manager.__aenter__()
        try:
            assert isinstance(session, AsyncSession)
        finally:
            # A clean exit must not suppress anything.
            assert await manager.__aexit__(None, None, None) is not True

    run(scenario())


def test_module_does_not_wrap_sync_database_calls_in_threads() -> None:
    """The issue rejects ``asyncio.to_thread`` as the async mechanism.

    Parsed as an AST rather than matched as text, so the docstring that names
    the rejected pattern does not count as using it.
    """

    tree = ast.parse(Path(db_session.__file__).read_text(encoding="utf-8"))
    anti_patterns = {"to_thread", "run_in_executor", "run_until_complete"}
    offenders = sorted(
        {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr in anti_patterns
        }
    )
    assert offenders == [], f"synchronous database calls wrapped for async: {offenders}"

    # The async path is SQLAlchemy's own asyncio extension, not a thread bridge.
    assert AsyncSessionLocal.class_ is AsyncSession
    assert isinstance(async_engine, AsyncEngine)
    assert "sqlalchemy.ext.asyncio" in inspect.getsource(db_session)


def test_greenlet_is_importable_and_backs_a_real_async_round_trip(async_db) -> None:
    """Regression guard for the macOS arm64 greenlet marker gap.

    SQLAlchemy's greenlet requirement is marker-gated on ``platform_machine``
    values such as ``aarch64``/``x86_64``. macOS arm64 reports ``arm64``, so the
    marker misses and ``AsyncSession`` fails with "the greenlet library is
    required to use this function" unless the project declares it explicitly.
    The round trip is what actually exercises the greenlet bridge.
    """

    assert greenlet.__version__

    async def round_trip() -> int:
        async with async_db.factory() as session:
            result = await session.execute(text("SELECT 1"))
            return int(result.scalar_one())

    assert run(round_trip()) == 1


# --------------------------------------------------------------------------- #
# Database support range
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        # Production path passes through unchanged.
        (
            "postgresql+psycopg://user:pw@localhost:5432/onestep",
            "postgresql+psycopg://user:pw@localhost:5432/onestep",
        ),
        # The project's synchronous PostgreSQL spellings upgrade to psycopg3 async.
        (
            "postgresql://user:pw@localhost:5432/onestep",
            "postgresql+psycopg://user:pw@localhost:5432/onestep",
        ),
        (
            "postgresql+psycopg2://user:pw@localhost:5432/onestep",
            "postgresql+psycopg://user:pw@localhost:5432/onestep",
        ),
        # Test path.
        ("sqlite+aiosqlite:///tmp/onestep.db", "sqlite+aiosqlite:///tmp/onestep.db"),
        ("sqlite:///tmp/onestep.db", "sqlite+aiosqlite:///tmp/onestep.db"),
        ("sqlite+pysqlite:///:memory:", "sqlite+aiosqlite:///:memory:"),
    ],
)
def test_supported_urls_resolve_to_their_async_driver(configured: str, expected: str) -> None:
    assert resolve_async_database_url(configured) == expected


def test_supported_async_dialects_are_exactly_postgres_and_sqlite() -> None:
    assert SUPPORTED_ASYNC_DIALECTS == frozenset({"postgresql+psycopg", "sqlite+aiosqlite"})


@pytest.mark.parametrize(
    "unsupported",
    [
        "mysql+pymysql://user:pw@localhost:3306/onestep",
        "mysql+aiomysql://user:pw@localhost:3306/onestep",
        "mysql+mysqldb://user:pw@localhost:3306/onestep",
        "mariadb+pymysql://user:pw@localhost:3306/onestep",
        "oracle+oracledb://user:pw@localhost:1521/onestep",
        "mssql+pyodbc://user:pw@localhost/onestep",
    ],
)
def test_unsupported_dialect_raises_instead_of_falling_back_to_sync(unsupported: str) -> None:
    """An unsupported database must fail explicitly, never silently degrade."""

    with pytest.raises(UnsupportedAsyncDatabaseError) as excinfo:
        resolve_async_database_url(unsupported)

    message = str(excinfo.value)
    assert "Supported async dialects" in message
    assert "postgresql+psycopg" in message
    assert "sqlite+aiosqlite" in message
    # MySQL specifically must be called out as unsupported.
    assert "MySQL is not supported" in message
    # And it must be clear that no synchronous fallback happened.
    assert "never falls back to synchronous" in message
    # The offending dialect is named, so the operator knows what to change.
    assert unsupported.split("://", 1)[0] in message


def test_unsupported_dialect_raises_from_engine_creation_too() -> None:
    """The guard holds at the engine boundary, not just the URL helper."""

    with pytest.raises(UnsupportedAsyncDatabaseError):
        create_async_engine_from_url("mysql+aiomysql://user:pw@localhost:3306/onestep")


def test_unparseable_url_raises_explicit_error() -> None:
    with pytest.raises(UnsupportedAsyncDatabaseError) as excinfo:
        resolve_async_database_url("definitely not a database url")

    assert "Could not parse" in str(excinfo.value)


def test_unsupported_dialect_does_not_break_synchronous_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Importing the module must not require an async driver.

    A deployment on a URL outside the async support range keeps the synchronous
    API working; only an actual async request fails, and it fails loudly.
    """

    # The lazy accessors are the mechanism; calling one must raise rather than
    # returning a synchronous engine.
    monkeypatch.setattr(db_session.settings, "database_url", "mysql+pymysql://u:p@h/db")
    monkeypatch.delattr(db_session, "async_engine", raising=False)

    with pytest.raises(UnsupportedAsyncDatabaseError):
        db_session.get_async_engine()


@pytest.mark.skipif(
    not os.environ.get(POSTGRES_URL_ENV),
    reason=f"set {POSTGRES_URL_ENV} to a reachable PostgreSQL to run this test",
)
def test_postgresql_async_path_works_and_releases_the_connection() -> None:
    """The documented production path, exercised against a real PostgreSQL."""

    url = os.environ[POSTGRES_URL_ENV]
    probe_engine = create_async_engine_from_url(url)
    probe = PoolProbe(probe_engine)

    async def scenario() -> None:
        factory = create_async_session_factory(probe_engine)
        # A short work unit commits and releases its connection.
        async with factory() as session:
            assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
            await session.commit()

        # An independent task keeps running while the database is busy: the
        # event loop is not blocked by the in-flight query.
        ticks = 0
        stop = False

        async def ticker() -> None:
            nonlocal ticks
            while not stop:
                ticks += 1
                await asyncio.sleep(0.01)

        ticker_task = asyncio.create_task(ticker())
        try:
            async with factory() as session:
                await session.execute(text("SELECT pg_sleep(1.0)"))
                await session.commit()
        finally:
            stop = True
            await ticker_task

        assert ticks > 5, f"event loop was blocked during the query (ticks={ticks})"
        assert probe.checkedout() == 0

    try:
        run(scenario())
    finally:
        run(probe_engine.dispose())


# --------------------------------------------------------------------------- #
# Transaction convention: commit / rollback / release
# --------------------------------------------------------------------------- #


def test_successful_work_unit_commits(async_db: AsyncTestDatabase) -> None:
    async def work_unit() -> None:
        async with session_scope() as session:
            await session.execute(
                _INSERT_SERVICE,
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "name": "billing",
                    "environment": "prod",
                },
            )

    run(work_unit())
    assert run(async_db.count_services()) == 1


def test_successful_work_unit_calls_commit_then_close(
    async_db: AsyncTestDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The success path commits and then releases the session, in that order."""

    calls = install_recording_factory(monkeypatch)

    async def work_unit() -> None:
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))

    run(work_unit())
    assert calls == ["commit", "close"]


def test_failed_work_unit_calls_rollback_then_close(
    async_db: AsyncTestDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure path must roll back explicitly, not merely close."""

    calls = install_recording_factory(monkeypatch)

    async def work_unit() -> None:
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
            raise RuntimeError("failure")

    with pytest.raises(RuntimeError, match="failure"):
        run(work_unit())
    assert calls == ["rollback", "close"]


def test_cancelled_work_unit_calls_rollback_then_close(
    async_db: AsyncTestDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancellation must roll back and release, not leave the session dangling."""

    calls = install_recording_factory(monkeypatch)
    entered = asyncio.Event()

    async def work_unit() -> None:
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
            entered.set()
            await asyncio.sleep(30)

    async def scenario() -> None:
        task = asyncio.create_task(work_unit())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())
    assert calls == ["rollback", "close"]


def test_work_unit_that_raises_rolls_back_and_leaves_no_partial_row(
    async_db: AsyncTestDatabase,
) -> None:
    async def work_unit() -> None:
        async with session_scope() as session:
            await session.execute(
                _INSERT_SERVICE,
                {
                    "id": "22222222-2222-2222-2222-222222222222",
                    "name": "partial",
                    "environment": "prod",
                },
            )
            # The row is flushed to the open transaction before the failure.
            await session.flush()
            raise RuntimeError("downstream failure")

    with pytest.raises(RuntimeError, match="downstream failure"):
        run(work_unit())

    # No partial row survived, and no connection leaked.
    assert run(async_db.count_services()) == 0
    assert async_db.probe.checkedout() == 0


def test_work_unit_rolls_back_on_non_exception_base_exception(
    async_db: AsyncTestDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``except BaseException`` is required so odd failures still roll back.

    A plain ``except Exception`` would skip the rollback here; the recorded
    calls make that difference observable.
    """

    calls = install_recording_factory(monkeypatch)

    class FatalSignal(BaseException):
        pass

    async def work_unit() -> None:
        async with session_scope() as session:
            await session.execute(
                _INSERT_SERVICE,
                {
                    "id": "33333333-3333-3333-3333-333333333333",
                    "name": "fatal",
                    "environment": "prod",
                },
            )
            await session.flush()
            raise FatalSignal

    with pytest.raises(FatalSignal):
        run(work_unit())

    assert calls == ["rollback", "close"]
    assert run(async_db.count_services()) == 0
    assert async_db.probe.checkedout() == 0


def test_cancellation_releases_the_session_and_leaves_no_open_transaction(
    async_db: AsyncTestDatabase,
) -> None:
    """Cancelling a work unit must not strand a transaction or a connection."""

    baseline = async_db.probe.checkedout()
    entered = asyncio.Event()
    captured: dict[str, AsyncSession] = {}

    async def work_unit() -> None:
        async with session_scope() as session:
            captured["session"] = session
            await session.execute(
                _INSERT_SERVICE,
                {
                    "id": "44444444-4444-4444-4444-444444444444",
                    "name": "cancelled",
                    "environment": "prod",
                },
            )
            entered.set()
            await asyncio.sleep(30)

    async def scenario() -> None:
        task = asyncio.create_task(work_unit())
        await entered.wait()
        # The work unit really did hold a connection while it was open.
        assert async_db.probe.checkedout() == baseline + 1

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())

    session = captured["session"]
    assert session.in_transaction() is False
    assert async_db.probe.checkedout() == baseline
    # The uncommitted insert is gone.
    assert run(async_db.count_services()) == 0


def test_cancellation_is_not_swallowed(async_db: AsyncTestDatabase) -> None:
    """Releasing on cancellation must not convert it into a success."""

    async def work_unit() -> str:
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
            await asyncio.sleep(30)
        return "finished"

    async def scenario() -> None:
        task = asyncio.create_task(work_unit())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled() is True

    run(scenario())


def test_session_scope_yields_exactly_one_session_per_work_unit(
    async_db: AsyncTestDatabase,
) -> None:
    """One session per work unit: the same object throughout the block."""

    seen: list[int] = []

    async def work_unit() -> None:
        async with session_scope() as session:
            seen.append(id(session))
            await session.execute(text("SELECT 1"))
            seen.append(id(session))

    run(work_unit())
    assert len(seen) == 2
    assert seen[0] == seen[1]


# --------------------------------------------------------------------------- #
# Transaction convention: no sharing across concurrent tasks
# --------------------------------------------------------------------------- #


def test_concurrent_work_units_never_share_one_session(async_db: AsyncTestDatabase) -> None:
    """Concurrent work units must each get their own ``AsyncSession``.

    Sharing one ``AsyncSession`` between concurrent tasks does not merely
    corrupt state: SQLAlchemy serialises access and the tasks deadlock. The
    in-block assertion therefore checks distinctness eagerly, and the timeout is
    the backstop that turns a deadlock into a failure instead of a hang.
    """

    sessions: list[int] = []
    committed: list[int] = []

    async def work_unit(index: int) -> None:
        async with session_scope() as session:
            # Checked eagerly: a later assertion would never be reached if two
            # tasks deadlocked on a shared session first.
            assert id(session) not in sessions, "one AsyncSession shared across concurrent tasks"
            sessions.append(id(session))
            await session.execute(
                _INSERT_SERVICE,
                {
                    "id": f"{index:08d}-0000-0000-0000-000000000000",
                    "name": f"svc-{index}",
                    "environment": "prod",
                },
            )
            # Interleave: if sessions were shared, this would corrupt state.
            await asyncio.sleep(0.01)
            committed.append(index)

    async def scenario() -> None:
        await asyncio.wait_for(
            asyncio.gather(*(work_unit(index) for index in range(6))),
            timeout=30,
        )

    run(scenario())

    assert len(sessions) == 6
    assert len(set(sessions)) == 6, "one AsyncSession was shared across concurrent tasks"
    assert sorted(committed) == list(range(6))
    # Every work unit committed exactly one row and released its connection.
    assert run(async_db.count_services()) == 6
    assert async_db.probe.checkedout() == 0


def test_one_session_is_not_used_by_two_tasks_at_once(async_db: AsyncTestDatabase) -> None:
    """A single ``AsyncSession`` is not concurrency-safe; the factory never reuses one."""

    async def scenario() -> None:
        first = db_session.get_async_session_factory()()
        second = db_session.get_async_session_factory()()
        try:
            assert first is not second
            await first.execute(text("SELECT 1"))
            await second.execute(text("SELECT 1"))
        finally:
            await first.close()
            await second.close()

    run(scenario())


# --------------------------------------------------------------------------- #
# Transaction convention: return plain data
# --------------------------------------------------------------------------- #


def test_committed_values_stay_readable_after_the_transaction_closed(
    async_db: AsyncTestDatabase,
) -> None:
    """``expire_on_commit=False`` keeps plain values usable after the work unit.

    This is what lets a work unit return plain data instead of a live ORM
    instance whose attribute access would trigger implicit IO after the
    transaction closed.
    """

    async def work_unit() -> tuple[str, str]:
        async with session_scope() as session:
            session.add(
                Service(
                    id=uuid.UUID("55555555-5555-5555-5555-555555555555"),
                    name="plain-data",
                    environment="prod",
                    latest_deployment_version="1",
                )
            )
            await session.flush()
            service = await session.get(
                Service, uuid.UUID("55555555-5555-5555-5555-555555555555")
            )
            assert service is not None
            # Plain data extracted inside the work unit.
            return service.name, service.environment

    assert run(work_unit()) == ("plain-data", "prod")
    assert async_db.probe.checkedout() == 0


def test_orm_instance_detached_after_work_unit_is_not_a_live_session(
    async_db: AsyncTestDatabase,
) -> None:
    """Documents why the convention prefers plain data over ORM instances.

    Plain column values stay readable thanks to ``expire_on_commit=False``, but
    an unloaded relationship on the detached instance raises
    ``DetachedInstanceError``: that is exactly the implicit IO the convention
    tells callers to avoid by returning plain data.
    """

    captured: dict[str, Any] = {}

    async def work_unit() -> None:
        async with session_scope() as session:
            session.add(
                Service(
                    id=uuid.UUID("66666666-6666-6666-6666-666666666666"),
                    name="detached",
                    environment="prod",
                    latest_deployment_version="1",
                )
            )
            await session.flush()
            captured["service"] = await session.get(
                Service, uuid.UUID("66666666-6666-6666-6666-666666666666")
            )

    run(work_unit())

    service = captured["service"]
    # A column already loaded stays readable without a round trip...
    assert service.name == "detached"
    # ...but an unloaded relationship cannot be fetched: the session is closed.
    with pytest.raises(DetachedInstanceError):
        _ = service.instances


# --------------------------------------------------------------------------- #
# FastAPI dependency
# --------------------------------------------------------------------------- #


def _build_app() -> FastAPI:
    app = FastAPI()

    @app.get("/count")
    async def count(session: AsyncSession = Depends(get_async_session)) -> dict[str, int]:
        result = await session.execute(text("SELECT count(*) FROM services"))
        return {"count": int(result.scalar_one())}

    @app.post("/insert-then-fail")
    async def insert_then_fail(
        session: AsyncSession = Depends(get_async_session),
    ) -> dict[str, int]:
        await session.execute(
            _INSERT_SERVICE,
            {"id": "77777777-7777-7777-7777-777777777777", "name": "boom", "environment": "prod"},
        )
        await session.flush()
        raise HTTPException(status_code=500, detail="boom")

    @app.post("/insert")
    async def insert(session: AsyncSession = Depends(get_async_session)) -> dict[str, int]:
        await session.execute(
            _INSERT_SERVICE,
            {"id": "88888888-8888-8888-8888-888888888888", "name": "ok", "environment": "prod"},
        )
        return {"inserted": 1}

    return app


def test_fastapi_dependency_commits_on_success(async_db: AsyncTestDatabase) -> None:
    with TestClient(_build_app()) as client:
        assert client.post("/insert").status_code == 200
        assert client.get("/count").json() == {"count": 1}

    assert run(async_db.count_services()) == 1
    assert async_db.probe.checkedout() == 0


def test_fastapi_dependency_rolls_back_when_the_handler_fails(
    async_db: AsyncTestDatabase,
) -> None:
    with TestClient(_build_app()) as client:
        assert client.post("/insert-then-fail").status_code == 500
        assert client.get("/count").json() == {"count": 0}

    assert run(async_db.count_services()) == 0
    assert async_db.probe.checkedout() == 0


def test_fastapi_dependency_closes_the_session(async_db: AsyncTestDatabase) -> None:
    """Driving the dependency generator directly proves the release path."""

    async def scenario() -> None:
        generator = get_async_session()
        session = await generator.__anext__()
        await session.execute(text("SELECT 1"))
        assert session.in_transaction() is True
        with pytest.raises(StopAsyncIteration):
            await generator.__anext__()
        assert session.in_transaction() is False

    run(scenario())
    assert async_db.probe.checkedout() == 0


def test_fastapi_dependency_calls_commit_then_close(
    async_db: AsyncTestDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = install_recording_factory(monkeypatch)

    async def scenario() -> None:
        generator = get_async_session()
        session = await generator.__anext__()
        await session.execute(text("SELECT 1"))
        with pytest.raises(StopAsyncIteration):
            await generator.__anext__()

    run(scenario())
    assert calls == ["commit", "close"]


def test_fastapi_dependency_calls_rollback_then_close_on_failure(
    async_db: AsyncTestDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = install_recording_factory(monkeypatch)

    async def scenario() -> None:
        generator = get_async_session()
        session = await generator.__anext__()
        await session.execute(text("SELECT 1"))
        with pytest.raises(RuntimeError, match="handler failed"):
            await generator.athrow(RuntimeError("handler failed"))

    run(scenario())
    assert calls == ["rollback", "close"]


def test_fastapi_dependency_propagates_and_rolls_back_on_exception(
    async_db: AsyncTestDatabase,
) -> None:
    async def scenario() -> None:
        generator = get_async_session()
        session = await generator.__anext__()
        await session.execute(
            _INSERT_SERVICE,
            {"id": "99999999-9999-9999-9999-999999999999", "name": "gen", "environment": "prod"},
        )
        await session.flush()
        with pytest.raises(RuntimeError, match="generator boom"):
            await generator.athrow(RuntimeError("generator boom"))
        assert session.in_transaction() is False

    run(scenario())
    assert run(async_db.count_services()) == 0
    assert async_db.probe.checkedout() == 0


def test_fastapi_dependency_releases_on_cancellation(async_db: AsyncTestDatabase) -> None:
    """A cancelled request must not strand its connection.

    ASGI closes the dependency generator when the request is torn down, so this
    drives ``aclose()`` the way the framework does after cancelling the handler.
    """

    baseline = async_db.probe.checkedout()
    captured: dict[str, Any] = {}

    async def handler_like() -> None:
        generator = get_async_session()
        session = await generator.__anext__()
        captured["session"] = session
        captured["generator"] = generator
        await session.execute(text("SELECT 1"))
        captured["entered"] = True
        await asyncio.sleep(30)

    async def scenario() -> None:
        task = asyncio.create_task(handler_like())
        while not captured.get("entered"):
            await asyncio.sleep(0.01)
        assert async_db.probe.checkedout() == baseline + 1

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # The framework then finalises the dependency generator.
        await captured["generator"].aclose()

    run(scenario())

    assert captured["session"].in_transaction() is False
    assert async_db.probe.checkedout() == baseline


# --------------------------------------------------------------------------- #
# Event loop responsiveness
# --------------------------------------------------------------------------- #


def test_event_loop_is_not_blocked_by_async_database_work(async_db: AsyncTestDatabase) -> None:
    """Independent tasks keep running while an async work unit is in flight.

    The synchronous control below proves the measurement is meaningful: a
    synchronous database call cannot yield to the loop, while the async path
    does.
    """

    sync_engine = db_session.create_engine_from_url(
        async_db.url.replace("+aiosqlite", "+pysqlite")
    )

    async def scenario() -> tuple[int, int]:
        ticks = 0
        running = True

        async def ticker() -> None:
            nonlocal ticks
            while running:
                ticks += 1
                await asyncio.sleep(0)

        ticker_task = asyncio.create_task(ticker())
        try:
            await asyncio.sleep(0)  # let the ticker start
            before = ticks

            async with session_scope() as session:
                for _ in range(20):
                    await session.execute(text("SELECT 1"))
                await session.commit()

            async_delta = ticks - before

            # Control: a synchronous database call cannot yield to the loop.
            control_before = ticks
            with sync_engine.connect() as connection:
                for _ in range(20):
                    connection.execute(text("SELECT 1"))
            control_delta = ticks - control_before
        finally:
            running = False
            await ticker_task

        return async_delta, control_delta

    try:
        async_delta, control_delta = run(scenario())
    finally:
        sync_engine.dispose()

    assert async_delta > 0, "the event loop never ran during async database work"
    assert control_delta == 0, "the synchronous control unexpectedly yielded"


# --------------------------------------------------------------------------- #
# The synchronous API must keep working
# --------------------------------------------------------------------------- #


def test_synchronous_api_is_still_exported() -> None:
    assert isinstance(engine, sa.engine.Engine)
    assert callable(SessionLocal)
    assert inspect.isgeneratorfunction(get_db_session)
    assert callable(init_db)


def test_db_package_exports_the_async_interface() -> None:
    """``onestep_control_plane_api.db`` re-exports the async names."""

    import onestep_control_plane_api.db as db

    assert db.async_engine is db_session.get_async_engine()
    assert db.AsyncSessionLocal is db_session.get_async_session_factory()
    assert db.session_scope is session_scope
    assert db.get_async_session is get_async_session
    for name in ("async_engine", "AsyncSessionLocal", "session_scope", "get_async_session"):
        assert name in db.__all__, f"{name} missing from db.__all__"
        assert name in dir(db), f"{name} missing from dir(db)"


def test_importing_the_db_package_does_not_build_an_async_engine() -> None:
    """Import stays side-effect free: an unsupported URL still imports cleanly."""

    import subprocess
    import sys

    program = (
        "import onestep_control_plane_api.db as db\n"
        "from onestep_control_plane_api.db import session\n"
        "assert 'async_engine' not in vars(session), 'engine built at import time'\n"
        "assert db.SessionLocal is not None\n"
        "print('lazy-ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        env={**os.environ, "ONESTEP_CP_DATABASE_URL": "mysql+pymysql://u:p@h/db"},
        check=False,
    )
    # Importing must succeed even though the configured dialect is unsupported.
    assert result.returncode == 0, result.stderr
    assert "lazy-ok" in result.stdout


def test_synchronous_session_dependency_still_yields_a_session() -> None:
    dependency = get_db_session()
    session = next(dependency)
    try:
        assert isinstance(session, Session)
    finally:
        dependency.close()


def test_synchronous_engine_and_init_db_still_work(tmp_path: Path) -> None:
    """``init_db`` creates the schema on a synchronous engine, as before."""

    sync_engine = db_session.create_engine_from_url(f"sqlite:///{tmp_path / 'sync.db'}")
    try:
        init_db(sync_engine)
        assert sa.inspect(sync_engine).has_table("services")
    finally:
        sync_engine.dispose()


def test_synchronous_and_async_apis_coexist(async_db: AsyncTestDatabase) -> None:
    """Both stacks are usable side by side in one commit."""

    async def write_async() -> None:
        async with session_scope() as session:
            await session.execute(
                _INSERT_SERVICE,
                {
                    "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                    "name": "both",
                    "environment": "prod",
                },
            )

    run(write_async())

    sync_engine = db_session.create_engine_from_url(async_db.url.replace("+aiosqlite", "+pysqlite"))
    try:
        with Session(sync_engine) as session:
            assert session.scalar(text("SELECT count(*) FROM services")) == 1
    finally:
        sync_engine.dispose()


def test_sqlite_foreign_keys_are_enabled_on_the_async_engine(
    async_db: AsyncTestDatabase,
) -> None:
    """The async sqlite engine keeps the PRAGMA the sync engine already sets."""

    async def scenario() -> int:
        async with async_db.factory() as session:
            result = await session.execute(text("PRAGMA foreign_keys"))
            return int(result.scalar_one())

    assert run(scenario()) == 1


def test_async_session_factory_can_be_built_for_an_explicit_engine(tmp_path: Path) -> None:
    """The factory is usable directly, not only through the module singleton."""

    explicit_engine = create_async_engine_from_url(
        f"sqlite+aiosqlite:///{tmp_path / 'explicit.db'}"
    )
    try:
        factory = create_async_session_factory(explicit_engine)
        assert factory.kw["autoflush"] is False
        assert factory.kw["expire_on_commit"] is False

        async def scenario() -> int:
            async with factory() as session:
                result = await session.execute(text("SELECT 42"))
                return int(result.scalar_one())

        assert run(scenario()) == 42
    finally:
        run(explicit_engine.dispose())


def test_async_iterator_annotation_is_used_for_the_dependency() -> None:
    """Guards the public signature FastAPI inspects."""

    hints = typing.get_type_hints(get_async_session)
    assert hints["return"] == AsyncIterator[AsyncSession]


def test_async_engine_singleton_is_cached_after_first_use(async_db: AsyncTestDatabase) -> None:
    """The lazy accessor caches, so repeated use returns the same engine."""

    assert db_session.get_async_engine() is async_db.engine
    assert db_session.get_async_session_factory() is async_db.factory


# --------------------------------------------------------------------------- #
# #213: the synchronous engine factory is pool-instrumented
# --------------------------------------------------------------------------- #


def test_create_engine_from_url_instruments_the_engine_pool(tmp_path: Path) -> None:
    """Every engine from the factory must land in the pool-wait observability.

    The synchronous engine carries all REST routes, so its checkout waits were
    completely blind before #213. A checkout must be observable through the
    registry, under the factory's bounded pool name.
    """

    from onestep_control_plane_api.ops import observability as obs

    sync_engine = db_session.create_engine_from_url(f"sqlite:///{tmp_path / 'instr.db'}")
    try:
        with sync_engine.connect():
            pass  # one checkout through the instrumented pool.connect
    finally:
        sync_engine.dispose()

    histogram = next(
        sample
        for sample in obs.collect_prometheus_snapshot().histograms
        if sample.name == "onestep_control_plane_db_pool_wait_seconds"
        and dict(sample.labels)["name"] == db_session.SYNC_POOL_METRIC_NAME
    )
    assert histogram.count >= 1


def test_create_engine_from_url_instrumentation_is_idempotent(tmp_path: Path) -> None:
    """Re-instrumenting an already-instrumented engine must not double-count.

    The assertion uses a count *delta*: the ``("default", QueuePool)`` series is
    shared by every QueuePool engine instrumented under the default name in this
    process, so an absolute count would depend on test ordering.
    """

    from onestep_control_plane_api.ops import observability as obs

    def _pool_wait_count() -> int:
        return sum(
            sample.count
            for sample in obs.collect_prometheus_snapshot().histograms
            if sample.name == "onestep_control_plane_db_pool_wait_seconds"
            and dict(sample.labels)["name"] == db_session.SYNC_POOL_METRIC_NAME
        )

    sync_engine = db_session.create_engine_from_url(f"sqlite:///{tmp_path / 'idem.db'}")
    try:
        # The factory already instrumented it; a repeat call is a no-op.
        assert obs.instrument_engine(sync_engine, name=db_session.SYNC_POOL_METRIC_NAME) is True

        before = _pool_wait_count()
        with sync_engine.connect():
            pass
        with sync_engine.connect():
            pass
    finally:
        sync_engine.dispose()

    # Exactly one observation per checkout: no double-wrapping.
    assert _pool_wait_count() - before == 2


def test_ensure_sync_engine_instrumented_covers_the_import_time_engine() -> None:
    """The lifespan/exporter seam instruments the module-level engine.

    Since #216 the import-time ``engine`` is already instrumented by the
    factory itself (the ops import cycle that forced the #215 import-time
    skip gate is gone), so this seam is belt-and-braces. It stays as a single
    idempotent entry point for callers that hold no ``engine`` reference. The
    call never opens a connection: it only wraps ``pool.connect``.
    """

    assert db_session.ensure_sync_engine_instrumented() is True
    # Idempotent: a second call is a no-op that still reports success.
    assert db_session.ensure_sync_engine_instrumented() is True


def test_create_engine_from_url_survives_instrumentation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Instrumentation is observation only: its failure must not break engine creation.

    Covers both failure shapes the wiring must tolerate -- the observability
    import itself failing (e.g. a broken install) and ``instrument_engine``
    raising at call time.
    """

    from onestep_control_plane_api.ops import observability as obs

    # 1. The deferred import fails.
    monkeypatch.setitem(
        __import__("sys").modules, "onestep_control_plane_api.ops.observability", None
    )
    try:
        broken_engine = db_session.create_engine_from_url(f"sqlite:///{tmp_path / 'broken.db'}")
        assert broken_engine is not None
        with broken_engine.connect() as connection:
            assert connection.execute(text("SELECT 1")).scalar() == 1
        broken_engine.dispose()
    finally:
        monkeypatch.undo()

    # 2. instrument_engine raises at call time.
    def _boom(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("instrumentation exploded")

    monkeypatch.setattr(obs, "instrument_engine", _boom)
    try:
        raised_engine = db_session.create_engine_from_url(f"sqlite:///{tmp_path / 'raised.db'}")
        assert raised_engine is not None
        with raised_engine.connect() as connection:
            assert connection.execute(text("SELECT 1")).scalar() == 1
        raised_engine.dispose()
    finally:
        monkeypatch.undo()
