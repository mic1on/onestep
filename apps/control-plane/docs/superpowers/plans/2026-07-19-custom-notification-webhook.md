# Custom Notification Webhook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `custom` notification provider with GET/POST method selection, visual query parameters, visual POST JSON body parameters, and event-field token rendering while preserving Feishu and WeCom behavior.

**Architecture:** Keep the existing notification matching, dedupe, delivery audit, and frontend settings page. Add one nullable channel config column, a focused custom-webhook rendering helper, provider-aware dispatch in `notification_service.py`, and compact custom-provider controls in `NotificationSettingsPage.tsx`.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy, Alembic, httpx, pytest, React 19, TypeScript, Vite/Vitest, Testing Library, Tailwind, lucide-react.

---

## File Structure

- Create `backend/alembic/versions/202607190001_add_notification_custom_config.py`: nullable `notification_channels.custom_config_json` migration.
- Create `backend/src/onestep_control_plane_api/api/notification_custom.py`: allowed variables, template validation, value rendering, rendered request payload, and preview text for custom channels.
- Modify `backend/src/onestep_control_plane_api/db/models.py`: add `NotificationChannel.custom_config_json`.
- Modify `backend/src/onestep_control_plane_api/api/notification_helpers.py`: add `custom` to `NotificationProvider`.
- Modify `backend/src/onestep_control_plane_api/api/schemas.py`: add custom config schemas and provider/config validators.
- Modify `backend/src/onestep_control_plane_api/api/notification_service.py`: persist/summarize custom config, validate merged patches, build custom request payloads, dispatch GET/POST.
- Modify `backend/tests/test_notifications_api.py`: API round-trip and validation tests.
- Modify `backend/tests/test_notification_service.py`: custom GET/POST rendering and dispatch tests.
- Modify `frontend/src/api.ts`: custom provider and config types.
- Modify `frontend/src/api.notifications.test.ts`: API contract coverage for custom config.
- Modify `frontend/src/components/NotificationSettingsPage.tsx`: provider option, method selector, param editors, insert-field menu, validation, payload shaping.
- Create `frontend/src/components/NotificationSettingsPage.test.tsx`: focused custom-provider form tests.
- Modify `frontend/src/i18n.tsx`: English and Chinese labels for custom provider, method, params, insert-field, and validation text.

---

### Task 1: Backend Schema And API Contract

**Files:**
- Create: `backend/alembic/versions/202607190001_add_notification_custom_config.py`
- Modify: `backend/src/onestep_control_plane_api/db/models.py`
- Modify: `backend/src/onestep_control_plane_api/api/notification_helpers.py`
- Modify: `backend/src/onestep_control_plane_api/api/schemas.py`
- Modify: `backend/src/onestep_control_plane_api/api/notification_service.py`
- Test: `backend/tests/test_notifications_api.py`

- [ ] **Step 1: Write failing API round-trip test**

Add this test to `backend/tests/test_notifications_api.py` after `test_notification_channels_crud_round_trip`:

```python
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
```

- [ ] **Step 2: Write failing API validation test**

Add this test to `backend/tests/test_notifications_api.py` after `test_notification_channel_validation_errors`:

```python
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
```

- [ ] **Step 3: Run the new API tests and verify they fail**

Run:

```bash
uv run pytest backend/tests/test_notifications_api.py::test_custom_notification_channel_crud_round_trip backend/tests/test_notifications_api.py::test_custom_notification_channel_validation_errors -q
```

Expected: FAIL because `custom` is not a supported provider and `custom_config` is not in the schema.

- [ ] **Step 4: Add database migration**

Create `backend/alembic/versions/202607190001_add_notification_custom_config.py`:

```python
"""Add custom notification channel config.

Revision ID: 202607190001
Revises: 202607180002
Create Date: 2026-07-19 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202607190001"
down_revision: str | None = "202607180002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in set(inspector.get_table_names())


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_table("notification_channels"):
        return
    if not _has_column("notification_channels", "custom_config_json"):
        op.add_column(
            "notification_channels",
            sa.Column("custom_config_json", json_type, nullable=True),
        )


def downgrade() -> None:
    if not _has_table("notification_channels"):
        return
    if _has_column("notification_channels", "custom_config_json"):
        op.drop_column("notification_channels", "custom_config_json")
```

- [ ] **Step 5: Add model column**

In `backend/src/onestep_control_plane_api/db/models.py`, add this field after `missed_start_grace_seconds`:

```python
    custom_config_json: Mapped[dict[str, object] | None] = mapped_column(JSON_TYPE)
```

- [ ] **Step 6: Extend provider type**

In `backend/src/onestep_control_plane_api/api/notification_helpers.py`, change the provider literal to:

```python
NotificationProvider = Literal["feishu", "wechat_work", "custom"]
```

