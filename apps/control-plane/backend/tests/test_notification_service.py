from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from onestep_control_plane_api.api.notification_service import (
    dispatch_runtime_task_event_notifications as dispatch_runtime_task_event_notifications_async,
)
from onestep_control_plane_api.api.notification_service import (
    drain_notification_outbox,
    scan_and_dispatch_instance_connectivity_notifications,
    scan_and_dispatch_missed_start_notifications,
)
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db.models import (
    Instance,
    NotificationChannel,
    NotificationDelivery,
    NotificationInstanceState,
    Service,
    TaskDefinition,
    TaskEvent,
)
from onestep_control_plane_api.db.session import session_scope


def seed_runtime_service(db_session) -> tuple[Service, Instance]:
    service = Service(
        name="billing-sync",
        environment="prod",
        latest_deployment_version="1.0.0",
        latest_sync_at=datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC),
    )
    db_session.add(service)
    db_session.commit()
    instance = Instance(
        service_id=service.id,
        instance_id=uuid4(),
        node_name="vm-1",
        deployment_version="1.0.0",
        status="ok",
        created_at=datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC),
        updated_at=datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC),
    )
    db_session.add(instance)
    db_session.commit()
    db_session.refresh(service)
    db_session.refresh(instance)
    return service, instance


def seed_channel(db_session, *, event_types: list[str]) -> NotificationChannel:
    channel = NotificationChannel(
        name=f"ops-{len(event_types)}",
        provider="feishu",
        webhook_url="https://example.com/hook",
        enabled=True,
        service_scopes_json=[{"name": "billing-sync", "environment": "prod"}],
        event_types_json=event_types,
        missed_start_grace_seconds=300,
    )
    db_session.add(channel)
    db_session.commit()
    db_session.refresh(channel)
    return channel


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



