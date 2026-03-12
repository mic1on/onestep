from datetime import UTC, datetime
from uuid import UUID

import onestep_control_plane_api.api.common as common
from onestep_control_plane_api.api.schemas import ServiceDescriptor
from onestep_control_plane_api.db.models import (
    Instance,
    Service,
    TaskDefinition,
    TaskEvent,
    TaskMetricWindow,
)
from sqlalchemy import select


def make_service_payload(
    instance_id: str = "8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df",
) -> dict[str, str]:
    return {
        "name": "billing-sync",
        "environment": "prod",
        "node_name": "vm-prod-3",
        "instance_id": instance_id,
        "deployment_version": "1.0.0a0+c435c99",
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


def test_heartbeat_implicitly_creates_service_and_instance(
    client,
    auth_headers,
    db_session,
) -> None:
    response = client.post(
        "/api/v1/agents/heartbeat",
        headers=auth_headers,
        json={
            "service": make_service_payload(),
            "runtime": {
                "onestep_version": "1.0.0a0",
                "python_version": "3.11.14",
                "hostname": "vm-prod-3",
                "pid": 18231,
                "started_at": "2026-03-08T17:29:50Z",
            },
            "health": {
                "status": "ok",
                "uptime_s": 70,
                "inflight_tasks": 3,
            },
            "sent_at": "2026-03-08T17:31:00Z",
            "sequence": 2,
        },
    )

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"

    service = db_session.scalar(
        select(Service).where(Service.name == "billing-sync", Service.environment == "prod")
    )
    assert service is not None
    assert service.latest_deployment_version == "1.0.0a0+c435c99"

    instance = db_session.scalar(
        select(Instance).where(
            Instance.instance_id == UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df")
        )
    )
    assert instance is not None
    assert instance.service_id == service.id
    assert instance.hostname == "vm-prod-3"
    assert instance.pid == 18231
    assert instance.node_name == "vm-prod-3"
    assert instance.status == "ok"
    assert instance.started_at == datetime(2026, 3, 8, 17, 29, 50, tzinfo=UTC)
    assert instance.last_seen_at is not None


def test_heartbeat_updates_last_seen_at_for_existing_instance(
    client,
    auth_headers,
    db_session,
) -> None:
    first = client.post(
        "/api/v1/agents/heartbeat",
        headers=auth_headers,
        json={
            "service": make_service_payload(),
            "runtime": {
                "onestep_version": "1.0.0a0",
                "python_version": "3.11.14",
                "hostname": "vm-prod-3",
                "pid": 18231,
                "started_at": "2026-03-08T17:29:50Z",
            },
            "health": {
                "status": "ok",
                "uptime_s": 70,
                "inflight_tasks": 3,
            },
            "sent_at": "2026-03-08T17:31:00Z",
            "sequence": 2,
        },
    )
    assert first.status_code == 202

    instance = db_session.scalar(
        select(Instance).where(
            Instance.instance_id == UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df")
        )
    )
    assert instance is not None
    first_seen = instance.last_seen_at

    second = client.post(
        "/api/v1/agents/heartbeat",
        headers=auth_headers,
        json={
            "service": make_service_payload(),
            "runtime": {
                "onestep_version": "1.0.0a1",
                "python_version": "3.11.14",
                "hostname": "vm-prod-3",
                "pid": 18231,
                "started_at": "2026-03-08T17:29:50Z",
            },
            "health": {
                "status": "degraded",
                "uptime_s": 130,
                "inflight_tasks": 6,
            },
            "sent_at": "2026-03-08T17:32:00Z",
            "sequence": 3,
        },
    )

    assert second.status_code == 202

    db_session.expire_all()
    updated_instance = db_session.scalar(
        select(Instance).where(
            Instance.instance_id == UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df")
        )
    )
    assert updated_instance is not None
    assert updated_instance.last_seen_at is not None
    assert first_seen is not None
    assert updated_instance.last_seen_at > first_seen
    assert updated_instance.status == "degraded"
    assert updated_instance.onestep_version == "1.0.0a1"
    assert updated_instance.last_heartbeat_sequence == 3
    assert updated_instance.last_heartbeat_sent_at == datetime(2026, 3, 8, 17, 32, tzinfo=UTC)


def test_stale_heartbeat_does_not_roll_back_instance_snapshot(
    client,
    auth_headers,
    db_session,
) -> None:
    newest = client.post(
        "/api/v1/agents/heartbeat",
        headers=auth_headers,
        json={
            "service": {
                **make_service_payload(),
                "deployment_version": "1.1.0",
            },
            "runtime": {
                "onestep_version": "1.1.0",
                "python_version": "3.11.14",
                "hostname": "vm-prod-3",
                "pid": 18231,
                "started_at": "2026-03-08T17:29:50Z",
            },
            "health": {
                "status": "degraded",
                "uptime_s": 130,
                "inflight_tasks": 6,
            },
            "sent_at": "2026-03-08T17:32:00Z",
            "sequence": 5,
        },
    )
    assert newest.status_code == 202

    db_session.expire_all()
    after_newest = db_session.scalar(
        select(Instance).where(
            Instance.instance_id == UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df")
        )
    )
    assert after_newest is not None
    latest_seen = after_newest.last_seen_at

    stale = client.post(
        "/api/v1/agents/heartbeat",
        headers=auth_headers,
        json={
            "service": make_service_payload(),
            "runtime": {
                "onestep_version": "1.0.0",
                "python_version": "3.11.14",
                "hostname": "vm-prod-3",
                "pid": 18231,
                "started_at": "2026-03-08T17:29:50Z",
            },
            "health": {
                "status": "ok",
                "uptime_s": 70,
                "inflight_tasks": 3,
            },
            "sent_at": "2026-03-08T17:31:00Z",
            "sequence": 4,
        },
    )
    assert stale.status_code == 202

    db_session.expire_all()
    service = db_session.scalar(
        select(Service).where(Service.name == "billing-sync", Service.environment == "prod")
    )
    instance = db_session.scalar(
        select(Instance).where(
            Instance.instance_id == UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df")
        )
    )
    assert service is not None
    assert service.latest_deployment_version == "1.1.0"
    assert instance is not None
    assert instance.status == "degraded"
    assert instance.deployment_version == "1.1.0"
    assert instance.onestep_version == "1.1.0"
    assert instance.last_heartbeat_sequence == 5
    assert instance.last_heartbeat_sent_at == datetime(2026, 3, 8, 17, 32, tzinfo=UTC)
    assert instance.last_seen_at == latest_seen


def test_conflicting_service_cannot_reuse_existing_instance_id(
    client,
    auth_headers,
    db_session,
) -> None:
    first = client.post(
        "/api/v1/agents/heartbeat",
        headers=auth_headers,
        json={
            "service": {
                **make_service_payload(),
                "name": "svc-a",
            },
            "runtime": {
                "onestep_version": "1.0.0",
                "python_version": "3.11.14",
                "hostname": "vm-prod-3",
                "pid": 18231,
                "started_at": "2026-03-08T17:29:50Z",
            },
            "health": {
                "status": "ok",
                "uptime_s": 70,
                "inflight_tasks": 3,
            },
            "sent_at": "2026-03-08T17:31:00Z",
            "sequence": 1,
        },
    )
    assert first.status_code == 202

    conflict = client.post(
        "/api/v1/agents/heartbeat",
        headers=auth_headers,
        json={
            "service": {
                **make_service_payload(),
                "name": "svc-b",
            },
            "runtime": {
                "onestep_version": "1.1.0",
                "python_version": "3.11.14",
                "hostname": "vm-prod-3",
                "pid": 18231,
                "started_at": "2026-03-08T17:29:50Z",
            },
            "health": {
                "status": "degraded",
                "uptime_s": 130,
                "inflight_tasks": 6,
            },
            "sent_at": "2026-03-08T17:32:00Z",
            "sequence": 2,
        },
    )

    assert conflict.status_code == 409
    assert "instance_id is already bound" in conflict.json()["detail"]

    db_session.expire_all()
    services = db_session.scalars(select(Service).order_by(Service.name)).all()
    assert [service.name for service in services] == ["svc-a"]

    instance = db_session.scalar(
        select(Instance).where(
            Instance.instance_id == UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df")
        )
    )
    assert instance is not None
    assert instance.service.name == "svc-a"


def test_metrics_do_not_require_prior_heartbeat(client, auth_headers, db_session) -> None:
    response = client.post(
        "/api/v1/agents/metrics",
        headers=auth_headers,
        json={
            "service": make_service_payload("33fb10d0-7580-4552-b8ca-4ef55f98f844"),
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
        },
    )

    assert response.status_code == 202
    assert response.json()["ingested_count"] == 1

    service = db_session.scalar(
        select(Service).where(Service.name == "billing-sync", Service.environment == "prod")
    )
    assert service is not None

    instance = db_session.scalar(
        select(Instance).where(
            Instance.instance_id == UUID("33fb10d0-7580-4552-b8ca-4ef55f98f844")
        )
    )
    assert instance is not None
    assert instance.service_id == service.id
    assert instance.status == "unknown"
    assert instance.last_seen_at is None

    metric_window = db_session.scalar(select(TaskMetricWindow))
    assert metric_window is not None
    assert metric_window.task_name == "sync_users"
    assert metric_window.succeeded == 118
    assert metric_window.instance_id == UUID("33fb10d0-7580-4552-b8ca-4ef55f98f844")


def test_sync_implicitly_creates_service_instance_and_task_definitions(
    client,
    auth_headers,
    db_session,
) -> None:
    response = client.post("/api/v1/agents/sync", headers=auth_headers, json=make_sync_payload())

    assert response.status_code == 202
    assert response.json()["service_name"] == "billing-sync"
    assert response.json()["topology_hash"] == "sha256:6d5b0d1f"
    assert response.json()["task_count"] == 2

    service = db_session.scalar(
        select(Service).where(Service.name == "billing-sync", Service.environment == "prod")
    )
    assert service is not None
    assert service.latest_topology_hash == "sha256:6d5b0d1f"
    assert service.latest_sync_at is not None

    instance = db_session.scalar(
        select(Instance).where(
            Instance.instance_id == UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df")
        )
    )
    assert instance is not None
    assert instance.service_id == service.id
    assert instance.last_sync_at is not None
    assert instance.last_topology_hash == "sha256:6d5b0d1f"
    assert instance.app_snapshot_json is not None
    assert instance.app_snapshot_json["topology_hash"] == "sha256:6d5b0d1f"
    assert instance.status == "unknown"
    assert instance.last_seen_at is None

    task_definitions = db_session.scalars(
        select(TaskDefinition).order_by(TaskDefinition.task_name)
    ).all()
    assert [task_definition.task_name for task_definition in task_definitions] == [
        "process_orders",
        "sync_users",
    ]
    sync_users = next(
        task_definition
        for task_definition in task_definitions
        if task_definition.task_name == "sync_users"
    )
    assert (
        sync_users.description
        == "Sync billing users from the source of record into the warehouse."
    )
    assert sync_users.source_kind == "interval"
    assert sync_users.source_config_json == {
        "seconds": 3600,
        "immediate": False,
        "overlap": "skip",
    }
    assert sync_users.emit_json == [
        {
            "kind": "mysql_table_sink",
            "name": "mysql:dw_users",
            "config": {"table": "dw_users", "mode": "upsert", "keys": ["id"]},
        }
    ]
    assert sync_users.concurrency == 16
    assert sync_users.timeout_s == 30.0
    assert sync_users.retry_policy == {
        "kind": "max_attempts",
        "config": {"max_attempts": 5, "delay_s": 10},
    }


def test_sync_retry_with_same_instance_and_topology_hash_is_idempotent(
    client,
    auth_headers,
    db_session,
) -> None:
    payload = make_sync_payload()

    first = client.post("/api/v1/agents/sync", headers=auth_headers, json=payload)
    assert first.status_code == 202

    task_definition_timestamps = {
        task_definition.task_name: task_definition.updated_at
        for task_definition in db_session.scalars(select(TaskDefinition)).all()
    }
    instance = db_session.scalar(
        select(Instance).where(
            Instance.instance_id == UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df")
        )
    )
    assert instance is not None
    first_last_sync_at = instance.last_sync_at

    second = client.post("/api/v1/agents/sync", headers=auth_headers, json=payload)
    assert second.status_code == 202

    db_session.expire_all()
    task_definitions = db_session.scalars(
        select(TaskDefinition).order_by(TaskDefinition.task_name)
    ).all()
    assert len(task_definitions) == 2
    assert {
        task_definition.task_name: task_definition.updated_at
        for task_definition in task_definitions
    } == task_definition_timestamps

    updated_instance = db_session.scalar(
        select(Instance).where(
            Instance.instance_id == UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df")
        )
    )
    assert updated_instance is not None
    assert updated_instance.last_sync_at is not None
    assert first_last_sync_at is not None
    assert updated_instance.last_sync_at >= first_last_sync_at


def test_sync_out_of_order_payload_does_not_rollback_instance_snapshot(
    client,
    auth_headers,
    db_session,
) -> None:
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

    first = client.post("/api/v1/agents/sync", headers=auth_headers, json=newest_payload)
    assert first.status_code == 202

    service = db_session.scalar(
        select(Service).where(Service.name == "billing-sync", Service.environment == "prod")
    )
    instance = db_session.scalar(
        select(Instance).where(
            Instance.instance_id == UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df")
        )
    )
    assert service is not None
    assert instance is not None
    first_service_sync_at = service.latest_sync_at
    first_instance_sync_at = instance.last_sync_at

    stale = client.post("/api/v1/agents/sync", headers=auth_headers, json=make_sync_payload())
    assert stale.status_code == 202

    db_session.expire_all()
    updated_service = db_session.scalar(
        select(Service).where(Service.name == "billing-sync", Service.environment == "prod")
    )
    updated_instance = db_session.scalar(
        select(Instance).where(
            Instance.instance_id == UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df")
        )
    )
    task_definitions = db_session.scalars(
        select(TaskDefinition).order_by(TaskDefinition.task_name)
    ).all()

    assert updated_service is not None
    assert updated_service.latest_deployment_version == "1.0.0a1+c999999"
    assert updated_service.latest_topology_hash == "sha256:new-topology"
    assert updated_service.latest_sync_at == first_service_sync_at

    assert updated_instance is not None
    assert updated_instance.deployment_version == "1.0.0a1+c999999"
    assert updated_instance.onestep_version == "1.0.0a1"
    assert updated_instance.hostname == "vm-prod-4"
    assert updated_instance.last_topology_hash == "sha256:new-topology"
    assert updated_instance.last_sync_at == first_instance_sync_at
    assert updated_instance.last_sync_sequence == 6
    assert updated_instance.last_sync_sent_at == datetime(2026, 3, 8, 17, 31, 6, tzinfo=UTC)

    assert [task_definition.task_name for task_definition in task_definitions] == ["sync_users"]
    assert task_definitions[0].description == "Sync users with the faster retry profile."
    assert task_definitions[0].source_name == "interval:1800s"
    assert task_definitions[0].concurrency == 64
    assert task_definitions[0].topology_hash == "sha256:new-topology"


def test_sync_updates_task_definitions_when_topology_changes(
    client,
    auth_headers,
    db_session,
) -> None:
    first = client.post("/api/v1/agents/sync", headers=auth_headers, json=make_sync_payload())
    assert first.status_code == 202

    updated_payload = make_sync_payload(
        topology_hash="sha256:changed-topology",
        tasks=[
            {
                "name": "sync_users",
                "description": "Sync users with the updated topology definition.",
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
            },
            {
                "name": "cleanup_orphans",
                "description": "Sweep orphaned records after the primary sync completes.",
                "source": {
                    "kind": "interval",
                    "name": "interval:86400s",
                    "config": {"seconds": 86400},
                },
                "emit": [],
                "concurrency": 1,
                "timeout_s": 120.0,
                "retry": {"kind": "no_retry", "config": {}},
            },
        ],
    )
    updated_payload["sent_at"] = "2026-03-08T17:31:06Z"
    updated_payload["sequence"] = 6
    second = client.post("/api/v1/agents/sync", headers=auth_headers, json=updated_payload)
    assert second.status_code == 202

    db_session.expire_all()
    service = db_session.scalar(
        select(Service).where(Service.name == "billing-sync", Service.environment == "prod")
    )
    assert service is not None
    assert service.latest_topology_hash == "sha256:changed-topology"

    task_definitions = db_session.scalars(
        select(TaskDefinition).order_by(TaskDefinition.task_name)
    ).all()
    assert [task_definition.task_name for task_definition in task_definitions] == [
        "cleanup_orphans",
        "sync_users",
    ]
    sync_users = next(
        task_definition
        for task_definition in task_definitions
        if task_definition.task_name == "sync_users"
    )
    assert sync_users.source_name == "interval:1800s"
    assert sync_users.description == "Sync users with the updated topology definition."
    assert sync_users.concurrency == 64
    assert sync_users.timeout_s == 45.0
    assert sync_users.emit_json == []
    assert sync_users.topology_hash == "sha256:changed-topology"


def test_sync_then_query_tasks_returns_topology_data(client, auth_headers) -> None:
    sync = client.post("/api/v1/agents/sync", headers=auth_headers, json=make_sync_payload())
    assert sync.status_code == 202

    response = client.get("/api/v1/services/billing-sync/tasks", params={"environment": "prod"})
    assert response.status_code == 200

    payload = response.json()
    by_task_name = {item["task_name"]: item for item in payload["items"]}
    assert (
        by_task_name["sync_users"]["description"]
        == "Sync billing users from the source of record into the warehouse."
    )
    assert by_task_name["sync_users"]["source_name"] == "interval:3600s"
    assert by_task_name["sync_users"]["source_kind"] == "interval"
    assert by_task_name["sync_users"]["emit"] == [
        {
            "kind": "mysql_table_sink",
            "name": "mysql:dw_users",
            "config": {"table": "dw_users", "mode": "upsert", "keys": ["id"]},
        }
    ]
    assert by_task_name["sync_users"]["retry_policy"] == {
        "kind": "max_attempts",
        "config": {"max_attempts": 5, "delay_s": 10},
    }
    assert by_task_name["sync_users"]["topology_hash"] == "sha256:6d5b0d1f"


def test_metrics_conflict_on_reused_instance_id(client, auth_headers, db_session) -> None:
    first = client.post(
        "/api/v1/agents/heartbeat",
        headers=auth_headers,
        json={
            "service": {
                **make_service_payload("33fb10d0-7580-4552-b8ca-4ef55f98f844"),
                "name": "svc-a",
            },
            "runtime": {
                "onestep_version": "1.0.0",
                "python_version": "3.11.14",
                "hostname": "vm-prod-3",
                "pid": 18231,
                "started_at": "2026-03-08T17:29:50Z",
            },
            "health": {
                "status": "ok",
                "uptime_s": 70,
                "inflight_tasks": 3,
            },
            "sent_at": "2026-03-08T17:31:00Z",
            "sequence": 1,
        },
    )
    assert first.status_code == 202

    conflict = client.post(
        "/api/v1/agents/metrics",
        headers=auth_headers,
        json={
            "service": {
                **make_service_payload("33fb10d0-7580-4552-b8ca-4ef55f98f844"),
                "name": "svc-b",
            },
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
        },
    )

    assert conflict.status_code == 409
    assert len(db_session.scalars(select(TaskMetricWindow)).all()) == 0


def test_metrics_retry_with_same_window_id_is_idempotent(client, auth_headers, db_session) -> None:
    payload = {
        "service": make_service_payload("2080e816-1fd7-4a4f-874f-e6390d666dd2"),
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

    first = client.post("/api/v1/agents/metrics", headers=auth_headers, json=payload)
    second = client.post("/api/v1/agents/metrics", headers=auth_headers, json=payload)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["ingested_count"] == 1
    assert second.json()["ingested_count"] == 0
    assert len(db_session.scalars(select(TaskMetricWindow)).all()) == 1


def test_events_retry_with_same_event_id_is_idempotent(client, auth_headers, db_session) -> None:
    payload = {
        "service": make_service_payload("9d91f3e5-655b-498f-a5c8-f99c5c3b518e"),
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
                },
                "meta": {
                    "source": "interval:3600s",
                },
            }
        ],
    }

    first = client.post("/api/v1/agents/events", headers=auth_headers, json=payload)
    second = client.post("/api/v1/agents/events", headers=auth_headers, json=payload)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["ingested_count"] == 1
    assert second.json()["ingested_count"] == 0

    events = db_session.scalars(select(TaskEvent)).all()
    assert len(events) == 1
    assert events[0].event_id == "evt_01JNXABCDEF"
    assert events[0].failure_kind == "timeout"
    assert events[0].message == "task exceeded timeout"


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


