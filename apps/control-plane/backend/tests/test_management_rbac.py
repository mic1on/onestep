from __future__ import annotations

import asyncio
import io
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from onestep_control_plane_api.api.worker_agent_connection_registry import (
    worker_agent_connection_registry,
)
from onestep_control_plane_api.auth.service import LocalAuthService
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db.models import (
    Connector,
    ConsoleSession,
    Worker,
    WorkerAgentCommand,
    WorkerDeployment,
    WorkerDeploymentEvent,
    WorkflowPackage,
)
from sqlalchemy import select

ROLES = ("anonymous", "viewer", "operator", "admin", "dev-bypass")
OPERATIONS = (
    "upload",
    "connector-create",
    "connector-update",
    "connector-delete",
    "worker-create",
    "worker-update",
    "worker-delete",
    "download",
    "deploy",
    "deployment-create",
    "deployment-create-stopped",
    "stop",
    "restart",
)
ADMIN_OPERATIONS = frozenset(
    {"download", "deploy", "deployment-create", "deployment-create-stopped", "stop", "restart"}
)
PACKAGE_SECRET = "mysql://user:restricted-secret@db/orders"


@pytest.fixture()
def managed_resources(client, worker_agent_registration_token):
    """Create valid resources before enabling authentication for each matrix case."""

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("handler.py", "async def handler(ctx, item):\n    return item.payload\n")
    content = archive.getvalue()
    response = client.post(
        "/api/v1/workflow-packages",
        params={"workflow_id": str(uuid4()), "version": "1", "entrypoint": "handler.py"},
        headers={"content-type": "application/zip"},
        content=content,
    )
    assert response.status_code == 200
    package_id = response.json()["package_id"]
    response = client.post(
        "/api/v1/connectors",
        json={"name": "database", "type": "mysql", "secret": {"dsn": PACKAGE_SECRET}},
    )
    assert response.status_code == 200
    connector_id = response.json()["id"]
    response = client.post(
        "/api/v1/workers",
        json={
            "name": "existing-worker",
            "handler_package_id": package_id,
            "source_config": {
                "type": "mysql_query",
                "connector_id": connector_id,
                "fields": {"query": "SELECT 1"},
            },
        },
    )
    assert response.status_code == 200
    worker_id = response.json()["id"]
    response = client.post(
        "/api/v1/worker-agents/register",
        json={
            "registration_token": worker_agent_registration_token,
            "display_name": "managed-agent",
            "execution_mode": "subprocess",
            "max_concurrent_deployments": 2,
            "capabilities": ["deployment.start", "deployment.stop", "deployment.restart"],
        },
    )
    assert response.status_code == 200
    agent_id = response.json()["worker_agent_id"]
    response = client.post(
        "/api/v1/worker-deployments",
        json={
            "workflow_package_id": package_id,
            "worker_agent_id": agent_id,
            "desired_status": "stopped",
        },
    )
    assert response.status_code == 200
    return {
        "content": content,
        "package_id": package_id,
        "connector_id": connector_id,
        "worker_id": worker_id,
        "agent_id": agent_id,
        "deployment_id": response.json()["deployment_id"],
    }


def authenticate_role(client, db_session, role):
    if role == "dev-bypass":
        return
    LocalAuthService(db_session).create_user(
        username="test-user",
        password="secret-pass",
        role_names=["admin" if role == "anonymous" else role],
    )
    if role != "anonymous":
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "test-user", "password": "secret-pass"},
        )
        assert response.status_code == 200


def management_state(db_session):
    """Include rows, mutable columns and disk artifacts, not only response codes."""

    models = (
        Connector,
        Worker,
        WorkflowPackage,
        WorkerDeployment,
        WorkerAgentCommand,
        WorkerDeploymentEvent,
    )
    rows = {
        model.__tablename__: db_session.execute(
            select(model.__table__).order_by(*model.__table__.primary_key)
        ).all()
        for model in models
    }
    package_dir = Path(settings.worker_package_storage_dir)
    files = {str(path): path.read_bytes() for path in package_dir.rglob("*") if path.is_file()}
    return rows, files