- [ ] **Step 7: Add custom config schemas**

In `backend/src/onestep_control_plane_api/api/schemas.py`, import the validator helper:

```python
from onestep_control_plane_api.api.notification_custom import validate_custom_template_value
```

Add these schemas immediately before `NotificationChannelBase`:

```python
NotificationWebhookMethod = Literal["GET", "POST"]


class NotificationCustomParam(APIModel):
    key: str = Field(min_length=1, max_length=255)
    value: str = Field(default="", max_length=2000)

    @field_validator("key", "value", mode="before")
    @classmethod
    def normalize_param_strings(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        return value.strip()

    @field_validator("value")
    @classmethod
    def validate_template_value(cls, value: str) -> str:
        validate_custom_template_value(value)
        return value


class NotificationCustomConfig(APIModel):
    method: NotificationWebhookMethod = "POST"
    query_params: list[NotificationCustomParam] = Field(default_factory=list)
    body_params: list[NotificationCustomParam] = Field(default_factory=list)

    @field_validator("method", mode="before")
    @classmethod
    def normalize_method(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        return value.strip().upper()

    @staticmethod
    def _validate_unique_keys(params: list[NotificationCustomParam], field_name: str) -> None:
        keys = [param.key for param in params]
        if len(set(keys)) != len(keys):
            raise ValueError(f"{field_name} keys must be unique")

    @model_validator(mode="after")
    def validate_custom_config(self) -> NotificationCustomConfig:
        self._validate_unique_keys(self.query_params, "query_params")
        self._validate_unique_keys(self.body_params, "body_params")
        if self.method == "GET" and self.body_params:
            raise ValueError("body_params are only supported for POST custom webhooks")
        return self
```

Add `custom_config` to `NotificationChannelBase`, `NotificationChannelUpdateRequest`, and `NotificationChannelSummary`:

```python
    custom_config: NotificationCustomConfig | None = None
```

Add provider/config validation to `NotificationChannelBase`:

```python
    @model_validator(mode="after")
    def validate_provider_custom_config(self) -> NotificationChannelBase:
        if self.provider == "custom" and self.custom_config is None:
            raise ValueError("custom_config is required when provider is custom")
        if self.provider != "custom" and self.custom_config is not None:
            raise ValueError("custom_config is only supported when provider is custom")
        return self
```

- [ ] **Step 8: Persist and summarize custom config**

In `backend/src/onestep_control_plane_api/api/notification_service.py`, update `_build_channel_summary`:

```python
        custom_config=channel.custom_config_json,
```

Update `create_notification_channel`:

```python
        custom_config_json=(
            payload.custom_config.model_dump() if payload.custom_config is not None else None
        ),
```

Add this service helper near `_validate_merged_missed_start_settings`:

```python
def _validate_merged_custom_config(provider: str, custom_config: dict[str, Any] | None) -> None:
    if provider == "custom" and custom_config is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="custom_config is required when provider is custom",
        )
    if provider != "custom" and custom_config is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="custom_config is only supported when provider is custom",
        )
```

In `update_notification_channel`, compute and validate merged custom state:

```python
    merged_provider = update_data["provider"] if "provider" in update_data else channel.provider
    merged_custom_config = (
        update_data["custom_config"]
        if "custom_config" in update_data
        else channel.custom_config_json
    )
    _validate_merged_custom_config(merged_provider, merged_custom_config)
```

Assign updated custom config:

```python
    if "custom_config" in update_data:
        channel.custom_config_json = update_data["custom_config"]
```

- [ ] **Step 9: Run API tests and verify they pass**

Run:

```bash
uv run pytest backend/tests/test_notifications_api.py::test_custom_notification_channel_crud_round_trip backend/tests/test_notifications_api.py::test_custom_notification_channel_validation_errors -q
```

Expected: PASS.

- [ ] **Step 10: Commit backend API contract**

Run:

```bash
git add backend/alembic/versions/202607190001_add_notification_custom_config.py backend/src/onestep_control_plane_api/db/models.py backend/src/onestep_control_plane_api/api/notification_helpers.py backend/src/onestep_control_plane_api/api/schemas.py backend/src/onestep_control_plane_api/api/notification_service.py backend/tests/test_notifications_api.py
git commit -m "feat: add custom notification channel config"
```

Expected: commit succeeds and does not stage unrelated dirty files.

---

### Task 2: Custom Request Rendering And Dispatch

**Files:**
- Create: `backend/src/onestep_control_plane_api/api/notification_custom.py`
- Modify: `backend/src/onestep_control_plane_api/api/notification_service.py`
- Test: `backend/tests/test_notification_service.py`

- [ ] **Step 1: Write failing custom GET dispatch test**

Add this helper to `backend/tests/test_notification_service.py` near `seed_channel`:

