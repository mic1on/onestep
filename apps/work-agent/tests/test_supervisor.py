from __future__ import annotations

import asyncio
import os

import pytest

from onestep_worker_agent.state import DeploymentState, DeploymentStateStore
from onestep_worker_agent.supervisor import (
    DEFAULT_RUNTIME_REQUIREMENTS,
    INSTALLED_MARKER,
    DeploymentSpec,
    InstallError,
    SubprocessSupervisor,
    _installed_marker_content,
    resolve_onestep_executable,
)


def test_supervisor_rejects_when_slots_are_full(tmp_path) -> None:
    supervisor = SubprocessSupervisor(work_dir=tmp_path, max_concurrent_deployments=1)
    supervisor.reserve_slot("deployment-1")

    with pytest.raises(RuntimeError, match="no deployment slots available"):
        supervisor.reserve_slot("deployment-2")


def test_supervisor_builds_onestep_environment(tmp_path) -> None:
    supervisor = SubprocessSupervisor(work_dir=tmp_path, max_concurrent_deployments=1)
    spec = DeploymentSpec(
        deployment_id="deployment-1",
        worker_agent_id="agent-1",
        runtime_instance_id="runtime-1",
        package_dir=tmp_path / "deployment-1",
        entrypoint="worker.yaml",
        env={"CUSTOM": "value"},
    )

    env = supervisor.build_environment(spec)

    assert env["ONESTEP_DEPLOYMENT_ID"] == "deployment-1"
    assert env["ONESTEP_WORKER_AGENT_ID"] == "agent-1"
    assert env["ONESTEP_RUNTIME_INSTANCE_ID"] == "runtime-1"
    assert env["ONESTEP_INSTANCE_ID"] == "runtime-1"
    assert env["CUSTOM"] == "value"


def test_supervisor_rejects_invalid_capacity(tmp_path) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        SubprocessSupervisor(work_dir=tmp_path, max_concurrent_deployments=0)


def test_supervisor_recovers_live_pids_and_cleans_dead_pids(tmp_path, monkeypatch) -> None:
    store = DeploymentStateStore(tmp_path / "deployments.json")
    store.save_all(
        {
            "deployment-live": DeploymentState(
                deployment_id="deployment-live",
                runtime_instance_id="runtime-live",
                package_dir=tmp_path / "live",
                entrypoint="worker.yaml",
                env={},
                pid=100,
            ),
            "deployment-dead": DeploymentState(
                deployment_id="deployment-dead",
                runtime_instance_id="runtime-dead",
                package_dir=tmp_path / "dead",
                entrypoint="worker.yaml",
                env={},
                pid=200,
            ),
        }
    )
    supervisor = SubprocessSupervisor(
        work_dir=tmp_path,
        max_concurrent_deployments=1,
        state_store=store,
    )
    monkeypatch.setattr(supervisor, "_pid_is_alive", lambda pid: pid == 100)

    recovered = supervisor.recover_running_deployments()

    assert recovered == ["deployment-live"]
    assert supervisor.running_deployments() == ["deployment-live"]
    assert set(store.load_all()) == {"deployment-live"}


def test_supervisor_persists_state_after_start_and_removes_after_stop(
    tmp_path,
    monkeypatch,
) -> None:
    class FakeProcess:
        pid = 321

        def terminate(self) -> None:
            return None

        async def wait(self) -> int:
            return 0

    async def fake_create_subprocess_exec(*args, **kwargs) -> FakeProcess:
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    store = DeploymentStateStore(tmp_path / "deployments.json")
    supervisor = SubprocessSupervisor(
        work_dir=tmp_path,
        max_concurrent_deployments=1,
        state_store=store,
    )
    spec = DeploymentSpec(
        deployment_id="deployment-1",
        worker_agent_id="agent-1",
        runtime_instance_id="runtime-1",
        package_dir=tmp_path / "deployment-1",
        entrypoint="worker.yaml",
        env={"EXAMPLE": "1"},
    )

    asyncio.run(supervisor.start(spec))

    assert store.load_all()["deployment-1"].pid == 321

    asyncio.run(supervisor.stop("deployment-1"))

    assert store.load_all() == {}