def dispatch_runtime_task_event_notifications(db_session, *, task_events):
    """Drive the async dispatch from a synchronous test.

    The dispatch opens its own short work unit on the test's async engine — the
    same database the synchronous ``db_session`` fixture reads — exactly as the
    events-ingest path does now that it awaits the dispatch.
    """

    async def _work_unit() -> int:
        async with session_scope() as session:
            return await dispatch_runtime_task_event_notifications_async(
                session, task_events=task_events
            )

    return asyncio.run(_work_unit())

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

    def fake_post_webhook(delivery, *, webhook_url: str, timeout_s: float = 5.0) -> None:
        sent_requests.append(
            {
                "provider": delivery.channel.provider,
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

    drain_notification_outbox(db_session)
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


def test_custom_webhook_post_sends_rendered_query_and_body(
    db_session, async_db, monkeypatch
) -> None:
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

    def fake_post_webhook(delivery, *, webhook_url: str, timeout_s: float = 5.0) -> None:
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

    drain_notification_outbox(db_session)
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


def test_runtime_notifications_build_absolute_console_url_when_base_url_configured(
    db_session,
    async_db,
    monkeypatch,
) -> None:
    original_base_url = settings.console_base_url
    settings.console_base_url = "https://cp.example"
    try:
        service, instance = seed_runtime_service(db_session)
        seed_channel(db_session, event_types=["task_succeeded"])

        task_event = TaskEvent(
            event_id="evt-runtime-succeeded-with-absolute-url",
            service_id=service.id,
            instance_id=instance.instance_id,
            task_name="sync_users",
            kind="succeeded",
            occurred_at=datetime(2026, 4, 30, 2, 5, 8, tzinfo=UTC),
            attempts=1,
            duration_ms=8000,
            meta_json={"scheduled_at": "2026-04-30T02:00:00Z"},
            received_at=datetime(2026, 4, 30, 2, 5, 9, tzinfo=UTC),
        )
        db_session.add(task_event)
        db_session.commit()
        db_session.refresh(task_event)

        sent_payloads: list[dict[str, object] | None] = []

        def fake_post_webhook(delivery, *, webhook_url: str, timeout_s: float = 5.0) -> None:
            sent_payloads.append(delivery.request_payload_json)
            delivery.status = "succeeded"
            delivery.sent_at = datetime(2026, 4, 30, 2, 5, 10, tzinfo=UTC)
            delivery.response_status_code = 200

        monkeypatch.setattr(
            "onestep_control_plane_api.api.notification_service._post_webhook",
            fake_post_webhook,
        )

        created_count = dispatch_runtime_task_event_notifications(
            db_session, task_events=[task_event]
        )
        drain_notification_outbox(db_session)
        assert created_count == 1
        payload = sent_payloads[0]
        assert payload is not None
        assert (
            "**详情**：[打开详情](https://cp.example/services/billing-sync/tasks/sync_users?environment=prod)"
            in payload["card"]["body"]["elements"][0]["content"]
        )
    finally:
        settings.console_base_url = original_base_url


def test_dispatch_runtime_task_event_notifications_creates_one_delivery_per_new_event(
    db_session, async_db, monkeypatch
) -> None:
    service, instance = seed_runtime_service(db_session)
    seed_channel(db_session, event_types=["task_failed"])

    task_event = TaskEvent(
        event_id="evt-runtime-failed",
        service_id=service.id,
        instance_id=instance.instance_id,
        task_name="sync_users",
        kind="failed",
        occurred_at=datetime(2026, 4, 30, 2, 5, 0, tzinfo=UTC),
        attempts=1,
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

    def fake_post_webhook(delivery, *, webhook_url: str, timeout_s: float = 5.0) -> None:
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
    drain_notification_outbox(db_session)
    assert created_count == 1
    assert len(sent_payloads) == 1
    deliveries = db_session.query(NotificationDelivery).all()
    assert len(deliveries) == 1
    assert deliveries[0].event_type == "task_failed"
    assert deliveries[0].status == "succeeded"

    duplicate_count = dispatch_runtime_task_event_notifications(
        db_session,
        task_events=[task_event],
    )
    assert duplicate_count == 0
    assert db_session.query(NotificationDelivery).count() == 1


def test_dispatch_runtime_task_event_notifications_renders_success_summary_and_metrics(
    db_session, async_db, monkeypatch
) -> None:
    service, instance = seed_runtime_service(db_session)
    seed_channel(db_session, event_types=["task_succeeded"])

    task_event = TaskEvent(
        event_id="evt-runtime-succeeded",
        service_id=service.id,
        instance_id=instance.instance_id,
        task_name="sync_users",
        kind="succeeded",
        occurred_at=datetime(2026, 4, 30, 2, 5, 8, tzinfo=UTC),
        attempts=1,
        duration_ms=8000,
        meta_json={
            "scheduled_at": "2026-04-30T02:00:00Z",
            "notification": {
                "summary": "状态同步完成，更新了 12 个设备状态",
                "metrics": [
                    {"label": "总设备数", "value": 100},
                    {"label": "已检查", "value": 100},
                    {"label": "需更新", "value": 20},
                    {"label": "已更新", "value": 12},
                ],
            },
        },
        received_at=datetime(2026, 4, 30, 2, 5, 9, tzinfo=UTC),
    )
    db_session.add(task_event)
    db_session.commit()
    db_session.refresh(task_event)

    sent_payloads: list[dict[str, object] | None] = []

    def fake_post_webhook(delivery, *, webhook_url: str, timeout_s: float = 5.0) -> None:
        sent_payloads.append(delivery.request_payload_json)
        delivery.status = "succeeded"
        delivery.sent_at = datetime(2026, 4, 30, 2, 5, 10, tzinfo=UTC)
        delivery.response_status_code = 200

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        fake_post_webhook,
    )

    created_count = dispatch_runtime_task_event_notifications(db_session, task_events=[task_event])
    drain_notification_outbox(db_session)
    assert created_count == 1
    assert len(sent_payloads) == 1

    payload = sent_payloads[0]
    assert payload is not None
    detail_content = payload["card"]["body"]["elements"][0]["content"]
    metric_content = payload["card"]["body"]["elements"][1]["content"]
    assert "**摘要**：状态同步完成，更新了 12 个设备状态" in detail_content
    assert "• **总设备数**：100" in metric_content
    assert "• **已检查**：100" in metric_content
    assert "• **需更新**：20" in metric_content
    assert "• **已更新**：12" in metric_content


def test_dispatch_runtime_task_event_notifications_ignores_malformed_success_notification_payload(
    db_session, async_db, monkeypatch
) -> None:
    service, instance = seed_runtime_service(db_session)
    seed_channel(db_session, event_types=["task_succeeded"])

    task_event = TaskEvent(
        event_id="evt-runtime-succeeded-malformed-notification",
        service_id=service.id,
        instance_id=instance.instance_id,
        task_name="sync_users",
        kind="succeeded",
        occurred_at=datetime(2026, 4, 30, 2, 5, 8, tzinfo=UTC),
        attempts=1,
        duration_ms=8000,
        meta_json={
            "scheduled_at": "2026-04-30T02:00:00Z",
            "notification": {
                "summary": {"text": "should be ignored"},
                "metrics": [
                    {"label": "总设备数", "value": {"count": 100}},
                    {"label": "已更新", "value": 12},
                    "bad-item",
                    {"label": "   ", "value": 1},
                ],
            },
        },
        received_at=datetime(2026, 4, 30, 2, 5, 9, tzinfo=UTC),
    )
    db_session.add(task_event)
    db_session.commit()
    db_session.refresh(task_event)

    sent_payloads: list[dict[str, object] | None] = []

    def fake_post_webhook(delivery, *, webhook_url: str, timeout_s: float = 5.0) -> None:
        sent_payloads.append(delivery.request_payload_json)
        delivery.status = "succeeded"
        delivery.sent_at = datetime(2026, 4, 30, 2, 5, 10, tzinfo=UTC)
        delivery.response_status_code = 200

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        fake_post_webhook,
    )

    created_count = dispatch_runtime_task_event_notifications(db_session, task_events=[task_event])
    drain_notification_outbox(db_session)
    assert created_count == 1
    assert len(sent_payloads) == 1
    assert db_session.query(NotificationDelivery).count() == 1

    payload = sent_payloads[0]
    assert payload is not None
    detail_content = payload["card"]["body"]["elements"][0]["content"]
    metric_content = payload["card"]["body"]["elements"][1]["content"]
    assert "**摘要**" not in detail_content
    assert "总设备数" not in metric_content
    assert "• **已更新**：12" in metric_content


def test_scan_and_dispatch_missed_start_notifications_creates_single_delivery(
    db_session, monkeypatch
) -> None:
    service, instance = seed_runtime_service(db_session)
    instance.last_seen_at = datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC)
    seed_channel(db_session, event_types=["task_missed_start"])
    task_definition = TaskDefinition(
        service_id=service.id,
        task_name="sync_users",
        source_name="interval:300s",
        source_kind="interval",
        source_config_json={"seconds": 300, "immediate": False, "timezone": "UTC"},
        updated_at=datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC),
    )
    db_session.add(task_definition)
    db_session.commit()

    sent_payloads: list[dict[str, object] | None] = []

    def fake_post_webhook(delivery, *, webhook_url: str, timeout_s: float = 5.0) -> None:
        sent_payloads.append(delivery.request_payload_json)
        delivery.status = "succeeded"
        delivery.sent_at = datetime(2026, 4, 30, 2, 11, 0, tzinfo=UTC)
        delivery.response_status_code = 200

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        fake_post_webhook,
    )

    created_count = scan_and_dispatch_missed_start_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC),
    )
    drain_notification_outbox(db_session)
    assert created_count == 1
    assert len(sent_payloads) == 1
    deliveries = db_session.query(NotificationDelivery).all()
    assert len(deliveries) == 1
    assert deliveries[0].event_type == "task_missed_start"
    assert deliveries[0].scheduled_at == datetime(2026, 4, 30, 2, 5, 0, tzinfo=UTC)

    duplicate_count = scan_and_dispatch_missed_start_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 10, 30, tzinfo=UTC),
    )
    assert duplicate_count == 0
    assert db_session.query(NotificationDelivery).count() == 1


def test_scan_and_dispatch_missed_start_notifications_skips_started_slot(
    db_session, monkeypatch
) -> None:
    service, instance = seed_runtime_service(db_session)
    instance.last_seen_at = datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC)
    seed_channel(db_session, event_types=["task_missed_start"])
    task_definition = TaskDefinition(
        service_id=service.id,
        task_name="sync_users",
        source_name="interval:300s",
        source_kind="interval",
        source_config_json={"seconds": 300, "immediate": False, "timezone": "UTC"},
        updated_at=datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC),
    )
    db_session.add(task_definition)
    started_event = TaskEvent(
        event_id="evt-runtime-started",
        service_id=service.id,
        instance_id=instance.instance_id,
        task_name="sync_users",
        kind="started",
        occurred_at=datetime(2026, 4, 30, 2, 5, 1, tzinfo=UTC),
        meta_json={"scheduled_at": "2026-04-30T02:05:00Z"},
        received_at=datetime(2026, 4, 30, 2, 5, 1, tzinfo=UTC),
    )
    db_session.add(started_event)
    db_session.commit()

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        lambda delivery, *, webhook_url, timeout_s=5.0: None,
    )
    created_count = scan_and_dispatch_missed_start_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC),
    )
    assert created_count == 0
    assert db_session.query(NotificationDelivery).count() == 0


