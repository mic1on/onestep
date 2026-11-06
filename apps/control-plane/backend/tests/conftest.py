import asyncio
import time
import uuid
from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from onestep_control_plane_api.auth.service import LocalAuthService
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db import session as db_session_module
from onestep_control_plane_api.db.base import Base
from onestep_control_plane_api.db.session import (
    SessionLocal,
    create_async_session_factory,
    get_db_session,
)
from onestep_control_plane_api.main import app
from onestep_control_plane_api.ops.readiness import (
    build_default_background_task_states,
    get_expected_migration_heads,
)
from onestep_control_plane_api.workers.notification_outbox_worker import (
    NOTIFICATION_OUTBOX_WORKER_NAME,
)
from onestep_control_plane_api.workers.notification_scanner import (
    NOTIFICATION_MISSED_START_SCANNER_NAME,
)
from onestep_control_plane_api.workers.retention_worker import RETENTION_WORKER_NAME
from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool, StaticPool

TEST_INGEST_TOKEN = "test-ingest-token"
TEST_WORKER_AGENT_REGISTRATION_TOKEN = "test-worker-registration-token"
TEST_HEAD_REVISION = get_expected_migration_heads()[0]


def shared_cache_database_name() -> str:
    """A unique name for one test's shared in-memory SQLite database."""

    return f"onestep_test_{uuid.uuid4().hex}"


def shared_cache_sync_url(name: str) -> str:
    """Sync URL for a shared in-memory database.

    ``uri=true`` MUST stay inside the URL. SQLAlchemy's sqlite dialect builds
    ``uri`` into the DBAPI connect arguments only from the URL query, and
    silently drops it when passed through ``connect_args``. Without it the
    whole ``file:...`` string is treated as a literal filename, which creates a
    stray file in the working directory and silently turns the database into a
    file-backed one that leaks state between runs.
    """

    return f"sqlite+pysqlite:///file:{name}?mode=memory&cache=shared&uri=true"


def shared_cache_async_url(name: str) -> str:
    """Async URL for the same shared in-memory database as the sync URL."""

    return f"sqlite+aiosqlite:///file:{name}?mode=memory&cache=shared&uri=true"


def assert_uri_query_is_honoured(engine) -> None:
    """Guard the silent failure mode described in ``shared_cache_sync_url``."""

    _, connect_args = engine.dialect.create_connect_args(engine.url)
    assert connect_args.get("uri") is True, (
        "the shared-cache URL lost its uri=true query parameter; SQLAlchemy's sqlite "
        "dialect drops `uri` passed via connect_args, and without it the shared "
        "in-memory database silently degrades to a stray file-backed one"
    )


