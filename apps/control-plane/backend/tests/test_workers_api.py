from __future__ import annotations

import hashlib
import io
import zipfile
from uuid import uuid4


def _create_worker_payload(name="order-sync"):
    return {
        "name": name,
        "description": "sync orders",
        "handler_ref": "myworker.handlers:sync",
        "source_config": {
            "type": "interval",
            "connector_id": None,
            "fields": {"minutes": 5},
        },
        "sink_configs": [],
        "env": {"API_BASE_URL": "https://api.example.com"},
    }


def _register_worker_agent(client, worker_agent_registration_token) -> dict[str, object]:
    response = client.post(
        "/api/v1/worker-agents/register",
        json={
            "registration_token": worker_agent_registration_token,
            "display_name": "dev-agent",
            "execution_mode": "subprocess",
            "max_concurrent_deployments": 2,
            "capabilities": ["deployment.start", "deployment.stop"],
        },
    )
    assert response.status_code == 200
    return response.json()


def _upload_handler_package(client) -> dict[str, object]:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("myworker/handlers.py", "async def sync(ctx, item):\n    return item.payload\n")
    content = buffer.getvalue()
    response = client.post(
        "/api/v1/workflow-packages",
        params={
            "workflow_id": str(uuid4()),
            "version": "2026.06.17",
            "filename": "handler.zip",
            "entrypoint": "myworker/handlers.py",
        },
        headers={"content-type": "application/zip"},
        content=content,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["checksum_sha256"] == hashlib.sha256(content).hexdigest()
    return payload


def test_create_and_list_worker(client):
    response = client.post("/api/v1/workers", json=_create_worker_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "order-sync"
    assert body["handler_ref"] == "myworker.handlers:sync"
    assert body["status"] == "draft"
    assert body["source_config"]["type"] == "interval"
    assert body["env"] == {"API_BASE_URL": "https://api.example.com"}

    listing = client.get("/api/v1/workers")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["name"] == "order-sync"
    assert listing.json()["items"][0]["env"] == {"API_BASE_URL": "https://api.example.com"}


def test_update_worker(client):
    create = client.post("/api/v1/workers", json=_create_worker_payload())
    worker_id = create.json()["id"]

    response = client.put(
        f"/api/v1/workers/{worker_id}",
        json={"description": "updated desc", "env": {"LOG_LEVEL": "debug"}, "status": "ready"},
    )
    assert response.status_code == 200
    assert response.json()["description"] == "updated desc"
    assert response.json()["env"] == {"LOG_LEVEL": "debug"}
    assert response.json()["status"] == "ready"


def test_delete_worker(client):
    create = client.post("/api/v1/workers", json=_create_worker_payload())
    worker_id = create.json()["id"]

    response = client.delete(f"/api/v1/workers/{worker_id}")
    assert response.status_code == 204

    gone = client.get(f"/api/v1/workers/{worker_id}")
    assert gone.status_code == 404


def test_create_rejects_duplicate_name(client):
    client.post("/api/v1/workers", json=_create_worker_payload())
    response = client.post("/api/v1/workers", json=_create_worker_payload())
    assert response.status_code == 409


def test_deploy_without_handler_package_returns_422(client):
    create = client.post("/api/v1/workers", json=_create_worker_payload())
    worker_id = create.json()["id"]

    response = client.post(
        f"/api/v1/workers/{worker_id}/deploy",
        json={"worker_agent_id": "11111111-1111-1111-1111-111111111111"},
    )
    assert response.status_code == 422


def test_deploy_worker_uses_saved_env(client, worker_agent_registration_token):
    registration = _register_worker_agent(client, worker_agent_registration_token)
    package = _upload_handler_package(client)
    payload = _create_worker_payload()
    payload["handler_package_id"] = package["package_id"]
    create = client.post("/api/v1/workers", json=payload)
    worker_id = create.json()["id"]

    response = client.post(
        f"/api/v1/workers/{worker_id}/deploy",
        json={"worker_agent_id": registration["worker_agent_id"]},
    )

    assert response.status_code == 200
    assert response.json()["env"] == {"API_BASE_URL": "https://api.example.com"}
