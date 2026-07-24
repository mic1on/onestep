from __future__ import annotations

from onestep_worker_agent.state import DeploymentState, DeploymentStateStore


def test_deployment_state_store_round_trip(tmp_path) -> None:
    store = DeploymentStateStore(tmp_path / "deployments.json")
    state = DeploymentState(
        deployment_id="deployment-1",
        runtime_instance_id="runtime-1",
        package_dir=tmp_path / "deployments" / "deployment-1" / "package",
        entrypoint="worker.yaml",
        env={"EXAMPLE": "1"},
        pid=123,
    )

    store.upsert(state)

    assert store.load_all() == {"deployment-1": state}

    store.remove("deployment-1")

    assert store.load_all() == {}