def create_test_engine(name: str | None = None):
    """Sync engine over a shared in-memory database.

    ``StaticPool`` keeps exactly one connection open for the whole test, which
    is what keeps the shared in-memory database alive: SQLite destroys it once
    the last connection closes, and the async engine relies on the sync engine
    holding it open.
    """

    database_name = name or shared_cache_database_name()
    engine = create_engine(
        shared_cache_sync_url(database_name),
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    assert_uri_query_is_honoured(engine)
    return engine


def create_test_async_engine(name: str) -> AsyncEngine:
    """Async engine over the SAME shared in-memory database as the sync engine.

    ``NullPool`` is deliberate. The aiosqlite dialect defaults to ``StaticPool``
    for ``file:`` URLs, which would hold one aiosqlite connection (and its
    worker thread) across the separate event loops that each ``asyncio.run`` in
    a test creates. ``NullPool`` opens a connection per checkout inside the
    loop that uses it. The sync ``StaticPool`` connection keeps the shared
    in-memory database alive, because SQLite destroys it once the last
    connection to it closes.
    """

    engine = create_async_engine(
        shared_cache_async_url(name),
        future=True,
        poolclass=NullPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    assert_uri_query_is_honoured(engine.sync_engine)
    return engine


class AsyncTestHarness:
    """The async half of a test database: engine, factory and a checkout probe."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.engine: AsyncEngine = create_test_async_engine(name)
        self.factory: async_sessionmaker[AsyncSession] = create_async_session_factory(self.engine)
        self.open_sessions: list[AsyncSession] = []
        # Wrap the factory so every session this test opens is tracked.
        self.factory = async_sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
            class_=_TrackingSession,
        )
        # ``class_`` is instantiated with the sessionmaker's kwargs, so the
        # tracker list is injected through a bound factory instead.
        self.factory = _tracking_factory(self.engine, self.open_sessions)
        self.checkouts = 0
        self.checkins = 0
        event.listen(self.engine.sync_engine, "checkout", self._on_checkout)
        event.listen(self.engine.sync_engine, "checkin", self._on_checkin)

    def _on_checkout(self, *_args: object, **_kwargs: object) -> None:
        self.checkouts += 1

    def _on_checkin(self, *_args: object, **_kwargs: object) -> None:
        self.checkins += 1

    @property
    def leaked_connections(self) -> int:
        return self.checkouts - self.checkins

    def checkedout(self) -> int:
        """Live connections held by this engine.

        ``NullPool`` and ``StaticPool`` have no ``checkedout()``, so the
        engine-level checkout/checkin counters are the authoritative probe.
        """

        checkedout = getattr(self.engine.pool, "checkedout", None)
        if callable(checkedout):
            return int(checkedout())
        return self.leaked_connections

    def session(self) -> AsyncSession:
        return self.factory()

    def dispose(self) -> None:
        asyncio.run(self.engine.dispose())


def _tracking_factory(engine, open_sessions):
    """An async session factory whose sessions report themselves as open."""

    base = async_sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False, class_=AsyncSession
    )

    def factory() -> AsyncSession:
        session = base()
        original_close = session.close

        async def close() -> None:
            if session in open_sessions:
                open_sessions.remove(session)
            await original_close()

        session.close = close  # type: ignore[method-assign]
        open_sessions.append(session)
        return session

    return factory


class _TrackingSession(AsyncSession):
    """An ``AsyncSession`` that records whether it was closed.

    ``AsyncSession.close()`` returning a connection to the pool is what fixes
    the leak, but it is also what garbage collection eventually does for free —
    so a probe based only on pool counters can miss a missing ``close()``
    entirely. Counting live sessions directly is the discriminating probe.
    """

    def __init__(self, *args: object, open_sessions: list, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._open_sessions = open_sessions
        self._open_sessions.append(self)

    async def close(self) -> None:
        if self in self._open_sessions:
            self._open_sessions.remove(self)
        await super().close()


def override_db_session(engine) -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


def pytest_configure() -> None:
    import onestep_control_plane_api.db.models  # noqa: F401


@pytest.fixture()
def configure_ingest_tokens() -> Generator[None, None, None]:
    original_tokens = settings.ingest_tokens
    settings.ingest_tokens = [TEST_INGEST_TOKEN]
    yield
    settings.ingest_tokens = original_tokens


@pytest.fixture()
def test_database_name() -> str:
    """One shared in-memory database name per test."""

    return shared_cache_database_name()


@pytest.fixture()
def test_engine(test_database_name):
    engine = create_test_engine(test_database_name)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        connection.execute(text("DELETE FROM alembic_version"))
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:version_num)"),
            {"version_num": TEST_HEAD_REVISION},
        )
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def async_test_engine(test_database_name) -> Generator[AsyncTestHarness, None, None]:
    """Async engine over the SAME database as the sync ``test_engine``.

    Depends on ``test_database_name`` rather than ``test_engine`` so that a test
    can take either engine alone. When both are requested, both connect to the
    same shared in-memory database, so data seeded through the synchronous
    ``db_session`` is visible to the async session and vice versa.

    The sync engine is created here as the keep-alive owner when it was not
    requested: SQLite destroys a shared in-memory database once the last
    connection to it closes, and the async engine opens connections only for the
    duration of a query.
    """

    keeper = create_test_engine(test_database_name)
    Base.metadata.create_all(keeper)
    harness = AsyncTestHarness(test_database_name)
    try:
        yield harness
    finally:
        harness.dispose()
        Base.metadata.drop_all(keeper)
        keeper.dispose()


@pytest.fixture()
def async_session(async_test_engine) -> AsyncSession:
    """An ``AsyncSession`` bound to the same database as the sync fixtures.

    Not entered as a context manager: callers drive it directly and must close
    it. ``session_scope()`` is the preferred way to run a work unit.
    """

    return async_test_engine.session()


@pytest.fixture()
def async_db(test_database_name, monkeypatch) -> Generator[AsyncTestHarness, None, None]:
    """Point the process-wide async session factory at the test database.

    This is what makes ``session_scope()`` — and therefore the whole converted
    agent WS path — hit the same shared in-memory database as the synchronous
    ``db_session`` fixture. Without it, ``session_scope()`` would open a
    connection to the configured PostgreSQL URL and a WS test would fail with a
    connection error instead of exercising the conversion.

    The async factory is monkeypatched rather than the engine alone because
    ``session_scope`` resolves the factory once per call through
    ``get_async_session_factory()``, which caches into the module global.
    """

    keeper = create_test_engine(test_database_name)
    Base.metadata.create_all(keeper)
    harness = AsyncTestHarness(test_database_name)
    monkeypatch.setattr(db_session_module, "async_engine", harness.engine)
    monkeypatch.setattr(db_session_module, "AsyncSessionLocal", harness.factory)
    try:
        yield harness
    finally:
        harness.dispose()
        Base.metadata.drop_all(keeper)
        keeper.dispose()


_WS_DRAIN_TIMEOUT_S = 10.0
_WS_QUIESCENT_POLL_S = 0.005
_WS_QUIESCENT_ROUNDS = 3


def _ws_wait_until_quiescent(session, *, work_units_probe=None) -> None:
    """Block until the WS handler has no work left in flight.

    The converted agent WS handler does its database work in short async work
    units, so it suspends *between* receiving a frame and finishing that frame's
    writes. Starlette's ``WebSocketTestSession.__exit__`` sends the disconnect
    and cancels the handler's scope in the same breath, so a synchronous test
    that reads the database right after the ``with`` block can observe state
    from before the last frame was processed. The previous synchronous handler
    had no such suspension point, which is why these tests never had to care.

    There is no "handler finished" signal to await: ``_run`` awaits ``app(...)``
    and then sleeps forever, so its task future only completes on cancellation.
    The probe below instead samples the harness's connection accounting and
    returns once the connection count has been back at its idle value for
    several consecutive samples, which means no work unit is checked out.

    This is a test-harness concern only. A real client keeps the socket open,
    and the handler's work units are shielded so a real cancellation still
    commits or rolls back atomically rather than half-writing a frame.
    """

    if work_units_probe is None:
        return

    idle = work_units_probe()
    stable_rounds = 0
    deadline = time.monotonic() + _WS_DRAIN_TIMEOUT_S
    while stable_rounds < _WS_QUIESCENT_ROUNDS and time.monotonic() < deadline:
        time.sleep(_WS_QUIESCENT_POLL_S)
        if work_units_probe() == idle:
            stable_rounds += 1
        else:
            idle = work_units_probe()
            stable_rounds = 0


class _DrainingWebSocketSession:
    """A WS session proxy that waits for the server on ``__exit__``.

    Why a proxy rather than an assigned ``__exit__``: ``with`` resolves
    ``__exit__`` on the *type*, so assigning it on the instance is silently
    ignored. Attribute delegation is explicit because the real session carries
    portal state a generic ``__getattr__`` must not shadow.
    """

    def __init__(self, session) -> None:
        self._session = session
        self._probe = None

    @property
    def accepted_subprotocol(self):
        return self._session.accepted_subprotocol

    @property
    def extra_headers(self):
        return self._session.extra_headers

    def send(self, message):
        return self._session.send(message)

    def send_text(self, data):
        return self._session.send_text(data)

    def send_bytes(self, data):
        return self._session.send_bytes(data)

    def send_json(self, data, mode="text"):
        return self._session.send_json(data, mode=mode)

    def receive(self):
        return self._session.receive()

    def receive_text(self):
        return self._session.receive_text()

    def receive_bytes(self):
        return self._session.receive_bytes()

    def receive_json(self, mode="text"):
        return self._session.receive_json(mode=mode)

    def close(self, code=1000, reason=None):
        return self._session.close(code, reason)

    def __enter__(self):
        # The real ``__enter__`` starts the portal and the handler task.
        self._session.__enter__()
        return self

    def __exit__(self, *args):
        # Queue the disconnect behind the frames already sent, so the handler
        # processes them in order before it sees the close.
        try:
            self._session.close(1000)
        except Exception:  # pragma: no cover - socket already gone
            pass
        _ws_wait_until_quiescent(self._session, work_units_probe=self._probe)
        return self._session.__exit__(*args)


def _patch_ws_drain(test_client: TestClient, harness) -> TestClient:
    """Make ``test_client.websocket_connect`` return draining WS sessions."""

    original_connect = test_client.websocket_connect

    def _connect(url: str, subprotocols=None, **kwargs):
        session = original_connect(url, subprotocols=subprotocols, **kwargs)
        wrapper = _DrainingWebSocketSession(session)
        wrapper._probe = harness.checkedout
        return wrapper

    test_client.websocket_connect = _connect  # type: ignore[method-assign]
    return test_client


@pytest.fixture()
def client(
    test_engine,
    async_db,
    configure_ingest_tokens,
    tmp_path,
) -> Generator[TestClient, None, None]:
    original_username = settings.console_auth_username
    original_password = settings.console_auth_password
    original_worker_registration_tokens = settings.worker_agent_registration_tokens
    original_worker_package_storage_dir = settings.worker_package_storage_dir
    settings.console_auth_username = ""
    settings.console_auth_password = ""
    settings.worker_agent_registration_tokens = [TEST_WORKER_AGENT_REGISTRATION_TOKEN]
    settings.worker_package_storage_dir = str(tmp_path / "worker-packages")
    original_connector_secret = settings.connector_secret
    settings.connector_secret = "test-connector-secret"
    from onestep_control_plane_api.api.connector_service import _reset_cipher

    _reset_cipher()

    def _override() -> Generator[Session, None, None]:
        yield from override_db_session(test_engine)

    app.dependency_overrides[get_db_session] = _override
    app.state.session_factory = lambda: Session(test_engine)
    app.state.background_task_states = build_default_background_task_states()
    for state in app.state.background_task_states.values():
        now = datetime.now(UTC)
        state.mark_started(now)
        state.mark_leader("local", when=now)
        state.mark_success(now)
    app.state.background_task_refs = {
        NOTIFICATION_MISSED_START_SCANNER_NAME: object(),
        NOTIFICATION_OUTBOX_WORKER_NAME: object(),
        RETENTION_WORKER_NAME: object(),
    }
    with TestClient(app) as test_client:
        yield _patch_ws_drain(test_client, async_db)
    app.dependency_overrides.clear()
    app.state.session_factory = SessionLocal
    app.state.background_task_states = build_default_background_task_states()
    app.state.background_task_refs = {
        NOTIFICATION_MISSED_START_SCANNER_NAME: None,
        NOTIFICATION_OUTBOX_WORKER_NAME: None,
        RETENTION_WORKER_NAME: None,
    }
    settings.console_auth_username = original_username
    settings.console_auth_password = original_password
    settings.worker_agent_registration_tokens = original_worker_registration_tokens
    settings.worker_package_storage_dir = original_worker_package_storage_dir
    settings.connector_secret = original_connector_secret
    _reset_cipher()


@pytest.fixture()
def db_session(test_engine) -> Generator[Session, None, None]:
    with Session(test_engine) as session:
        yield session


@pytest.fixture()
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_INGEST_TOKEN}"}


@pytest.fixture()
def worker_agent_registration_token() -> str:
    return TEST_WORKER_AGENT_REGISTRATION_TOKEN


def create_local_user(
    db_session: Session,
    *,
    username: str,
    password: str,
    roles: list[str] | tuple[str, ...],
):
    return LocalAuthService(db_session).create_user(
        username=username,
        password=password,
        role_names=roles,
    )


def login_local_user(
    client: TestClient,
    *,
    username: str,
    password: str,
):
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response
