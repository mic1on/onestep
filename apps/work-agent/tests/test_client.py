from __future__ import annotations

import asyncio
import contextlib
import gc
import hashlib
import io
import json
import random
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

    @property
    def used_slots(self) -> int:
        return len(self.reserved)

    def running_deployments(self) -> list[str]:
        return sorted(self.reserved)

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


# Captured before any test monkeypatches asyncio.sleep, so the helpers below can
# yield the event loop without polluting the delays a test records.
_REAL_SLEEP = asyncio.sleep


async def _fast_sleep(delay: float) -> None:
    """Stand-in for asyncio.sleep: yields the loop without really waiting.

    Tests monkeypatch this onto asyncio so the control loop's real reconnect
    sleeps collapse to a single event-loop step.
    """
    assert delay >= 0.0, delay
    await _REAL_SLEEP(0)


class _UpperBoundRng:
    """An injected RNG that always returns the top of the jitter range.

    Pinning the draw to the budget turns the reconnect delay into the exact
    backoff budget, so the backoff sequence can be asserted exactly instead of
    hoping a sampled delay lands where the test expects.
    """

    def uniform(self, start: float, end: float) -> float:
        return end


class _MidRangeRng:
    """An injected RNG that always returns a fixed fraction of the jitter range.

    Unlike :class:`_UpperBoundRng`, the drawn value is *strictly inside* the
    range, so a delay that equals the raw budget proves the draw was discarded
    rather than merely pinned to its upper bound.
    """

    def uniform(self, start: float, end: float) -> float:
        return start + (end - start) * 0.75


class _ZeroRng:
    """An injected RNG that always returns the bottom of the jitter range.

    Used where the test needs the reconnect to happen immediately rather than
    after a real delay, without monkeypatching asyncio.sleep.
    """

    def uniform(self, start: float, end: float) -> float:
        return start


class _ScriptedWebSocket:
    """A websocket double that behaves like a minimal control plane.

    It answers `hello` with a `hello_ack` carrying `heartbeat_interval_s`, and
    parks the receive iteration until the test scripts a frame, so a test can
    hold receive open while it makes the heartbeat fail.
    """

    def __init__(self, *, heartbeat_interval_s: int = 1, close_after_hello_ack: bool = False):
        self.sent: list[dict[str, object]] = []
        self.heartbeats: list[dict[str, object]] = []
        self.send_error: BaseException | None = None
        self.heartbeat_interval_s = heartbeat_interval_s
        self.close_after_hello_ack = close_after_hello_ack
        self.receive_parked = False
        self._incoming: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, text: str) -> None:
        payload = json.loads(text)
        self.sent.append(payload)
        if payload["type"] == "hello":
            self._incoming.append(
                json.dumps(
                    {
                        "type": "hello_ack",
                        "payload": {"heartbeat_interval_s": self.heartbeat_interval_s},
                    }
                )
            )
            return
        if payload["type"] == "heartbeat":
            self.heartbeats.append(payload)
            if self.send_error is not None:
                raise self.send_error

    async def recv(self) -> str:
        assert self._incoming, "no scripted frame available for recv()"
        return self._incoming.pop(0)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if self._incoming:
            return self._incoming.pop(0)
        if self.close_after_hello_ack:
            # The plane closed the session normally, without an error frame.
            raise StopAsyncIteration
        # Hold the receive loop open: this is the "still waiting" state a
        # heartbeat failure has to break out of.
        self.receive_parked = True
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


def _identity() -> AgentIdentity:
    return AgentIdentity(
        worker_agent_id=UUID("11111111-1111-4111-8111-111111111111"),
        connection_token="connection-token",
    )


def _pending_tasks() -> set[asyncio.Task]:
    """Every task still pending apart from the test's own driver task."""
    current = asyncio.current_task()
    return {task for task in asyncio.all_tasks() if task is not current and not task.done()}


