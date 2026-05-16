from __future__ import annotations

from collections.abc import Generator

import pytest
from onestep_control_plane_api.auth.service import LocalAuthService
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db.models import NotificationChannel, Service


@pytest.fixture(autouse=True)
def restore_console_auth_settings() -> Generator[None, None, None]:
    original_username = settings.console_auth_username
    original_password = settings.console_auth_password
    try:
        yield
    finally:
        settings.console_auth_username = original_username
        settings.console_auth_password = original_password


def login_console_role(client, *, username: str, role: str) -> None:
    with client.app.state.session_factory() as session:
        LocalAuthService(session).create_user(
            username=username,
            password="secret-pass",
            role_names=[role],
        )
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "secret-pass"},
    )
    assert response.status_code == 200


def seed_service(db_session, *, name: str, environment: str) -> Service:
    service = Service(
        name=name,
        environment=environment,
        latest_deployment_version="1.0.0",
    )
    db_session.add(service)
    db_session.commit()
    db_session.refresh(service)
    return service


def test_notification_channels_crud_round_trip(client, db_session) -> None:
    login_console_role(client, username="operator", role="operator")
    seed_service(db_session, name="billing-worker", environment="prod")
    seed_service(db_session, name="invoice-worker", environment="staging")

    services_response = client.get("/api/v1/settings/notifications/services")
    assert services_response.status_code == 200
    assert services_response.json() == {
        "items": [
            {"name": "billing-worker", "environment": "prod"},
            {"name": "invoice-worker", "environment": "staging"},
        ]
    }

    create_response = client.post(
        "/api/v1/settings/notifications/channels",
        json={
            "name": "ops-feishu",
            "provider": "feishu",
            "webhook_url": "https://example.com/hook/feishu",
            "enabled": True,
            "service_scopes": [{"name": "billing-worker", "environment": "prod"}],
            "event_types": ["task_started", "task_failed", "task_missed_start"],
            "missed_start_grace_seconds": 600,
        },
    )
    assert create_response.status_code == 201
    created = create_response.json()
    assert created["name"] == "ops-feishu"
    assert created["provider"] == "feishu"
    assert "webhook_url" not in created
    assert created["webhook_url_masked"] != "https://example.com/hook/feishu"
    assert created["webhook_url_masked"].endswith("ishu")
    assert created["service_scopes"] == [{"name": "billing-worker", "environment": "prod"}]
    assert created["event_types"] == ["task_started", "task_failed", "task_missed_start"]
    assert created["missed_start_grace_seconds"] == 600

    list_response = client.get("/api/v1/settings/notifications/channels")
    assert list_response.status_code == 200
    assert list_response.json()["items"] == [created]

    channel_id = created["id"]
    patch_response = client.patch(
        f"/api/v1/settings/notifications/channels/{channel_id}",
        json={
            "provider": "wechat_work",
            "enabled": False,
            "service_scopes": [
                {"name": "billing-worker", "environment": "prod"},
                {"name": "invoice-worker", "environment": "staging"},
            ],
            "event_types": ["task_succeeded"],
            "missed_start_grace_seconds": 300,
        },
    )
    assert patch_response.status_code == 200
    updated = patch_response.json()
    assert updated["provider"] == "wechat_work"
    assert updated["enabled"] is False
    assert updated["service_scopes"] == [
        {"name": "billing-worker", "environment": "prod"},
        {"name": "invoice-worker", "environment": "staging"},
    ]
    assert updated["event_types"] == ["task_succeeded"]
    assert updated["missed_start_grace_seconds"] == 300

    test_response = client.post(
        f"/api/v1/settings/notifications/channels/{channel_id}/test",
        json={"message": "manual smoke check"},
    )
    assert test_response.status_code == 200
    assert test_response.json() == {
        "status": "accepted",
        "channel_id": channel_id,
        "provider": "wechat_work",
        "preview_text": "manual smoke check",
    }

    delete_response = client.delete(f"/api/v1/settings/notifications/channels/{channel_id}")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"status": "deleted"}

    assert db_session.query(NotificationChannel).count() == 0


