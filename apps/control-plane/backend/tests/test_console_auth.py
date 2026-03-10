from __future__ import annotations

from collections.abc import Generator

import pytest

from onestep_control_plane_api.core.settings import settings


@pytest.fixture(autouse=True)
def restore_console_auth_settings() -> Generator[None, None, None]:
    original_username = settings.console_auth_username
    original_password = settings.console_auth_password
    original_ttl = settings.console_auth_session_ttl_s
    try:
        yield
    finally:
        settings.console_auth_username = original_username
        settings.console_auth_password = original_password
        settings.console_auth_session_ttl_s = original_ttl


def test_auth_session_reports_disabled_when_console_auth_not_configured(client) -> None:
    response = client.get("/api/v1/auth/session")

    assert response.status_code == 200
    assert response.json() == {
        "auth_configured": False,
        "authenticated": False,
        "username": None,
    }


def test_console_login_session_and_logout_round_trip(client) -> None:
    settings.console_auth_username = "admin"
    settings.console_auth_password = "secret-pass"

    unauthenticated = client.get("/api/v1/auth/session")
    assert unauthenticated.status_code == 200
    assert unauthenticated.json() == {
        "auth_configured": True,
        "authenticated": False,
        "username": None,
    }

    invalid_login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "wrong-pass"},
    )
    assert invalid_login.status_code == 401

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "secret-pass"},
    )
    assert login.status_code == 200
    assert "onestep_cp_console_session=" in login.headers["set-cookie"]
    assert login.json() == {
        "auth_configured": True,
        "authenticated": True,
        "username": "admin",
    }

    authenticated = client.get("/api/v1/auth/session")
    assert authenticated.status_code == 200
    assert authenticated.json() == {
        "auth_configured": True,
        "authenticated": True,
        "username": "admin",
    }

    logout = client.post("/api/v1/auth/logout")
    assert logout.status_code == 200
    assert logout.json() == {
        "auth_configured": True,
        "authenticated": False,
        "username": None,
    }

    after_logout = client.get("/api/v1/auth/session")
    assert after_logout.status_code == 200
    assert after_logout.json() == {
        "auth_configured": True,
        "authenticated": False,
        "username": None,
    }


def test_query_and_docs_require_console_auth_when_configured(client) -> None:
    settings.console_auth_username = "admin"
    settings.console_auth_password = "secret-pass"

    services = client.get("/api/v1/services", params={"environment": "prod"})
    assert services.status_code == 401
    assert services.json()["detail"] == "authentication required"

    docs = client.get("/docs")
    assert docs.status_code == 401

    openapi = client.get("/openapi.json")
    assert openapi.status_code == 401

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "secret-pass"},
    )
    assert login.status_code == 200

    authenticated_services = client.get("/api/v1/services", params={"environment": "prod"})
    assert authenticated_services.status_code == 200
    assert authenticated_services.json()["items"] == []

    authenticated_docs = client.get("/docs")
    assert authenticated_docs.status_code == 200
    assert "Swagger UI" in authenticated_docs.text

    authenticated_openapi = client.get("/openapi.json")
    assert authenticated_openapi.status_code == 200
    assert "/api/v1/services" in authenticated_openapi.json()["paths"]
