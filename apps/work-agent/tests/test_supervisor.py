from __future__ import annotations

import asyncio

import pytest

from onestep_worker_agent.state import DeploymentState, DeploymentStateStore
from onestep_worker_agent.supervisor import DeploymentSpec, SubprocessSupervisor


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
