from __future__ import annotations

from onestep_control_plane_api.api.agent_ingestion_service import ingest_metrics_request
from onestep_control_plane_api.api.routers.prometheus import (
    build_prometheus_metrics,
    reset_prometheus_metrics_cache,
)
from onestep_control_plane_api.api.schemas import MetricsIngestRequest
from onestep_control_plane_api.core.settings import settings
from sqlalchemy import event

INSTANCE_ID = "33fb10d0-7580-4552-b8ca-4ef55f98f844"


def _metrics_payload(
    *,
    suffix: str,
    succeeded: int,
    failed: int,
    inflight: int,
) -> dict[str, object]:
    return {
        "service": {
            "name": "billing-sync",
            "environment": "prod",
            "node_name": "vm-prod-3",
            "instance_id": INSTANCE_ID,
            "deployment_version": "1.0.0a0+c435c99",
        },
        "sent_at": "2026-03-08T17:31:00Z",
        "sequence": 3,
        "window": {
            "started_at": f"2026-03-08T17:{suffix}:00Z",
            "ended_at": f"2026-03-08T17:{suffix}:30Z",
        },
        "tasks": [
            {
                "task_name": "sync_users",
                "window_id": f"sync_users:{suffix}",
                "fetched": succeeded + failed,
                "started": succeeded + failed,
                "succeeded": succeeded,
                "retried": 0,
                "failed": failed,
                "dead_lettered": 0,
                "cancelled": 0,
                "timeouts": 0,
                "inflight": inflight,
                "avg_duration_ms": 134.2,
                "p95_duration_ms": 280.0,
                "custom_metrics": [
                    {
                        "name": "rows_success",
                        "kind": "counter",
                        "value": succeeded,
                        "labels": {},
                    },
                    {
                        "name": "rows_failed",
                        "kind": "counter",
                        "value": failed,
                        "labels": {"reason": "bad\"input\nline"},
                    },
                    {
                        "name": "batch_size",
                        "kind": "gauge",
                        "value": succeeded + failed,
                        "labels": {},
                    },
                ],
            }
        ],
    }


def test_prometheus_metrics_requires_bearer_token(client) -> None:
    response = client.get("/metrics")

    assert response.status_code == 401


def test_prometheus_metrics_exports_runtime_and_custom_metrics(
    client,
    db_session,
    auth_headers,
) -> None:
    ingest_metrics_request(
        db_session,
        MetricsIngestRequest.model_validate(
            _metrics_payload(suffix="30", succeeded=118, failed=2, inflight=2)
        ),
    )
    ingest_metrics_request(
        db_session,
        MetricsIngestRequest.model_validate(
            _metrics_payload(suffix="31", succeeded=5, failed=1, inflight=0)
        ),
    )

    response = client.get("/metrics", headers=auth_headers)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert (
        'onestep_task_succeeded_total{environment="prod",'
        f'instance_id="{INSTANCE_ID}",service="billing-sync",task="sync_users"}} 123'
    ) in body
    assert (
        'onestep_task_failed_total{environment="prod",'
        f'instance_id="{INSTANCE_ID}",service="billing-sync",task="sync_users"}} 3'
    ) in body
    assert (
        'onestep_task_inflight{environment="prod",'
        f'instance_id="{INSTANCE_ID}",service="billing-sync",task="sync_users"}} 0'
    ) in body
    assert (
        'onestep_task_custom_counter_total{environment="prod",'
        f'instance_id="{INSTANCE_ID}",metric="rows_success",service="billing-sync",'
        'task="sync_users"} 123'
    ) in body
    assert (
        'onestep_task_custom_counter_total{environment="prod",'
        f'instance_id="{INSTANCE_ID}",metric="rows_failed",reason="bad\\"input\\nline",'
        'service="billing-sync",task="sync_users"} 3'
    ) in body
    assert (
        'onestep_task_custom_gauge{environment="prod",'
        f'instance_id="{INSTANCE_ID}",metric="batch_size",service="billing-sync",'
        'task="sync_users"} 6'
    ) in body


def test_prometheus_metrics_keeps_custom_metric_kinds_separate(
    client,
    db_session,
    auth_headers,
) -> None:
    counter_payload = _metrics_payload(suffix="30", succeeded=3, failed=0, inflight=0)
    counter_payload["tasks"][0]["custom_metrics"] = [
        {
            "name": "shared_metric",
            "kind": "counter",
            "value": 3,
            "labels": {"tenant": "acme"},
        }
    ]
    gauge_payload = _metrics_payload(suffix="31", succeeded=0, failed=0, inflight=0)
    gauge_payload["tasks"][0]["custom_metrics"] = [
        {
            "name": "shared_metric",
            "kind": "gauge",
            "value": 7,
            "labels": {"tenant": "acme"},
        }
    ]
    ingest_metrics_request(
        db_session,
        MetricsIngestRequest.model_validate(counter_payload),
    )
    ingest_metrics_request(
        db_session,
        MetricsIngestRequest.model_validate(gauge_payload),
    )

    response = client.get("/metrics", headers=auth_headers)

    assert response.status_code == 200
    body = response.text
    assert (
        'onestep_task_custom_counter_total{environment="prod",'
        f'instance_id="{INSTANCE_ID}",metric="shared_metric",service="billing-sync",'
        'task="sync_users",tenant="acme"} 3'
    ) in body
    assert (
        'onestep_task_custom_gauge{environment="prod",'
        f'instance_id="{INSTANCE_ID}",metric="shared_metric",service="billing-sync",'
        'task="sync_users",tenant="acme"} 7'
    ) in body


def test_prometheus_metrics_reuses_cached_response(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "prometheus_cache_ttl_s", 60.0)
    reset_prometheus_metrics_cache()
    ingest_metrics_request(
        db_session,
        MetricsIngestRequest.model_validate(
            _metrics_payload(suffix="30", succeeded=118, failed=2, inflight=2)
        ),
    )

    statement_count = 0

    def count_statements(*_args: object) -> None:
        nonlocal statement_count
        statement_count += 1

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", count_statements)
    try:
        first = build_prometheus_metrics(db_session)
        count_after_first_scrape = statement_count
        second = build_prometheus_metrics(db_session)
    finally:
        event.remove(engine, "before_cursor_execute", count_statements)
        reset_prometheus_metrics_cache()

    assert "onestep_task_succeeded_total" in first
    assert first == second
    assert count_after_first_scrape > 0
    assert statement_count == count_after_first_scrape
