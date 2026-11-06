from __future__ import annotations

from collections.abc import Generator
from types import SimpleNamespace
from uuid import UUID, uuid4

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
            "event_types": [
                "task_started",
                "task_failed",
                "task_missed_start",
                "instance_offline",
            ],
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
    assert created["event_types"] == [
        "task_started",
        "task_failed",
        "task_missed_start",
        "instance_offline",
    ]
    assert created["missed_start_grace_seconds"] == 600

    list_response = client.get("/api/v1/settings/notifications/channels")
    assert list_response.status_code == 200
    assert list_response.json()["items"] == [created]

    channel_id = created["id"]
    enabled_patch_response = client.patch(
        f"/api/v1/settings/notifications/channels/{channel_id}/enabled",
        json={"enabled": False},
    )
    assert enabled_patch_response.status_code == 200
    enabled_patched = enabled_patch_response.json()
    assert enabled_patched["enabled"] is False
    assert enabled_patched["name"] == "ops-feishu"
    assert enabled_patched["provider"] == "feishu"
    assert enabled_patched["event_types"] == [
        "task_started",
        "task_failed",
        "task_missed_start",
        "instance_offline",
    ]

    enabled_patch_extra_field_response = client.patch(
        f"/api/v1/settings/notifications/channels/{channel_id}/enabled",
        json={"enabled": True, "name": "should-not-be-accepted"},
    )
    assert enabled_patch_extra_field_response.status_code == 422

    patch_response = client.patch(
        f"/api/v1/settings/notifications/channels/{channel_id}",
        json={
            "provider": "wechat_work",
            "enabled": True,
            "service_scopes": [
                {"name": "billing-worker", "environment": "prod"},
                {"name": "invoice-worker", "environment": "staging"},
            ],
            "event_types": ["task_succeeded", "instance_online"],
            "missed_start_grace_seconds": 300,
        },
    )
    assert patch_response.status_code == 200
    updated = patch_response.json()
    assert updated["provider"] == "wechat_work"
    assert updated["enabled"] is True
    assert updated["service_scopes"] == [
        {"name": "billing-worker", "environment": "prod"},
        {"name": "invoice-worker", "environment": "staging"},
    ]
    assert updated["event_types"] == ["task_succeeded", "instance_online"]
    assert updated["missed_start_grace_seconds"] == 300

    test_response = client.post(
        f"/api/v1/settings/notifications/channels/{channel_id}/test",
        json={"message": "manual smoke check"},
    )
    assert test_response.status_code == 200
    body = test_response.json()
    assert body["status"] == "accepted"
    assert body["channel_id"] == channel_id
    assert body["provider"] == "wechat_work"
    assert body["preview_text"] == "manual smoke check"
    # The test performs a real request now, so the response reports the real
    # outcome. This channel points at an unreachable URL, which is the point: the
    # API previously returned "accepted" without sending anything, so a broken
    # channel looked healthy until an incident.
    assert body["delivered"] is False
    assert body["error_message"] is not None

    delete_response = client.delete(f"/api/v1/settings/notifications/channels/{channel_id}")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"status": "deleted"}

    assert db_session.query(NotificationChannel).count() == 0


def test_custom_notification_channel_crud_round_trip(client, db_session) -> None:
    login_console_role(client, username="operator-custom", role="operator")
    seed_service(db_session, name="billing-worker", environment="prod")

    create_response = client.post(
        "/api/v1/settings/notifications/channels",
        json={
            "name": "ops-custom",
            "provider": "custom",
            "webhook_url": "https://example.com/notify",
            "enabled": True,
            "service_scopes": [{"name": "billing-worker", "environment": "prod"}],
            "event_types": ["task_failed", "instance_offline"],
            "missed_start_grace_seconds": 300,
            "custom_config": {
                "method": "POST",
                "query_params": [
                    {"key": "service", "value": "{{ service_name }}"},
                    {"key": "event", "value": "{{ event_type }}"},
                ],
                "body_params": [
                    {"key": "task", "value": "{{ task_name }}"},
                    {"key": "detail_url", "value": "{{ console_url }}"},
                ],
            },
        },
    )

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["provider"] == "custom"
    assert created["custom_config"] == {
        "method": "POST",
        "query_params": [
            {"key": "service", "value": "{{ service_name }}"},
            {"key": "event", "value": "{{ event_type }}"},
        ],
        "body_params": [
            {"key": "task", "value": "{{ task_name }}"},
            {"key": "detail_url", "value": "{{ console_url }}"},
        ],
    }

    list_response = client.get("/api/v1/settings/notifications/channels")
    assert list_response.status_code == 200
    assert list_response.json()["items"] == [created]

    channel_id = created["id"]
    patch_response = client.patch(
        f"/api/v1/settings/notifications/channels/{channel_id}",
        json={
            "custom_config": {
                "method": "GET",
                "query_params": [{"key": "service", "value": "{{ service_name }}"}],
                "body_params": [],
            }
        },
    )

    assert patch_response.status_code == 200
    assert patch_response.json()["custom_config"] == {
        "method": "GET",
        "query_params": [{"key": "service", "value": "{{ service_name }}"}],
        "body_params": [],
    }


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


