from datetime import UTC, datetime
from uuid import UUID

import onestep_control_plane_api.api.common as common
import pytest
from fastapi import HTTPException
from onestep_control_plane_api.api.agent_ingestion_service import (
    ingest_events_request,
    ingest_heartbeat_request,
    ingest_metrics_request,
    ingest_sync_request,
)
from onestep_control_plane_api.api.schemas import (
    EventsIngestRequest,
    HeartbeatIngestRequest,
    MetricsIngestRequest,
    ServiceDescriptor,
    SyncIngestRequest,
)
from onestep_control_plane_api.db.models import (
    Instance,
    NotificationChannel,
    NotificationDelivery,
    Service,
    TaskCustomMetricWindow,
    TaskDefinition,
    TaskEvent,
    TaskMetricWindow,
)
from sqlalchemy import select, update

DESCRIPTION_UNSET = object()


def make_service_payload(
    instance_id: str = "8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df",
    *,
    description: str | None | object = DESCRIPTION_UNSET,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "billing-sync",
        "environment": "prod",
        "node_name": "vm-prod-3",
        "instance_id": instance_id,
        "deployment_version": "1.0.0a0+c435c99",
    }
    if description is not DESCRIPTION_UNSET:
        payload["description"] = description
    return payload


def make_heartbeat_payload(
    instance_id: str = "8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df",
    *,
    status: str = "ok",
    sequence: int = 2,
    sent_at: str = "2026-03-08T17:31:00Z",
    deployment_version: str = "1.0.0a0+c435c99",
    onestep_version: str = "1.0.0a0",
    task_controls: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "service": {
            **make_service_payload(instance_id),
            "deployment_version": deployment_version,
        },
        "runtime": {
            "onestep_version": onestep_version,
            "python_version": "3.11.14",
            "hostname": "vm-prod-3",
            "pid": 18231,
            "started_at": "2026-03-08T17:29:50Z",
        },
        "health": {
            "status": status,
            "uptime_s": 70,
            "inflight_tasks": 3,
            "task_controls": task_controls or [],
        },
        "sent_at": sent_at,
        "sequence": sequence,
    }


def make_metrics_payload(
    instance_id: str = "33fb10d0-7580-4552-b8ca-4ef55f98f844",
) -> dict[str, object]:
    return {
        "service": make_service_payload(instance_id),
        "sent_at": "2026-03-08T17:31:00Z",
        "sequence": 3,
        "window": {
            "started_at": "2026-03-08T17:30:30Z",
            "ended_at": "2026-03-08T17:31:00Z",
        },
        "tasks": [
            {
                "task_name": "sync_users",
                "window_id": "sync_users:2026-03-08T17:30:30Z:2026-03-08T17:31:00Z",
                "fetched": 120,
                "started": 120,
                "succeeded": 118,
                "retried": 1,
                "failed": 1,
                "dead_lettered": 1,
                "cancelled": 0,
                "timeouts": 1,
                "inflight": 2,
                "avg_duration_ms": 134.2,
                "p95_duration_ms": 280.0,
            }
        ],
    }


def make_events_payload(
    instance_id: str = "9d91f3e5-655b-498f-a5c8-f99c5c3b518e",
) -> dict[str, object]:
    return {
        "service": make_service_payload(instance_id),
        "sent_at": "2026-03-08T17:31:01Z",
        "sequence": 4,
        "events": [
            {
                "event_id": "evt_01JNXABCDEF",
                "kind": "failed",
                "task_name": "sync_users",
                "occurred_at": "2026-03-08T17:30:58Z",
                "attempts": 3,
                "duration_ms": 30012,
                "failure": {
                    "kind": "timeout",
                    "exception_type": "TimeoutError",
                    "message": "task exceeded timeout",
                    "traceback": (
                        "Traceback (most recent call last):\n"
                        "TimeoutError: task exceeded timeout\n"
                    ),
                },
                "meta": {"source": "interval:3600s"},
            }
        ],
    }