def test_notification_channel_validation_errors(client) -> None:
    login_console_role(client, username="operator", role="operator")

    duplicate_scopes_response = client.post(
        "/api/v1/settings/notifications/channels",
        json={
            "name": "ops-feishu",
            "provider": "feishu",
            "webhook_url": "https://example.com/hook/feishu",
            "enabled": True,
            "service_scopes": [
                {"name": "billing-worker", "environment": "prod"},
                {"name": "billing-worker", "environment": "prod"},
            ],
            "event_types": ["task_started"],
        },
    )
    assert duplicate_scopes_response.status_code == 422

    invalid_grace_response = client.post(
        "/api/v1/settings/notifications/channels",
        json={
            "name": "ops-wecom",
            "provider": "wechat_work",
            "webhook_url": "https://example.com/hook/wecom",
            "enabled": True,
            "service_scopes": [],
            "event_types": ["task_failed"],
            "missed_start_grace_seconds": 120,
        },
    )
    assert invalid_grace_response.status_code == 422


def test_notification_channel_name_conflict_and_not_found(client) -> None:
    login_console_role(client, username="operator", role="operator")

    first = client.post(
        "/api/v1/settings/notifications/channels",
        json={
            "name": "ops-feishu",
            "provider": "feishu",
            "webhook_url": "https://example.com/hook/feishu",
            "enabled": True,
            "service_scopes": [],
            "event_types": ["task_started"],
        },
    )
    assert first.status_code == 201

    conflict = client.post(
        "/api/v1/settings/notifications/channels",
        json={
            "name": "ops-feishu",
            "provider": "wechat_work",
            "webhook_url": "https://example.com/hook/wecom",
            "enabled": True,
            "service_scopes": [],
            "event_types": ["task_failed"],
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "notification channel name already exists"

    missing_patch = client.patch(
        "/api/v1/settings/notifications/channels/00000000-0000-0000-0000-000000000001",
        json={"enabled": False},
    )
    assert missing_patch.status_code == 404

    missing_delete = client.delete(
        "/api/v1/settings/notifications/channels/00000000-0000-0000-0000-000000000001"
    )
    assert missing_delete.status_code == 404


def test_notification_routes_require_console_auth(client) -> None:
    # create a local user so auth is enforced instead of falling back to dev-open mode
    with client.app.state.session_factory() as session:
        LocalAuthService(session).create_user(
            username="admin",
            password="secret-pass",
            role_names=["admin"],
        )

    response = client.get("/api/v1/settings/notifications/channels")
    assert response.status_code == 401
    assert response.json()["detail"] == "authentication required"


def test_notification_viewer_can_read_channels_but_cannot_write(client, db_session) -> None:
    login_console_role(client, username="operator", role="operator")

    create_response = client.post(
        "/api/v1/settings/notifications/channels",
        json={
            "name": "ops-feishu",
            "provider": "feishu",
            "webhook_url": "https://example.com/hook/feishu",
            "enabled": True,
            "service_scopes": [],
            "event_types": ["task_failed"],
        },
    )
    assert create_response.status_code == 201
    channel_id = create_response.json()["id"]

    client.post("/api/v1/auth/logout")
    login_console_role(client, username="viewer", role="viewer")

    list_response = client.get("/api/v1/settings/notifications/channels")
    assert list_response.status_code == 200
    listed_channel = list_response.json()["items"][0]
    assert "webhook_url" not in listed_channel
    assert listed_channel["webhook_url_masked"] != "https://example.com/hook/feishu"

    create_forbidden = client.post(
        "/api/v1/settings/notifications/channels",
        json={
            "name": "viewer-channel",
            "provider": "feishu",
            "webhook_url": "https://example.com/hook/viewer",
            "enabled": True,
            "service_scopes": [],
            "event_types": ["task_failed"],
        },
    )
    assert create_forbidden.status_code == 403
    assert create_forbidden.json()["detail"] == "insufficient role for command execution"

    patch_forbidden = client.patch(
        f"/api/v1/settings/notifications/channels/{channel_id}",
        json={"enabled": False},
    )
    assert patch_forbidden.status_code == 403
    assert patch_forbidden.json()["detail"] == "insufficient role for command execution"

    test_forbidden = client.post(
        f"/api/v1/settings/notifications/channels/{channel_id}/test",
        json={"message": "viewer should not test"},
    )
    assert test_forbidden.status_code == 403
    assert test_forbidden.json()["detail"] == "insufficient role for command execution"

    delete_forbidden = client.delete(
        f"/api/v1/settings/notifications/channels/{channel_id}"
    )
    assert delete_forbidden.status_code == 403
    assert delete_forbidden.json()["detail"] == "insufficient role for command execution"

    assert db_session.query(NotificationChannel).count() == 1