def test_custom_notification_channel_validation_errors(client) -> None:
    login_console_role(client, username="operator-custom-validation", role="operator")

    missing_config = client.post(
        "/api/v1/settings/notifications/channels",
        json={
            "name": "custom-missing-config",
            "provider": "custom",
            "webhook_url": "https://example.com/notify",
            "enabled": True,
            "service_scopes": [],
            "event_types": ["task_failed"],
        },
    )
    assert missing_config.status_code == 422

    feishu_with_config = client.post(
        "/api/v1/settings/notifications/channels",
        json={
            "name": "feishu-with-config",
            "provider": "feishu",
            "webhook_url": "https://example.com/notify",
            "enabled": True,
            "service_scopes": [],
            "event_types": ["task_failed"],
            "custom_config": {"method": "GET", "query_params": [], "body_params": []},
        },
    )
    assert feishu_with_config.status_code == 422

    duplicate_query_keys = client.post(
        "/api/v1/settings/notifications/channels",
        json={
            "name": "custom-duplicate-query",
            "provider": "custom",
            "webhook_url": "https://example.com/notify",
            "enabled": True,
            "service_scopes": [],
            "event_types": ["task_failed"],
            "custom_config": {
                "method": "GET",
                "query_params": [
                    {"key": "service", "value": "{{ service_name }}"},
                    {"key": "service", "value": "{{ event_type }}"},
                ],
                "body_params": [],
            },
        },
    )
    assert duplicate_query_keys.status_code == 422

    unknown_variable = client.post(
        "/api/v1/settings/notifications/channels",
        json={
            "name": "custom-unknown-variable",
            "provider": "custom",
            "webhook_url": "https://example.com/notify",
            "enabled": True,
            "service_scopes": [],
            "event_types": ["task_failed"],
            "custom_config": {
                "method": "POST",
                "query_params": [{"key": "service", "value": "{{ missing_field }}"}],
                "body_params": [],
            },
        },
    )
    assert unknown_variable.status_code == 422

    get_with_body = client.post(
        "/api/v1/settings/notifications/channels",
        json={
            "name": "custom-get-body",
            "provider": "custom",
            "webhook_url": "https://example.com/notify",
            "enabled": True,
            "service_scopes": [],
            "event_types": ["task_failed"],
            "custom_config": {
                "method": "GET",
                "query_params": [],
                "body_params": [{"key": "event", "value": "{{ event_type }}"}],
            },
        },
    )
    assert get_with_body.status_code == 422


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

    missing_enabled_patch = client.patch(
        "/api/v1/settings/notifications/channels/00000000-0000-0000-0000-000000000001/enabled",
        json={"enabled": False},
    )
    assert missing_enabled_patch.status_code == 404

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

    enabled_patch_forbidden = client.patch(
        f"/api/v1/settings/notifications/channels/{channel_id}/enabled",
        json={"enabled": False},
    )
    assert enabled_patch_forbidden.status_code == 403
    assert enabled_patch_forbidden.json()["detail"] == "insufficient role for command execution"

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


# --------------------------------------------------------------------------------------
# The test button must actually send, and delivery history must be readable
# --------------------------------------------------------------------------------------


