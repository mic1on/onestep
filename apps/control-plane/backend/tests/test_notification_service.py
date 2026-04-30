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
