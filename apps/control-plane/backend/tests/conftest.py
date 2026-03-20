from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db.base import Base
from onestep_control_plane_api.db.session import SessionLocal, get_db_session
from onestep_control_plane_api.main import app
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

TEST_DATABASE_URL = "sqlite+pysqlite:///:memory:"
TEST_INGEST_TOKEN = "test-ingest-token"


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
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def client(test_engine, configure_ingest_tokens) -> Generator[TestClient, None, None]:
    def _override() -> Generator[Session, None, None]:
        yield from override_db_session(test_engine)

    app.dependency_overrides[get_db_session] = _override
    app.state.session_factory = lambda: Session(test_engine)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    app.state.session_factory = SessionLocal


@pytest.fixture()
def db_session(test_engine) -> Generator[Session, None, None]:
    with Session(test_engine) as session:
        yield session


@pytest.fixture()
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_INGEST_TOKEN}"}