def management_request(client, resources, operation):
    if operation == "upload":
        return client.post(
            "/api/v1/workflow-packages",
            params={"workflow_id": str(uuid4()), "version": "2", "entrypoint": "handler.py"},
            headers={"content-type": "application/zip"},
            content=resources["content"],
        )
    if operation.startswith("connector-"):
        action = operation.removeprefix("connector-")
        if action == "create":
            return client.post("/api/v1/connectors", json={"name": "new-database", "type": "mysql"})
        url = f"/api/v1/connectors/{resources['connector_id']}"
        if action == "update":
            return client.put(url, json={"name": "renamed-database"})
        return client.delete(url)
    if operation.startswith("worker-"):
        action = operation.removeprefix("worker-")
        if action == "create":
            return client.post("/api/v1/workers", json={"name": "new-worker"})
        url = f"/api/v1/workers/{resources['worker_id']}"
        if action == "update":
            return client.put(url, json={"name": "renamed-worker"})
        return client.delete(url)
    if operation == "download":
        return client.get(f"/api/v1/workers/{resources['worker_id']}/package/download")
    if operation == "deploy":
        return client.post(
            f"/api/v1/workers/{resources['worker_id']}/deploy",
            json={"worker_agent_id": resources["agent_id"]},
        )
    if operation.startswith("deployment-create"):
        return client.post(
            "/api/v1/worker-deployments",
            json={
                "workflow_package_id": resources["package_id"],
                "worker_agent_id": resources["agent_id"],
                "desired_status": "stopped" if operation.endswith("stopped") else "running",
            },
        )
    return client.post(f"/api/v1/worker-deployments/{resources['deployment_id']}/{operation}")


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("operation", OPERATIONS)
def test_management_authorization_matrix(client, db_session, managed_resources, role, operation):
    authenticate_role(client, db_session, role)
    before = management_state(db_session)

    response = management_request(client, managed_resources, operation)

    if role == "anonymous":
        expected_status = 401
    elif role == "viewer" or (role == "operator" and operation in ADMIN_OPERATIONS):
        expected_status = 403
    else:
        expected_status = 204 if operation.endswith("-delete") else 200
    assert response.status_code == expected_status, response.text
    if expected_status in {401, 403}:
        assert management_state(db_session) == before
        assert "restricted-secret" not in response.text
    elif operation == "download":
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert PACKAGE_SECRET in archive.read("worker.yaml").decode()
    else:
        assert management_state(db_session) != before


@pytest.mark.parametrize("role", ROLES)
def test_management_reads_preserve_role_access(client, db_session, managed_resources, role):
    authenticate_role(client, db_session, role)
    resources = managed_resources
    paths = (
        "/api/v1/connectors",
        f"/api/v1/connectors/{resources['connector_id']}",
        "/api/v1/workers",
        f"/api/v1/workers/{resources['worker_id']}",
        "/api/v1/worker-agents",
        f"/api/v1/worker-agents/{resources['agent_id']}",
        "/api/v1/worker-deployments",
        f"/api/v1/worker-deployments/{resources['deployment_id']}",
        f"/api/v1/worker-deployments/{resources['deployment_id']}/events",
    )
    for path in paths:
        response = client.get(path)
        assert response.status_code == (401 if role == "anonymous" else 200), path
        assert "restricted-secret" not in response.text


@pytest.mark.parametrize("operation", ("stop", "restart"))
def test_deployment_control_requires_recent_admin_login(
    client,
    db_session,
    managed_resources,
    operation,
):
    authenticate_role(client, db_session, "admin")
    session = db_session.scalar(select(ConsoleSession))
    session.authenticated_at = datetime.now(UTC) - timedelta(
        seconds=settings.console_sensitive_auth_window_s + 1
    )
    db_session.commit()
    before = management_state(db_session)

    response = management_request(client, managed_resources, operation)

    assert response.status_code == 401
    assert response.json()["detail"] == "recent authentication required for destructive commands"
    assert management_state(db_session) == before


@pytest.mark.parametrize("role", ("viewer", "operator"))
def test_denied_deployment_requests_never_reach_connected_agent(
    client,
    db_session,
    managed_resources,
    role,
):
    authenticate_role(client, db_session, role)
    queue = asyncio.Queue()
    agent_id = UUID(managed_resources["agent_id"])
    asyncio.run(
        worker_agent_connection_registry.register(
            worker_agent_id=agent_id,
            session_id="denied-session",
            send_queue=queue,
        )
    )
    try:
        for operation in ("deploy", "deployment-create", "stop", "restart"):
            response = management_request(client, managed_resources, operation)
            assert response.status_code == 403
        assert queue.empty()
    finally:
        asyncio.run(
            worker_agent_connection_registry.unregister(
                worker_agent_id=agent_id,
                session_id="denied-session",
            )
        )


@pytest.mark.parametrize("operation", ("upload", "download", "deploy", "stop"))
def test_management_dev_bypass_is_never_available_in_production(
    client,
    managed_resources,
    monkeypatch,
    operation,
):
    monkeypatch.setattr(settings, "app_env", "prod")
    response = management_request(client, managed_resources, operation)
    assert response.status_code == 401