def make_sync_payload(
    instance_id: str = "8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df",
    topology_hash: str = "sha256:6d5b0d1f",
    tasks: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "service": make_service_payload(instance_id),
        "runtime": {
            "onestep_version": "1.0.0a0",
            "python_version": "3.11.14",
            "hostname": "vm-prod-3",
            "pid": 18231,
            "started_at": "2026-03-08T17:29:50Z",
        },
        "app": {
            "name": "billing-sync",
            "shutdown_timeout_s": 30.0,
            "topology_hash": topology_hash,
            "tasks": tasks
            or [
                {
                    "name": "sync_users",
                    "description": (
                        "Sync billing users from the source of record into the warehouse."
                    ),
                    "source": {
                        "kind": "interval",
                        "name": "interval:3600s",
                        "config": {
                            "seconds": 3600,
                            "immediate": False,
                            "overlap": "skip",
                        },
                    },
                    "emit": [
                        {
                            "kind": "mysql_table_sink",
                            "name": "mysql:dw_users",
                            "config": {
                                "table": "dw_users",
                                "mode": "upsert",
                                "keys": ["id"],
                            },
                        }
                    ],
                    "concurrency": 16,
                    "timeout_s": 30.0,
                    "retry": {
                        "kind": "max_attempts",
                        "config": {
                            "max_attempts": 5,
                            "delay_s": 10,
                        },
                    },
                },
                {
                    "name": "process_orders",
                    "description": "Consume queued orders and push downstream fulfillment updates.",
                    "source": {
                        "kind": "rabbitmq_queue",
                        "name": "rabbitmq:orders",
                        "config": {
                            "queue": "orders",
                            "prefetch": 100,
                        },
                    },
                    "emit": [],
                    "concurrency": 32,
                    "timeout_s": None,
                    "retry": {
                        "kind": "no_retry",
                        "config": {},
                    },
                },
            ],
        },
        "sent_at": "2026-03-08T17:31:05Z",
        "sequence": 5,
    }


def ingest_heartbeat(db_session, payload: dict[str, object]):
    return ingest_heartbeat_request(
        db_session,
        HeartbeatIngestRequest.model_validate(payload),
    )


def ingest_metrics(db_session, payload: dict[str, object]):
    return ingest_metrics_request(
        db_session,
        MetricsIngestRequest.model_validate(payload),
    )


def ingest_events(db_session, payload: dict[str, object]):
    return ingest_events_request(
        db_session,
        EventsIngestRequest.model_validate(payload),
    )


def ingest_sync(db_session, payload: dict[str, object]):
    return ingest_sync_request(
        db_session,
        SyncIngestRequest.model_validate(payload),
    )


def test_heartbeat_ingestion_service_creates_service_and_instance(db_session) -> None:
    response = ingest_heartbeat(db_session, make_heartbeat_payload())

    assert response.status == "accepted"
    service = db_session.scalar(
        select(Service).where(Service.name == "billing-sync", Service.environment == "prod")
    )
    instance = db_session.scalar(
        select(Instance).where(
            Instance.instance_id == UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df")
        )
    )

    assert service is not None
    assert service.latest_deployment_version == "1.0.0a0+c435c99"
    assert instance is not None
    assert instance.hostname == "vm-prod-3"
    assert instance.pid == 18231
    assert instance.node_name == "vm-prod-3"
    assert instance.status == "ok"
    assert instance.started_at == datetime(2026, 3, 8, 17, 29, 50, tzinfo=UTC)
    assert instance.last_seen_at is not None


def test_stale_heartbeat_does_not_roll_back_instance_snapshot(db_session) -> None:
    ingest_heartbeat(
        db_session,
        make_heartbeat_payload(
            status="degraded",
            sequence=5,
            sent_at="2026-03-08T17:32:00Z",
            deployment_version="1.1.0",
            onestep_version="1.1.0",
        ),
    )
    db_session.expire_all()
    first_instance = db_session.scalar(select(Instance))
    assert first_instance is not None
    latest_seen = first_instance.last_seen_at

    ingest_heartbeat(db_session, make_heartbeat_payload())
    db_session.expire_all()

    service = db_session.scalar(select(Service))
    instance = db_session.scalar(select(Instance))
    assert service is not None
    assert service.latest_deployment_version == "1.1.0"
    assert instance is not None
    assert instance.status == "degraded"
    assert instance.deployment_version == "1.1.0"
    assert instance.onestep_version == "1.1.0"
    assert instance.last_heartbeat_sequence == 5
    assert instance.last_heartbeat_sent_at == datetime(2026, 3, 8, 17, 32, tzinfo=UTC)
    assert instance.last_seen_at == latest_seen


