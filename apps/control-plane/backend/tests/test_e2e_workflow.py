"""End-to-end workflow verification.

Runs the full chain through the real FastAPI HTTP stack in a single ordered
test, mirroring the frontend usage flow:
  connectors → workers → compiler → deploy.

This validates integration points that unit tests cover individually but
don't connect end-to-end.
"""

from __future__ import annotations

import io
import zipfile
from uuid import UUID

import yaml
from onestep_control_plane_api.api.connector_service import build_connector_summary
from onestep_control_plane_api.api.worker_compiler import compile_worker_yaml, merge_package
from onestep_control_plane_api.db.models import Connector
from sqlalchemy import select


def test_e2e_full_workflow(client, db_session):
    """The complete Lambda-style flow: connector → worker → compile → deploy."""
    # ── Step 1: Create a connector (MySQL with DSN secret) ──────────────
    create_conn = client.post(
        "/api/v1/connectors",
        json={
            "name": "prod-mysql",
            "type": "mysql",
            "config": {},
            "secret": {"dsn": "mysql://admin:supersecret@10.0.0.5:3306/orders"},
        },
    )
    assert create_conn.status_code == 200
    connector = create_conn.json()
    connector_id = connector["id"]
    # Secret must be masked in API responses. The password is extracted from the
    # DSN and stored separately (runtime URL construction uses it).
    assert connector["secret"]["dsn"] == "****"
    assert connector["secret"]["password"] == "****"

    # ── Step 2: Connector appears in filtered list ───────────────────────
    listing = client.get("/api/v1/connectors", params={"type": "mysql"})
    assert listing.status_code == 200
    assert any(c["id"] == connector_id for c in listing.json()["items"])

    # ── Step 3: Create a worker (source + sink both reference the connector) ─
    create_worker = client.post(
        "/api/v1/workers",
        json={
            "name": "order-sync",
            "description": "incremental sync from MySQL",
            "handler_ref": "myworker.handlers:sync_order",
            "source_config": {
                "type": "mysql_incremental",
                "connector_id": connector_id,
                "fields": {"table": "orders", "key": "id", "cursor": ["updated_at", "id"]},
            },
            "sink_configs": [
                {
                    "type": "mysql_table_sink",
                    "connector_id": connector_id,
                    "fields": {"table": "orders_synced", "mode": "upsert", "keys": ["id"]},
                }
            ],
        },
    )
    assert create_worker.status_code == 200
    worker = create_worker.json()
    worker_id = worker["id"]
    assert worker["source_config"]["type"] == "mysql_incremental"
    assert len(worker["sink_configs"]) == 1

    # ── Step 4: Mark worker as ready ─────────────────────────────────────
    mark_ready = client.put(f"/api/v1/workers/{worker_id}", json={"status": "ready"})
    assert mark_ready.status_code == 200
    assert mark_ready.json()["status"] == "ready"

    # ── Step 5: Verify compiler output (decrypt connector + dedup) ───────
    row = db_session.scalar(select(Connector).where(Connector.id == UUID(connector_id)))
    assert row is not None
    summary = build_connector_summary(row, include_cleartext_secret=True)
    # The password was extracted from the DSN and stored separately.
    assert summary["secret"]["password"] == "supersecret"

    connectors_map = {
        connector_id: {
            "type": summary["type"],
            "config": summary["config"],
            "secret": summary["secret"],
        }
    }
    yml = compile_worker_yaml(
        {
            "name": "order-sync",
            "description": "incremental sync from MySQL",
            "handler_ref": "myworker.handlers:sync_order",
            "source": {
                "type": "mysql_incremental",
                "connector_id": connector_id,
                "fields": {"table": "orders", "key": "id", "cursor": ["updated_at", "id"]},
            },
            "sinks": [
                {
                    "type": "mysql_table_sink",
                    "connector_id": connector_id,
                    "fields": {"table": "synced", "mode": "upsert", "keys": ["id"]},
                }
            ],
        },
        connectors_map,
    )
    data = yaml.safe_load(yml)
    assert data["app"]["name"] == "order-sync"
    assert data["reporter"]["service_description"] == "incremental sync from MySQL"
    assert data["tasks"][0]["handler"]["ref"] == "myworker.handlers:sync_order"
    # Connector dedup: source + sink share connector_id → one resource.
    mysql_resources = [v for v in data["resources"].values() if v["type"] == "mysql"]
    assert len(mysql_resources) == 1
    # The DSN is reconstructed from host/database + the extracted password.
    assert "supersecret" in str(mysql_resources[0])
    # emit is a list with one entry.
    assert len(data["tasks"][0]["emit"]) == 1

    # ── Step 6: Multi-sink compiler produces multi-element emit ──────────
    yml_multi = compile_worker_yaml(
        {
            "name": "fanout",
            "handler_ref": "handler:handler",
            "source": {"type": "interval", "connector_id": None, "fields": {"minutes": 5}},
            "sinks": [
                {"type": "http_sink", "connector_id": None, "fields": {"url": "https://a"}},
                {"type": "http_sink", "connector_id": None, "fields": {"url": "https://b"}},
                {"type": "http_sink", "connector_id": None, "fields": {"url": "https://c"}},
            ],
        },
        {},
    )
    multi = yaml.safe_load(yml_multi)
    assert len(multi["tasks"][0]["emit"]) == 3

    # ── Step 7: Deploy without handler package → 422 ─────────────────────
    deploy_fail = client.post(
        f"/api/v1/workers/{worker_id}/deploy",
        json={"worker_agent_id": "11111111-1111-1111-1111-111111111111"},
    )
    assert deploy_fail.status_code == 422

    # ── Step 8: Upload handler zip + attach to worker ────────────────────
    handler_buf = io.BytesIO()
    with zipfile.ZipFile(handler_buf, "w") as zf:
        zf.writestr("handler.py", "def handler(ctx, item):\n    return item\n")
    handler_bytes = handler_buf.getvalue()

    upload = client.post(
        "/api/v1/workflow-packages"
        "?workflow_id=22222222-2222-2222-2222-222222222222&version=v1"
        "&filename=handler.zip&entrypoint=worker.yaml",
        content=handler_bytes,
        headers={"Content-Type": "application/zip"},
    )
    assert upload.status_code == 200
    package_id = upload.json()["package_id"]

    attach = client.put(
        f"/api/v1/workers/{worker_id}",
        json={"handler_package_id": package_id},
    )
    assert attach.status_code == 200
    assert attach.json()["handler_package_id"] == package_id

    # ── Step 9: Package merge verification ───────────────────────────────
    merged = merge_package(handler_bytes, yml)
    with zipfile.ZipFile(io.BytesIO(merged), "r") as zf:
        assert "worker.yaml" in zf.namelist()
        assert "handler.py" in zf.namelist()
        # The merged worker.yaml is the compiled one.
        merged_yaml = yaml.safe_load(zf.read("worker.yaml"))
        assert merged_yaml["app"]["name"] == "order-sync"

    # ── Step 10: Delete worker + connector cleanup ───────────────────────
    del_worker = client.delete(f"/api/v1/workers/{worker_id}")
    assert del_worker.status_code == 204

    del_conn = client.delete(f"/api/v1/connectors/{connector_id}")
    assert del_conn.status_code == 204

    gone = client.get(f"/api/v1/workers/{worker_id}")
    assert gone.status_code == 404
