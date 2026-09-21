"""Database engines and sessions for the control plane.

Two APIs live side by side in this module.

Synchronous (legacy, still supported)
    ``engine``, ``SessionLocal``, ``get_db_session`` and ``init_db``. These keep
    serving the existing HTTP routers and background workers unchanged.

Native async (the supported path for new database work)
    ``async_engine``, ``AsyncSessionLocal``, ``get_async_session`` and
    ``session_scope``. They are backed by SQLAlchemy's asyncio extension, which
    bridges through greenlet internally. No synchronous database call is wrapped
    in ``asyncio.to_thread``; that is explicitly not the mechanism used here.

Database support range
    ===============================================  ========================================
    URL                                              Status
    ===============================================  ========================================
    ``postgresql+psycopg://``                        Supported. Production path (psycopg3 async).
    ``postgresql://``, ``postgresql+psycopg2://``    Mapped to ``postgresql+psycopg``.
    ``sqlite+aiosqlite://``                          Supported. Test path.
    ``sqlite://``, ``sqlite+pysqlite://``            Mapped to ``sqlite+aiosqlite``.
    anything else (``mysql+*``, ``mariadb+*``, ...)  Not supported. Raises
                                                     ``UnsupportedAsyncDatabaseError``.
    ===============================================  ========================================

    MySQL is not supported because no async MySQL driver is a project
    dependency. ``aiosqlite`` ships in the ``test`` and ``dev`` extras only, so
    the async engine works in development and CI while the production image
    (``uv sync --frozen --no-dev``) keeps just the PostgreSQL async driver. An
    unsupported dialect raises instead of silently degrading to the synchronous
    engine.

    ``async_engine`` and ``AsyncSessionLocal`` are created on first use rather
    than at import time. Importing this module therefore never requires an async
    driver, so a deployment that configures a URL outside the async support
    range can keep running the synchronous stack; it fails with an explicit
    error only when async access is actually requested. Once created, both are
    cached as ordinary module attributes and can be monkeypatched like any
    other module global.

Transaction convention
    One ``AsyncSession`` per database work unit, managed by ``session_scope``:

    1. Exactly one session per work unit. An ``AsyncSession`` is not
       concurrency-safe, so it is never shared across concurrent tasks.
    2. Transactions stay short. Do not hold a transaction open across a network
       send/receive or any ``await`` that is not part of the database work unit.
    3. Commit on success, roll back on exception, always release the session.
       ``AsyncSession.close()`` also returns the pooled connection when the
       enclosing task is cancelled.
    4. Return plain data (dataclasses, dicts, tuples, primitives) from a work
       unit rather than ORM instances whose attribute access would trigger
       implicit IO after the transaction closed.
    5. Transaction ownership stays with the work unit. Lower-level helpers must
       not commit on their own; they accept the caller's session instead.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db.base import Base

logger = logging.getLogger("onestep_control_plane_api.db.session")

#: Async dialects this project supports. PostgreSQL through psycopg3 is the
#: production path; sqlite+aiosqlite is the test path.
SUPPORTED_ASYNC_DIALECTS: frozenset[str] = frozenset({"postgresql+psycopg", "sqlite+aiosqlite"})

#: Synchronous URL spellings used elsewhere in the project, mapped to the async
#: driver this project depends on. These are upgrades to async, never a fallback
#: to synchronous access.
_SYNC_DIALECT_TO_ASYNC: dict[str, str] = {
    "postgresql": "postgresql+psycopg",
    "postgresql+psycopg2": "postgresql+psycopg",
    "sqlite": "sqlite+aiosqlite",
    "sqlite+pysqlite": "sqlite+aiosqlite",
}

_LAZY_ASYNC_NAMES = ("async_engine", "AsyncSessionLocal")

#: Metric label for the process-wide synchronous engine's pool. A pool name is
#: a Prometheus label, so it must be a small constant (see the bounded-label
#: contract in :mod:`onestep_control_plane_api.ops.observability`).
SYNC_POOL_METRIC_NAME = "default"

if TYPE_CHECKING:
    # Resolved on first use by ``get_async_engine`` / ``get_async_session_factory``
    # and exposed through the module-level ``__getattr__`` below. These
    # declarations keep both names statically typed for callers and checkers.
    async_engine: AsyncEngine
    AsyncSessionLocal: async_sessionmaker[AsyncSession]


class UnsupportedAsyncDatabaseError(RuntimeError):
    """The configured database URL has no supported asynchronous driver."""


def resolve_async_database_url(database_url: str) -> str:
    """Return the async driver URL for ``database_url``.

    Supported async dialects pass through unchanged, the project's synchronous
    URL spellings are mapped to their async driver, and anything else raises
    :class:`UnsupportedAsyncDatabaseError` so that an unsupported database can
    never silently fall back to synchronous access.
    """

    try:
        url = sa.engine.make_url(database_url)
    except sa.exc.ArgumentError as exc:
        raise UnsupportedAsyncDatabaseError(
            f"Could not parse {database_url!r} as a SQLAlchemy database URL: {exc}"
        ) from exc

    if url.drivername in SUPPORTED_ASYNC_DIALECTS:
        return url.render_as_string(hide_password=False)

    mapped_drivername = _SYNC_DIALECT_TO_ASYNC.get(url.drivername)
    if mapped_drivername is not None:
        return url.set(drivername=mapped_drivername).render_as_string(hide_password=False)

    supported = ", ".join(sorted(SUPPORTED_ASYNC_DIALECTS))
    raise UnsupportedAsyncDatabaseError(
        f"Unsupported async database dialect {url.drivername!r}. Supported async dialects: "
        f"{supported} (PostgreSQL through psycopg3 async for production, sqlite+aiosqlite for "
        f"tests). MySQL is not supported because no async MySQL driver is a project dependency. "
        f"The async engine never falls back to synchronous database access."
    )


def _instrument_sync_engine(engine: Engine) -> bool:
    """Attach pool-checkout instrumentation to a synchronous engine.

    Idempotent, and deliberately tolerant: instrumentation is observation only,
    so a failure here must never block engine creation or application startup.
    Mirrors the defensive style of the scanner's ``_instrument_async_engine``.

    The :mod:`onestep_control_plane_api.ops.observability` import is deferred to
    call time. ``ops.observability`` itself never imports ``db.session`` (zero
    dependency cycles by contract), but importing it while *this* module is
    still mid-import can trip the pre-existing ``ops.__init__`` ->
    ``ops.readiness`` -> workers -> ``api.__init__`` -> ``api.routers.health``
    -> ``ops.readiness`` cycle (documented in the latency diagnostics runbook)
    and leave broken residue in ``sys.modules``. The
    :data:`_MODULE_IMPORT_COMPLETE` gate therefore skips the import-time
    self-call; the module-level ``engine`` is instrumented later by the app
    lifespan and the ``/metrics`` exporter, which import observability safely
    after the API package has initialized. Any *later* ``create_engine_from_url``
    call (tests, scripts) runs after import completed and is instrumented
    immediately.
    """

    if not globals().get("_MODULE_IMPORT_COMPLETE", False):
        return False

    try:
        from onestep_control_plane_api.ops.observability import instrument_engine

        return instrument_engine(engine, name=SYNC_POOL_METRIC_NAME)
    except Exception:
        logger.warning(
            "could not attach pool instrumentation to the synchronous engine",
            exc_info=True,
        )
        return False


def ensure_sync_engine_instrumented() -> bool:
    """Idempotently instrument the module-level synchronous engine.

    Called from the app lifespan and the ``/metrics`` exporter, which import
    :mod:`onestep_control_plane_api.ops.observability` safely after the API
    package has initialized. The import-time engine cannot instrument itself
    (see :func:`_instrument_sync_engine` for why), so this is the production
    path that closes the gap.
    """

    return _instrument_sync_engine(engine)


def create_engine_from_url(database_url: str) -> Engine:
    is_sqlite = database_url.startswith("sqlite")
    connect_args = {"check_same_thread": False} if is_sqlite else {}
    engine = sa.create_engine(
        database_url,
        connect_args=connect_args,
        future=True,
        pool_pre_ping=not is_sqlite,
    )

    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection: object, _: object) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    _instrument_sync_engine(engine)

    return engine


def create_async_engine_from_url(database_url: str) -> AsyncEngine:
    """Create an :class:`AsyncEngine` for a supported async database URL."""

    async_url = resolve_async_database_url(database_url)
    is_sqlite = async_url.startswith("sqlite")

    try:
        created = create_async_engine(async_url, future=True, pool_pre_ping=not is_sqlite)
    except ModuleNotFoundError as exc:
        raise UnsupportedAsyncDatabaseError(
            f"The async database driver for {async_url!r} is not installed: {exc}. "
            f"PostgreSQL async needs the 'psycopg[binary]' dependency; sqlite+aiosqlite needs "
            f"the 'aiosqlite' dependency, which ships in the 'test' and 'dev' extras."
        ) from exc

    if is_sqlite:

        @event.listens_for(created.sync_engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection: object, _: object) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return created


def create_async_session_factory(bind: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build the project's async session factory for ``bind``.

    ``autoflush`` is disabled so that flushing is an explicit decision inside a
    work unit, and ``expire_on_commit`` is disabled so committed values stay
    readable without a second round trip after the transaction closed.
    """

    return async_sessionmaker(
        bind=bind,
        autoflush=False,
        expire_on_commit=False,
        class_=AsyncSession,
    )


