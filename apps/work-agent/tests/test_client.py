from __future__ import annotations

import asyncio
import hashlib
import io
import json
import zipfile
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from onestep_worker_agent import client as client_module
from onestep_worker_agent.client import handle_control_message, run_control_loop
from onestep_worker_agent.config import AgentConfig
from onestep_worker_agent.identity import AgentIdentity
from onestep_worker_agent.supervisor import InstallError


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
        self.started_envs: list[dict[str, str]] = []
        self.stopped: list[str] = []
        self.check_timeouts: list[float | None] = []
        self.stop_grace: list[float | None] = []
        self.installed: list[tuple[str, str]] = []  # (deployment_id, mode)
        self.venv_paths: list[str] = []
        self.install_failure: Exception | None = None

    def reserve_slot(self, deployment_id: str) -> None:
        self.reserved.add(deployment_id)

    def release_slot(self, deployment_id: str) -> None:
        self.reserved.discard(deployment_id)

    def venv_path_for(self, package_checksum: str) -> Path:
        path = Path(f"/fake/venvs/{package_checksum}/venv")
        self.venv_paths.append(str(path))
        return path

    async def install(self, spec, *, mode: str, timeout_s: float | None = None) -> None:
        self.installed.append((spec.deployment_id, mode))
        if self.install_failure is not None:
            raise self.install_failure

    async def check(self, spec, *, timeout_s: float | None = None) -> int:
        self.checked.append(spec.entrypoint)
        self.check_timeouts.append(timeout_s)
        return 0

    async def start(self, spec):
        self.started.append(spec.deployment_id)
        self.started_envs.append(dict(spec.env))
        return object()

    async def stop(self, deployment_id: str, *, grace_seconds: float | None = None):
        self.stopped.append(deployment_id)
        self.stop_grace.append(grace_seconds)
        self.release_slot(deployment_id)
        return 0


def _build_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("worker.yaml", "app:\n  name: demo\n")
    return buffer.getvalue()


def _build_zip_with_requirements() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("worker.yaml", "app:\n  name: demo\n")
        archive.writestr("requirements.txt", "httpx>=0.28\n")
    return buffer.getvalue()


def _build_zip_with_pyproject_and_requirements() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("worker.yaml", "app:\n  name: demo\n")
        archive.writestr("pyproject.toml", "[project]\nname='demo'\nversion='0.1.0'\n")
        archive.writestr("requirements.txt", "onestep-mysql>=0.3.0\n")
    return buffer.getvalue()


