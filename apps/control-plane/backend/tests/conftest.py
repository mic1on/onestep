from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from onestep_control_plane_api.auth.service import LocalAuthService
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db.base import Base
from onestep_control_plane_api.db.session import SessionLocal, get_db_session
from onestep_control_plane_api.main import app
from onestep_control_plane_api.ops.readiness import (
    build_default_background_task_states,
    get_expected_migration_heads,
)
from onestep_control_plane_api.workers.notification_scanner import (
    NOTIFICATION_MISSED_START_SCANNER_NAME,
)
from onestep_control_plane_api.workers.retention_worker import RETENTION_WORKER_NAME
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

TEST_DATABASE_URL = "sqlite+pysqlite:///:memory:"
TEST_INGEST_TOKEN = "test-ingest-token"
TEST_WORKER_AGENT_REGISTRATION_TOKEN = "test-worker-registration-token"
TEST_HEAD_REVISION = get_expected_migration_heads()[0]


def create_test_engine():
    return create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


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
def test_engine():
    engine = create_test_engine()
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS alembic_version "
                "(version_num VARCHAR(32) NOT NULL)"
            )
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
def client(test_engine, configure_ingest_tokens, tmp_path) -> Generator[TestClient, None, None]:
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
        RETENTION_WORKER_NAME: object(),
    }
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    app.state.session_factory = SessionLocal
    app.state.background_task_states = build_default_background_task_states()
    app.state.background_task_refs = {
        NOTIFICATION_MISSED_START_SCANNER_NAME: None,
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