def test_missed_start_scan_skips_interval_started_slot_without_scheduled_at(
    db_session,
    monkeypatch,
) -> None:
    service, instance = seed_runtime_service(db_session)
    instance.last_seen_at = datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC)
    seed_channel(db_session, event_types=["task_missed_start"])
    task_definition = TaskDefinition(
        service_id=service.id,
        task_name="sync_users",
        source_name="interval:300s",
        source_kind="interval",
        source_config_json={"seconds": 300, "immediate": False, "timezone": "UTC"},
        updated_at=datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC),
    )
    db_session.add(task_definition)
    started_event = TaskEvent(
        event_id="evt-runtime-started-without-scheduled-at",
        service_id=service.id,
        instance_id=instance.instance_id,
        task_name="sync_users",
        kind="started",
        occurred_at=datetime(2026, 4, 30, 2, 5, 4, tzinfo=UTC),
        meta_json={"source": "interval:300s"},
        received_at=datetime(2026, 4, 30, 2, 5, 4, tzinfo=UTC),
    )
    db_session.add(started_event)
    db_session.commit()

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        lambda delivery, *, webhook_url, timeout_s=5.0: None,
    )
    created_count = scan_and_dispatch_missed_start_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC),
    )
    assert created_count == 0
    assert db_session.query(NotificationDelivery).count() == 0


def test_missed_start_scan_skips_interval_started_slot_with_nearby_scheduled_at(
    db_session,
    monkeypatch,
) -> None:
    service, instance = seed_runtime_service(db_session)
    service.latest_sync_at = datetime(2026, 5, 6, 2, 41, 22, 819707, tzinfo=UTC)
    instance.last_seen_at = datetime(2026, 5, 6, 2, 46, 39, 929637, tzinfo=UTC)
    seed_channel(db_session, event_types=["task_missed_start"])
    task_definition = TaskDefinition(
        service_id=service.id,
        task_name="sync_user_record",
        source_name="interval:3600s",
        source_kind="interval",
        source_config_json={"seconds": 3600, "immediate": False, "timezone": "Asia/Shanghai"},
        updated_at=datetime(2026, 5, 6, 2, 41, 22, 819707, tzinfo=UTC),
    )
    db_session.add(task_definition)
    started_event = TaskEvent(
        event_id="evt-runtime-started-nearby-scheduled-at",
        service_id=service.id,
        instance_id=instance.instance_id,
        task_name="sync_user_record",
        kind="started",
        occurred_at=datetime(2026, 5, 6, 2, 41, 20, 464828, tzinfo=UTC),
        meta_json={
            "source": "interval:3600s",
            "sequence": 1,
            "scheduled_at": "2026-05-06T10:41:15.672439+08:00",
        },
        received_at=datetime(2026, 5, 6, 2, 41, 22, 522608, tzinfo=UTC),
    )
    db_session.add(started_event)
    db_session.commit()

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        lambda delivery, *, webhook_url, timeout_s=5.0: None,
    )
    created_count = scan_and_dispatch_missed_start_notifications(
        db_session,
        now=datetime(2026, 5, 6, 2, 46, 39, 929637, tzinfo=UTC),
    )
    assert created_count == 0
    assert db_session.query(NotificationDelivery).count() == 0


def test_missed_start_scan_keeps_interval_missed_start_when_no_started_event(
    db_session,
    monkeypatch,
) -> None:
    service, instance = seed_runtime_service(db_session)
    instance.last_seen_at = datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC)
    seed_channel(db_session, event_types=["task_missed_start"])
    task_definition = TaskDefinition(
        service_id=service.id,
        task_name="sync_users",
        source_name="interval:300s",
        source_kind="interval",
        source_config_json={"seconds": 300, "immediate": False, "timezone": "UTC"},
        updated_at=datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC),
    )
    db_session.add(task_definition)
    db_session.commit()

    sent_payloads: list[dict[str, object] | None] = []

    def fake_post_webhook(delivery, *, webhook_url: str, timeout_s: float = 5.0) -> None:
        sent_payloads.append(delivery.request_payload_json)
        delivery.status = "succeeded"
        delivery.sent_at = datetime(2026, 4, 30, 2, 11, 0, tzinfo=UTC)
        delivery.response_status_code = 200

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        fake_post_webhook,
    )

    created_count = scan_and_dispatch_missed_start_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC),
    )
    drain_notification_outbox(db_session)
    assert created_count == 1
    assert len(sent_payloads) == 1
    deliveries = db_session.query(NotificationDelivery).all()
    assert len(deliveries) == 1
    assert deliveries[0].scheduled_at == datetime(2026, 4, 30, 2, 5, 0, tzinfo=UTC)


def test_scan_and_dispatch_missed_start_notifications_skips_offline_service(
    db_session, monkeypatch
) -> None:
    service, instance = seed_runtime_service(db_session)
    instance.last_seen_at = datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC)
    seed_channel(db_session, event_types=["task_missed_start"])
    task_definition = TaskDefinition(
        service_id=service.id,
        task_name="sync_users",
        source_name="interval:300s",
        source_kind="interval",
        source_config_json={"seconds": 300, "immediate": False, "timezone": "UTC"},
        updated_at=datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC),
    )
    db_session.add(task_definition)
    db_session.commit()

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        lambda delivery, *, webhook_url, timeout_s=5.0: None,
    )

    created_count = scan_and_dispatch_missed_start_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC),
    )

    assert created_count == 0
    assert db_session.query(NotificationDelivery).count() == 0


