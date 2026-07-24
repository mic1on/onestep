from __future__ import annotations

import hashlib
from uuid import uuid4

from onestep_control_plane_api.db.models import (
    WorkerAgent,
    WorkerDeployment,
    WorkflowPackage,
)
from sqlalchemy import select


def test_worker_agent_package_and_deployment_relationships(db_session) -> None:
    worker_agent_id = uuid4()
    package_id = uuid4()
    deployment_id = uuid4()
    checksum = hashlib.sha256(b"package").hexdigest()

    agent = WorkerAgent(
        worker_agent_id=worker_agent_id,
        display_name="dev-agent",
        status="online",
        execution_mode="subprocess",
        max_concurrent_deployments=2,
        used_slots=0,
        labels_json={"env": "dev"},
        capabilities_json=["deployment.start", "deployment.stop"],
        platform_json={"system": "Darwin"},
        connection_token_hash="a" * 64,
    )
    package = WorkflowPackage(
        package_id=package_id,
        workflow_id=uuid4(),
        version="1",
        filename="workflow.zip",
        content_type="application/zip",
        checksum_sha256=checksum,
        size_bytes=7,
        storage_path="/tmp/workflow.zip",
        entrypoint="worker.yaml",
        created_by="tester",
    )
    deployment = WorkerDeployment(
        deployment_id=deployment_id,
        workflow_package_id=package_id,
        worker_agent_id=worker_agent_id,
        desired_status="running",
        observed_status="assigned",
        execution_mode="subprocess",
        params_json={},
        env_json={},
        credential_refs_json=[],
        package_checksum=checksum,
        created_by="tester",
    )

    db_session.add_all([agent, package, deployment])
    db_session.commit()

    loaded = db_session.scalar(
        select(WorkerDeployment).where(WorkerDeployment.deployment_id == deployment_id)
    )
    assert loaded is not None
    assert loaded.worker_agent.display_name == "dev-agent"
    assert loaded.workflow_package.checksum_sha256 == checksum