```python
def seed_custom_channel(
    db_session,
    *,
    method: str,
    query_params: list[dict[str, str]],
    body_params: list[dict[str, str]],
    event_types: list[str],
) -> NotificationChannel:
    channel = NotificationChannel(
        name=f"ops-custom-{method.lower()}-{len(query_params)}-{len(body_params)}",
        provider="custom",
        webhook_url="https://example.com/custom",
        enabled=True,
        service_scopes_json=[{"name": "billing-sync", "environment": "prod"}],
        event_types_json=event_types,
        missed_start_grace_seconds=300,
        custom_config_json={
            "method": method,
            "query_params": query_params,
            "body_params": body_params,
        },
    )
    db_session.add(channel)
    db_session.commit()
    db_session.refresh(channel)
    return channel
```

Add this test:

```python
def test_custom_webhook_get_sends_rendered_query_params(db_session, monkeypatch) -> None:
    service, instance = seed_runtime_service(db_session)
    seed_custom_channel(
        db_session,
        method="GET",
        query_params=[
            {"key": "service", "value": "{{ service_name }}"},
            {"key": "event", "value": "{{ event_type }}"},
            {"key": "missing_task", "value": "{{ task_name }}"},
        ],
        body_params=[],
        event_types=["instance_offline"],
    )

    instance.last_seen_at = datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC)
    db_session.commit()

    sent_requests: list[dict[str, object]] = []

    def fake_post_webhook(delivery, *, webhook_url: str, provider: str, timeout_s: float = 5.0) -> None:
        sent_requests.append(
            {
                "provider": provider,
                "webhook_url": webhook_url,
                "payload": delivery.request_payload_json,
            }
        )
        delivery.status = "succeeded"
        delivery.sent_at = datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC)
        delivery.response_status_code = 200

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        fake_post_webhook,
    )

    scan_and_dispatch_instance_connectivity_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 0, 5, tzinfo=UTC),
    )
    created_count = scan_and_dispatch_instance_connectivity_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC),
    )

    assert created_count == 1
    assert sent_requests == [
        {
            "provider": "custom",
            "webhook_url": "https://example.com/custom",
            "payload": {
                "method": "GET",
                "query": {
                    "service": "billing-sync",
                    "event": "instance_offline",
                    "missing_task": "",
                },
                "body": {},
            },
        }
    ]
```

- [ ] **Step 2: Write failing custom POST dispatch test**

Add this test to `backend/tests/test_notification_service.py`:

```python
def test_custom_webhook_post_sends_rendered_query_and_body(db_session, monkeypatch) -> None:
    service, instance = seed_runtime_service(db_session)
    seed_custom_channel(
        db_session,
        method="POST",
        query_params=[{"key": "service", "value": "{{ service_name }}"}],
        body_params=[
            {"key": "event", "value": "{{ event_type }}"},
            {"key": "task", "value": "{{ task_name }}"},
            {"key": "failure", "value": "{{ failure_message }}"},
            {"key": "attempts", "value": "{{ attempts }}"},
        ],
        event_types=["task_failed"],
    )

    task_event = TaskEvent(
        event_id="evt-runtime-custom-failed",
        service_id=service.id,
        instance_id=instance.instance_id,
        task_name="sync_users",
        kind="failed",
        occurred_at=datetime(2026, 4, 30, 2, 5, 0, tzinfo=UTC),
        attempts=2,
        duration_ms=1234,
        failure_kind="timeout",
        exception_type="TimeoutError",
        message="upstream timeout",
        meta_json={"scheduled_at": "2026-04-30T02:00:00Z"},
        received_at=datetime(2026, 4, 30, 2, 5, 1, tzinfo=UTC),
    )
    db_session.add(task_event)
    db_session.commit()
    db_session.refresh(task_event)

    sent_payloads: list[dict[str, object] | None] = []

    def fake_post_webhook(delivery, *, webhook_url: str, provider: str, timeout_s: float = 5.0) -> None:
        sent_payloads.append(delivery.request_payload_json)
        delivery.status = "succeeded"
        delivery.sent_at = datetime(2026, 4, 30, 2, 5, 2, tzinfo=UTC)
        delivery.response_status_code = 200

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        fake_post_webhook,
    )

    created_count = dispatch_runtime_task_event_notifications(
        db_session,
        task_events=[task_event],
    )

    assert created_count == 1
    assert sent_payloads == [
        {
            "method": "POST",
            "query": {"service": "billing-sync"},
            "body": {
                "event": "task_failed",
                "task": "sync_users",
                "failure": "upstream timeout",
                "attempts": "2",
            },
        }
    ]
```

- [ ] **Step 3: Run rendering tests and verify they fail**

Run:

```bash
uv run pytest backend/tests/test_notification_service.py::test_custom_webhook_get_sends_rendered_query_params backend/tests/test_notification_service.py::test_custom_webhook_post_sends_rendered_query_and_body -q
```