def test_scan_and_dispatch_missed_start_notifications_skips_slots_before_online_restart(
    db_session, monkeypatch
) -> None:
    service, instance = seed_runtime_service(db_session)
    instance.started_at = datetime(2026, 4, 30, 2, 8, 0, tzinfo=UTC)
    instance.last_seen_at = datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC)
    seed_channel(db_session, event_types=["task_missed_start"])
    task_definition = TaskDefinition(
        service_id=service.id,
        task_name="sync_users",
        source_name="interval:300s",
        source_kind="interval",
        source_config_json={"seconds": 300, "immediate": False, "timezone": "UTC"},
        updated_at=datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC),
    )
    db_session.add(task_definition)
    db_session.commit()

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        lambda delivery, *, webhook_url, timeout_s=5.0: None,
    )

    created_count = scan_and_dispatch_missed_start_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC),
    )

    assert created_count == 0
    assert db_session.query(NotificationDelivery).count() == 0


def test_scan_and_dispatch_missed_start_notifications_anchors_interval_to_online_restart(
    db_session, monkeypatch
) -> None:
    service, instance = seed_runtime_service(db_session)
    service.latest_sync_at = datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC)
    instance.started_at = datetime(2026, 4, 30, 2, 2, 30, tzinfo=UTC)
    instance.last_seen_at = datetime(2026, 4, 30, 2, 11, 0, tzinfo=UTC)
    seed_channel(db_session, event_types=["task_missed_start"])
    task_definition = TaskDefinition(
        service_id=service.id,
        task_name="sync_users",
        source_name="interval:300s",
        source_kind="interval",
        source_config_json={"seconds": 300, "immediate": False, "timezone": "UTC"},
        updated_at=datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC),
    )
    db_session.add(task_definition)
    started_event = TaskEvent(
        event_id="evt-runtime-started-after-restart",
        service_id=service.id,
        instance_id=instance.instance_id,
        task_name="sync_users",
        kind="started",
        occurred_at=datetime(2026, 4, 30, 2, 7, 31, tzinfo=UTC),
        meta_json={"scheduled_at": "2026-04-30T02:07:30+00:00"},
        received_at=datetime(2026, 4, 30, 2, 7, 32, tzinfo=UTC),
    )
    db_session.add(started_event)
    db_session.commit()

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        lambda delivery, *, webhook_url, timeout_s=5.0: None,
    )

    created_count = scan_and_dispatch_missed_start_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 13, 0, tzinfo=UTC),
    )

    assert created_count == 0
    assert db_session.query(NotificationDelivery).count() == 0


def test_scan_and_dispatch_missed_start_notifications_uses_restart_interval_slot_when_missing(
    db_session, monkeypatch
) -> None:
    service, instance = seed_runtime_service(db_session)
    service.latest_sync_at = datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC)
    instance.started_at = datetime(2026, 4, 30, 2, 2, 30, tzinfo=UTC)
    instance.last_seen_at = datetime(2026, 4, 30, 2, 13, 0, tzinfo=UTC)
    seed_channel(db_session, event_types=["task_missed_start"])
    task_definition = TaskDefinition(
        service_id=service.id,
        task_name="sync_users",
        source_name="interval:300s",
        source_kind="interval",
        source_config_json={"seconds": 300, "immediate": False, "timezone": "UTC"},
        updated_at=datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC),
    )
    db_session.add(task_definition)
    db_session.commit()

    sent_payloads: list[dict[str, object] | None] = []

    def fake_post_webhook(delivery, *, webhook_url: str, timeout_s: float = 5.0) -> None:
        sent_payloads.append(delivery.request_payload_json)
        delivery.status = "succeeded"
        delivery.sent_at = datetime(2026, 4, 30, 2, 13, 0, tzinfo=UTC)
        delivery.response_status_code = 200

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        fake_post_webhook,
    )

    created_count = scan_and_dispatch_missed_start_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 13, 0, tzinfo=UTC),
    )

    drain_notification_outbox(db_session)
    assert created_count == 1
    assert len(sent_payloads) == 1
    deliveries = db_session.query(NotificationDelivery).all()
    assert len(deliveries) == 1
    assert deliveries[0].scheduled_at == datetime(2026, 4, 30, 2, 7, 30, tzinfo=UTC)


def test_scan_and_dispatch_missed_start_notifications_ignores_stale_seen_before_plane_restart(
    db_session, monkeypatch
) -> None:
    service, instance = seed_runtime_service(db_session)
    instance.last_seen_at = datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC)
    seed_channel(db_session, event_types=["task_missed_start"])
    task_definition = TaskDefinition(
        service_id=service.id,
        task_name="sync_users",
        source_name="interval:300s",
        source_kind="interval",
        source_config_json={"seconds": 300, "immediate": False, "timezone": "UTC"},
        updated_at=datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC),
    )
    db_session.add(task_definition)
    db_session.commit()

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        lambda delivery, *, webhook_url, timeout_s=5.0: None,
    )

    created_count = scan_and_dispatch_missed_start_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 10, 30, tzinfo=UTC),
        min_last_seen_at=datetime(2026, 4, 30, 2, 10, 15, tzinfo=UTC),
    )

    assert created_count == 0
    assert db_session.query(NotificationDelivery).count() == 0


def test_scan_and_dispatch_missed_start_notifications_checks_pre_plane_restart_interval_slots(
    db_session, monkeypatch
) -> None:
    service, instance = seed_runtime_service(db_session)
    instance.last_seen_at = datetime(2026, 4, 30, 2, 11, 0, tzinfo=UTC)
    seed_channel(db_session, event_types=["task_missed_start"])
    task_definition = TaskDefinition(
        service_id=service.id,
        task_name="sync_users",
        source_name="interval:300s",
        source_kind="interval",
        source_config_json={"seconds": 300, "immediate": False, "timezone": "UTC"},
        updated_at=datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC),
    )
    db_session.add(task_definition)
    db_session.commit()

    sent_payloads: list[dict[str, object] | None] = []

    def fake_post_webhook(delivery, *, webhook_url: str, timeout_s: float = 5.0) -> None:
        sent_payloads.append(delivery.request_payload_json)
        delivery.status = "succeeded"
        delivery.sent_at = datetime(2026, 4, 30, 2, 11, 0, tzinfo=UTC)
        delivery.response_status_code = 200

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        fake_post_webhook,
    )

    created_count = scan_and_dispatch_missed_start_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 11, 0, tzinfo=UTC),
        min_last_seen_at=datetime(2026, 4, 30, 2, 8, 0, tzinfo=UTC),
    )

    drain_notification_outbox(db_session)
    assert created_count == 1
    assert len(sent_payloads) == 1
    deliveries = db_session.query(NotificationDelivery).all()
    assert len(deliveries) == 1
    assert deliveries[0].scheduled_at == datetime(2026, 4, 30, 2, 5, 0, tzinfo=UTC)