def test_supervisor_drains_child_output_to_log_file(tmp_path, monkeypatch) -> None:
    # Regression: start() used stdout/stderr=PIPE without ever reading them,
    # which blocks the child once the pipe buffer fills. Output must land in a
    # file under the deployment dir, and the file must be closed on stop.
    captured: dict[str, object] = {}

    class FakeProcess:
        pid = 321

        def terminate(self) -> None:
            return None

        async def wait(self) -> int:
            return 0

    async def fake_create_subprocess_exec(*args, **kwargs) -> FakeProcess:
        captured["stdout"] = kwargs.get("stdout")
        captured["stderr"] = kwargs.get("stderr")
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    store = DeploymentStateStore(tmp_path / "deployments.json")
    supervisor = SubprocessSupervisor(
        work_dir=tmp_path,
        max_concurrent_deployments=1,
        state_store=store,
    )
    spec = DeploymentSpec(
        deployment_id="deployment-1",
        worker_agent_id="agent-1",
        runtime_instance_id="runtime-1",
        package_dir=tmp_path / "deployments" / "deployment-1" / "package",
        entrypoint="worker.yaml",
        env={},
    )

    asyncio.run(supervisor.start(spec))

    # stdout must be a real file object (not PIPE) so the pipe never blocks.
    assert captured["stdout"] is not asyncio.subprocess.PIPE
    assert captured["stderr"] == asyncio.subprocess.STDOUT
    log_handle = supervisor._log_files["deployment-1"]
    assert log_handle.closed is False

    asyncio.run(supervisor.stop("deployment-1"))

    assert log_handle.closed is True
    assert "deployment-1" not in supervisor._log_files


def test_supervisor_check_timeout_kills_process(tmp_path, monkeypatch) -> None:
    # When timeout_s elapses, check() must kill the child rather than hang.
    events: list[str] = []

    class HangingProcess:
        async def wait(self) -> int:
            # Never returns on its own — only the timeout/kill path ends it.
            await asyncio.sleep(10)
            return 0

        def kill(self) -> None:
            events.append("killed")

    async def fake_create_subprocess_exec(*args, **kwargs) -> HangingProcess:
        return HangingProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    supervisor = SubprocessSupervisor(work_dir=tmp_path, max_concurrent_deployments=1)
    spec = DeploymentSpec(
        deployment_id="deployment-1",
        worker_agent_id="agent-1",
        runtime_instance_id="runtime-1",
        package_dir=tmp_path / "deployment-1",
        entrypoint="worker.yaml",
        env={},
    )

    with pytest.raises(TimeoutError):
        asyncio.run(supervisor.check(spec, timeout_s=0.01))

    assert events == ["killed"]


def test_supervisor_check_without_timeout_waits_to_completion(tmp_path, monkeypatch) -> None:
    class FakeProcess:
        async def wait(self) -> int:
            return 7

    async def fake_create_subprocess_exec(*args, **kwargs) -> FakeProcess:
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    supervisor = SubprocessSupervisor(work_dir=tmp_path, max_concurrent_deployments=1)
    spec = DeploymentSpec(
        deployment_id="deployment-1",
        worker_agent_id="agent-1",
        runtime_instance_id="runtime-1",
        package_dir=tmp_path / "deployment-1",
        entrypoint="worker.yaml",
        env={},
    )

    returncode = asyncio.run(supervisor.check(spec))
    assert returncode == 7


# --- dependency installation ----------------------------------------------