Expected: FAIL because `_post_webhook` does not receive a `provider` argument and custom request payloads are not rendered.

- [ ] **Step 4: Create custom rendering helper**

Create `backend/src/onestep_control_plane_api/api/notification_custom.py`:

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from onestep_control_plane_api.api.notification_helpers import (
    NotificationEventRecord,
    format_datetime_for_message,
)

CUSTOM_WEBHOOK_VARIABLES = frozenset(
    {
        "event_type",
        "service_name",
        "service_environment",
        "task_name",
        "occurred_at",
        "scheduled_at",
        "duration_ms",
        "attempts",
        "instance_id",
        "node_name",
        "console_url",
        "failure_message",
        "success_summary",
    }
)
_TOKEN_RE = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")


@dataclass(frozen=True, slots=True)
class RenderedCustomWebhookRequest:
    method: str
    query: dict[str, str]
    body: dict[str, str]

    def to_delivery_payload(self) -> dict[str, object]:
        return {"method": self.method, "query": self.query, "body": self.body}


def validate_custom_template_value(value: str) -> None:
    for match in _TOKEN_RE.finditer(value):
        variable_name = match.group(1)
        if variable_name not in CUSTOM_WEBHOOK_VARIABLES:
            raise ValueError(f"unsupported custom webhook variable: {variable_name}")


def _event_value(event: NotificationEventRecord, variable_name: str) -> str:
    if variable_name == "failure_message":
        return event.failure.message if event.failure and event.failure.message else ""
    value = getattr(event, variable_name, None)
    if value is None:
        return ""
    if variable_name in {"occurred_at", "scheduled_at"}:
        return format_datetime_for_message(value) or ""
    return str(value)


def render_custom_template_value(value: str, event: NotificationEventRecord) -> str:
    def replace(match: re.Match[str]) -> str:
        return _event_value(event, match.group(1))

    return _TOKEN_RE.sub(replace, value)


def _render_params(
    params: list[dict[str, str]],
    event: NotificationEventRecord,
) -> dict[str, str]:
    return {
        param["key"]: render_custom_template_value(param.get("value", ""), event)
        for param in params
        if param.get("key")
    }


def render_custom_webhook_request(
    custom_config: dict[str, Any],
    event: NotificationEventRecord,
) -> RenderedCustomWebhookRequest:
    method = str(custom_config.get("method", "POST")).upper()
    query = _render_params(list(custom_config.get("query_params") or []), event)
    body = (
        _render_params(list(custom_config.get("body_params") or []), event)
        if method == "POST"
        else {}
    )
    return RenderedCustomWebhookRequest(method=method, query=query, body=body)


def build_custom_webhook_preview(custom_config: dict[str, Any] | None) -> str:
    if not custom_config:
        return "Custom webhook is not configured."
    method = str(custom_config.get("method", "POST")).upper()
    query_count = len(list(custom_config.get("query_params") or []))
    body_count = len(list(custom_config.get("body_params") or [])) if method == "POST" else 0
    return f"Custom webhook {method}: {query_count} query params, {body_count} body params"
```

- [ ] **Step 5: Build provider-aware request payloads**

In `backend/src/onestep_control_plane_api/api/notification_service.py`, import:

```python
from onestep_control_plane_api.api.notification_custom import (
    build_custom_webhook_preview,
    render_custom_webhook_request,
)
```

Add this helper near `_persist_pending_delivery`:

```python
def _build_delivery_request_payload(
    channel: NotificationChannel,
    notification_event: NotificationEventRecord,
) -> dict[str, object]:
    if channel.provider == "custom":
        rendered = render_custom_webhook_request(
            channel.custom_config_json or {},
            notification_event,
        )
        return rendered.to_delivery_payload()
    return build_webhook_payload(channel.provider, notification_event)
```

Change `_persist_pending_delivery` so `request_payload_json` uses the helper:

```python
        request_payload_json=_build_delivery_request_payload(channel, notification_event),
```

- [ ] **Step 6: Make dispatch provider-aware**

In `backend/src/onestep_control_plane_api/api/notification_service.py`, change `_post_webhook` signature and body:

```python
def _post_webhook(
    delivery: NotificationDelivery,
    *,
    webhook_url: str,
    provider: str,
    timeout_s: float = DEFAULT_WEBHOOK_TIMEOUT_S,
) -> None:
    try:
        with httpx.Client(timeout=timeout_s) as client:
            if provider == "custom":
                request_payload = delivery.request_payload_json or {}
                method = str(request_payload.get("method", "POST")).upper()
                query = request_payload.get("query") or {}
                body = request_payload.get("body") or {}
                if method == "GET":
                    response = client.get(webhook_url, params=query)
                else:
                    response = client.post(webhook_url, params=query, json=body)
            else:
                response = client.post(webhook_url, json=delivery.request_payload_json)
        delivery.response_status_code = response.status_code
        delivery.response_body = response.text[:4000] if response.text else None
        delivery.status = "succeeded" if response.is_success else "failed"
        if not response.is_success:
            delivery.error_message = f"webhook responded with status {response.status_code}"
    except Exception as exc:
        delivery.status = "failed"
        delivery.error_message = str(exc)
    finally:
        delivery.sent_at = utcnow()