def test_heartbeat_task_controls_merge_into_instance_snapshot(db_session) -> None:
    ingest_sync(db_session, make_sync_payload())
    ingest_heartbeat(
        db_session,
        make_heartbeat_payload(
            task_controls=[
                {
                    "task_name": "sync_users",
                    "pause_requested": True,
                    "paused": True,
                    "accepting_new_work": False,
                    "runner_count": 2,
                    "parked_runner_count": 2,
                    "fetching_runner_count": 0,
                    "inflight_task_count": 0,
                }
            ]
        ),
    )
    db_session.expire_all()

    instance = db_session.scalar(select(Instance))
    assert instance is not None
    assert instance.app_snapshot_json is not None
    assert instance.app_snapshot_json["topology_hash"] == "sha256:6d5b0d1f"
    assert instance.app_snapshot_json["task_control_states"] == [
        {
            "task_name": "sync_users",
            "supported_commands": [],
            "pause_requested": True,
            "paused": True,
            "accepting_new_work": False,
            "runner_count": 2,
            "parked_runner_count": 2,
            "fetching_runner_count": 0,
            "inflight_task_count": 0,
        }
    ]


def test_sync_drops_task_control_states_for_removed_tasks(db_session) -> None:
    ingest_sync(db_session, make_sync_payload())
    ingest_heartbeat(
        db_session,
        make_heartbeat_payload(
            task_controls=[
                {
                    "task_name": "sync_users",
                    "pause_requested": True,
                    "paused": True,
                    "accepting_new_work": False,
                    "runner_count": 2,
                    "parked_runner_count": 2,
                    "fetching_runner_count": 0,
                    "inflight_task_count": 0,
                }
            ]
        ),
    )

    renamed_payload = make_sync_payload(
        topology_hash="sha256:renamed-topology",
        tasks=[{"name": "sync_accounts"}],
    )
    renamed_payload["sent_at"] = "2026-03-08T17:31:06Z"
    renamed_payload["sequence"] = 6
    ingest_sync(db_session, renamed_payload)
    db_session.expire_all()

    instance = db_session.scalar(select(Instance))
    task_definitions = db_session.scalars(select(TaskDefinition)).all()

    assert instance is not None
    assert instance.app_snapshot_json is not None
    assert instance.app_snapshot_json["topology_hash"] == "sha256:renamed-topology"
    assert instance.app_snapshot_json["task_control_states"] == []
    assert [task_definition.task_name for task_definition in task_definitions] == [
        "sync_accounts"
    ]