def _create_channel(client, *, provider: str = "feishu", webhook_url: str) -> str:
    response = client.post(
        "/api/v1/settings/notifications/channels",
        json={
            "name": f"probe-{uuid4().hex[:8]}",
            "provider": provider,
            "webhook_url": webhook_url,
            "enabled": True,
            "service_scopes": [],
            "event_types": ["task_failed"],
            "missed_start_grace_seconds": 300,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


class _FakeWebhookClient:
    """Stand-in for ``httpx.Client`` scoped to the notification service module.

    ``httpx.Client`` cannot be patched globally: the test client is itself httpx-based,
    so a global patch also intercepts the request under test and the endpoint never
    runs. Patching only the name bound in ``notification_service`` keeps the real
    ``_post_webhook`` logic (status mapping, error capture) under test while leaving
    the test transport alone.
    """

    calls: list[tuple[str, str]] = []
    response_factory = None

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def __enter__(self) -> _FakeWebhookClient:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def post(self, url: str, **kwargs: object):

        type(self).calls.append(("POST", url))
        assert type(self).response_factory is not None
        return type(self).response_factory(url)

    def get(self, url: str, **kwargs: object):
        return self.post(url, **kwargs)


@pytest.fixture()
def fake_webhook(monkeypatch):
    """Intercept only the notification service's outbound webhook client."""

    import httpx
    from onestep_control_plane_api.api import notification_service as service_module

    _FakeWebhookClient.calls = []

    def ok(url: str):
        return httpx.Response(200, json={"code": 0}, request=httpx.Request("POST", url))

    def boom(url: str):
        return httpx.Response(500, text="boom", request=httpx.Request("POST", url))

    monkeypatch.setattr(service_module, "httpx", SimpleNamespace(Client=_FakeWebhookClient))
    return SimpleNamespace(
        calls=_FakeWebhookClient.calls,
        respond_ok=lambda: setattr(_FakeWebhookClient, "response_factory", ok),
        respond_error=lambda: setattr(_FakeWebhookClient, "response_factory", boom),
    )


def test_notification_test_actually_posts_to_the_webhook(client, fake_webhook) -> None:
    """The test endpoint must perform a real request, not just render a preview.

    Regression guard: this endpoint used to return ``status="accepted"`` while the
    console displayed "Test accepted by {provider}", and no HTTP request was ever
    made. A channel with a wrong URL or an expired token therefore reported success,
    and the operator only discovered it during a real incident. A test that does not
    send is worse than no test at all.
    """

    fake_webhook.respond_ok()
    login_console_role(client, username="admin", role="admin")
    channel_id = _create_channel(client, webhook_url="https://hooks.example.com/feishu")

    response = client.post(
        f"/api/v1/settings/notifications/channels/{channel_id}/test",
        json={"message": "connectivity probe"},
    )

    assert response.status_code == 200
    assert fake_webhook.calls == [("POST", "https://hooks.example.com/feishu")], (
        "the test endpoint must actually POST to the channel's webhook"
    )
    body = response.json()
    assert body["delivered"] is True
    assert body["response_status_code"] == 200
    assert body["error_message"] is None


def test_notification_test_reports_a_failing_webhook_as_not_delivered(
    client, fake_webhook
) -> None:
    """A failing webhook must be reported as a failure, not swallowed as accepted."""

    fake_webhook.respond_error()
    login_console_role(client, username="admin", role="admin")
    channel_id = _create_channel(client, webhook_url="https://hooks.example.com/broken")

    response = client.post(
        f"/api/v1/settings/notifications/channels/{channel_id}/test",
        json={},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["delivered"] is False
    assert body["response_status_code"] == 500
    assert body["error_message"] is not None


def test_notification_test_is_recorded_in_delivery_history(client, fake_webhook) -> None:
    """A test attempt shows up in the delivery history alongside real notifications."""

    fake_webhook.respond_ok()
    login_console_role(client, username="admin", role="admin")
    channel_id = _create_channel(client, webhook_url="https://hooks.example.com/feishu")

    client.post(f"/api/v1/settings/notifications/channels/{channel_id}/test", json={})

    history = client.get(
        "/api/v1/settings/notifications/deliveries",
        params={"channel_id": channel_id},
    )

    assert history.status_code == 200
    items = history.json()["items"]
    assert any(item["event_type"] == "test" for item in items), (
        f"the test attempt must appear in delivery history: {items}"
    )


def test_delivery_history_is_readable_and_ordered_newest_first(client, db_session) -> None:
    """Delivery history exposes the outcome an operator needs to trust a channel."""

    from onestep_control_plane_api.db.models import NotificationDelivery

    login_console_role(client, username="admin", role="admin")
    channel_id = _create_channel(client, webhook_url="https://hooks.example.com/feishu")

    with client.app.state.session_factory() as session:
        for index in range(3):
            session.add(
                NotificationDelivery(
                    channel_id=UUID(channel_id),
                    dedupe_key=f"history-{index}-{uuid4().hex}",
                    event_type="task_failed",
                    service_name="billing-sync",
                    service_environment="prod",
                    task_name="sync_users",
                    status="succeeded" if index % 2 else "failed",
                    response_status_code=200 if index % 2 else 500,
                    error_message=None if index % 2 else "webhook responded with status 500",
                )
            )
        session.commit()

    response = client.get(
        "/api/v1/settings/notifications/deliveries",
        params={"channel_id": channel_id},
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 3
    assert items[0]["created_at"] >= items[-1]["created_at"]
    failed = [item for item in items if item["status"] == "failed"]
    assert failed and failed[0]["error_message"] is not None