```

Change `_dispatch_delivery`:

```python
def _dispatch_delivery(
    db: Session,
    *,
    delivery: NotificationDelivery,
    webhook_url: str,
    provider: str,
    timeout_s: float = DEFAULT_WEBHOOK_TIMEOUT_S,
) -> None:
    _post_webhook(delivery, webhook_url=webhook_url, provider=provider, timeout_s=timeout_s)
    db.commit()
```

Change pending delivery tuples from:

```python
pending_deliveries: list[tuple[NotificationDelivery, str]] = []
pending_deliveries.append((delivery, channel.webhook_url))
for delivery, webhook_url in pending_deliveries:
    _dispatch_delivery(db, delivery=delivery, webhook_url=webhook_url)
```

to:

```python
pending_deliveries: list[tuple[NotificationDelivery, str, str]] = []
pending_deliveries.append((delivery, channel.webhook_url, channel.provider))
for delivery, webhook_url, provider in pending_deliveries:
    _dispatch_delivery(db, delivery=delivery, webhook_url=webhook_url, provider=provider)
```

Make this tuple change in runtime task, missed-start, and instance-connectivity dispatch paths.

- [ ] **Step 7: Customize test preview text**

In `build_notification_test_response`, change preview construction to:

```python
    preview_text = (
        build_custom_webhook_preview(channel.custom_config_json)
        if channel.provider == "custom" and payload.message is None
        else payload.message or f"Test notification for channel {channel.name}"
    )
```

- [ ] **Step 8: Run custom rendering tests and verify they pass**

Run:

```bash
uv run pytest backend/tests/test_notification_service.py::test_custom_webhook_get_sends_rendered_query_params backend/tests/test_notification_service.py::test_custom_webhook_post_sends_rendered_query_and_body -q
```

Expected: PASS.

- [ ] **Step 9: Run existing notification service smoke tests**

Run:

```bash
uv run pytest backend/tests/test_notification_service.py::test_dispatch_runtime_task_event_notifications_creates_one_delivery_per_new_event backend/tests/test_notification_service.py::test_dispatch_runtime_task_event_notifications_renders_success_summary_and_metrics -q
```

Expected: PASS, proving Feishu behavior still builds provider payloads.

- [ ] **Step 10: Commit custom dispatch**

Run:

```bash
git add backend/src/onestep_control_plane_api/api/notification_custom.py backend/src/onestep_control_plane_api/api/notification_service.py backend/tests/test_notification_service.py
git commit -m "feat: render custom notification webhooks"
```

Expected: commit succeeds.

---

### Task 3: Frontend API Types And Contract

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/api.notifications.test.ts`

- [ ] **Step 1: Write failing API contract test**

In `frontend/src/api.notifications.test.ts`, extend `channelBody` in the mutation test or add a new test:

```ts
it('writes custom notification channel config through the backend contract', async () => {
  const channelBody = {
    id: 'channel-custom',
    name: 'ops-custom',
    provider: 'custom',
    webhook_url_masked: 'https://example.com/***',
    enabled: true,
    service_scopes: [],
    event_types: ['task_failed'],
    missed_start_grace_seconds: 300,
    custom_config: {
      method: 'POST',
      query_params: [{ key: 'service', value: '{{ service_name }}' }],
      body_params: [{ key: 'event', value: '{{ event_type }}' }],
    },
    created_at: '2026-07-19T00:00:00Z',
    updated_at: '2026-07-19T00:00:00Z',
  };
  const fetchMock = vi.spyOn(window, 'fetch').mockResolvedValueOnce(jsonResponse(channelBody));

  await createNotificationChannel({
    name: 'ops-custom',
    provider: 'custom',
    webhook_url: 'https://example.com/hook',
    enabled: true,
    service_scopes: [],
    event_types: ['task_failed'],
    missed_start_grace_seconds: 300,
    custom_config: {
      method: 'POST',
      query_params: [{ key: 'service', value: '{{ service_name }}' }],
      body_params: [{ key: 'event', value: '{{ event_type }}' }],
    },
  });

  expect(calledPath(fetchMock.mock.calls[0])).toBe('/api/v1/settings/notifications/channels');
  expect(JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body))).toEqual({
    name: 'ops-custom',
    provider: 'custom',
    webhook_url: 'https://example.com/hook',
    enabled: true,
    service_scopes: [],
    event_types: ['task_failed'],
    missed_start_grace_seconds: 300,
    custom_config: {
      method: 'POST',
      query_params: [{ key: 'service', value: '{{ service_name }}' }],
      body_params: [{ key: 'event', value: '{{ event_type }}' }],
    },
  });
});
```