def test_scan_and_dispatch_missed_start_notifications_checks_pre_plane_restart_cron_slots(
    db_session, monkeypatch
) -> None:
    service, instance = seed_runtime_service(db_session)
    instance.started_at = datetime(2026, 4, 30, 0, 0, 0, tzinfo=UTC)
    instance.last_seen_at = datetime(2026, 4, 30, 2, 11, 0, tzinfo=UTC)
    seed_channel(db_session, event_types=["task_missed_start"])
    task_definition = TaskDefinition(
        service_id=service.id,
        task_name="sync_users",
        source_name="cron:* * * * *",
        source_kind="cron",
        source_config_json={"expression": "* * * * *", "timezone": "UTC"},
        updated_at=datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC),
    )
    db_session.add(task_definition)
    db_session.commit()

    sent_payloads: list[dict[str, object] | None] = []

    def fake_post_webhook(delivery, *, webhook_url: str, timeout_s: float = 5.0) -> None:
        sent_payloads.append(delivery.request_payload_json)
        delivery.status = "succeeded"
        delivery.sent_at = datetime(2026, 4, 30, 2, 11, 0, tzinfo=UTC)
        delivery.response_status_code = 200

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        fake_post_webhook,
    )

    created_count = scan_and_dispatch_missed_start_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 11, 0, tzinfo=UTC),
        min_last_seen_at=datetime(2026, 4, 30, 2, 8, 30, tzinfo=UTC),
    )

    drain_notification_outbox(db_session)
    assert created_count == 1
    assert len(sent_payloads) == 1
    deliveries = db_session.query(NotificationDelivery).all()
    assert len(deliveries) == 1
    assert deliveries[0].scheduled_at == datetime(2026, 4, 30, 2, 6, 0, tzinfo=UTC)

    duplicate_count = scan_and_dispatch_missed_start_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 11, 30, tzinfo=UTC),
        min_last_seen_at=datetime(2026, 4, 30, 2, 8, 30, tzinfo=UTC),
    )
    assert duplicate_count == 0
    assert db_session.query(NotificationDelivery).count() == 1


def test_missed_start_scan_alerts_for_immediate_interval_inside_the_grace_interval_window(
    db_session, monkeypatch
) -> None:
    """Regression: the blind-spot warning must never swallow a real missed start.

    With ``immediate: true`` the interval schedule is anchored at the online start, so
    the first slot is due after ``grace_seconds`` alone. An earlier version of the
    visibility predicate demanded ``grace_seconds + interval_seconds`` and the caller
    used that verdict to ``continue`` past the whole slot loop, so this exact shape --
    a one-hour interval, a service up 3599 s, a task that never started -- produced
    zero notifications. It must produce exactly one.
    """

    service, instance = seed_runtime_service(db_session)
    service.latest_sync_at = datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC)
    instance.started_at = datetime(2026, 4, 30, 2, 0, 1, tzinfo=UTC)
    instance.created_at = datetime(2026, 4, 30, 2, 0, 1, tzinfo=UTC)
    instance.last_seen_at = datetime(2026, 4, 30, 3, 0, 0, tzinfo=UTC)
    seed_channel(db_session, event_types=["task_missed_start"])
    task_definition = TaskDefinition(
        service_id=service.id,
        task_name="sync_users",
        source_name="interval:3600s",
        source_kind="interval",
        source_config_json={"seconds": 3600, "immediate": True, "timezone": "UTC"},
        updated_at=datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC),
    )
    db_session.add(task_definition)
    db_session.commit()

    sent_payloads: list[dict[str, object] | None] = []

    def fake_post_webhook(delivery, *, webhook_url: str, timeout_s: float = 5.0) -> None:
        sent_payloads.append(delivery.request_payload_json)
        delivery.status = "succeeded"
        delivery.sent_at = datetime(2026, 4, 30, 3, 0, 1, tzinfo=UTC)
        delivery.response_status_code = 200

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        fake_post_webhook,
    )

    created_count = scan_and_dispatch_missed_start_notifications(
        db_session,
        now=datetime(2026, 4, 30, 3, 0, 0, tzinfo=UTC),
    )

    drain_notification_outbox(db_session)
    assert created_count == 1
    assert len(sent_payloads) == 1
    deliveries = db_session.query(NotificationDelivery).all()
    assert len(deliveries) == 1
    assert deliveries[0].event_type == "task_missed_start"
    assert deliveries[0].scheduled_at == datetime(2026, 4, 30, 2, 0, 1, tzinfo=UTC)


def test_missed_start_scan_skips_immediate_interval_slot_that_did_start(
    db_session, monkeypatch
) -> None:
    """Control for the regression above: the same shape with a started slot is silent.

    Without this, an assertion that "a notification appears" could pass because the
    scan reports unconditionally rather than because the slot really is missed.
    """

    service, instance = seed_runtime_service(db_session)
    service.latest_sync_at = datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC)
    instance.started_at = datetime(2026, 4, 30, 2, 0, 1, tzinfo=UTC)
    instance.created_at = datetime(2026, 4, 30, 2, 0, 1, tzinfo=UTC)
    instance.last_seen_at = datetime(2026, 4, 30, 3, 0, 0, tzinfo=UTC)
    seed_channel(db_session, event_types=["task_missed_start"])
    task_definition = TaskDefinition(
        service_id=service.id,
        task_name="sync_users",
        source_name="interval:3600s",
        source_kind="interval",
        source_config_json={"seconds": 3600, "immediate": True, "timezone": "UTC"},
        updated_at=datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC),
    )
    db_session.add(task_definition)
    started_event = TaskEvent(
        event_id="evt-immediate-started",
        service_id=service.id,
        instance_id=instance.instance_id,
        task_name="sync_users",
        kind="started",
        occurred_at=datetime(2026, 4, 30, 2, 0, 2, tzinfo=UTC),
        meta_json={"scheduled_at": "2026-04-30T02:00:01+00:00"},
        received_at=datetime(2026, 4, 30, 2, 0, 3, tzinfo=UTC),
    )
    db_session.add(started_event)
    db_session.commit()

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        lambda delivery, *, webhook_url, timeout_s=5.0: None,
    )

    created_count = scan_and_dispatch_missed_start_notifications(
        db_session,
        now=datetime(2026, 4, 30, 3, 0, 0, tzinfo=UTC),
    )

    assert created_count == 0
    assert db_session.query(NotificationDelivery).count() == 0


