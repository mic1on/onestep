from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from onestep_control_plane_api.api.notification_service import (
    dispatch_runtime_task_event_notifications,
    scan_and_dispatch_missed_start_notifications,
)
from onestep_control_plane_api.db.models import (
    Instance,
    NotificationChannel,
    NotificationDelivery,
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

    created_count = dispatch_runtime_task_event_notifications(db_session, task_events=[task_event])
    assert created_count == 1
    assert len(sent_payloads) == 1
    deliveries = db_session.query(NotificationDelivery).all()
    assert len(deliveries) == 1
    assert deliveries[0].event_type == "task_failed"
    assert deliveries[0].status == "succeeded"

    duplicate_count = dispatch_runtime_task_event_notifications(db_session, task_events=[task_event])
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
    content = payload["card"]["elements"][0]["text"]["content"]
    assert "摘要: 状态同步完成，更新了 12 个设备状态" in content
    assert "总设备数: 100" in content
    assert "已检查: 100" in content
    assert "需更新: 20" in content
    assert "已更新: 12" in content


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
    content = payload["card"]["elements"][0]["text"]["content"]
    assert "摘要:" not in content
    assert "总设备数:" not in content
    assert "已更新: 12" in content


def test_scan_and_dispatch_missed_start_notifications_creates_single_delivery(
    db_session, monkeypatch
) -> None:
    service, _instance = seed_runtime_service(db_session)
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


def test_scan_and_dispatch_missed_start_notifications_skips_interval_started_slot_without_scheduled_at(
    db_session, monkeypatch
) -> None:
    service, instance = seed_runtime_service(db_session)
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


def test_scan_and_dispatch_missed_start_notifications_skips_interval_started_slot_with_nearby_scheduled_at(
    db_session, monkeypatch
) -> None:
    service, instance = seed_runtime_service(db_session)
    service.latest_sync_at = datetime(2026, 5, 6, 2, 41, 22, 819707, tzinfo=UTC)
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


def test_scan_and_dispatch_missed_start_notifications_keeps_interval_missed_start_when_no_started_event(
    db_session, monkeypatch
) -> None:
    service, _instance = seed_runtime_service(db_session)
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