- [ ] **Step 2: Run API contract test and verify it fails**

Run:

```bash
pnpm --filter onestep-control-plane-ui test -- api.notifications
```

Expected: TypeScript test compile fails because `custom` and `custom_config` are not typed.

- [ ] **Step 3: Add TypeScript API types**

In `frontend/src/api.ts`, change provider and add config types:

```ts
export type NotificationProvider = 'feishu' | 'wechat_work' | 'custom';
export type NotificationWebhookMethod = 'GET' | 'POST';

export interface NotificationCustomParam {
  key: string;
  value: string;
}

export interface NotificationCustomConfig {
  method: NotificationWebhookMethod;
  query_params: NotificationCustomParam[];
  body_params: NotificationCustomParam[];
}
```

Add `custom_config` to channel and input shapes:

```ts
  custom_config: NotificationCustomConfig | null;
```

```ts
  custom_config?: NotificationCustomConfig | null;
```

- [ ] **Step 4: Run API contract test and verify it passes**

Run:

```bash
pnpm --filter onestep-control-plane-ui test -- api.notifications
```

Expected: PASS.

- [ ] **Step 5: Commit frontend API contract**

Run:

```bash
git add frontend/src/api.ts frontend/src/api.notifications.test.ts
git commit -m "feat: type custom notification webhooks"
```

Expected: commit succeeds.

---

### Task 4: Frontend Custom Provider Form

**Files:**
- Modify: `frontend/src/components/NotificationSettingsPage.tsx`
- Create: `frontend/src/components/NotificationSettingsPage.test.tsx`
- Modify: `frontend/src/i18n.tsx`

- [ ] **Step 1: Write failing form test for custom payload**

Create `frontend/src/components/NotificationSettingsPage.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { I18nProvider } from '../i18n';
import NotificationSettingsPage from './NotificationSettingsPage';

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function renderPage() {
  return render(
    <I18nProvider initialLocale="en">
      <NotificationSettingsPage onAuthRequired={vi.fn()} onNotify={vi.fn()} />
    </I18nProvider>,
  );
}

describe('NotificationSettingsPage custom webhooks', () => {
  it('saves a custom POST channel with query and body params', async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .spyOn(window, 'fetch')
      .mockResolvedValueOnce(jsonResponse({ items: [] }))
      .mockResolvedValueOnce(jsonResponse({ items: [] }))
      .mockResolvedValueOnce(
        jsonResponse({
          id: 'channel-custom',
          name: 'ops-custom',
          provider: 'custom',
          webhook_url_masked: 'https://example.com/***',
          enabled: true,
          service_scopes: [],
          event_types: ['task_failed'],
          missed_start_grace_seconds: 300,
          custom_config: {
            method: 'POST',
            query_params: [{ key: 'service', value: '{{ service_name }}' }],
            body_params: [{ key: 'event', value: '{{ event_type }}' }],
          },
          created_at: '2026-07-19T00:00:00Z',
          updated_at: '2026-07-19T00:00:00Z',
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ items: [] }))
      .mockResolvedValueOnce(jsonResponse({ items: [] }));

    renderPage();

    await user.type(await screen.findByLabelText(/name/i), 'ops-custom');
    await user.click(screen.getByRole('button', { name: /custom/i }));
    await user.type(screen.getByLabelText(/webhook url/i), 'https://example.com/hook');
    await user.click(screen.getByRole('button', { name: /add query param/i }));
    await user.type(screen.getByLabelText(/query parameter key 1/i), 'service');
    await user.type(screen.getByLabelText(/query parameter value 1/i), '{{ service_name }}');
    await user.click(screen.getByRole('button', { name: /add body param/i }));
    await user.type(screen.getByLabelText(/body parameter key 1/i), 'event');
    await user.type(screen.getByLabelText(/body parameter value 1/i), '{{ event_type }}');
    await user.click(screen.getByLabelText(/task failed/i));
    await user.click(screen.getByRole('button', { name: /create channel/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(5));
    const createBody = JSON.parse(String((fetchMock.mock.calls[2][1] as RequestInit).body));
    expect(createBody.custom_config).toEqual({
      method: 'POST',
      query_params: [{ key: 'service', value: '{{ service_name }}' }],
      body_params: [{ key: 'event', value: '{{ event_type }}' }],
    });
  });
});
```

- [ ] **Step 2: Run form test and verify it fails**

Run:

