from __future__ import annotations

import hashlib
import io
import zipfile
from uuid import UUID, uuid4

import yaml
from onestep_control_plane_api.api.connector_service import get_cipher
from onestep_control_plane_api.db.models import Worker, WorkerAgentCommand
from sqlalchemy import select


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


def _hello_message(worker_agent_id: str) -> dict[str, object]:
    return {
        "type": "hello",
        "message_id": "msg_hello_1",
        "sent_at": "2026-06-16T09:00:00Z",
        "payload": {
            "protocol_version": "1",
            "worker_agent_id": worker_agent_id,
            "capabilities": ["deployment.start", "deployment.stop", "deployment.restart"],
            "max_concurrent_deployments": 2,
            "used_slots": 0,
            "running_deployments": [],
        },
    }


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
    assert body["reporting_enabled"] is True
    assert body["reporting_config"] == {"mode": "platform", "endpoint_url": None}
    assert body["reporting_token_configured"] is False

    listing = client.get("/api/v1/workers")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["name"] == "order-sync"
    assert listing.json()["items"][0]["env"] == {"API_BASE_URL": "https://api.example.com"}
    assert listing.json()["items"][0]["reporting_enabled"] is True
    assert listing.json()["items"][0]["reporting_config"] == {
        "mode": "platform",
        "endpoint_url": None,
    }


def test_create_worker_keeps_http_sink_fields_catalog_defaults_apply_at_compile_time(client):
    payload = _create_worker_payload()
    payload["sink_configs"] = [
        {
            "type": "http_sink",
            "connector_id": None,
            "fields": {"url": "https://example.com/events"},
        }
    ]

    response = client.post("/api/v1/workers", json=payload)

    assert response.status_code == 200
    assert response.json()["sink_configs"][0]["fields"] == {
        "url": "https://example.com/events",
    }


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


def test_create_worker_with_custom_reporting_encrypts_token(client, db_session):
    payload = _create_worker_payload()
    payload["reporting_config"] = {
        "mode": "custom",
        "endpoint_url": " https://telemetry.example.com ",
    }
    payload["reporting_secret"] = {"token": "custom-token"}

    response = client.post("/api/v1/workers", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["reporting_enabled"] is True
    assert body["reporting_config"] == {
        "mode": "custom",
        "endpoint_url": "https://telemetry.example.com",
    }
    assert body["reporting_token_configured"] is True
    assert "custom-token" not in response.text

    worker = db_session.scalar(select(Worker).where(Worker.id == UUID(body["id"])))
    assert worker is not None
    assert worker.reporting_secret_encrypted
    assert "custom-token" not in worker.reporting_secret_encrypted
    assert get_cipher().decrypt(worker.reporting_secret_encrypted)["token"] == "custom-token"


def test_custom_reporting_requires_token_when_enabled(client):
    payload = _create_worker_payload()
    payload["reporting_config"] = {
        "mode": "custom",
        "endpoint_url": "https://telemetry.example.com",
    }

    response = client.post("/api/v1/workers", json=payload)

    assert response.status_code == 422
    assert response.json()["detail"] == "custom reporting token is required"


def test_update_custom_reporting_keeps_existing_token_when_omitted(client, db_session):
    payload = _create_worker_payload()
    payload["reporting_config"] = {
        "mode": "custom",
        "endpoint_url": "https://telemetry.example.com",
    }
    payload["reporting_secret"] = {"token": "custom-token"}
    create = client.post("/api/v1/workers", json=payload)
    worker_id = create.json()["id"]

    response = client.put(
        f"/api/v1/workers/{worker_id}",
        json={
            "reporting_config": {
                "mode": "custom",
                "endpoint_url": "https://next.example.com",
            }
        },
    )

    assert response.status_code == 200
    assert response.json()["reporting_token_configured"] is True
    worker = db_session.scalar(select(Worker).where(Worker.id == UUID(worker_id)))
    assert worker is not None
    assert get_cipher().decrypt(worker.reporting_secret_encrypted)["token"] == "custom-token"


def test_update_platform_reporting_clears_custom_token(client, db_session):
    payload = _create_worker_payload()
    payload["reporting_config"] = {
        "mode": "custom",
        "endpoint_url": "https://telemetry.example.com",
    }
    payload["reporting_secret"] = {"token": "custom-token"}
    create = client.post("/api/v1/workers", json=payload)
    worker_id = create.json()["id"]

    response = client.put(
        f"/api/v1/workers/{worker_id}",
        json={"reporting_config": {"mode": "platform"}},
    )

    assert response.status_code == 200
    assert response.json()["reporting_config"] == {"mode": "platform", "endpoint_url": None}
    assert response.json()["reporting_token_configured"] is False
    worker = db_session.scalar(select(Worker).where(Worker.id == UUID(worker_id)))
    assert worker is not None
    assert worker.reporting_secret_encrypted is None


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


def test_download_worker_package_includes_compiled_worker_yaml(client):
    package = _upload_handler_package(client)
    payload = _create_worker_payload()
    payload["handler_package_id"] = package["package_id"]
    create = client.post("/api/v1/workers", json=payload)
    worker_id = create.json()["id"]

    response = client.get(f"/api/v1/workers/{worker_id}/package/download")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content), "r") as archive:
        names = set(archive.namelist())
        assert "myworker/handlers.py" in names
        assert "worker.yaml" in names
        worker_yaml = archive.read("worker.yaml").decode()
    compiled = yaml.safe_load(worker_yaml)
    assert compiled["app"]["name"] == "order-sync"
    assert compiled["reporter"]["service_description"] == "sync orders"
    assert compiled["tasks"][0]["handler"]["ref"] == "myworker.handlers:sync"