def _config(tmp_path: Path) -> AgentConfig:
    return AgentConfig(
        plane_url="http://control-plane.test",
        registration_token="registration-token",
        work_dir=tmp_path,
        identity_path=tmp_path / "identity.json",
        deployment_state_path=tmp_path / "deployments.json",
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
    event_types = [
        message["payload"]["event_type"]
        for message in websocket.messages
        if message["type"] == "deployment_event"
    ]
    assert event_types == ["preparing", "installing", "checking", "running"]
    result = _last_command_result(websocket.messages)
    assert result["payload"]["status"] == "succeeded"
    assert "runtime_instance_id" in result["payload"]["result"]
    assert http_client.requests == [
        (
            "/api/v1/workflow-packages/package/download",
            {"Authorization": "Bearer connection-token"},
        )
    ]
    assert (tmp_path / "deployments" / deployment_id / "package" / "worker.yaml").exists()
    assert supervisor.installed == [(deployment_id, "runtime")]
    assert supervisor.checked == ["worker.yaml"]
    assert supervisor.started == [deployment_id]
    assert supervisor.started_envs == [
        {
            "EXAMPLE": "1",
            "ONESTEP_CONTROL_PLANE_URL": "http://control-plane.test",
            "ONESTEP_CONTROL_PLANE_TOKEN": "connection-token",
        }
    ]


def test_start_deployment_records_params_and_credential_refs_in_event(tmp_path) -> None:
    # The plane dispatches params (call-time args) and credential_refs per
    # deployment. The agent records them on the preparing event so the
    # deployment timeline stays auditable, even though it does not yet inject
    # them into the subprocess.
    content = _build_zip()
    checksum = hashlib.sha256(content).hexdigest()
    identity = AgentIdentity(
        worker_agent_id=UUID("11111111-1111-4111-8111-111111111111"),
        connection_token="connection-token",
    )
    deployment_id = str(uuid4())
    websocket = FakeWebSocket()

    asyncio.run(
        handle_control_message(
            websocket=websocket,
            http_client=FakeHttpClient(content),
            config=_config(tmp_path),
            identity=identity,
            supervisor=FakeSupervisor(),
            message={
                "type": "command",
                "payload": {
                    "command_id": str(uuid4()),
                    "kind": "start_deployment",
                    "args": {
                        "deployment_id": deployment_id,
                        "package_checksum": checksum,
                        "download_url": "/api/v1/workflow-packages/package/download",
                        "entrypoint": "worker.yaml",
                        "env": {"EXAMPLE": "1"},
                        "params": {"retries": 3, "mode": "strict"},
                        "credential_refs": ["db-creds", "api-key"],
                    },
                },
            },
        )
    )

    preparing = next(
        message
        for message in websocket.messages
        if message["type"] == "deployment_event"
        and message["payload"]["event_type"] == "preparing"
    )
    assert preparing["payload"]["payload"]["params"] == {"retries": 3, "mode": "strict"}
    assert preparing["payload"]["payload"]["credential_refs"] == ["db-creds", "api-key"]


def test_start_deployment_defaults_missing_params_and_credential_refs(tmp_path) -> None:
    # params / credential_refs are optional on the wire; absent values must
    # not crash the handler and must be recorded as empty.
    content = _build_zip()
    checksum = hashlib.sha256(content).hexdigest()
    identity = AgentIdentity(
        worker_agent_id=UUID("11111111-1111-4111-8111-111111111111"),
        connection_token="connection-token",
    )
    deployment_id = str(uuid4())
    websocket = FakeWebSocket()

    asyncio.run(
        handle_control_message(
            websocket=websocket,
            http_client=FakeHttpClient(content),
            config=_config(tmp_path),
            identity=identity,
            supervisor=FakeSupervisor(),
            message={
                "type": "command",
                "payload": {
                    "command_id": str(uuid4()),
                    "kind": "start_deployment",
                    "args": {
                        "deployment_id": deployment_id,
                        "package_checksum": checksum,
                        "download_url": "/api/v1/workflow-packages/package/download",
                        "entrypoint": "worker.yaml",
                    },
                },
            },
        )
    )

    preparing = next(
        message
        for message in websocket.messages
        if message["type"] == "deployment_event"
        and message["payload"]["event_type"] == "preparing"
    )
    assert preparing["payload"]["payload"]["params"] == {}
    assert preparing["payload"]["payload"]["credential_refs"] == []


def test_start_deployment_installs_dependencies_when_package_declares_them(tmp_path) -> None:
    # A package shipping requirements.txt triggers an `installing` phase between
    # download and check; the supervisor.install is invoked with that mode.
    content = _build_zip_with_requirements()
    checksum = hashlib.sha256(content).hexdigest()
    identity = AgentIdentity(
        worker_agent_id=UUID("11111111-1111-4111-8111-111111111111"),
        connection_token="connection-token",
    )
    deployment_id = str(uuid4())
    websocket = FakeWebSocket()
    supervisor = FakeSupervisor()

    asyncio.run(
        handle_control_message(
            websocket=websocket,
            http_client=FakeHttpClient(content),
            config=_config(tmp_path),
            identity=identity,
            supervisor=supervisor,
            message={
                "type": "command",
                "payload": {
                    "command_id": str(uuid4()),
                    "kind": "start_deployment",
                    "args": {
                        "deployment_id": deployment_id,
                        "package_checksum": checksum,
                        "download_url": "/api/v1/workflow-packages/package/download",
                        "entrypoint": "worker.yaml",
                    },
                },
            },
        )
    )

    event_types = [
        message["payload"]["event_type"]
        for message in websocket.messages
        if message["type"] == "deployment_event"
    ]
    assert event_types == ["preparing", "installing", "checking", "running"]
    assert supervisor.installed == [(deployment_id, "requirements")]
    assert supervisor.started == [deployment_id]


def test_start_deployment_installs_package_and_requirements_when_both_exist(
    tmp_path,
) -> None:
    content = _build_zip_with_pyproject_and_requirements()
    checksum = hashlib.sha256(content).hexdigest()
    identity = AgentIdentity(
        worker_agent_id=UUID("11111111-1111-4111-8111-111111111111"),
        connection_token="connection-token",
    )
    deployment_id = str(uuid4())
    websocket = FakeWebSocket()
    supervisor = FakeSupervisor()

    asyncio.run(
        handle_control_message(
            websocket=websocket,
            http_client=FakeHttpClient(content),
            config=_config(tmp_path),
            identity=identity,
            supervisor=supervisor,
            message={
                "type": "command",
                "payload": {
                    "command_id": str(uuid4()),
                    "kind": "start_deployment",
                    "args": {
                        "deployment_id": deployment_id,
                        "package_checksum": checksum,
                        "download_url": "/api/v1/workflow-packages/package/download",
                        "entrypoint": "worker.yaml",
                    },
                },
            },
        )
    )

    assert supervisor.installed == [(deployment_id, "package+requirements")]
    assert supervisor.started == [deployment_id]


def test_start_deployment_installs_runtime_when_package_has_no_dependencies(
    tmp_path,
) -> None:
    # Even a package without user dependencies runs in a default runtime venv so
    # platform-generated connector resources are available consistently.
    content = _build_zip()
    checksum = hashlib.sha256(content).hexdigest()
    identity = AgentIdentity(
        worker_agent_id=UUID("11111111-1111-4111-8111-111111111111"),
        connection_token="connection-token",
    )
    deployment_id = str(uuid4())
    websocket = FakeWebSocket()
    supervisor = FakeSupervisor()

    asyncio.run(
        handle_control_message(
            websocket=websocket,
            http_client=FakeHttpClient(content),
            config=_config(tmp_path),
            identity=identity,
            supervisor=supervisor,
            message={
                "type": "command",
                "payload": {
                    "command_id": str(uuid4()),
                    "kind": "start_deployment",
                    "args": {
                        "deployment_id": deployment_id,
                        "package_checksum": checksum,
                        "download_url": "/api/v1/workflow-packages/package/download",
                        "entrypoint": "worker.yaml",
                    },
                },
            },
        )
    )

    event_types = [
        message["payload"]["event_type"]
        for message in websocket.messages
        if message["type"] == "deployment_event"
    ]
    assert event_types == ["preparing", "installing", "checking", "running"]
    assert supervisor.installed == [(deployment_id, "runtime")]


def test_start_deployment_fails_when_dependency_install_fails(tmp_path) -> None:
    # An InstallError from the install phase fails the deployment: the slot is
    # released, a `failed` event is emitted, and start is never reached.
    content = _build_zip_with_requirements()
    checksum = hashlib.sha256(content).hexdigest()
    identity = AgentIdentity(
        worker_agent_id=UUID("11111111-1111-4111-8111-111111111111"),
        connection_token="connection-token",
    )
    deployment_id = str(uuid4())
    websocket = FakeWebSocket()
    supervisor = FakeSupervisor()
    supervisor.install_failure = InstallError("pip install exited with code 1")

    asyncio.run(
        handle_control_message(
            websocket=websocket,
            http_client=FakeHttpClient(content),
            config=_config(tmp_path),
            identity=identity,
            supervisor=supervisor,
            message={
                "type": "command",
                "payload": {
                    "command_id": str(uuid4()),
                    "kind": "start_deployment",
                    "args": {
                        "deployment_id": deployment_id,
                        "package_checksum": checksum,
                        "download_url": "/api/v1/workflow-packages/package/download",
                        "entrypoint": "worker.yaml",
                    },
                },
            },
        )
    )

    event_types = [
        message["payload"]["event_type"]
        for message in websocket.messages
        if message["type"] == "deployment_event"
    ]
    assert "running" not in event_types
    assert event_types[-1] == "failed"
    assert supervisor.started == []
    result = _last_command_result(websocket.messages)
    assert result["payload"]["status"] == "failed"


def test_handle_stop_deployment_stops_existing_process(tmp_path) -> None:
    identity = AgentIdentity(
        worker_agent_id=UUID("11111111-1111-4111-8111-111111111111"),
        connection_token="connection-token",
    )
    command_id = str(uuid4())
    deployment_id = str(uuid4())
    websocket = FakeWebSocket()
    supervisor = FakeSupervisor()
    supervisor.reserve_slot(deployment_id)

    asyncio.run(
        handle_control_message(
            websocket=websocket,
            http_client=FakeHttpClient(b""),
            config=_config(tmp_path),
            identity=identity,
            supervisor=supervisor,
            message={
                "type": "command",
                "payload": {
                    "command_id": command_id,
                    "kind": "stop_deployment",
                    "args": {"deployment_id": deployment_id},
                },
            },
        )
    )

    assert websocket.messages[0]["type"] == "command_ack"
    assert websocket.messages[0]["payload"]["status"] == "accepted"
    event_types = [
        message["payload"]["event_type"]
        for message in websocket.messages
        if message["type"] == "deployment_event"
    ]
    assert event_types == ["stopping", "stopped"]
    result = _last_command_result(websocket.messages)
    assert result["payload"]["status"] == "succeeded"
    assert result["payload"]["result"] == {"returncode": 0}
    assert supervisor.stopped == [deployment_id]
    assert deployment_id not in supervisor.reserved


def test_handle_restart_deployment_stops_then_starts(tmp_path) -> None:
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
    supervisor.reserve_slot(deployment_id)

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
                    "kind": "restart_deployment",
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
    event_types = [
        message["payload"]["event_type"]
        for message in websocket.messages
        if message["type"] == "deployment_event"
    ]
    assert event_types == ["preparing", "installing", "checking", "running"]
    result = _last_command_result(websocket.messages)
    assert result["payload"]["status"] == "succeeded"
    assert supervisor.stopped == [deployment_id]
    assert supervisor.installed == [(deployment_id, "runtime")]
    assert supervisor.checked == ["worker.yaml"]
    assert supervisor.started == [deployment_id]


def _last_command_result(messages: list[dict[str, object]]) -> dict[str, object]:
    results = [message for message in messages if message["type"] == "command_result"]
    assert results
    return results[-1]


def test_run_control_loop_reconnects_after_session_failure(tmp_path, monkeypatch) -> None:
    # Regression: a single dropped session used to terminate the agent process.
    # The control plane relies on the agent reconnecting so pending commands
    # can be re-dispatched on the next `hello`.
    identity = AgentIdentity(
        worker_agent_id=UUID("11111111-1111-4111-8111-111111111111"),
        connection_token="connection-token",
    )
    config = _config(tmp_path)
    supervisor = FakeSupervisor()

    attempts = {"n": 0}

    class _FakeWs:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def recv(self):
            return "{}"

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

        async def send(self, _text):
            return None

    async def fake_connect(_url, _headers):
        attempts["n"] += 1
        if attempts["n"] < 3:  # first two sessions fail, third succeeds
            raise OSError("connection reset")
        return _FakeWs()

    # Skip the backoff sleeps so the test runs fast.
    real_sleep = asyncio.sleep

    async def fast_sleep(delay):
        await real_sleep(0)

    monkeypatch.setattr(client_module, "_connect_ws", fake_connect)
    monkeypatch.setattr(asyncio, "sleep", fast_sleep)

    # The loop runs forever after the 3rd session succeeds. Time out to stop
    # the test, and confirm at least 3 connect attempts (i.e. it retried).
    async def _runner():
        await asyncio.wait_for(
            run_control_loop(config=config, identity=identity, supervisor=supervisor),
            timeout=2.0,
        )

    with pytest.raises(TimeoutError):
        asyncio.run(_runner())
    assert attempts["n"] >= 3


def test_command_timeout_s_is_passed_to_supervisor(tmp_path) -> None:
    # The control plane sets timeout_s on every command; the agent must forward
    # it to supervisor.check (and to supervisor.stop on restart/stop).
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
                    "timeout_s": 45,
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

    assert supervisor.check_timeouts == [45.0]
    result = _last_command_result(websocket.messages)
    assert result["payload"]["status"] == "succeeded"


def test_missing_timeout_s_keeps_supervisor_default(tmp_path) -> None:
    # Older servers / other senders may omit timeout_s; the agent must not
    # crash and must leave the supervisor's default in place.
    content = _build_zip()
    checksum = hashlib.sha256(content).hexdigest()
    identity = AgentIdentity(
        worker_agent_id=UUID("11111111-1111-4111-8111-111111111111"),
        connection_token="connection-token",
    )
    deployment_id = str(uuid4())
    supervisor = FakeSupervisor()

    asyncio.run(
        handle_control_message(
            websocket=FakeWebSocket(),
            http_client=FakeHttpClient(content),
            config=_config(tmp_path),
            identity=identity,
            supervisor=supervisor,
            message={
                "type": "command",
                "payload": {
                    "command_id": str(uuid4()),
                    "kind": "start_deployment",
                    "args": {
                        "deployment_id": deployment_id,
                        "package_checksum": checksum,
                        "download_url": "/api/v1/workflow-packages/package/download",
                        "entrypoint": "worker.yaml",
                    },
                },
            },
        )
    )

    assert supervisor.check_timeouts == [None]


def test_stop_command_timeout_s_is_passed_to_supervisor(tmp_path) -> None:
    identity = AgentIdentity(
        worker_agent_id=UUID("11111111-1111-4111-8111-111111111111"),
        connection_token="connection-token",
    )
    deployment_id = str(uuid4())
    websocket = FakeWebSocket()
    supervisor = FakeSupervisor()
    supervisor.reserve_slot(deployment_id)

    asyncio.run(
        handle_control_message(
            websocket=websocket,
            http_client=FakeHttpClient(b""),
            config=_config(tmp_path),
            identity=identity,
            supervisor=supervisor,
            message={
                "type": "command",
                "payload": {
                    "command_id": str(uuid4()),
                    "kind": "stop_deployment",
                    "timeout_s": 20,
                    "args": {"deployment_id": deployment_id},
                },
            },
        )
    )

    assert supervisor.stop_grace == [20.0]
    assert supervisor.stopped == [deployment_id]