```bash
pnpm --filter onestep-control-plane-ui test -- NotificationSettingsPage
```

Expected: FAIL because the custom provider controls and param editors do not exist.

- [ ] **Step 3: Add i18n labels**

In `frontend/src/i18n.tsx`, add English keys near the existing notification labels:

```ts
  'notifications.provider.custom': 'Custom',
  'notifications.method': 'Method',
  'notifications.queryParams': 'Query parameters',
  'notifications.bodyParams': 'JSON body parameters',
  'notifications.addQueryParam': 'Add query param',
  'notifications.addBodyParam': 'Add body param',
  'notifications.insertField': 'Insert field',
  'notifications.queryParamKey': 'Query parameter key {index}',
  'notifications.queryParamValue': 'Query parameter value {index}',
  'notifications.bodyParamKey': 'Body parameter key {index}',
  'notifications.bodyParamValue': 'Body parameter value {index}',
```

Add Chinese keys in the Chinese dictionary:

```ts
  'notifications.provider.custom': '自定义',
  'notifications.method': '请求方法',
  'notifications.queryParams': 'Query 参数',
  'notifications.bodyParams': 'JSON Body 参数',
  'notifications.addQueryParam': '添加 Query 参数',
  'notifications.addBodyParam': '添加 Body 参数',
  'notifications.insertField': '插入字段',
  'notifications.queryParamKey': 'Query 参数键 {index}',
  'notifications.queryParamValue': 'Query 参数值 {index}',
  'notifications.bodyParamKey': 'Body 参数键 {index}',
  'notifications.bodyParamValue': 'Body 参数值 {index}',
```

- [ ] **Step 4: Extend form state and provider values**

In `frontend/src/components/NotificationSettingsPage.tsx`, add imports:

```ts
  PlusCircle,
  Trash,
```

Extend `FormState`:

```ts
  customMethod: 'GET' | 'POST';
  queryParams: Array<{ key: string; value: string }>;
  bodyParams: Array<{ key: string; value: string }>;
```

Change providers:

```ts
const PROVIDER_VALUES: NotificationProvider[] = ['feishu', 'wechat_work', 'custom'];
```

Set empty form defaults:

```ts
  customMethod: 'POST',
  queryParams: [],
  bodyParams: [],
```

Set channel edit values:

```ts
    customMethod: channel.custom_config?.method ?? 'POST',
    queryParams: channel.custom_config?.query_params ?? [],
    bodyParams: channel.custom_config?.body_params ?? [],
```

- [ ] **Step 5: Add payload shaping**

In `saveChannel`, build custom config:

```ts
    const customConfig =
      form.provider === 'custom'
        ? {
            method: form.customMethod,
            query_params: form.queryParams
              .map((param) => ({ key: param.key.trim(), value: param.value.trim() }))
              .filter((param) => param.key || param.value),
            body_params:
              form.customMethod === 'POST'
                ? form.bodyParams
                    .map((param) => ({ key: param.key.trim(), value: param.value.trim() }))
                    .filter((param) => param.key || param.value)
                : [],
          }
        : undefined;
```

Add to payload:

```ts
      ...(customConfig ? { custom_config: customConfig } : {}),
```

When changing provider away from custom, keep form state in memory but omit it from payload.

- [ ] **Step 6: Add compact param editor controls**

Add this local render helper inside `NotificationSettingsPage` before `return`:

```tsx
  function renderParamEditor(
    title: string,
    params: Array<{ key: string; value: string }>,
    setParams: (params: Array<{ key: string; value: string }>) => void,
    addLabel: string,
    keyLabelPrefix: string,
    valueLabelPrefix: string,
  ) {
    return (
      <div>
        <div className="text-[11px] font-bold uppercase tracking-wide text-slate-500">{title}</div>
        <div className="mt-2 space-y-2">
          {params.map((param, index) => (
            <div className="grid grid-cols-[minmax(86px,0.8fr)_minmax(0,1fr)_32px] gap-2" key={index}>
              <input
                aria-label={t(keyLabelPrefix, { index: index + 1 })}
                className="rounded-lg border border-slate-200 px-2.5 py-2 text-xs font-semibold outline-hidden transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
                onChange={(event) => {
                  const next = [...params];
                  next[index] = { ...next[index], key: event.target.value };
                  setParams(next);
                }}
                value={param.key}
              />
              <input
                aria-label={t(valueLabelPrefix, { index: index + 1 })}
                className="rounded-lg border border-slate-200 px-2.5 py-2 font-mono text-xs font-semibold outline-hidden transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
                onChange={(event) => {
                  const next = [...params];
                  next[index] = { ...next[index], value: event.target.value };
                  setParams(next);
                }}
                value={param.value}
              />
              <button
                aria-label={t('button.delete')}
                className="grid h-8 w-8 place-items-center rounded-md text-slate-400 transition-colors hover:bg-rose-50 hover:text-rose-600"
                onClick={() => setParams(params.filter((_, itemIndex) => itemIndex !== index))}
                type="button"
              >
                <Trash className="h-4 w-4" />
              </button>
            </div>
          ))}
          <button
            className="flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-slate-300 px-3 py-2 text-xs font-bold text-slate-600 transition-colors hover:border-indigo-200 hover:bg-indigo-50 hover:text-indigo-700"
            onClick={() => setParams([...params, { key: '', value: '' }])}
            type="button"
          >
            <PlusCircle className="h-4 w-4" />
            {addLabel}
          </button>
        </div>
      </div>
    );
  }
```

