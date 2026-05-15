from __future__ import annotations

from collections.abc import Generator

import pytest
from onestep_control_plane_api.auth.service import LocalAuthService
from onestep_control_plane_api.core.settings import settings


@pytest.fixture(autouse=True)
def restore_console_auth_settings() -> Generator[None, None, None]:
    original_username = settings.console_auth_username
    original_password = settings.console_auth_password
    original_ttl = settings.console_auth_session_ttl_s
    original_env = settings.app_env
    try:
        yield
    finally:
        settings.console_auth_username = original_username
        settings.console_auth_password = original_password
        settings.console_auth_session_ttl_s = original_ttl
        settings.app_env = original_env


def test_auth_session_reports_disabled_when_console_auth_not_configured(client) -> None:
    response = client.get("/api/v1/auth/session")

    assert response.status_code == 200
    assert response.json() == {
        "auth_configured": False,
        "bootstrap_required": False,
        "authenticated": False,
        "username": None,
        "role": None,
        "roles": [],
    }


def test_local_console_login_session_and_logout_round_trip(client, db_session) -> None:
    LocalAuthService(db_session).create_user(
        username="admin",
        password="secret-pass",
        role_names=["admin"],
    )

    unauthenticated = client.get("/api/v1/auth/session")
    assert unauthenticated.status_code == 200
    assert unauthenticated.json() == {
        "auth_configured": True,
        "bootstrap_required": False,
        "authenticated": False,
        "username": None,
        "role": None,
        "roles": [],
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
        "bootstrap_required": False,
        "authenticated": True,
        "username": "admin",
        "role": "admin",
        "roles": ["admin"],
    }

    authenticated = client.get("/api/v1/auth/session")
    assert authenticated.status_code == 200
    assert authenticated.json() == {
        "auth_configured": True,
        "bootstrap_required": False,
        "authenticated": True,
        "username": "admin",
        "role": "admin",
        "roles": ["admin"],
    }

    logout = client.post("/api/v1/auth/logout")
    assert logout.status_code == 200
    assert logout.json() == {
        "auth_configured": True,
        "bootstrap_required": False,
        "authenticated": False,
        "username": None,
        "role": None,
        "roles": [],
    }

    after_logout = client.get("/api/v1/auth/session")
    assert after_logout.status_code == 200
    assert after_logout.json() == {
        "auth_configured": True,
        "bootstrap_required": False,
        "authenticated": False,
        "username": None,
        "role": None,
        "roles": [],
    }


def test_auth_session_requires_bootstrap_in_non_dev_without_local_users(client) -> None:
    settings.app_env = "prod"

    response = client.get("/api/v1/auth/session")

    assert response.status_code == 200
    assert response.json() == {
        "auth_configured": False,
        "bootstrap_required": True,
        "authenticated": False,
        "username": None,
        "role": None,
        "roles": [],
    }

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "secret-pass"},
    )
    assert login.status_code == 503
    assert login.json()["detail"] == "local admin bootstrap is required before console login"


def test_query_and_docs_require_console_auth_when_local_auth_exists(client, db_session) -> None:
    LocalAuthService(db_session).create_user(
        username="viewer",
        password="secret-pass",
        role_names=["viewer"],
    )

    services = client.get("/api/v1/services", params={"environment": "prod"})
    assert services.status_code == 401
    assert services.json()["detail"] == "authentication required"

    docs = client.get("/docs")
    assert docs.status_code == 401

    openapi = client.get("/openapi.json")
    assert openapi.status_code == 401

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "viewer", "password": "secret-pass"},
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


def test_revoked_local_session_is_rejected(client, db_session) -> None:
    service = LocalAuthService(db_session)
    service.create_user(username="operator", password="secret-pass", role_names=["operator"])

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "operator", "password": "secret-pass"},
    )
    assert login.status_code == 200

    cookie_value = login.cookies.get("onestep_cp_console_session")
    assert cookie_value is not None
    assert service.revoke_console_session(cookie_value) is True

    response = client.get("/api/v1/services", params={"environment": "prod"})
    assert response.status_code == 401
    assert response.json()["detail"] == "authentication required"