class _LoopDiagnostics:
    """Records everything the event loop reports while a test drives the agent.

    The handler is installed before the control loop starts, so an orphan task
    ("Task was destroyed but it is pending!") or an unretrieved exception
    ("Task exception was never retrieved") is captured no matter when it is
    reported, instead of only during a final drain.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._contexts: list[dict[str, object]] = []
        self._previous = loop.get_exception_handler()
        loop.set_exception_handler(self._record)

    def _record(self, _loop: asyncio.AbstractEventLoop, context: dict[str, object]) -> None:
        self._contexts.append(context)

    def messages(self) -> list[str]:
        """Force finalization of unreferenced tasks, then return what was logged."""
        gc.collect()
        return [str(context.get("message")) for context in self._contexts]

    def restore(self) -> None:
        self._loop.set_exception_handler(self._previous)


def _assert_handler_observes_reports() -> None:
    """Guard the audit itself: a live handler must see a deliberate report.

    Without this, a broken collector would silently report "no problems" and
    the orphan/unretrieved assertions would pass for the wrong reason.
    """
    loop = asyncio.get_running_loop()
    diagnostics = _LoopDiagnostics(loop)
    try:
        sentinel = "onestep-heartbeat-test-sentinel"
        loop.call_exception_handler({"message": sentinel})
        assert diagnostics.messages() == [sentinel], "the audit did not observe the loop handler"
    finally:
        diagnostics.restore()


async def _drive_control_loop(coro, *, until, max_steps: int = 20000) -> list[str]:
    """Run the control loop until `until()` holds, then cancel and audit it.

    Progress is measured in event-loop steps rather than wall-clock time, so
    the test neither sleeps nor races a timeout, and a loop that stops making
    progress fails loudly instead of hanging.
    """
    loop = asyncio.get_running_loop()
    _assert_handler_observes_reports()
    diagnostics = _LoopDiagnostics(loop)
    task = asyncio.create_task(coro)
    try:
        for _ in range(max_steps):
            if until():
                break
            await _REAL_SLEEP(0)
        else:
            raise AssertionError(
                f"control loop did not reach the expected state in {max_steps} steps"
            )
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    try:
        pending = _pending_tasks()
        assert pending == set(), f"orphan tasks left behind: {pending!r}"
        return diagnostics.messages()
    finally:
        diagnostics.restore()


def test_run_control_loop_supervises_heartbeat_failure(tmp_path, monkeypatch) -> None:
    # Regression (#195): the heartbeat sender used to be a bare create_task the
    # receive loop never observed. When its send raised, that task died
    # silently while receive kept waiting, so the session never reconnected.
    heartbeat_error = OSError("heartbeat send failed")
    sessions: list[_ScriptedWebSocket] = []
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)

    async def fake_connect(_url, _headers):
        websocket = _ScriptedWebSocket(heartbeat_interval_s=1)
        websocket.send_error = heartbeat_error
        sessions.append(websocket)
        return websocket

    monkeypatch.setattr(client_module, "_connect_ws", fake_connect)

    diagnostics = asyncio.run(
        _drive_control_loop(
            run_control_loop(
                config=_config(tmp_path),
                identity=_identity(),
                supervisor=FakeSupervisor(),
                rng=_UpperBoundRng(),
            ),
            until=lambda: len(sessions) >= 2,
        )
    )

    # The heartbeat really did fail while receive was still parked...
    assert sessions[0].heartbeats, "the heartbeat sender never ran"
    assert sessions[0].receive_parked, "receive was not still waiting when the heartbeat failed"
    # ...and the session still ended and drove the unified reconnect path.
    assert len(sessions) >= 2, "the session did not reconnect after the heartbeat failure"
    assert diagnostics == [], f"unretrieved errors after the session: {diagnostics}"


def test_supervise_session_raises_heartbeat_failure_and_leaves_no_tasks() -> None:
    # Unit-level counterpart: the supervised session must surface the heartbeat
    # failure rather than swallow it, and must leave nothing running.
    heartbeat_error = RuntimeError("heartbeat socket closed")

    async def receive_loop() -> None:
        await asyncio.Event().wait()  # never completes on its own

    async def heartbeat_loop() -> None:
        await asyncio.sleep(0)
        raise heartbeat_error

    async def _main() -> None:
        with pytest.raises(RuntimeError, match="heartbeat socket closed"):
            await client_module._supervise_session(receive_loop(), heartbeat_loop())
        assert _pending_tasks() == set(), f"orphan tasks: {_pending_tasks()!r}"

    asyncio.run(_main())


def test_supervise_session_receive_failure_cancels_heartbeat() -> None:
    # The symmetric case: receive fails first and the heartbeat must still be
    # cancelled and awaited instead of left running as an orphan.
    receive_error = ConnectionError("receive loop dropped")
    cancelled = asyncio.Event()

    async def receive_loop() -> None:
        raise receive_error

    async def heartbeat_loop() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def _main() -> None:
        with pytest.raises(ConnectionError, match="receive loop dropped"):
            await client_module._supervise_session(receive_loop(), heartbeat_loop())
        assert cancelled.is_set(), "heartbeat was not cancelled when receive failed"
        assert _pending_tasks() == set(), f"orphan tasks: {_pending_tasks()!r}"

    asyncio.run(_main())


def _exception_retrieved(task: asyncio.Task) -> bool:
    """Whether a task's exception has been retrieved, so asyncio will not log it.

    This mirrors the flag asyncio itself consults in `Future.__del__` before
    logging "exception was never retrieved". Reading it does not clear it
    (unlike calling `task.exception()`), so an assertion built on it cannot
    satisfy itself, and it does not depend on when the task happens to be
    garbage-collected.
    """
    flag = getattr(task, "_log_traceback", None)
    assert isinstance(flag, bool), (
        "asyncio task introspection changed; update _exception_retrieved to the new API"
    )
    return not flag


def test_supervise_session_retrieves_both_failures_when_both_fail() -> None:
    # Both loops failing is the case that leaks an unretrieved exception: only
    # one failure can be raised to the caller, so the companion's exception
    # must still be retrieved during cleanup or asyncio reports it as lost.
    receive_error = ValueError("receive failed too")
    heartbeat_error = RuntimeError("heartbeat failed too")
    tasks: dict[str, asyncio.Task] = {}

    async def receive_loop() -> None:
        tasks["receive"] = asyncio.current_task()
        raise receive_error

    async def heartbeat_loop() -> None:
        tasks["heartbeat"] = asyncio.current_task()
        raise heartbeat_error

    async def _main() -> None:
        # Prove the introspection below can actually detect an unretrieved
        # exception, so this test cannot pass for the wrong reason.
        async def _unretrieved() -> None:
            raise RuntimeError("introspection probe")

        probe = asyncio.create_task(_unretrieved())
        await _REAL_SLEEP(0)
        assert _exception_retrieved(probe) is False, "introspection cannot detect a lost exception"
        probe.exception()  # retrieve it, mirroring what the code under test must do
        assert _exception_retrieved(probe) is True

        with pytest.raises(Exception) as caught:  # noqa: PT011 - either failure may win
            await client_module._supervise_session(receive_loop(), heartbeat_loop())
        assert caught.value in (receive_error, heartbeat_error)
        assert _pending_tasks() == set(), f"orphan tasks: {_pending_tasks()!r}"
        # The failure raised to the caller is retrieved by definition; the
        # companion's must have been retrieved by the cleanup path.
        assert _exception_retrieved(tasks["receive"]) is True
        assert _exception_retrieved(tasks["heartbeat"]) is True, (
            "the companion task's exception was never retrieved"
        )

    asyncio.run(_main())


def test_supervise_session_clean_receive_end_cancels_heartbeat() -> None:
    # A normal receive end (server close frame) must return normally after
    # cancelling the heartbeat, so the caller takes the clean-close path.
    cancelled = asyncio.Event()

    async def receive_loop() -> None:
        return None

    async def heartbeat_loop() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def _main() -> None:
        await client_module._supervise_session(receive_loop(), heartbeat_loop())
        assert cancelled.is_set(), "heartbeat was not cancelled when receive ended"
        assert _pending_tasks() == set(), f"orphan tasks: {_pending_tasks()!r}"

    asyncio.run(_main())


def test_supervise_session_cancellation_propagates_and_leaves_no_orphans() -> None:
    # Cancelling the session (agent shutdown) must cancel and await both loops
    # and propagate CancelledError to the caller.
    started = asyncio.Event()
    cleaned: list[str] = []

    def _loop(name: str):
        async def _run() -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.append(name)

        return _run

    async def _main() -> None:
        session = asyncio.create_task(
            client_module._supervise_session(_loop("receive")(), _loop("heartbeat")())
        )
        await started.wait()
        await _REAL_SLEEP(0)
        session.cancel()
        with pytest.raises(asyncio.CancelledError):
            await session
        assert sorted(cleaned) == ["heartbeat", "receive"], cleaned
        assert _pending_tasks() == set(), f"orphan tasks: {_pending_tasks()!r}"

    asyncio.run(_main())


def test_run_control_loop_recovers_from_real_server_restart(tmp_path, monkeypatch) -> None:
    # End-to-end against a real websocket server (no _connect_ws stub): the
    # plane shuts down and comes back on the same port, and the agent must
    # reconnect on its own and re-send `hello`, leaving no orphan task and no
    # unretrieved exception behind.
    import websockets

    hellos: list[dict[str, object]] = []
    hellos_seen = asyncio.Event()

    async def _handler(websocket) -> None:
        try:
            async for raw in websocket:
                message = json.loads(raw)
                if message.get("type") == "hello":
                    hellos.append(message)
                    hellos_seen.set()
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "hello_ack",
                                "payload": {"heartbeat_interval_s": 1},
                            }
                        )
                    )
        except websockets.exceptions.ConnectionClosed:
            return

    async def _main() -> list[str]:
        loop = asyncio.get_running_loop()
        _assert_handler_observes_reports()
        diagnostics = _LoopDiagnostics(loop)

        server = await websockets.serve(_handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        config = AgentConfig(
            plane_url=f"http://127.0.0.1:{port}",
            registration_token="registration-token",
            work_dir=tmp_path,
            identity_path=tmp_path / "identity.json",
            deployment_state_path=tmp_path / "deployments.json",
            display_name="test-agent",
            max_concurrent_deployments=1,
        )
        # Zero jitter so the real reconnect happens immediately; the injected
        # RNG is the same seam the backoff tests use.
        control = asyncio.create_task(
            run_control_loop(
                config=config,
                identity=_identity(),
                supervisor=FakeSupervisor(),
                rng=_ZeroRng(),
            )
        )
        try:
            await asyncio.wait_for(hellos_seen.wait(), timeout=10.0)
            assert len(hellos) == 1, hellos

            # Server goes down: existing sessions are closed.
            server.close()
            await server.wait_closed()
            hellos_seen.clear()

            # ...and comes back on the same port.
            restarted = await websockets.serve(_handler, "127.0.0.1", port)
            try:
                await asyncio.wait_for(hellos_seen.wait(), timeout=10.0)
                assert len(hellos) >= 2, f"the agent never reconnected: {hellos}"
            finally:
                restarted.close()
                await restarted.wait_closed()
        finally:
            control.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await control
        try:
            pending = _pending_tasks()
            assert pending == set(), f"orphan tasks left behind: {pending!r}"
            return diagnostics.messages()
        finally:
            diagnostics.restore()

    diagnostics = asyncio.run(_main())
    assert diagnostics == [], f"unretrieved errors after the restart: {diagnostics}"


def test_run_control_loop_recovers_after_server_restart(tmp_path, monkeypatch) -> None:
    # Server stop/restart recovery: a clean close and a refused connection must
    # both lead to another session, leaving no orphan task and no unretrieved
    # exception.
    connects: list[str] = []
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)

    async def fake_connect(_url, _headers):
        connects.append("attempt")
        if len(connects) == 3:
            raise OSError("connection refused while the plane restarts")
        return _ScriptedWebSocket(close_after_hello_ack=True)

    monkeypatch.setattr(client_module, "_connect_ws", fake_connect)

    diagnostics = asyncio.run(
        _drive_control_loop(
            run_control_loop(
                config=_config(tmp_path),
                identity=_identity(),
                supervisor=FakeSupervisor(),
                rng=_UpperBoundRng(),
            ),
            until=lambda: len(connects) >= 4,
        )
    )
    assert len(connects) >= 4, f"the agent stopped reconnecting after the restart: {connects}"
    assert diagnostics == [], f"unretrieved errors after the restart: {diagnostics}"


def test_run_control_loop_cancellation_exits_cleanly(tmp_path, monkeypatch) -> None:
    # Agent shutdown: cancelling the control loop mid-session must not leave a
    # task behind or produce an unretrieved exception.
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
    websockets: list[_ScriptedWebSocket] = []

    async def fake_connect(_url, _headers):
        websocket = _ScriptedWebSocket(heartbeat_interval_s=1)
        websockets.append(websocket)
        return websocket

    monkeypatch.setattr(client_module, "_connect_ws", fake_connect)

    diagnostics = asyncio.run(
        _drive_control_loop(
            run_control_loop(
                config=_config(tmp_path),
                identity=_identity(),
                supervisor=FakeSupervisor(),
                rng=_UpperBoundRng(),
            ),
            until=lambda: bool(websockets) and websockets[0].receive_parked,
        )
    )
    assert diagnostics == [], f"unretrieved errors after cancellation: {diagnostics}"


def test_reconnect_backoff_ramps_is_capped_and_resets_after_stable_session(
    tmp_path, monkeypatch
) -> None:
    # With the draw pinned to the top of the range, the recorded delay *is* the
    # backoff budget, so this asserts the exact ramp, the cap, and the reset
    # instead of sampling random values and hoping.
    # Six short failures ramp 1,2,4,8,16,30; the seventh session then stays up
    # past the stability threshold, which must reset the budget to 1 again.
    # Extra entries keep a stray extra session from raising StopIteration
    # instead of failing the assertion below.
    durations = iter([0.0] * 6 + [120.0, 0.0, 0.0, 0.0])
    clock = {"now": 0.0}
    delays: list[float] = []
    monkeypatch.setattr(client_module, "_monotonic", lambda: clock["now"])

    async def fake_run_one_session(**_kwargs) -> None:
        clock["now"] += next(durations)
        raise RuntimeError("session failed")

    monkeypatch.setattr(client_module, "_run_one_session", fake_run_one_session)

    async def recording_sleep(delay: float) -> None:
        delays.append(delay)
        await _REAL_SLEEP(0)

    monkeypatch.setattr(asyncio, "sleep", recording_sleep)

    diagnostics = asyncio.run(
        _drive_control_loop(
            run_control_loop(
                config=_config(tmp_path),
                identity=_identity(),
                supervisor=FakeSupervisor(),
                rng=_UpperBoundRng(),
            ),
            until=lambda: len(delays) >= 8,
        )
    )

    # Exponential ramp, capped at max_backoff...
    assert delays[:6] == [1.0, 2.0, 4.0, 8.0, 16.0, client_module._MAX_BACKOFF_SECONDS], delays
    # ...and the session that stayed up past the stability threshold resets the
    # budget, so the next retry is drawn from the base again (not 30s).
    assert delays[6:] == [1.0, 2.0], delays
    assert diagnostics == [], f"unretrieved errors: {diagnostics}"


def test_clean_close_reconnects_from_base_backoff(tmp_path, monkeypatch) -> None:
    # A clean close must reconnect promptly from the base budget rather than
    # escalating the retry the way a failing session does.
    #
    # The first two sessions FAIL on purpose so the budget has escalated to 4.0
    # before any clean close happens. Without those failures the budget is still
    # at its base value on the clean-close branch, so the test would pass even
    # if the reset were deleted -- it could not tell a real reset apart from a
    # budget that had never escalated.
    sessions = {"n": 0}
    delays: list[float] = []

    async def fake_run_one_session(**_kwargs) -> None:
        sessions["n"] += 1
        if sessions["n"] <= 2:
            raise RuntimeError("session failed")

    monkeypatch.setattr(client_module, "_run_one_session", fake_run_one_session)

    async def recording_sleep(delay: float) -> None:
        delays.append(delay)
        await _REAL_SLEEP(0)

    monkeypatch.setattr(asyncio, "sleep", recording_sleep)

    diagnostics = asyncio.run(
        _drive_control_loop(
            run_control_loop(
                config=_config(tmp_path),
                identity=_identity(),
                supervisor=FakeSupervisor(),
                rng=_MidRangeRng(),  # draws 0.75 * budget, strictly inside the range
            ),
            until=lambda: len(delays) >= 4,
        )
    )
    assert sessions["n"] >= 4, sessions
    # The two failing sessions escalate the budget: 1.0 then 2.0. _MidRangeRng
    # draws 0.75 of each range, so these are the drawn values, not the raw budget.
    assert delays[:2] == [
        client_module._BASE_BACKOFF_SECONDS * 0.75,
        client_module._BASE_BACKOFF_SECONDS * 2 * 0.75,
    ], delays
    # Every clean close after that must drop back to the BASE budget instead of
    # keeping the escalated 4.0. This is the assertion that fails if the
    # clean-close reset is lost.
    #
    # The expected value is 0.75 * base rather than the base itself. With
    # _UpperBoundRng the drawn value EQUALS the budget, so a clean close that
    # discarded the jitter draw entirely would still produce an identical number
    # and this test could not tell them apart. Drawing strictly inside the range
    # makes the jittered value distinguishable from the raw budget, so this
    # assertion also fails if jitter is dropped from the clean-close branch.
    assert delays[2:] == [client_module._BASE_BACKOFF_SECONDS * 0.75] * 2, (
        f"clean close did not use the jittered base budget: {delays}"
    )
    assert diagnostics == [], f"unretrieved errors: {diagnostics}"


def test_error_path_uses_the_drawn_jitter_value(tmp_path, monkeypatch) -> None:
    # The production error path must use the value actually drawn from the
    # jitter source. A unit test on _jittered_backoff_delay alone cannot prove
    # this: a defect that stops run_control_loop calling the helper at all
    # leaves the helper -- and its unit test -- perfectly green.
    delays: list[float] = []

    async def fake_run_one_session(**_kwargs) -> None:
        raise RuntimeError("session failed")

    monkeypatch.setattr(client_module, "_run_one_session", fake_run_one_session)

    async def recording_sleep(delay: float) -> None:
        delays.append(delay)
        await _REAL_SLEEP(0)

    monkeypatch.setattr(asyncio, "sleep", recording_sleep)

    asyncio.run(
        _drive_control_loop(
            run_control_loop(
                config=_config(tmp_path),
                identity=_identity(),
                supervisor=FakeSupervisor(),
                rng=_MidRangeRng(),  # draws 0.75 * budget
            ),
            until=lambda: len(delays) >= 4,
        )
    )
    # Budgets ramp 1, 2, 4, 8 -> drawn delays 0.75, 1.5, 3.0, 6.0. Anything that
    # ignores the draw reports the raw budgets instead.
    expected = [0.75, 1.5, 3.0, 6.0]
    assert delays == pytest.approx(expected), (
        f"the drawn jitter value was not used; got {delays}, expected {expected}"
    )


def test_default_rng_produces_varying_reconnect_delays(tmp_path, monkeypatch) -> None:
    # With no rng injected the production path must draw from a real random
    # source, so repeated reconnects spread instead of landing on one delay.
    # This pins the end-to-end property: jitter reaches the *production* delay,
    # not merely the helper.
    delays: list[float] = []

    async def fake_run_one_session(**_kwargs) -> None:
        raise RuntimeError("session failed")

    monkeypatch.setattr(client_module, "_run_one_session", fake_run_one_session)

    async def recording_sleep(delay: float) -> None:
        delays.append(delay)
        await _REAL_SLEEP(0)

    monkeypatch.setattr(asyncio, "sleep", recording_sleep)

    # No rng argument: run_control_loop must build its own random.Random().
    asyncio.run(
        _drive_control_loop(
            run_control_loop(
                config=_config(tmp_path), identity=_identity(), supervisor=FakeSupervisor()
            ),
            until=lambda: len(delays) >= 12,
        )
    )
    # Full jitter over a growing budget: delays must not all be identical, and
    # none may escape the cap.
    assert len(set(delays)) > 1, f"reconnect delays were deterministic: {delays}"
    assert all(0.0 <= delay <= client_module._MAX_BACKOFF_SECONDS for delay in delays), delays


def test_jittered_backoff_spreads_retries_deterministically() -> None:
    # The distribution property, pinned by a seeded RNG so it is exact rather
    # than a lucky sample: agents retrying from the same budget must spread
    # across that budget instead of landing on one lockstep delay.
    budget = client_module._BASE_BACKOFF_SECONDS
    rng = random.Random(20250901)
    samples = [client_module._jittered_backoff_delay(budget, rng) for _ in range(500)]

    assert all(0.0 <= delay <= budget for delay in samples)
    assert len(set(samples)) > 400, "jitter collapsed to a handful of delays"
    assert min(samples) < budget * 0.05, min(samples)
    assert max(samples) > budget * 0.95, max(samples)
    assert abs(sum(samples) / len(samples) - budget / 2) < budget * 0.05

    # A seeded run replays exactly, so this cannot flake...
    replay = random.Random(20250901)
    assert samples == [client_module._jittered_backoff_delay(budget, replay) for _ in range(500)]
    # ...and the delays are genuinely random rather than a fixed constant.
    other = random.Random(7)
    assert [client_module._jittered_backoff_delay(budget, other) for _ in range(50)] != samples[:50]

    # The cap holds at the top of the exponential ramp, and the delay never
    # escapes the budget it was drawn from.
    capped = random.Random(11)
    for _ in range(200):
        assert client_module._jittered_backoff_delay(0.0, capped) == 0.0
        delay = client_module._jittered_backoff_delay(client_module._MAX_BACKOFF_SECONDS, capped)
        assert 0.0 <= delay <= client_module._MAX_BACKOFF_SECONDS


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