Use it under Webhook URL when `form.provider === 'custom'`:

```tsx
            {form.provider === 'custom' ? (
              <>
                <div>
                  <div className="text-[11px] font-bold uppercase tracking-wide text-slate-500">
                    {t('notifications.method')}
                  </div>
                  <div className="mt-2 grid grid-cols-2 gap-2 rounded-lg border border-slate-200 bg-slate-50 p-1">
                    {(['GET', 'POST'] as const).map((method) => (
                      <button
                        aria-pressed={form.customMethod === method}
                        className={`rounded-md px-3 py-2 text-sm font-bold transition-all ${
                          form.customMethod === method
                            ? 'bg-white text-indigo-700 shadow-xs ring-1 ring-indigo-100'
                            : 'text-slate-500 hover:bg-white/70 hover:text-slate-800'
                        }`}
                        key={method}
                        onClick={() => setForm((current) => ({ ...current, customMethod: method }))}
                        type="button"
                      >
                        {method}
                      </button>
                    ))}
                  </div>
                </div>
                {renderParamEditor(
                  t('notifications.queryParams'),
                  form.queryParams,
                  (queryParams) => setForm((current) => ({ ...current, queryParams })),
                  t('notifications.addQueryParam'),
                  'notifications.queryParamKey',
                  'notifications.queryParamValue',
                )}
                {form.customMethod === 'POST'
                  ? renderParamEditor(
                      t('notifications.bodyParams'),
                      form.bodyParams,
                      (bodyParams) => setForm((current) => ({ ...current, bodyParams })),
                      t('notifications.addBodyParam'),
                      'notifications.bodyParamKey',
                      'notifications.bodyParamValue',
                    )
                  : null}
              </>
            ) : null}
```

- [ ] **Step 7: Run form test and verify it passes**

Run:

```bash
pnpm --filter onestep-control-plane-ui test -- NotificationSettingsPage
```

Expected: PASS.

- [ ] **Step 8: Commit custom form UI**

Run:

```bash
git add frontend/src/components/NotificationSettingsPage.tsx frontend/src/components/NotificationSettingsPage.test.tsx frontend/src/i18n.tsx
git commit -m "feat: add custom notification webhook form"
```

Expected: commit succeeds.

---

### Task 5: Final Verification And Control-Plane Restart

**Files:**
- No new source files.
- Verify all files touched by Tasks 1-4.

- [ ] **Step 1: Run focused backend notification tests**

Run:

```bash
uv run pytest backend/tests/test_notifications_api.py backend/tests/test_notification_service.py
```

Expected: PASS.

- [ ] **Step 2: Run focused frontend tests**

Run:

```bash
pnpm --filter onestep-control-plane-ui test -- api.notifications NotificationSettingsPage
```

Expected: PASS.

- [ ] **Step 3: Run frontend type/build check**

Run:

```bash
pnpm --filter onestep-control-plane-ui build
```

Expected: PASS.

- [ ] **Step 4: Rebuild and restart control-plane container**

Run from `/Users/miclon/development/onestep/onestep-control-plane`:

```bash
docker compose build plane
docker compose up -d plane
docker compose ps
```

Expected: `plane` container is running and healthy.

- [ ] **Step 5: Inspect final diff for unrelated changes**

Run:

```bash
git status --short
git diff --stat
```

Expected: only files from this plan are changed, except pre-existing dirty files that were already present before implementation. Do not revert unrelated pre-existing changes.

---

## Self-Review

- Spec coverage: Tasks cover the `custom` provider, GET/POST, query params, POST body params, variable allowlist, missing field rendering, backend validation, delivery audit payloads, Feishu/WeCom preservation, UI controls, focused tests, build, and container restart.
- Scope: This stays inside the existing notification system and does not add custom headers, secret storage, nested JSON, new runtime reporter fields, or a separate page.
- Type consistency: Backend uses `custom_config_json` in the model and `custom_config` in API schemas/responses. Frontend uses `custom_config` with `method`, `query_params`, and `body_params`. Request dispatch stores rendered `method`, `query`, and `body` in delivery payloads.
