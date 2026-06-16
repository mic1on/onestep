from __future__ import annotations

import pytest

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
