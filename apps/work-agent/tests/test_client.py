from __future__ import annotations

import asyncio
import hashlib
import io
import json
import zipfile
from pathlib import Path
from uuid import UUID, uuid4

from onestep_worker_agent.client import handle_control_message
from onestep_worker_agent.config import AgentConfig
from onestep_worker_agent.identity import AgentIdentity


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send(self, text: str) -> None:
        self.messages.append(json.loads(text))


class FakeResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


class FakeHttpClient:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.requests: list[tuple[str, dict[str, str]]] = []

    async def get(self, url: str, *, headers: dict[str, str]) -> FakeResponse:
        self.requests.append((url, headers))
        return FakeResponse(self.content)


class FakeSupervisor:
    def __init__(self) -> None:
        self.reserved: set[str] = set()
        self.checked: list[str] = []
        self.started: list[str] = []

    def reserve_slot(self, deployment_id: str) -> None:
        self.reserved.add(deployment_id)

    def release_slot(self, deployment_id: str) -> None:
        self.reserved.discard(deployment_id)

    async def check(self, spec) -> int:
        self.checked.append(spec.entrypoint)
        return 0

    async def start(self, spec):
        self.started.append(spec.deployment_id)
        return object()


def _build_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("worker.yaml", "app:\n  name: demo\n")
    return buffer.getvalue()


def _config(tmp_path: Path) -> AgentConfig:
    return AgentConfig(
        plane_url="http://control-plane.test",
        registration_token="registration-token",
        work_dir=tmp_path,
        identity_path=tmp_path / "identity.json",
        display_name="test-agent",
        max_concurrent_deployments=1,
    )


def test_handle_start_deployment_downloads_and_starts_package(tmp_path) -> None:
    content = _build_zip()
    checksum = hashlib.sha256(content).hexdigest()
    identity = AgentIdentity(
        worker_agent_id=UUID("11111111-1111-4111-8111-111111111111"),
        connection_token="connection-token",
    )
    command_id = str(uuid4())
    deployment_id = str(uuid4())
    websocket = FakeWebSocket()
    http_client = FakeHttpClient(content)
    supervisor = FakeSupervisor()

    asyncio.run(
        handle_control_message(
            websocket=websocket,
            http_client=http_client,
            config=_config(tmp_path),
            identity=identity,
            supervisor=supervisor,
            message={
                "type": "command",
                "payload": {
                    "command_id": command_id,
                    "kind": "start_deployment",
                    "args": {
                        "deployment_id": deployment_id,
                        "package_checksum": checksum,
                        "download_url": "/api/v1/workflow-packages/package/download",
                        "entrypoint": "worker.yaml",
                        "env": {"EXAMPLE": "1"},
                    },
                },
            },
        )
    )

    assert websocket.messages[0]["type"] == "command_ack"
    assert websocket.messages[0]["payload"]["status"] == "accepted"
    assert websocket.messages[1]["type"] == "command_result"
    assert websocket.messages[1]["payload"]["status"] == "succeeded"
    assert "runtime_instance_id" in websocket.messages[1]["payload"]["result"]
    assert http_client.requests == [
        (
            "/api/v1/workflow-packages/package/download",
            {"Authorization": "Bearer connection-token"},
        )
    ]
    assert (tmp_path / "deployments" / deployment_id / "package" / "worker.yaml").exists()
    assert supervisor.checked == ["worker.yaml"]
    assert supervisor.started == [deployment_id]