def test_download_worker_package_includes_custom_reporting_config(client):
    package = _upload_handler_package(client)
    payload = _create_worker_payload()
    payload["handler_package_id"] = package["package_id"]
    payload["reporting_config"] = {
        "mode": "custom",
        "endpoint_url": "https://telemetry.example.com",
    }
    payload["reporting_secret"] = {"token": "custom-token"}
    create = client.post("/api/v1/workers", json=payload)
    worker_id = create.json()["id"]

    response = client.get(f"/api/v1/workers/{worker_id}/package/download")

    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content), "r") as archive:
        worker_yaml = archive.read("worker.yaml").decode()
    compiled = yaml.safe_load(worker_yaml)
    assert compiled["reporter"] == {
        "base_url": "https://telemetry.example.com",
        "token": "${ONESTEP_WORKER_REPORTING_TOKEN}",
        "service_description": "sync orders",
    }
    assert "custom-token" not in worker_yaml


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


def test_deploy_worker_dispatches_start_command_to_connected_agent(
    client,
    db_session,
    worker_agent_registration_token,
):
    registration = _register_worker_agent(client, worker_agent_registration_token)
    package = _upload_handler_package(client)
    payload = _create_worker_payload()
    payload["handler_package_id"] = package["package_id"]
    create = client.post("/api/v1/workers", json=payload)
    worker_id = create.json()["id"]

    with client.websocket_connect(
        "/api/v1/worker-agents/ws",
        headers={"Authorization": f"Bearer {registration['connection_token']}"},
    ) as websocket:
        websocket.send_json(_hello_message(registration["worker_agent_id"]))
        websocket.receive_json()

        response = client.post(
            f"/api/v1/workers/{worker_id}/deploy",
            json={"worker_agent_id": registration["worker_agent_id"]},
        )

    assert response.status_code == 200
    deployment_id = response.json()["deployment_id"]
    command = db_session.scalar(
        select(WorkerAgentCommand).where(
            WorkerAgentCommand.deployment_id == UUID(deployment_id),
            WorkerAgentCommand.kind == "start_deployment",
        )
    )
    assert command is not None
    assert command.status == "dispatched"


def test_deploy_worker_custom_reporting_injects_token_only_into_command_env(
    client,
    db_session,
    worker_agent_registration_token,
):
    registration = _register_worker_agent(client, worker_agent_registration_token)
    package = _upload_handler_package(client)
    payload = _create_worker_payload()
    payload["handler_package_id"] = package["package_id"]
    payload["reporting_config"] = {
        "mode": "custom",
        "endpoint_url": "https://telemetry.example.com",
    }
    payload["reporting_secret"] = {"token": "custom-token"}
    create = client.post("/api/v1/workers", json=payload)
    worker_id = create.json()["id"]

    response = client.post(
        f"/api/v1/workers/{worker_id}/deploy",
        json={"worker_agent_id": registration["worker_agent_id"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["env"] == {"API_BASE_URL": "https://api.example.com"}
    assert "custom-token" not in response.text
    command = db_session.scalar(
        select(WorkerAgentCommand).where(
            WorkerAgentCommand.deployment_id == UUID(body["deployment_id"]),
            WorkerAgentCommand.kind == "start_deployment",
        )
    )
    assert command is not None
    assert command.args_json["env"] == {
        "API_BASE_URL": "https://api.example.com",
        "ONESTEP_WORKER_REPORTING_TOKEN": "custom-token",
    }