def test_missed_start_scan_alerts_for_cron_inside_the_grace_window(db_session, monkeypatch) -> None:
    """Cron slots do not depend on the online start, so the log verdict must not gate them.

    A cron expression's period is arbitrary, so the predicate expands the expression
    instead of assuming one minute. Here a 320 s online run still contains the due
    minute slot at 02:06, so the notification must go out even though the old
    ``grace + 60`` heuristic would have called this window blind.
    """

    service, instance = seed_runtime_service(db_session)
    instance.started_at = datetime(2026, 4, 30, 2, 5, 40, tzinfo=UTC)
    instance.created_at = datetime(2026, 4, 30, 2, 5, 40, tzinfo=UTC)
    instance.last_seen_at = datetime(2026, 4, 30, 2, 11, 0, tzinfo=UTC)
    seed_channel(db_session, event_types=["task_missed_start"])
    task_definition = TaskDefinition(
        service_id=service.id,
        task_name="sync_users",
        source_name="cron:* * * * *",
        source_kind="cron",
        source_config_json={"expression": "* * * * *", "timezone": "UTC"},
        updated_at=datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC),
    )
    db_session.add(task_definition)
    db_session.commit()

    sent_payloads: list[dict[str, object] | None] = []

    def fake_post_webhook(delivery, *, webhook_url: str, timeout_s: float = 5.0) -> None:
        sent_payloads.append(delivery.request_payload_json)
        delivery.status = "succeeded"
        delivery.sent_at = datetime(2026, 4, 30, 2, 11, 0, tzinfo=UTC)
        delivery.response_status_code = 200

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        fake_post_webhook,
    )

    created_count = scan_and_dispatch_missed_start_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 11, 0, tzinfo=UTC),
    )

    drain_notification_outbox(db_session)
    assert created_count == 1
    assert len(sent_payloads) == 1
    deliveries = db_session.query(NotificationDelivery).all()
    assert len(deliveries) == 1
    assert deliveries[0].scheduled_at == datetime(2026, 4, 30, 2, 6, 0, tzinfo=UTC)


def test_missed_start_scan_reports_blind_spot_warning_for_fast_restarts(
    db_session, monkeypatch
) -> None:
    """A genuinely blind window is annotated so silence cannot read as health.

    A service restarting every few minutes has no evaluable slot here, so the scan
    legitimately stays quiet -- the operator must still be able to tell "nothing was
    missed" from "nothing could be checked", which is what ``missed_start_scan_blind``
    provides. The notification path itself is covered by the immediate-schedule
    regression test above.
    """

    service, instance = seed_runtime_service(db_session)
    instance.started_at = datetime(2026, 4, 30, 2, 4, 20, tzinfo=UTC)
    instance.created_at = datetime(2026, 4, 30, 2, 4, 20, tzinfo=UTC)
    instance.last_seen_at = datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC)
    seed_channel(db_session, event_types=["task_missed_start"])
    task_definition = TaskDefinition(
        service_id=service.id,
        task_name="sync_users",
        source_name="interval:300s",
        source_kind="interval",
        source_config_json={"seconds": 300, "immediate": False, "timezone": "UTC"},
        updated_at=datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC),
    )
    db_session.add(task_definition)
    db_session.commit()

    warnings: list[tuple[str, dict[str, object]]] = []

    def fake_emit_structured_log(target_logger, level, event, **fields):
        warnings.append((event, fields))
        return fields

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service.emit_structured_log",
        fake_emit_structured_log,
    )
    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        lambda delivery, *, webhook_url, timeout_s=5.0: None,
    )

    created_count = scan_and_dispatch_missed_start_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC),
    )

    assert created_count == 0
    blind_events = [fields for event, fields in warnings if event == "missed_start_scan_blind"]
    assert len(blind_events) == 1
    assert blind_events[0]["task"] == "sync_users"
    assert blind_events[0]["reason"] == "service_restarts_faster_than_grace_plus_cadence"


def test_missed_start_scan_alerts_even_when_the_visibility_predicate_is_pessimistic(
    db_session, monkeypatch
) -> None:
    """Pin the contract: the predicate writes a log line and nothing else.

    Forcing the predicate to report a blind spot must not change what is delivered. The
    previous implementation let this verdict ``continue`` past the whole slot loop, so a
    pessimistic (or merely wrong) verdict silently dropped real notifications. Pinning it
    against a forced-False predicate keeps that coupling from creeping back even if the
    threshold is later adjusted again.
    """

    service, instance = seed_runtime_service(db_session)
    instance.last_seen_at = datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC)
    seed_channel(db_session, event_types=["task_missed_start"])
    task_definition = TaskDefinition(
        service_id=service.id,
        task_name="sync_users",
        source_name="interval:300s",
        source_kind="interval",
        source_config_json={"seconds": 300, "immediate": False, "timezone": "UTC"},
        updated_at=datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC),
    )
    db_session.add(task_definition)
    db_session.commit()

    blind_events: list[dict[str, object]] = []

    def fake_emit_structured_log(target_logger, level, event, **fields):
        if event == "missed_start_scan_blind":
            blind_events.append(fields)
        return fields

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._missed_start_is_detectable",
        lambda **kwargs: False,
    )
    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service.emit_structured_log",
        fake_emit_structured_log,
    )
    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        lambda delivery, *, webhook_url, timeout_s=5.0: None,
    )

    created_count = scan_and_dispatch_missed_start_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC),
    )

    assert blind_events, "the forced verdict must still be reported in the log"
    assert created_count == 1
    deliveries = db_session.query(NotificationDelivery).all()
    assert len(deliveries) == 1
    assert deliveries[0].event_type == "task_missed_start"
    assert deliveries[0].scheduled_at == datetime(2026, 4, 30, 2, 5, 0, tzinfo=UTC)


