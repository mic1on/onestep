from fastapi.testclient import TestClient

from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.main import create_app


def test_query_api_returns_cors_headers_for_allowed_origin() -> None:
    original_origins = settings.cors_allow_origins
    try:
        settings.cors_allow_origins = ["http://ui.example.test:5173"]
        client = TestClient(create_app())

        response = client.get(
            "/healthz",
            headers={"Origin": "http://ui.example.test:5173"},
        )

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://ui.example.test:5173"
    finally:
        settings.cors_allow_origins = original_origins


def test_query_api_does_not_return_cors_headers_when_origins_not_configured() -> None:
    original_origins = settings.cors_allow_origins
    try:
        settings.cors_allow_origins = []
        client = TestClient(create_app())

        response = client.get(
            "/healthz",
            headers={"Origin": "http://ui.example.test:5173"},
        )

        assert response.status_code == 200
        assert "access-control-allow-origin" not in response.headers
    finally:
        settings.cors_allow_origins = original_origins