#: True once this module finished importing. ``engine = create_engine_from_url(...)
#: `` runs *during* the import, and letting the factory attach pool
#: instrumentation at that moment would import
#: :mod:`onestep_control_plane_api.ops.observability` mid-import -- which can
#: trip the pre-existing ``ops.__init__`` -> ``ops.readiness`` -> workers ->
#: ``api.__init__`` -> ``api.routers.health`` -> ``ops.readiness`` import cycle
#: (documented in the latency diagnostics runbook) and leave broken module
#: residue in ``sys.modules`` for every later import to stumble over. The
#: import-time engine is instrumented later instead: by the app lifespan and
#: the ``/metrics`` exporter, both of which import observability safely after
#: the API package has fully initialized.
_MODULE_IMPORT_COMPLETE = False

engine = create_engine_from_url(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)

_MODULE_IMPORT_COMPLETE = True


def get_async_engine() -> AsyncEngine:
    """Return the process-wide :class:`AsyncEngine`, creating it on first use.

    Raises :class:`UnsupportedAsyncDatabaseError` when the configured URL has no
    supported async driver. The result is cached as the module attribute
    ``async_engine`` so later lookups and monkeypatching behave normally.
    """

    cached = globals().get("async_engine")
    if cached is not None:
        return cached  # type: ignore[no-any-return]

    created = create_async_engine_from_url(settings.database_url)
    globals()["async_engine"] = created
    return created