def test_scan_and_dispatch_missed_start_notifications_falls_back_to_control_plane_timezone(
    db_session, monkeypatch
) -> None:
    original_api_timezone = settings.api_response_timezone
    settings.api_response_timezone = ""
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    try:
        service, instance = seed_runtime_service(db_session)
        instance.last_seen_at = datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC)
        seed_channel(db_session, event_types=["task_missed_start"])
        task_definition = TaskDefinition(
            service_id=service.id,
            task_name="sync_users",
            source_name="cron:* * * * *",
            source_kind="cron",
            source_config_json={"expression": "* * * * *", "timezone": "CST"},
            updated_at=datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC),
        )
        db_session.add(task_definition)
        db_session.commit()

        sent_payloads: list[dict[str, object] | None] = []

        def fake_post_webhook(delivery, *, webhook_url, timeout_s=5.0) -> None:
            sent_payloads.append(delivery.request_payload_json)
            delivery.status = "succeeded"
            delivery.sent_at = datetime(2026, 4, 30, 2, 11, 0, tzinfo=UTC)
            delivery.response_status_code = 200

        monkeypatch.setattr(
            "onestep_control_plane_api.api.notification_service._post_webhook",
            fake_post_webhook,
        )

        created_count = scan_and_dispatch_missed_start_notifications(
            db_session,
            now=datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC),
        )

        drain_notification_outbox(db_session)
        assert created_count == 1
        assert len(sent_payloads) == 1
        detail_content = sent_payloads[0]["card"]["body"]["elements"][0]["content"]
        assert "**计划时间**：2026-04-30T10:05:00+08:00" in detail_content
        assert "**检测时间**：2026-04-30T10:10:00+08:00" in detail_content
    finally:
        settings.api_response_timezone = original_api_timezone


def test_instance_connectivity_scan_seeds_state_without_delivery(db_session, monkeypatch) -> None:
    service, instance = seed_runtime_service(db_session)
    instance.last_seen_at = datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC)
    seed_channel(db_session, event_types=["instance_online", "instance_offline"])
    db_session.commit()

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        lambda delivery, *, webhook_url, timeout_s=5.0: None,
    )

    created_count = scan_and_dispatch_instance_connectivity_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC),
    )

    assert created_count == 0
    assert db_session.query(NotificationDelivery).count() == 0

    state = db_session.query(NotificationInstanceState).one()
    assert state.channel_id is not None
    assert state.instance_id == instance.instance_id
    assert state.last_connectivity == "online"
    assert state.last_transition_at == datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC)
    assert service.name == "billing-sync"


def test_instance_connectivity_scan_sends_offline_once(db_session, monkeypatch) -> None:
    _, instance = seed_runtime_service(db_session)
    instance.last_seen_at = datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC)
    seed_channel(db_session, event_types=["instance_offline"])
    db_session.commit()

    sent_payloads: list[dict[str, object] | None] = []

    def fake_post_webhook(delivery, *, webhook_url: str, timeout_s: float = 5.0) -> None:
        sent_payloads.append(delivery.request_payload_json)
        delivery.status = "succeeded"
        delivery.sent_at = datetime(2026, 4, 30, 2, 12, 0, tzinfo=UTC)
        delivery.response_status_code = 200

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        fake_post_webhook,
    )

    seed_count = scan_and_dispatch_instance_connectivity_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC),
    )
    created_count = scan_and_dispatch_instance_connectivity_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 12, 0, tzinfo=UTC),
    )
    duplicate_count = scan_and_dispatch_instance_connectivity_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 12, 30, tzinfo=UTC),
    )

    drain_notification_outbox(db_session)
    assert seed_count == 0
    assert created_count == 1
    assert duplicate_count == 0
    assert len(sent_payloads) == 1

    deliveries = db_session.query(NotificationDelivery).all()
    assert len(deliveries) == 1
    assert deliveries[0].event_type == "instance_offline"

    payload = sent_payloads[0]
    assert payload is not None
    detail_content = payload["card"]["body"]["elements"][0]["content"]
    assert "**节点**：`vm-1`" in detail_content
    assert "**离线时间**：2026-04-30T02:11:30+00:00" in detail_content

    state = db_session.query(NotificationInstanceState).one()
    assert state.last_connectivity == "offline"
    assert state.last_transition_at == datetime(2026, 4, 30, 2, 11, 30, tzinfo=UTC)


def test_instance_connectivity_scan_sends_online_recovery_once(db_session, monkeypatch) -> None:
    _, instance = seed_runtime_service(db_session)
    instance.last_seen_at = datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC)
    seed_channel(db_session, event_types=["instance_online", "instance_offline"])
    db_session.commit()

    sent_payloads: list[dict[str, object] | None] = []

    def fake_post_webhook(delivery, *, webhook_url: str, timeout_s: float = 5.0) -> None:
        sent_payloads.append(delivery.request_payload_json)
        delivery.status = "succeeded"
        delivery.sent_at = datetime(2026, 4, 30, 2, 13, 0, tzinfo=UTC)
        delivery.response_status_code = 200

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        fake_post_webhook,
    )

    scan_and_dispatch_instance_connectivity_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC),
    )
    scan_and_dispatch_instance_connectivity_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 12, 0, tzinfo=UTC),
    )

    instance.last_seen_at = datetime(2026, 4, 30, 2, 13, 0, tzinfo=UTC)
    db_session.commit()

    created_count = scan_and_dispatch_instance_connectivity_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 13, 0, tzinfo=UTC),
    )
    duplicate_count = scan_and_dispatch_instance_connectivity_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 13, 20, tzinfo=UTC),
    )

    drain_notification_outbox(db_session)
    assert created_count == 1
    assert duplicate_count == 0
    assert len(sent_payloads) == 2

    deliveries = (
        db_session.query(NotificationDelivery).order_by(NotificationDelivery.created_at).all()
    )
    assert [delivery.event_type for delivery in deliveries] == [
        "instance_offline",
        "instance_online",
    ]

    payload = sent_payloads[-1]
    assert payload is not None
    detail_content = payload["card"]["body"]["elements"][0]["content"]
    assert "**上线时间**：2026-04-30T02:13:00+00:00" in detail_content

    state = db_session.query(NotificationInstanceState).one()
    assert state.last_connectivity == "online"
    assert state.last_transition_at == datetime(2026, 4, 30, 2, 13, 0, tzinfo=UTC)