def test_missing_or_invalid_bearer_token_returns_401(client) -> None:
    payload = {
        "service": make_service_payload(),
        "runtime": {
            "onestep_version": "1.0.0a0",
            "python_version": "3.11.14",
            "hostname": "vm-prod-3",
            "pid": 18231,
            "started_at": "2026-03-08T17:29:50Z",
        },
        "health": {
            "status": "ok",
            "uptime_s": 70,
            "inflight_tasks": 3,
        },
        "sent_at": "2026-03-08T17:31:00Z",
        "sequence": 2,
    }

    missing = client.post("/api/v1/agents/heartbeat", json=payload)
    invalid = client.post(
        "/api/v1/agents/heartbeat",
        headers={"Authorization": "Bearer wrong-token"},
        json=payload,
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401


def test_sync_missing_or_invalid_bearer_token_returns_401(client) -> None:
    payload = make_sync_payload()

    missing = client.post("/api/v1/agents/sync", json=payload)
    invalid = client.post(
        "/api/v1/agents/sync",
        headers={"Authorization": "Bearer wrong-token"},
        json=payload,
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401


def test_openapi_contains_ingestion_paths_and_security_scheme(client) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    payload = response.json()
    assert "/api/v1/agents/heartbeat" in payload["paths"]
    assert "/api/v1/agents/metrics" in payload["paths"]
    assert "/api/v1/agents/events" in payload["paths"]
    assert "/api/v1/agents/sync" in payload["paths"]
    assert payload["components"]["securitySchemes"]["IngestBearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "description": (
            "Bearer token used by OneStep reporters to push heartbeat, metrics, events, and sync."
        ),
    }
    assert payload["paths"]["/api/v1/agents/heartbeat"]["post"]["security"] == [
        {"IngestBearerAuth": []}
    ]
    assert payload["paths"]["/api/v1/agents/sync"]["post"]["security"] == [
        {"IngestBearerAuth": []}
    ]