def test_conflicting_service_cannot_reuse_existing_instance_id(db_session) -> None:
    ingest_heartbeat(
        db_session,
        {
            **make_heartbeat_payload(),
            "service": {
                **make_service_payload(),
                "name": "svc-a",
            },
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        ingest_metrics(
            db_session,
            {
                **make_metrics_payload("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df"),
                "service": {
                    **make_service_payload(),
                    "name": "svc-b",
                },
            },
        )

    assert exc_info.value.status_code == 409
    assert "instance_id is already bound" in str(exc_info.value.detail)


def test_metrics_ingestion_is_idempotent_without_prior_heartbeat(db_session) -> None:
    first = ingest_metrics(db_session, make_metrics_payload())
    second = ingest_metrics(db_session, make_metrics_payload())

    instance = db_session.scalar(
        select(Instance).where(
            Instance.instance_id == UUID("33fb10d0-7580-4552-b8ca-4ef55f98f844")
        )
    )
    metric_windows = db_session.scalars(select(TaskMetricWindow)).all()

    assert first.ingested_count == 1
    assert second.ingested_count == 0
    assert instance is not None
    assert instance.status == "unknown"
    assert len(metric_windows) == 1
    assert metric_windows[0].task_name == "sync_users"
    assert metric_windows[0].succeeded == 118


def test_metrics_ingestion_persists_custom_metrics_idempotently(db_session) -> None:
    payload = make_metrics_payload()
    payload["tasks"][0]["custom_metrics"] = [
        {
            "name": "rows_success",
            "kind": "counter",
            "value": 118,
            "labels": {},
        },
        {
            "name": "rows_failed",
            "kind": "counter",
            "value": 2,
            "labels": {"reason": "validation"},
        },
        {
            "name": "batch_size",
            "kind": "gauge",
            "value": 120,
            "labels": {},
        },
    ]

    first = ingest_metrics(db_session, payload)
    second = ingest_metrics(db_session, payload)

    custom_metrics = db_session.scalars(
        select(TaskCustomMetricWindow).order_by(TaskCustomMetricWindow.metric_name)
    ).all()
    assert first.ingested_count == 1
    assert second.ingested_count == 0
    assert len(custom_metrics) == 3
    by_name = {metric.metric_name: metric for metric in custom_metrics}
    assert by_name["rows_success"].metric_kind == "counter"
    assert by_name["rows_success"].metric_value == 118
    assert by_name["rows_success"].labels_json == {}
    assert by_name["rows_failed"].labels_json == {"reason": "validation"}
    assert len(by_name["rows_failed"].labels_hash) == 64
    assert by_name["batch_size"].metric_kind == "gauge"
    assert by_name["batch_size"].metric_value == 120


def test_metrics_ingestion_keeps_custom_metric_kinds_idempotent(db_session) -> None:
    payload = make_metrics_payload()
    payload["tasks"][0]["custom_metrics"] = [
        {
            "name": "shared_metric",
            "kind": "counter",
            "value": 5,
            "labels": {"tenant": "acme"},
        },
        {
            "name": "shared_metric",
            "kind": "gauge",
            "value": 9,
            "labels": {"tenant": "acme"},
        },
    ]

    first = ingest_metrics(db_session, payload)
    second = ingest_metrics(db_session, payload)

    custom_metrics = db_session.scalars(
        select(TaskCustomMetricWindow).order_by(TaskCustomMetricWindow.metric_kind)
    ).all()
    assert first.ingested_count == 1
    assert second.ingested_count == 0
    assert len(custom_metrics) == 2
    by_kind = {metric.metric_kind: metric for metric in custom_metrics}
    assert by_kind["counter"].metric_name == "shared_metric"
    assert by_kind["counter"].metric_value == 5
    assert by_kind["gauge"].metric_name == "shared_metric"
    assert by_kind["gauge"].metric_value == 9


def test_metrics_ingestion_rejects_invalid_custom_metric_labels(db_session) -> None:
    payload = make_metrics_payload()
    payload["tasks"][0]["custom_metrics"] = [
        {
            "name": "rows_success",
            "kind": "counter",
            "value": 1,
            "labels": {"service": "billing-sync"},
        }
    ]

    with pytest.raises(ValueError, match="reserved"):
        ingest_metrics(db_session, payload)


def test_events_ingestion_is_idempotent(db_session) -> None:
    first = ingest_events(db_session, make_events_payload())
    second = ingest_events(db_session, make_events_payload())

    events = db_session.scalars(select(TaskEvent)).all()
    assert first.ingested_count == 1
    assert second.ingested_count == 0
    assert len(events) == 1
    assert events[0].event_id == "evt_01JNXABCDEF"
    assert events[0].failure_kind == "timeout"
    assert events[0].message == "task exceeded timeout"


def test_sync_ingestion_creates_service_instance_and_task_definitions(db_session) -> None:
    response = ingest_sync(db_session, make_sync_payload())

    assert response.service_name == "billing-sync"
    assert response.topology_hash == "sha256:6d5b0d1f"
    assert response.task_count == 2

    service = db_session.scalar(
        select(Service).where(Service.name == "billing-sync", Service.environment == "prod")
    )
    instance = db_session.scalar(
        select(Instance).where(
            Instance.instance_id == UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df")
        )
    )
    task_definitions = db_session.scalars(
        select(TaskDefinition).order_by(TaskDefinition.task_name)
    ).all()

    assert service is not None
    assert service.latest_topology_hash == "sha256:6d5b0d1f"
    assert instance is not None
    assert instance.last_topology_hash == "sha256:6d5b0d1f"
    assert instance.app_snapshot_json is not None
    assert [task_definition.task_name for task_definition in task_definitions] == [
        "process_orders",
        "sync_users",
    ]


def test_sync_ingestion_persists_service_description(db_session) -> None:
    payload = make_sync_payload()
    payload["service"] = make_service_payload(
        description="  Reconciles billing data into the warehouse.  "
    )

    ingest_sync(db_session, payload)

    service = db_session.scalar(select(Service))
    assert service is not None
    assert service.description == "Reconciles billing data into the warehouse."


def test_newer_heartbeat_persists_service_description(db_session) -> None:
    payload = make_heartbeat_payload()
    payload["service"] = make_service_payload(
        description="Processes billing health checks."
    )

    ingest_heartbeat(db_session, payload)

    service = db_session.scalar(select(Service))
    assert service is not None
    assert service.description == "Processes billing health checks."


def test_missing_service_description_does_not_clear_existing_value(db_session) -> None:
    ingest_sync(db_session, make_sync_payload())
    service = db_session.scalar(select(Service))
    assert service is not None
    service.description = "Existing description"
    db_session.commit()

    payload = make_sync_payload()
    payload["sent_at"] = "2026-03-08T17:31:06Z"
    payload["sequence"] = 6
    ingest_sync(db_session, payload)

    db_session.refresh(service)
    assert service.description == "Existing description"


def test_explicit_null_service_description_clears_existing_value(db_session) -> None:
    ingest_sync(db_session, make_sync_payload())
    service = db_session.scalar(select(Service))
    assert service is not None
    service.description = "Existing description"
    db_session.commit()

    payload = make_sync_payload()
    payload["service"] = make_service_payload(description=None)
    payload["sent_at"] = "2026-03-08T17:31:06Z"
    payload["sequence"] = 6
    ingest_sync(db_session, payload)

    db_session.refresh(service)
    assert service.description is None


def test_blank_service_description_clears_existing_value(db_session) -> None:
    ingest_sync(db_session, make_sync_payload())
    service = db_session.scalar(select(Service))
    assert service is not None
    service.description = "Existing description"
    db_session.commit()

    payload = make_sync_payload()
    payload["service"] = make_service_payload(description="   ")
    payload["sent_at"] = "2026-03-08T17:31:06Z"
    payload["sequence"] = 6
    ingest_sync(db_session, payload)

    db_session.refresh(service)
    assert service.description is None


def test_metrics_and_events_do_not_update_service_description(db_session) -> None:
    payload = make_sync_payload()
    payload["service"] = make_service_payload(description="Original description")
    ingest_sync(db_session, payload)

    metrics_payload = make_metrics_payload()
    metrics_payload["service"] = make_service_payload(
        "33fb10d0-7580-4552-b8ca-4ef55f98f844",
        description="Metrics should not update metadata",
    )
    ingest_metrics(db_session, metrics_payload)

    events_payload = make_events_payload()
    events_payload["service"] = make_service_payload(
        "9d91f3e5-655b-498f-a5c8-f99c5c3b518e",
        description=None,
    )
    ingest_events(db_session, events_payload)

    service = db_session.scalar(select(Service))
    assert service is not None
    assert service.description == "Original description"


def test_sync_refreshes_task_definitions_for_newer_payload_with_same_topology_hash(
    db_session,
) -> None:
    ingest_sync(db_session, make_sync_payload())
    service = db_session.scalar(
        select(Service).where(Service.name == "billing-sync", Service.environment == "prod")
    )
    assert service is not None

    db_session.execute(
        update(TaskDefinition)
        .where(
            TaskDefinition.service_id == service.id,
            TaskDefinition.task_name == "sync_users",
        )
        .values(description=None)
    )
    db_session.commit()

    refreshed_payload = make_sync_payload()
    refreshed_payload["sent_at"] = "2026-03-08T17:31:06Z"
    refreshed_payload["sequence"] = 6
    ingest_sync(db_session, refreshed_payload)
    db_session.expire_all()

    sync_users = db_session.scalar(
        select(TaskDefinition).where(
            TaskDefinition.service_id == service.id,
            TaskDefinition.task_name == "sync_users",
        )
    )
    instance = db_session.scalar(select(Instance))

    assert sync_users is not None
    assert (
        sync_users.description
        == "Sync billing users from the source of record into the warehouse."
    )
    assert instance is not None
    assert instance.last_sync_sequence == 6
    assert instance.last_sync_sent_at == datetime(2026, 3, 8, 17, 31, 6, tzinfo=UTC)


def test_sync_out_of_order_payload_does_not_rollback_instance_snapshot(db_session) -> None:
    newest_payload = make_sync_payload(
        topology_hash="sha256:new-topology",
        tasks=[
            {
                "name": "sync_users",
                "description": "Sync users with the faster retry profile.",
                "source": {
                    "kind": "interval",
                    "name": "interval:1800s",
                    "config": {"seconds": 1800, "immediate": True},
                },
                "emit": [],
                "concurrency": 64,
                "timeout_s": 45.0,
                "retry": {
                    "kind": "max_attempts",
                    "config": {"max_attempts": 3, "delay_s": 5},
                },
            }
        ],
    )
    newest_payload["service"]["deployment_version"] = "1.0.0a1+c999999"
    newest_payload["runtime"]["onestep_version"] = "1.0.0a1"
    newest_payload["runtime"]["hostname"] = "vm-prod-4"
    newest_payload["sent_at"] = "2026-03-08T17:31:06Z"
    newest_payload["sequence"] = 6

    ingest_sync(db_session, newest_payload)
    db_session.expire_all()
    first_service = db_session.scalar(select(Service))
    first_instance = db_session.scalar(select(Instance))
    assert first_service is not None
    assert first_instance is not None
    first_service_sync_at = first_service.latest_sync_at
    first_instance_sync_at = first_instance.last_sync_at

    ingest_sync(db_session, make_sync_payload())
    db_session.expire_all()

    updated_service = db_session.scalar(select(Service))
    updated_instance = db_session.scalar(select(Instance))
    task_definitions = db_session.scalars(select(TaskDefinition)).all()

    assert updated_service is not None
    assert updated_service.latest_deployment_version == "1.0.0a1+c999999"
    assert updated_service.latest_topology_hash == "sha256:new-topology"
    assert updated_service.latest_sync_at == first_service_sync_at
    assert updated_instance is not None
    assert updated_instance.hostname == "vm-prod-4"
    assert updated_instance.last_topology_hash == "sha256:new-topology"
    assert updated_instance.last_sync_at == first_instance_sync_at
    assert updated_instance.last_sync_sequence == 6
    assert len(task_definitions) == 1
    assert task_definitions[0].description == "Sync users with the faster retry profile."


def test_sync_then_query_tasks_returns_topology_data(client, db_session) -> None:
    ingest_sync(db_session, make_sync_payload())

    response = client.get("/api/v1/services/billing-sync/tasks", params={"environment": "prod"})
    payload = response.json()
    by_task_name = {item["task_name"]: item for item in payload["items"]}

    assert response.status_code == 200
    assert (
        by_task_name["sync_users"]["description"]
        == "Sync billing users from the source of record into the warehouse."
    )
    assert by_task_name["sync_users"]["source_name"] == "interval:3600s"
    assert by_task_name["sync_users"]["source_kind"] == "interval"
    assert by_task_name["sync_users"]["topology_hash"] == "sha256:6d5b0d1f"


def test_sync_accepts_http_sink_emit_for_handlerless_task(client, db_session) -> None:
    ingest_sync(
        db_session,
        make_sync_payload(
            tasks=[
                {
                    "name": "forward_webhook_event",
                    "description": "Forward accepted payloads to the webhook endpoint.",
                    "source": None,
                    "emit": [
                        {
                            "kind": "http_sink",
                            "name": "webhook:intake",
                            "config": {
                                "url": "https://example.test/hooks/intake",
                                "method": "POST",
                            },
                        }
                    ],
                }
            ],
        ),
    )

    task_definition = db_session.scalar(select(TaskDefinition))
    assert task_definition is not None
    assert task_definition.source_name is None
    assert task_definition.source_kind is None
    assert task_definition.emit_json == [
        {
            "kind": "http_sink",
            "name": "webhook:intake",
            "config": {
                "url": "https://example.test/hooks/intake",
                "method": "POST",
            },
        }
    ]

    response = client.get("/api/v1/services/billing-sync/tasks", params={"environment": "prod"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["emit"] == [
        {
            "kind": "http_sink",
            "name": "webhook:intake",
            "config": {
                "url": "https://example.test/hooks/intake",
                "method": "POST",
            },
        }
    ]


def test_ensure_service_recovers_when_row_appears_after_initial_lookup(
    monkeypatch,
    db_session,
) -> None:
    identity = ServiceDescriptor.model_validate(make_service_payload())
    existing = Service(
        name=identity.name,
        environment=identity.environment,
        latest_deployment_version="0.9.0",
    )
    db_session.add(existing)
    db_session.commit()

    original_get_service = common.get_service
    calls = {"count": 0}

    def flaky_get_service(db, service_identity):
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return original_get_service(db, service_identity)

    monkeypatch.setattr(common, "get_service", flaky_get_service)
    service = common.ensure_service(db_session, identity, update_existing_version=True)

    assert service.id == existing.id
    assert service.latest_deployment_version == identity.deployment_version


def test_ensure_instance_stub_recovers_when_row_appears_after_initial_lookup(
    monkeypatch,
    db_session,
) -> None:
    identity = ServiceDescriptor.model_validate(make_service_payload())
    service = Service(
        name=identity.name,
        environment=identity.environment,
        latest_deployment_version=identity.deployment_version,
    )
    db_session.add(service)
    db_session.flush()

    existing = Instance(
        service=service,
        instance_id=identity.instance_id,
        node_name=identity.node_name,
        deployment_version=identity.deployment_version,
        status="unknown",
    )
    db_session.add(existing)
    db_session.commit()

    original_get_instance = common.get_instance
    calls = {"count": 0}

    def flaky_get_instance(db, instance_id):
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return original_get_instance(db, instance_id)

    monkeypatch.setattr(common, "get_instance", flaky_get_instance)
    instance = common.ensure_instance_stub(db_session, service=service, identity=identity)

    assert instance.id == existing.id
    assert instance.instance_id == identity.instance_id


def test_http_ingestion_routes_are_not_exposed_and_absent_from_openapi(client) -> None:
    assert client.post("/api/v1/agents/heartbeat", json=make_heartbeat_payload()).status_code == 404
    assert client.post("/api/v1/agents/sync", json=make_sync_payload()).status_code == 404
    assert client.post("/api/v1/agents/metrics", json=make_metrics_payload()).status_code == 404
    assert client.post("/api/v1/agents/events", json=make_events_payload()).status_code == 404

    payload = client.get("/openapi.json").json()
    assert "/api/v1/agents/heartbeat" not in payload["paths"]
    assert "/api/v1/agents/sync" not in payload["paths"]
    assert "/api/v1/agents/metrics" not in payload["paths"]
    assert "/api/v1/agents/events" not in payload["paths"]


def test_ingest_events_creates_notification_delivery_only_for_new_events(
    db_session, monkeypatch
) -> None:
    channel = NotificationChannel(
        name="ops-feishu",
        provider="feishu",
        webhook_url="https://example.com/hook",
        enabled=True,
        service_scopes_json=[{"name": "billing-sync", "environment": "prod"}],
        event_types_json=["task_failed"],
        missed_start_grace_seconds=300,
    )
    db_session.add(channel)
    db_session.commit()

    def fake_post_webhook(delivery, *, webhook_url: str, timeout_s: float = 5.0) -> None:
        delivery.status = "succeeded"
        delivery.sent_at = datetime(2026, 3, 8, 17, 31, 2, tzinfo=UTC)
        delivery.response_status_code = 200

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        fake_post_webhook,
    )

    first_response = ingest_events(db_session, make_events_payload())
    assert first_response.ingested_count == 1
    deliveries = db_session.scalars(select(NotificationDelivery)).all()
    assert len(deliveries) == 1
    assert deliveries[0].event_type == "task_failed"

    second_response = ingest_events(db_session, make_events_payload())
    assert second_response.ingested_count == 0
    deliveries = db_session.scalars(select(NotificationDelivery)).all()
    assert len(deliveries) == 1
