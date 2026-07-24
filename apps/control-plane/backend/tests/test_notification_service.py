from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from onestep_control_plane_api.api.notification_service import (
    dispatch_runtime_task_event_notifications,
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
    db_session, monkeypatch
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
    db_session, monkeypatch
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
    db_session, monkeypatch
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

        assert created_count == 1
        assert len(sent_payloads) == 1
        detail_content = sent_payloads[0]["card"]["body"]["elements"][0]["content"]
        assert "**计划时间**：2026-04-30T10:05:00+08:00" in detail_content
        assert "**检测时间**：2026-04-30T10:10:00+08:00" in detail_content
    finally:
        settings.api_response_timezone = original_api_timezone


def test_instance_connectivity_scan_seeds_state_without_delivery(
    db_session, monkeypatch
) -> None:
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


def test_instance_connectivity_scan_sends_offline_once(
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


def test_instance_connectivity_scan_sends_online_recovery_once(
    db_session, monkeypatch
) -> None:
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

    assert created_count == 1
    assert duplicate_count == 0
    assert len(sent_payloads) == 2

    deliveries = (
        db_session.query(NotificationDelivery)
        .order_by(NotificationDelivery.created_at)
        .all()
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

    assert first_offline_count == 1
    assert recovery_count == 0
    assert second_offline_count == 1
    assert len(sent_payloads) == 2
    assert db_session.query(NotificationDelivery).count() == 2

    state = db_session.query(NotificationInstanceState).one()
    assert state.last_connectivity == "offline"
    assert state.last_transition_at == datetime(2026, 4, 30, 2, 14, 30, tzinfo=UTC)