def test_detect_install_mode_returns_package_for_pyproject(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    assert SubprocessSupervisor.detect_install_mode(tmp_path) == "package"


def test_detect_install_mode_returns_requirements_when_only_that_exists(tmp_path) -> None:
    (tmp_path / "requirements.txt").write_text("httpx>=0.28\n")
    assert SubprocessSupervisor.detect_install_mode(tmp_path) == "requirements"


def test_detect_install_mode_returns_none_when_no_declaration(tmp_path) -> None:
    (tmp_path / "worker.yaml").write_text("app:\n  name: demo\n")
    assert SubprocessSupervisor.detect_install_mode(tmp_path) is None


def test_detect_install_mode_returns_package_and_requirements_when_both_exist(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (tmp_path / "requirements.txt").write_text("httpx>=0.28\n")
    assert SubprocessSupervisor.detect_install_mode(tmp_path) == "package+requirements"


def test_venv_path_for_is_keyed_on_checksum(tmp_path) -> None:
    supervisor = SubprocessSupervisor(work_dir=tmp_path, max_concurrent_deployments=1)
    path = supervisor.venv_path_for("abc123")
    assert path == tmp_path / "venvs" / "abc123" / "venv"


def test_build_environment_prepends_venv_bin_to_path(tmp_path) -> None:
    supervisor = SubprocessSupervisor(work_dir=tmp_path, max_concurrent_deployments=1)
    venv_dir = tmp_path / "venvs" / "checksum" / "venv"
    spec = DeploymentSpec(
        deployment_id="deployment-1",
        worker_agent_id="agent-1",
        runtime_instance_id="runtime-1",
        package_dir=tmp_path / "package",
        entrypoint="worker.yaml",
        env={},
        venv_dir=venv_dir,
        onestep_executable=str(venv_dir / "bin" / "onestep"),
    )

    env = supervisor.build_environment(spec)

    venv_bin = str(venv_dir / "bin")
    assert env["PATH"].startswith(venv_bin + os.pathsep)
    assert env["VIRTUAL_ENV"] == str(venv_dir)


def test_resolve_onestep_executable_uses_venv_when_provided(tmp_path) -> None:
    venv_dir = tmp_path / "venv"
    expected = str(venv_dir / "bin" / "onestep")
    assert resolve_onestep_executable(venv_dir) == expected


def test_install_creates_venv_and_writes_marker_on_success(tmp_path, monkeypatch) -> None:
    # Both venv creation and pip install succeed -> the .installed marker is
    # written so the next deploy of this checksum reuses the venv.
    calls: list[list[str]] = []

    class FakeProcess:
        async def wait(self) -> int:
            return 0

    async def fake_create_subprocess_exec(*args, **kwargs) -> FakeProcess:
        calls.append(list(args))
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    (tmp_path / "package").mkdir()
    (tmp_path / "package" / "requirements.txt").write_text("httpx>=0.28\n")
    supervisor = SubprocessSupervisor(work_dir=tmp_path, max_concurrent_deployments=1)
    venv_dir = supervisor.venv_path_for("checksum-1")
    spec = DeploymentSpec(
        deployment_id="deployment-1",
        worker_agent_id="agent-1",
        runtime_instance_id="runtime-1",
        package_dir=tmp_path / "package",
        entrypoint="worker.yaml",
        env={},
        venv_dir=venv_dir,
        onestep_executable=str(venv_dir / "bin" / "onestep"),
    )

    asyncio.run(supervisor.install(spec, mode="requirements"))

    marker = venv_dir.parent / INSTALLED_MARKER
    assert marker.exists()
    # First call creates the venv, then the agent installs its default runtime
    # dependencies before package-specific dependencies.
    assert "venv" in calls[0]
    assert calls[1][1:] == ["-m", "pip", "install", *DEFAULT_RUNTIME_REQUIREMENTS]
    assert calls[2][1:] == ["-m", "pip", "install", "-r", "requirements.txt"]


def test_install_runs_package_and_requirements_when_both_are_declared(
    tmp_path,
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    class FakeProcess:
        async def wait(self) -> int:
            return 0

    async def fake_create_subprocess_exec(*args, **kwargs) -> FakeProcess:
        calls.append(list(args))
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    (tmp_path / "package").mkdir()
    (tmp_path / "package" / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (tmp_path / "package" / "requirements.txt").write_text("onestep-mysql>=0.3.0\n")
    supervisor = SubprocessSupervisor(work_dir=tmp_path, max_concurrent_deployments=1)
    venv_dir = supervisor.venv_path_for("checksum-1")
    spec = DeploymentSpec(
        deployment_id="deployment-1",
        worker_agent_id="agent-1",
        runtime_instance_id="runtime-1",
        package_dir=tmp_path / "package",
        entrypoint="worker.yaml",
        env={},
        venv_dir=venv_dir,
        onestep_executable=str(venv_dir / "bin" / "onestep"),
    )

    asyncio.run(supervisor.install(spec, mode="package+requirements"))

    assert calls[1][1:] == ["-m", "pip", "install", *DEFAULT_RUNTIME_REQUIREMENTS]
    assert calls[2][1:] == ["-m", "pip", "install", "."]
    assert calls[3][1:] == ["-m", "pip", "install", "-r", "requirements.txt"]
    assert (venv_dir.parent / INSTALLED_MARKER).exists()


def test_install_skips_when_current_marker_already_present(tmp_path, monkeypatch) -> None:
    # An already-installed venv (marker present) must not re-run venv or pip.
    def fake_create_subprocess_exec(*args, **kwargs):
        raise AssertionError("no subprocess should run when the venv is reused")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    (tmp_path / "package").mkdir()
    supervisor = SubprocessSupervisor(work_dir=tmp_path, max_concurrent_deployments=1)
    venv_dir = supervisor.venv_path_for("checksum-1")
    venv_dir.parent.mkdir(parents=True, exist_ok=True)
    (venv_dir.parent / INSTALLED_MARKER).write_text(_installed_marker_content())
    spec = DeploymentSpec(
        deployment_id="deployment-1",
        worker_agent_id="agent-1",
        runtime_instance_id="runtime-1",
        package_dir=tmp_path / "package",
        entrypoint="worker.yaml",
        env={},
        venv_dir=venv_dir,
        onestep_executable=str(venv_dir / "bin" / "onestep"),
    )

    asyncio.run(supervisor.install(spec, mode="requirements"))  # must not raise


def test_install_reruns_when_marker_predates_default_runtime_requirements(
    tmp_path,
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    class FakeProcess:
        async def wait(self) -> int:
            return 0

    async def fake_create_subprocess_exec(*args, **kwargs) -> FakeProcess:
        calls.append(list(args))
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    (tmp_path / "package").mkdir()
    (tmp_path / "package" / "requirements.txt").write_text("httpx>=0.28\n")
    supervisor = SubprocessSupervisor(work_dir=tmp_path, max_concurrent_deployments=1)
    venv_dir = supervisor.venv_path_for("checksum-1")
    venv_dir.parent.mkdir(parents=True, exist_ok=True)
    (venv_dir.parent / INSTALLED_MARKER).write_text("ok\n")
    spec = DeploymentSpec(
        deployment_id="deployment-1",
        worker_agent_id="agent-1",
        runtime_instance_id="runtime-1",
        package_dir=tmp_path / "package",
        entrypoint="worker.yaml",
        env={},
        venv_dir=venv_dir,
        onestep_executable=str(venv_dir / "bin" / "onestep"),
    )

    asyncio.run(supervisor.install(spec, mode="requirements"))

    assert calls[1][1:] == ["-m", "pip", "install", *DEFAULT_RUNTIME_REQUIREMENTS]
    assert (venv_dir.parent / INSTALLED_MARKER).read_text() == _installed_marker_content()


def test_install_raises_when_venv_creation_fails(tmp_path, monkeypatch) -> None:
    class FakeProcess:
        async def wait(self) -> int:
            return 1  # venv creation fails

    async def fake_create_subprocess_exec(*args, **kwargs) -> FakeProcess:
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    (tmp_path / "package").mkdir()
    supervisor = SubprocessSupervisor(work_dir=tmp_path, max_concurrent_deployments=1)
    venv_dir = supervisor.venv_path_for("checksum-fail")
    spec = DeploymentSpec(
        deployment_id="deployment-1",
        worker_agent_id="agent-1",
        runtime_instance_id="runtime-1",
        package_dir=tmp_path / "package",
        entrypoint="worker.yaml",
        env={},
        venv_dir=venv_dir,
        onestep_executable=str(venv_dir / "bin" / "onestep"),
    )

    with pytest.raises(InstallError, match="venv creation"):
        asyncio.run(supervisor.install(spec, mode="requirements"))
    # No marker written on failure.
    assert not (venv_dir.parent / INSTALLED_MARKER).exists()


def test_install_raises_when_pip_install_fails(tmp_path, monkeypatch) -> None:
    class FakeProcess:
        async def wait(self) -> int:
            return 0

    class FailProcess:
        async def wait(self) -> int:
            return 1  # pip install fails

    state = {"call": 0}

    async def fake_create_subprocess_exec(*args, **kwargs):
        state["call"] += 1
        return FakeProcess() if state["call"] < 3 else FailProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    (tmp_path / "package").mkdir()
    (tmp_path / "package" / "pyproject.toml").write_text("[project]\nname='demo'\n")
    supervisor = SubprocessSupervisor(work_dir=tmp_path, max_concurrent_deployments=1)
    venv_dir = supervisor.venv_path_for("checksum-pipfail")
    spec = DeploymentSpec(
        deployment_id="deployment-1",
        worker_agent_id="agent-1",
        runtime_instance_id="runtime-1",
        package_dir=tmp_path / "package",
        entrypoint="worker.yaml",
        env={},
        venv_dir=venv_dir,
        onestep_executable=str(venv_dir / "bin" / "onestep"),
    )

    with pytest.raises(InstallError, match="pip install"):
        asyncio.run(supervisor.install(spec, mode="package"))
    assert not (venv_dir.parent / INSTALLED_MARKER).exists()
