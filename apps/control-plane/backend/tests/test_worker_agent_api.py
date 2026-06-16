from __future__ import annotations

import hashlib
from uuid import UUID, uuid4

from onestep_control_plane_api.db.models import WorkerAgent
from sqlalchemy import select


def register_worker_agent(client, worker_agent_registration_token) -> dict[str, object]:
    response = client.post(
        "/api/v1/worker-agents/register",
        json={
            "registration_token": worker_agent_registration_token,
            "display_name": "dev-agent",
            "agent_version": "0.1.0",
            "onestep_version": "1.4.0",
            "python_version": "3.14.0",
            "execution_mode": "subprocess",
            "max_concurrent_deployments": 2,
            "labels": {"env": "dev"},
            "capabilities": [
                "deployment.start",
                "deployment.stop",
                "unsupported.future",
            ],
            "platform": {"system": "Darwin"},
        },
    )
    assert response.status_code == 200
    return response.json()


def upload_workflow_package(client) -> dict[str, object]:
    content = b"PK\x03\x04fake workflow package"
    response = client.post(
        "/api/v1/workflow-packages",
        params={
            "workflow_id": str(uuid4()),
            "version": "2026.06.16",
            "filename": "workflow.zip",
            "entrypoint": "worker.yaml",
        },
        headers={"content-type": "application/zip"},
        content=content,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["checksum_sha256"] == hashlib.sha256(content).hexdigest()
    assert payload["size_bytes"] == len(content)
    return payload


def test_register_worker_agent_persists_hashed_connection_token(
    client,
    db_session,
    worker_agent_registration_token,
) -> None:
    payload = register_worker_agent(client, worker_agent_registration_token)

    worker_agent_id = UUID(payload["worker_agent_id"])
    connection_token = str(payload["connection_token"])
    assert payload["accepted_capabilities"] == ["deployment.start", "deployment.stop"]

    worker_agent = db_session.scalar(
        select(WorkerAgent).where(WorkerAgent.worker_agent_id == worker_agent_id)
    )
    assert worker_agent is not None
    assert worker_agent.display_name == "dev-agent"
    assert worker_agent.connection_token_hash != connection_token
    assert len(worker_agent.connection_token_hash) == 64

    list_response = client.get("/api/v1/worker-agents")
    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["worker_agent_id"] == str(worker_agent_id)


def test_register_worker_agent_rejects_invalid_registration_token(client) -> None:
    response = client.post(
        "/api/v1/worker-agents/register",
        json={
            "registration_token": "wrong",
            "display_name": "dev-agent",
            "execution_mode": "subprocess",
            "max_concurrent_deployments": 1,
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid worker agent registration token"


def test_upload_and_download_workflow_package(
    client,
    worker_agent_registration_token,
) -> None:
    registration = register_worker_agent(client, worker_agent_registration_token)
    package = upload_workflow_package(client)

    unauthenticated = client.get(
        f"/api/v1/workflow-packages/{package['package_id']}/download"
    )
    assert unauthenticated.status_code == 401

    unassigned = client.get(
        f"/api/v1/workflow-packages/{package['package_id']}/download",
        headers={"Authorization": f"Bearer {registration['connection_token']}"},
    )
    assert unassigned.status_code == 403

    deployment = client.post(
        "/api/v1/worker-deployments",
        json={
            "workflow_package_id": package["package_id"],
            "worker_agent_id": registration["worker_agent_id"],
        },
    )
    assert deployment.status_code == 200

    download = client.get(
        f"/api/v1/workflow-packages/{package['package_id']}/download",
        headers={"Authorization": f"Bearer {registration['connection_token']}"},
    )
    assert download.status_code == 200
    assert download.content == b"PK\x03\x04fake workflow package"
    assert download.headers["x-onestep-package-sha256"] == package["checksum_sha256"]


def test_create_and_list_worker_deployment(
    client,
    worker_agent_registration_token,
) -> None:
    registration = register_worker_agent(client, worker_agent_registration_token)
    package = upload_workflow_package(client)

    response = client.post(
        "/api/v1/worker-deployments",
        json={
            "workflow_package_id": package["package_id"],
            "worker_agent_id": registration["worker_agent_id"],
            "desired_status": "running",
            "params": {"batch": 10},
            "env": {"EXAMPLE": "1"},
            "credential_refs": ["mysql-main"],
        },
    )

    assert response.status_code == 200
    deployment = response.json()
    assert deployment["workflow_package_id"] == package["package_id"]
    assert deployment["worker_agent_id"] == registration["worker_agent_id"]
    assert deployment["desired_status"] == "running"
    assert deployment["observed_status"] == "assigned"
    assert deployment["package_checksum"] == package["checksum_sha256"]
    assert deployment["params"] == {"batch": 10}

    list_response = client.get(
        "/api/v1/worker-deployments",
        params={"worker_agent_id": registration["worker_agent_id"]},
    )
    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["deployment_id"] == deployment["deployment_id"]

    get_response = client.get(f"/api/v1/worker-deployments/{deployment['deployment_id']}")
    assert get_response.status_code == 200
    assert get_response.json()["deployment_id"] == deployment["deployment_id"]
