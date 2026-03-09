import pytest
from fastapi.testclient import TestClient
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def restore_ingest_tokens() -> None:
    original_tokens = settings.ingest_tokens
    try:
        yield
    finally:
        settings.ingest_tokens = original_tokens


def test_healthz() -> None:
    settings.ingest_tokens = ["dev-token"]
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "ingestion_auth_configured": True,
    }


def test_readyz() -> None:
    settings.ingest_tokens = []
    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["ingestion_auth_configured"] is False