def test_instance_connectivity_scan_updates_state_for_unsubscribed_recovery(
    db_session, monkeypatch
) -> None:
    _, instance = seed_runtime_service(db_session)
    instance.last_seen_at = datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC)
    seed_channel(db_session, event_types=["instance_offline"])
    db_session.commit()

    sent_payloads: list[dict[str, object] | None] = []

    def fake_post_webhook(delivery, *, webhook_url: str, timeout_s: float = 5.0) -> None:
        sent_payloads.append(delivery.request_payload_json)
        delivery.status = "succeeded"
        delivery.sent_at = datetime(2026, 4, 30, 2, 15, 0, tzinfo=UTC)
        delivery.response_status_code = 200

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        fake_post_webhook,
    )

    scan_and_dispatch_instance_connectivity_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 10, 0, tzinfo=UTC),
    )
    first_offline_count = scan_and_dispatch_instance_connectivity_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 12, 0, tzinfo=UTC),
    )

    instance.last_seen_at = datetime(2026, 4, 30, 2, 13, 0, tzinfo=UTC)
    db_session.commit()

    recovery_count = scan_and_dispatch_instance_connectivity_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 13, 0, tzinfo=UTC),
    )
    second_offline_count = scan_and_dispatch_instance_connectivity_notifications(
        db_session,
        now=datetime(2026, 4, 30, 2, 15, 0, tzinfo=UTC),
    )

    drain_notification_outbox(db_session)
    assert first_offline_count == 1
    assert recovery_count == 0
    assert second_offline_count == 1
    assert len(sent_payloads) == 2
    assert db_session.query(NotificationDelivery).count() == 2

    state = db_session.query(NotificationInstanceState).one()
    assert state.last_connectivity == "offline"
    assert state.last_transition_at == datetime(2026, 4, 30, 2, 14, 30, tzinfo=UTC)


# --------------------------------------------------------------------------------------
# missed-start detectability (the restart blind spot)
# --------------------------------------------------------------------------------------


def test_missed_start_is_undetectable_when_the_service_restarts_faster_than_grace() -> None:
    """Pin the interval blind-spot boundary for a non-immediate schedule.

    With ``immediate: false`` the first slot lands one interval after the online start,
    so an unbroken online run must cover that interval *plus* the whole grace period.
    Below that the check is not merely quiet, it is UNEVALUABLE -- measured directly:
    with a 300 s interval and 300 s grace, a service online 400 s yields zero candidate
    slots while one online an hour yields eleven. This test pins the boundary so a
    future refactor cannot quietly move it.
    """

    from onestep_control_plane_api.api.notification_service import _missed_start_is_detectable

    now = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)

    def detectable(online_seconds: int, *, grace: int = 300, interval: int = 300) -> bool:
        return _missed_start_is_detectable(
            now=now,
            online_started_at=now - timedelta(seconds=online_seconds),
            grace_seconds=grace,
            source_kind="interval",
            source_config={"seconds": interval, "immediate": False},
        )

    # Exactly at the boundary: a full interval AND a full grace must fit.
    assert detectable(600) is True, "grace + interval must be enough"
    # One second short: the grace period cannot elapse after a slot.
    assert detectable(599) is False
    # The pathological case from the analysis: restarts every 4 minutes.
    assert detectable(240) is False

    # A larger grace needs a correspondingly longer online run -- which is why raising
    # the grace period silently widens the blind spot.
    assert detectable(600, grace=600) is False
    assert detectable(1200, grace=600) is True


def test_missed_start_detectability_uses_grace_alone_when_immediate() -> None:
    """``immediate: true`` anchors the schedule at the online start, not one interval in.

    ``_derive_interval_anchor`` returns the anchor unchanged for immediate schedules, so
    the first slot is already due once the grace period elapses. Requiring a full extra
    interval here would mislabel a genuinely observable window as a blind spot -- and
    while the caller only logs that verdict, the boundary must still describe the scan
    it claims to describe.
    """

    from onestep_control_plane_api.api.notification_service import _missed_start_is_detectable

    now = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)

    def detectable(online_seconds: int, *, grace: int = 300, interval: int = 3600) -> bool:
        return _missed_start_is_detectable(
            now=now,
            online_started_at=now - timedelta(seconds=online_seconds),
            grace_seconds=grace,
            source_kind="interval",
            source_config={"seconds": interval, "immediate": True},
        )

    assert detectable(300) is True, "grace alone must be enough when immediate"
    assert detectable(299) is False
    # The case that used to be swallowed: an hour-long interval, service up just under
    # an hour. The slot at the online start is due after 300 s, so this is detectable.
    assert detectable(3599) is True


def test_missed_start_detectability_expands_cron_instead_of_assuming_a_period() -> None:
    """A cron schedule's period comes from its expression, never from the online start.

    ``_iter_expected_cron_slots`` never consults ``online_started_at``, so no fixed
    period may be invented for cron. The predicate asks the same generator the scan uses
    whether any already-due slot survives the post-restart guard, which keeps the
    verdict honest for every-minute and once-a-day schedules alike.
    """

    from onestep_control_plane_api.api.notification_service import _missed_start_is_detectable

    now = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)

    def detectable(online_seconds: int, expression: str) -> bool:
        return _missed_start_is_detectable(
            now=now,
            online_started_at=now - timedelta(seconds=online_seconds),
            grace_seconds=300,
            source_kind="cron",
            source_config={"expression": expression, "timezone": "UTC"},
        )

    # Every minute: the most recent due slot (11:55) is well inside a 400 s online run.
    assert detectable(400, "* * * * *") is True
    # Once a day at 03:00: with the same 400 s online run no due slot survives the guard,
    # which is a real blind spot rather than a wrong one-minute assumption.
    assert detectable(400, "0 3 * * *") is False
    # Same daily schedule, service online since before 03:00 -- now a slot is in range.
    assert detectable(10 * 3600, "0 3 * * *") is True


def test_missed_start_detectability_is_permissive_without_an_interval() -> None:
    """No interval means no period to wait out, so never suppress on this basis.

    Reporting a blind spot for an unparseable cadence would describe a config problem as
    a restart problem, so the predicate stays permissive.
    """

    from onestep_control_plane_api.api.notification_service import _missed_start_is_detectable

    now = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
    assert (
        _missed_start_is_detectable(
            now=now,
            online_started_at=now - timedelta(seconds=1),
            grace_seconds=300,
            source_kind="interval",
            source_config=None,
        )
        is True
    )
    assert (
        _missed_start_is_detectable(
            now=now,
            online_started_at=now - timedelta(seconds=1),
            grace_seconds=300,
            source_kind="interval",
            source_config={"seconds": 0, "immediate": False},
        )
        is True
    )