def get_async_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide async session factory, creating it on first use."""

    cached = globals().get("AsyncSessionLocal")
    if cached is not None:
        return cached  # type: ignore[no-any-return]

    created = create_async_session_factory(get_async_engine())
    globals()["AsyncSessionLocal"] = created
    return created


def __getattr__(name: str) -> object:
    if name == "async_engine":
        return get_async_engine()
    if name == "AsyncSessionLocal":
        return get_async_session_factory()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_ASYNC_NAMES})


def init_db(bind: Engine | None = None) -> None:
    import onestep_control_plane_api.db.models  # noqa: F401

    Base.metadata.create_all(bind or engine)


def get_db_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Run one database work unit on a single short-lived ``AsyncSession``.

    Commits when the block finishes, rolls back when it raises, and always
    closes the session so the pooled connection is released even when the
    enclosing task is cancelled. See the module docstring for the full
    transaction convention.
    """

    session = get_async_session_factory()()
    try:
        yield session
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    finally:
        await session.close()


async def get_async_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency exposing one request-scoped async session.

    Mirrors :func:`session_scope`: commit on success, rollback on exception and
    always close. Handlers that need several statements in one atomic work unit
    should use :func:`session_scope` directly.
    """

    session = get_async_session_factory()()
    try:
        yield session
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    finally:
        await session.close()
