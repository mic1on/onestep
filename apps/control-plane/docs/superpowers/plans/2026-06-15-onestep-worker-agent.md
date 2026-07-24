# OneStep Worker Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the MVP worker-agent path where hosts register with `onestep-control-plane`, receive workflow package deployments, and run them as local `onestep` subprocesses.

**Architecture:** Add control-plane persistence and APIs for worker agents, workflow packages, deployments, and worker commands. Implement `onestep-worker-agent` in a separate sibling repository that persists local identity, connects to the plane, downloads packages, and supervises subprocesses. Keep the current runtime Agent WS/reporting code separate from the new worker-agent control channel.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Pydantic, pytest, httpx, websockets, asyncio subprocesses, existing `onestep` CLI/runtime.

---

## Execution Assumptions

- Start from the current `onestep-control-plane` worktree after the pipeline builder schema work is preserved or landed. The current head migration is `202606150001_add_pipeline_builder_tables.py`; worker-agent migrations use the next revision, `202606150002`.
- Do not modify the existing runtime `/api/v1/agents/ws` protocol except where tests prove a shared helper can be reused without behavior change.
- Keep package storage local to the control-plane API process for MVP. Store package blobs under a configured directory, not in the database.
- Create or use a sibling `onestep-worker-agent` repository for the CLI and container artifact. Do not vendor worker-agent CLI code into `onestep-control-plane`.

## File Structure

Control plane backend:

- Create `backend/alembic/versions/202606150002_add_worker_agent_tables.py`: creates worker-agent, package, deployment, command, and event tables.
- Modify `backend/src/onestep_control_plane_api/db/models.py`: adds ORM models and relationships.
- Modify `backend/src/onestep_control_plane_api/core/settings.py`: adds worker-agent registration tokens and package storage directory.
- Modify `backend/src/onestep_control_plane_api/api/security.py`: adds worker-agent registration and connection token helpers.
- Modify `backend/src/onestep_control_plane_api/api/schemas.py`: adds request/response schemas for worker agents, packages, deployments, and commands.
- Create `backend/src/onestep_control_plane_api/api/worker_agent_service.py`: registration, package, deployment, and command state transitions.
- Create `backend/src/onestep_control_plane_api/api/worker_agent_connection_registry.py`: in-memory active worker-agent WS registry.
- Create `backend/src/onestep_control_plane_api/api/routers/worker_agents.py`: HTTP APIs for registration, package upload/download, deployment creation, and queries.
- Create `backend/src/onestep_control_plane_api/api/routers/worker_agent_ws.py`: worker-agent control websocket.
- Modify `backend/src/onestep_control_plane_api/api/routers/__init__.py`: include new routers.
- Modify `backend/tests/conftest.py`: configure worker-agent registration token and package storage temp directory.
- Create `backend/tests/test_worker_agent_models.py`: migration and model tests.
- Create `backend/tests/test_worker_agent_api.py`: registration, package, and deployment API tests.
- Create `backend/tests/test_worker_agent_ws.py`: control-channel tests.

Worker-agent independent repository:

- Create `../onestep-worker-agent/pyproject.toml`: standalone package metadata and console script.
- Create `../onestep-worker-agent/README.md`: standalone worker-agent usage.
- Create `../onestep-worker-agent/src/onestep_worker_agent/__init__.py`: package metadata.
- Create `../onestep-worker-agent/src/onestep_worker_agent/config.py`: CLI config and environment loading.
- Create `../onestep-worker-agent/src/onestep_worker_agent/identity.py`: local identity state file.
- Create `../onestep-worker-agent/src/onestep_worker_agent/client.py`: HTTP and WS client for the control plane.
- Create `../onestep-worker-agent/src/onestep_worker_agent/packages.py`: package download, checksum, and extraction.
- Create `../onestep-worker-agent/src/onestep_worker_agent/supervisor.py`: subprocess lifecycle and slot management.
- Create `../onestep-worker-agent/src/onestep_worker_agent/cli.py`: `onestep-worker-agent start`.
- Create `../onestep-worker-agent/tests/test_identity.py`: identity persistence tests.
- Create `../onestep-worker-agent/tests/test_packages.py`: package extraction and checksum tests.
- Create `../onestep-worker-agent/tests/test_supervisor.py`: subprocess and slot tests.

---

### Task 1: Add Worker-Agent Persistence Models

**Files:**
- Create: `backend/alembic/versions/202606150002_add_worker_agent_tables.py`
- Modify: `backend/src/onestep_control_plane_api/db/models.py`
- Modify: `backend/tests/test_migrations.py`
- Create: `backend/tests/test_worker_agent_models.py`

- [ ] **Step 1: Write migration expectation tests**

Add these assertions to `backend/tests/test_migrations.py` inside `test_alembic_upgrade_head_creates_expected_schema`:

```python
    assert "worker_agents" in inspector.get_table_names()
    assert "worker_agent_sessions" in inspector.get_table_names()
    assert "workflow_packages" in inspector.get_table_names()
    assert "worker_deployments" in inspector.get_table_names()
    assert "worker_agent_commands" in inspector.get_table_names()
    assert "worker_deployment_events" in inspector.get_table_names()

    assert {column["name"] for column in inspector.get_columns("worker_agents")} == {
        "id",
        "worker_agent_id",
        "display_name",
        "status",
        "execution_mode",
        "max_concurrent_deployments",
        "used_slots",
        "labels_json",
        "capabilities_json",
        "agent_version",
        "onestep_version",
        "python_version",
        "platform_json",
        "connection_token_hash",
        "registered_at",
        "last_seen_at",
        "created_at",
        "updated_at",
    }
    assert {column["name"] for column in inspector.get_columns("worker_deployments")} == {
        "id",
        "deployment_id",
        "workflow_package_id",
        "worker_agent_id",
        "desired_status",
        "observed_status",
        "runtime_instance_id",
        "execution_mode",
        "params_json",
        "env_json",
        "credential_refs_json",
        "package_checksum",
        "last_error_code",
        "last_error_message",
        "assigned_at",
        "started_at",
        "finished_at",
        "created_by",
        "created_at",
        "updated_at",
    }
```

Update `HEAD_REVISION`:

```python
HEAD_REVISION = "202606150002"
```

- [ ] **Step 2: Run migration test and verify it fails**

Run:

```bash
uv run pytest backend/tests/test_migrations.py::test_alembic_upgrade_head_creates_expected_schema -q
```

Expected: FAIL because revision `202606150002` and the worker-agent tables do not exist.

- [ ] **Step 3: Add ORM models**

Append these model classes in `backend/src/onestep_control_plane_api/db/models.py` near the other top-level models. Keep existing pipeline builder models intact.

```python
class WorkerAgent(Base):
    __tablename__ = "worker_agents"
    __table_args__ = (
        sa.UniqueConstraint("worker_agent_id", name="uq_worker_agents_worker_agent_id"),
        sa.Index("ix_worker_agents_status_last_seen_at", "status", "last_seen_at"),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    worker_agent_id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    display_name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="offline")
    execution_mode: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="subprocess")
    max_concurrent_deployments: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    used_slots: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    labels_json: Mapped[dict[str, str]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    capabilities_json: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False, default=list)
    agent_version: Mapped[str | None] = mapped_column(sa.String(64))
    onestep_version: Mapped[str | None] = mapped_column(sa.String(64))
    python_version: Mapped[str | None] = mapped_column(sa.String(64))
    platform_json: Mapped[dict[str, object]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    connection_token_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    registered_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    sessions: Mapped[list[WorkerAgentSession]] = relationship(
        back_populates="worker_agent",
        cascade="all, delete-orphan",
    )
    deployments: Mapped[list[WorkerDeployment]] = relationship(
        back_populates="worker_agent",
        cascade="all, delete-orphan",
    )
```

Add these remaining model classes immediately after `WorkerAgent`:

```python
class WorkerAgentSession(Base):
    __tablename__ = "worker_agent_sessions"
    __table_args__ = (
        sa.UniqueConstraint("session_id", name="uq_worker_agent_sessions_session_id"),
        sa.Index(
            "ix_worker_agent_sessions_agent_status_connected_at",
            "worker_agent_id",
            "status",
            "connected_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    worker_agent_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("worker_agents.worker_agent_id", ondelete="CASCADE"),
        nullable=False,
    )
    protocol_version: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    capabilities_json: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False, default=list)
    accepted_capabilities_json: Mapped[list[str]] = mapped_column(
        JSON_TYPE,
        nullable=False,
        default=list,
    )
    connected_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_hello_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_message_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    disconnected_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    worker_agent: Mapped[WorkerAgent] = relationship(back_populates="sessions")
```

```python
class WorkflowPackage(Base):
    __tablename__ = "workflow_packages"
    __table_args__ = (
        sa.UniqueConstraint("package_id", name="uq_workflow_packages_package_id"),
        sa.Index("ix_workflow_packages_workflow_id_created_at", "workflow_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    package_id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    workflow_id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    version: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    filename: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    storage_path: Mapped[str] = mapped_column(sa.Text, nullable=False)
    entrypoint: Mapped[str] = mapped_column(sa.String(255), nullable=False, default="worker.yaml")
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    created_by: Mapped[str] = mapped_column(sa.String(255), nullable=False, default="system")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)

    deployments: Mapped[list[WorkerDeployment]] = relationship(back_populates="workflow_package")
```

```python
class WorkerDeployment(Base):
    __tablename__ = "worker_deployments"
    __table_args__ = (
        sa.UniqueConstraint("deployment_id", name="uq_worker_deployments_deployment_id"),
        sa.Index(
            "ix_worker_deployments_agent_observed_status",
            "worker_agent_id",
            "observed_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    deployment_id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    workflow_package_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("workflow_packages.package_id", ondelete="RESTRICT"),
        nullable=False,
    )
    worker_agent_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("worker_agents.worker_agent_id", ondelete="RESTRICT"),
        nullable=False,
    )
    desired_status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="running")
    observed_status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="pending")
    runtime_instance_id: Mapped[UUID | None] = mapped_column(sa.Uuid(as_uuid=True))
    execution_mode: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="subprocess")
    params_json: Mapped[dict[str, object]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    env_json: Mapped[dict[str, str]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    credential_refs_json: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False, default=list)
    package_checksum: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(sa.String(128))
    last_error_message: Mapped[str | None] = mapped_column(sa.Text)
    assigned_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_by: Mapped[str] = mapped_column(sa.String(255), nullable=False, default="system")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    worker_agent: Mapped[WorkerAgent] = relationship(back_populates="deployments")
    workflow_package: Mapped[WorkflowPackage] = relationship(back_populates="deployments")
```

```python
class WorkerAgentCommand(Base):
    __tablename__ = "worker_agent_commands"
    __table_args__ = (
        sa.UniqueConstraint("command_id", name="uq_worker_agent_commands_command_id"),
        sa.Index("ix_worker_agent_commands_agent_status", "worker_agent_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    command_id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    worker_agent_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("worker_agents.worker_agent_id", ondelete="CASCADE"),
        nullable=False,
    )
    deployment_id: Mapped[UUID | None] = mapped_column(sa.Uuid(as_uuid=True))
    session_id: Mapped[str | None] = mapped_column(sa.String(255))
    kind: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    args_json: Mapped[dict[str, object]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    timeout_s: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="pending")
    ack_status: Mapped[str | None] = mapped_column(sa.String(32))
    result_json: Mapped[dict[str, object] | None] = mapped_column(JSON_TYPE)
    error_code: Mapped[str | None] = mapped_column(sa.String(128))
    error_message: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    dispatched_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    acked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )
```

```python
class WorkerDeploymentEvent(Base):
    __tablename__ = "worker_deployment_events"
    __table_args__ = (
        sa.Index("ix_worker_deployment_events_deployment_created_at", "deployment_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    deployment_id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    worker_agent_id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    observed_status: Mapped[str | None] = mapped_column(sa.String(32))
    message: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    payload_json: Mapped[dict[str, object]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
```

- [ ] **Step 4: Add the Alembic migration**

Create `backend/alembic/versions/202606150002_add_worker_agent_tables.py` using the model fields above. Set:

```python
revision: str = "202606150002"
down_revision: str | None = "202606150001"
```

Use one `_has_table(table_name: str) -> bool` helper copied from
`backend/alembic/versions/202603170002_add_agent_sessions.py`. In `upgrade()`,
create these six tables when absent: `worker_agents`, `worker_agent_sessions`,
`workflow_packages`, `worker_deployments`, `worker_agent_commands`, and
`worker_deployment_events`. In `downgrade()`, drop them in reverse dependency
order. Create the exact indexes and constraints named in Step 3.

- [ ] **Step 5: Add focused model test**

Create `backend/tests/test_worker_agent_models.py`:

```python
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
```

- [ ] **Step 6: Run tests**

Run:

```bash
uv run pytest backend/tests/test_migrations.py::test_alembic_upgrade_head_creates_expected_schema backend/tests/test_worker_agent_models.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/alembic/versions/202606150002_add_worker_agent_tables.py backend/src/onestep_control_plane_api/db/models.py backend/tests/test_migrations.py backend/tests/test_worker_agent_models.py
git commit -m "feat: add worker agent persistence models"
```

---

### Task 2: Add Registration, Package, And Deployment HTTP APIs

**Files:**
- Modify: `backend/src/onestep_control_plane_api/core/settings.py`
- Modify: `backend/src/onestep_control_plane_api/api/security.py`
- Modify: `backend/src/onestep_control_plane_api/api/schemas.py`
- Create: `backend/src/onestep_control_plane_api/api/worker_agent_service.py`
- Create: `backend/src/onestep_control_plane_api/api/routers/worker_agents.py`
- Modify: `backend/src/onestep_control_plane_api/api/routers/__init__.py`
- Modify: `backend/tests/conftest.py`
- Create: `backend/tests/test_worker_agent_api.py`

- [ ] **Step 1: Write failing API tests**

Create `backend/tests/test_worker_agent_api.py`:

```python
from __future__ import annotations

import hashlib
import io
import zipfile
from uuid import UUID

from onestep_control_plane_api.db.models import WorkerAgent, WorkerDeployment, WorkflowPackage
from sqlalchemy import select


def build_package_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("worker.yaml", "app:\n  name: demo\nresources: {}\ntasks: []\n")
        archive.writestr("src/demo_worker/__init__.py", "")
    return buffer.getvalue()


def register_agent(client) -> dict[str, object]:
    response = client.post(
        "/api/v1/worker-agents/register",
        json={
            "registration_token": "test-worker-registration-token",
            "display_name": "dev-agent",
            "agent_version": "0.1.0",
            "onestep_version": "1.4.2",
            "python_version": "3.11.14",
            "execution_mode": "subprocess",
            "max_concurrent_deployments": 2,
            "labels": {"env": "dev"},
            "capabilities": ["deployment.start", "deployment.stop", "deployment.restart"],
            "platform": {"system": "Darwin", "machine": "arm64"},
        },
    )
    assert response.status_code == 200
    return response.json()


def test_register_worker_agent_persists_identity(client, db_session) -> None:
    payload = register_agent(client)

    worker_agent_id = UUID(payload["worker_agent_id"])
    assert payload["connection_token"]
    assert payload["heartbeat_interval_s"] == 30

    agent = db_session.scalar(
        select(WorkerAgent).where(WorkerAgent.worker_agent_id == worker_agent_id)
    )
    assert agent is not None
    assert agent.display_name == "dev-agent"
    assert agent.status == "offline"
    assert agent.max_concurrent_deployments == 2
    assert agent.labels_json == {"env": "dev"}


def test_upload_package_and_create_deployment(client, db_session) -> None:
    registered = register_agent(client)
    package_bytes = build_package_bytes()
    checksum = hashlib.sha256(package_bytes).hexdigest()

    upload = client.post(
        "/api/v1/workflow-packages",
        data={
            "workflow_id": "11111111-1111-4111-8111-111111111111",
            "version": "1",
            "entrypoint": "worker.yaml",
        },
        files={"file": ("workflow.zip", package_bytes, "application/zip")},
    )
    assert upload.status_code == 200
    uploaded = upload.json()
    assert uploaded["checksum_sha256"] == checksum

    deployment = client.post(
        "/api/v1/worker-deployments",
        json={
            "workflow_package_id": uploaded["package_id"],
            "worker_agent_id": registered["worker_agent_id"],
            "params": {},
            "env": {},
            "credential_refs": [],
        },
    )
    assert deployment.status_code == 200
    created = deployment.json()
    assert created["desired_status"] == "running"
    assert created["observed_status"] == "assigned"
    assert created["package_checksum"] == checksum

    assert db_session.scalar(select(WorkflowPackage)) is not None
    assert db_session.scalar(select(WorkerDeployment)) is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest backend/tests/test_worker_agent_api.py -q
```

Expected: FAIL with 404 for `/api/v1/worker-agents/register`.

- [ ] **Step 3: Add settings and test fixtures**

In `backend/src/onestep_control_plane_api/core/settings.py`, add:

```python
    worker_agent_registration_tokens: Annotated[list[str], NoDecode] = Field(default_factory=list)
    worker_package_storage_dir: str = ".onestep-control-plane/packages"
```

Update the validator decorator:

```python
    @field_validator(
        "ingest_tokens",
        "cors_allow_origins",
        "worker_agent_registration_tokens",
        mode="before",
    )
```

In `backend/tests/conftest.py`, set the registration token and storage dir in the `client` fixture:

```python
    original_worker_tokens = settings.worker_agent_registration_tokens
    original_package_dir = settings.worker_package_storage_dir
    settings.worker_agent_registration_tokens = ["test-worker-registration-token"]
    settings.worker_package_storage_dir = str(tmp_path / "packages")
```

Restore both values after the test client exits.

- [ ] **Step 4: Add security helpers**

In `backend/src/onestep_control_plane_api/api/security.py`, add:

```python
import hashlib
```

Then add:

```python
def hash_worker_agent_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validate_worker_registration_token(token: str) -> bool:
    for configured_token in settings.worker_agent_registration_tokens:
        if secrets.compare_digest(token, configured_token):
            return True
    return False
```

- [ ] **Step 5: Add schemas**

In `backend/src/onestep_control_plane_api/api/schemas.py`, add literal types:

```python
WorkerAgentExecutionMode = Literal["subprocess"]
WorkerAgentStatus = Literal["offline", "online", "draining"]
WorkerDeploymentDesiredStatus = Literal["running", "stopped"]
WorkerDeploymentObservedStatus = Literal[
    "pending",
    "assigned",
    "preparing",
    "checking",
    "running",
    "stopping",
    "stopped",
    "failed",
    "cancelled",
]
```

Add request and response models:

```python
class WorkerAgentRegisterRequest(APIModel):
    registration_token: str = Field(min_length=1)
    display_name: str = Field(min_length=1, max_length=255)
    agent_version: str | None = Field(default=None, max_length=64)
    onestep_version: str | None = Field(default=None, max_length=64)
    python_version: str | None = Field(default=None, max_length=64)
    execution_mode: WorkerAgentExecutionMode = "subprocess"
    max_concurrent_deployments: int = Field(ge=1, le=256)
    labels: dict[str, str] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)
    platform: dict[str, Any] = Field(default_factory=dict)


class WorkerAgentRegisterResponse(APIModel):
    worker_agent_id: UUID
    connection_token: str
    heartbeat_interval_s: int
    accepted_capabilities: list[str]


class WorkflowPackageSummary(APIModel):
    package_id: UUID
    workflow_id: UUID
    version: str
    filename: str
    checksum_sha256: str
    size_bytes: int
    entrypoint: str
    created_at: datetime


class WorkerDeploymentCreateRequest(APIModel):
    workflow_package_id: UUID
    worker_agent_id: UUID
    params: dict[str, Any] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    credential_refs: list[str] = Field(default_factory=list)


class WorkerDeploymentSummary(APIModel):
    deployment_id: UUID
    workflow_package_id: UUID
    worker_agent_id: UUID
    desired_status: WorkerDeploymentDesiredStatus
    observed_status: WorkerDeploymentObservedStatus
    runtime_instance_id: UUID | None
    execution_mode: WorkerAgentExecutionMode
    package_checksum: str
    last_error_code: str | None
    last_error_message: str | None
    created_at: datetime
    updated_at: datetime
```

- [ ] **Step 6: Add service functions**

Create `backend/src/onestep_control_plane_api/api/worker_agent_service.py`:

```python
from __future__ import annotations

import hashlib
import secrets
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.common import utcnow
from onestep_control_plane_api.api.schemas import WorkerAgentRegisterRequest
from onestep_control_plane_api.api.security import (
    hash_worker_agent_token,
    validate_worker_registration_token,
)
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db.models import WorkerAgent, WorkerDeployment, WorkflowPackage


ACCEPTED_WORKER_CAPABILITIES = {
    "deployment.start",
    "deployment.stop",
    "deployment.restart",
    "agent.sync_state",
}


def register_worker_agent(db: Session, request: WorkerAgentRegisterRequest) -> tuple[WorkerAgent, str]:
    if not validate_worker_registration_token(request.registration_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid registration token")

    connection_token = secrets.token_urlsafe(32)
    agent = WorkerAgent(
        worker_agent_id=uuid4(),
        display_name=request.display_name,
        status="offline",
        execution_mode=request.execution_mode,
        max_concurrent_deployments=request.max_concurrent_deployments,
        used_slots=0,
        labels_json=request.labels,
        capabilities_json=[
            capability
            for capability in request.capabilities
            if capability in ACCEPTED_WORKER_CAPABILITIES
        ],
        agent_version=request.agent_version,
        onestep_version=request.onestep_version,
        python_version=request.python_version,
        platform_json=request.platform,
        connection_token_hash=hash_worker_agent_token(connection_token),
        registered_at=utcnow(),
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent, connection_token


async def store_workflow_package(
    db: Session,
    *,
    workflow_id: UUID,
    version: str,
    entrypoint: str,
    upload: UploadFile,
    created_by: str,
) -> WorkflowPackage:
    content = await upload.read()
    checksum = hashlib.sha256(content).hexdigest()
    package_id = uuid4()
    storage_root = Path(settings.worker_package_storage_dir)
    storage_root.mkdir(parents=True, exist_ok=True)
    storage_path = storage_root / f"{package_id}.zip"
    storage_path.write_bytes(content)

    package = WorkflowPackage(
        package_id=package_id,
        workflow_id=workflow_id,
        version=version,
        filename=upload.filename or f"{package_id}.zip",
        content_type=upload.content_type or "application/octet-stream",
        checksum_sha256=checksum,
        size_bytes=len(content),
        storage_path=str(storage_path),
        entrypoint=entrypoint,
        metadata_json={},
        created_by=created_by,
    )
    db.add(package)
    db.commit()
    db.refresh(package)
    return package


def create_worker_deployment(
    db: Session,
    *,
    package_id: UUID,
    worker_agent_id: UUID,
    params: dict[str, object],
    env: dict[str, str],
    credential_refs: list[str],
    created_by: str,
) -> WorkerDeployment:
    package = db.scalar(select(WorkflowPackage).where(WorkflowPackage.package_id == package_id))
    if package is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workflow package not found")
    agent = db.scalar(select(WorkerAgent).where(WorkerAgent.worker_agent_id == worker_agent_id))
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="worker agent not found")

    deployment = WorkerDeployment(
        deployment_id=uuid4(),
        workflow_package_id=package.package_id,
        worker_agent_id=agent.worker_agent_id,
        desired_status="running",
        observed_status="assigned",
        execution_mode="subprocess",
        params_json=params,
        env_json=env,
        credential_refs_json=credential_refs,
        package_checksum=package.checksum_sha256,
        assigned_at=utcnow(),
        created_by=created_by,
    )
    db.add(deployment)
    db.commit()
    db.refresh(deployment)
    return deployment
```

- [ ] **Step 7: Add router**

Create `backend/src/onestep_control_plane_api/api/routers/worker_agents.py`:

```python
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.schemas import (
    WorkerAgentRegisterRequest,
    WorkerAgentRegisterResponse,
    WorkerDeploymentCreateRequest,
    WorkerDeploymentSummary,
    WorkflowPackageSummary,
)
from onestep_control_plane_api.api.worker_agent_service import (
    create_worker_deployment,
    register_worker_agent,
    store_workflow_package,
)
from onestep_control_plane_api.db.session import get_db_session

router = APIRouter(tags=["worker-agents"])


@router.post("/api/v1/worker-agents/register", response_model=WorkerAgentRegisterResponse)
def register(request: WorkerAgentRegisterRequest, db: Session = Depends(get_db_session)):
    agent, connection_token = register_worker_agent(db, request)
    return WorkerAgentRegisterResponse(
        worker_agent_id=agent.worker_agent_id,
        connection_token=connection_token,
        heartbeat_interval_s=30,
        accepted_capabilities=agent.capabilities_json,
    )


@router.post("/api/v1/workflow-packages", response_model=WorkflowPackageSummary)
async def upload_workflow_package(
    workflow_id: UUID = Form(...),
    version: str = Form(...),
    entrypoint: str = Form("worker.yaml"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db_session),
):
    package = await store_workflow_package(
        db,
        workflow_id=workflow_id,
        version=version,
        entrypoint=entrypoint,
        upload=file,
        created_by="system",
    )
    return WorkflowPackageSummary.model_validate(package, from_attributes=True)


@router.post("/api/v1/worker-deployments", response_model=WorkerDeploymentSummary)
def create_deployment(
    request: WorkerDeploymentCreateRequest,
    db: Session = Depends(get_db_session),
):
    deployment = create_worker_deployment(
        db,
        package_id=request.workflow_package_id,
        worker_agent_id=request.worker_agent_id,
        params=request.params,
        env=request.env,
        credential_refs=request.credential_refs,
        created_by="system",
    )
    return WorkerDeploymentSummary.model_validate(deployment, from_attributes=True)
```

Update `backend/src/onestep_control_plane_api/api/routers/__init__.py`:

```python
from onestep_control_plane_api.api.routers.worker_agents import router as worker_agents_router
```

Include it before `agent_ws_router`:

```python
router.include_router(worker_agents_router)
```

- [ ] **Step 8: Run tests**

Run:

```bash
uv run pytest backend/tests/test_worker_agent_api.py backend/tests/test_worker_agent_models.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/src/onestep_control_plane_api/core/settings.py backend/src/onestep_control_plane_api/api/security.py backend/src/onestep_control_plane_api/api/schemas.py backend/src/onestep_control_plane_api/api/worker_agent_service.py backend/src/onestep_control_plane_api/api/routers/worker_agents.py backend/src/onestep_control_plane_api/api/routers/__init__.py backend/tests/conftest.py backend/tests/test_worker_agent_api.py
git commit -m "feat: add worker agent registration and deployment APIs"
```

---

### Task 3: Add Worker-Agent Control WebSocket

**Files:**
- Create: `backend/src/onestep_control_plane_api/api/worker_agent_connection_registry.py`
- Create: `backend/src/onestep_control_plane_api/api/routers/worker_agent_ws.py`
- Modify: `backend/src/onestep_control_plane_api/api/schemas.py`
- Modify: `backend/src/onestep_control_plane_api/api/worker_agent_service.py`
- Modify: `backend/src/onestep_control_plane_api/api/routers/__init__.py`
- Create: `backend/tests/test_worker_agent_ws.py`

- [ ] **Step 1: Write failing WS test**

Create `backend/tests/test_worker_agent_ws.py`:

```python
from __future__ import annotations

from uuid import UUID

from onestep_control_plane_api.db.models import WorkerAgent, WorkerAgentSession
from sqlalchemy import select

from backend.tests.test_worker_agent_api import register_agent


def test_worker_agent_ws_hello_and_heartbeat(client, db_session) -> None:
    registered = register_agent(client)
    worker_agent_id = registered["worker_agent_id"]
    token = registered["connection_token"]

    with client.websocket_connect(
        "/api/v1/worker-agents/ws",
        headers={"Authorization": f"Bearer {token}"},
    ) as websocket:
        websocket.send_json(
            {
                "type": "hello",
                "message_id": "msg_1",
                "sent_at": "2026-06-15T12:00:00Z",
                "payload": {
                    "protocol_version": "1",
                    "worker_agent_id": worker_agent_id,
                    "capabilities": ["deployment.start", "deployment.stop"],
                    "max_concurrent_deployments": 2,
                    "used_slots": 0,
                    "running_deployments": [],
                },
            }
        )
        response = websocket.receive_json()
        assert response["type"] == "hello_ack"
        assert response["payload"]["heartbeat_interval_s"] == 30

        websocket.send_json(
            {
                "type": "heartbeat",
                "message_id": "msg_2",
                "sent_at": "2026-06-15T12:00:05Z",
                "payload": {
                    "worker_agent_id": worker_agent_id,
                    "used_slots": 0,
                    "running_deployments": [],
                    "recent_errors": [],
                },
            }
        )

    db_session.expire_all()
    agent = db_session.scalar(
        select(WorkerAgent).where(WorkerAgent.worker_agent_id == UUID(worker_agent_id))
    )
    session = db_session.scalar(select(WorkerAgentSession))
    assert agent is not None
    assert agent.status == "offline"
    assert agent.used_slots == 0
    assert session is not None
    assert session.status == "disconnected"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest backend/tests/test_worker_agent_ws.py -q
```

Expected: FAIL with 404 for `/api/v1/worker-agents/ws`.

- [ ] **Step 3: Add WS schemas**

Add to `backend/src/onestep_control_plane_api/api/schemas.py`:

```python
WorkerAgentWsMessageType = Literal[
    "hello",
    "hello_ack",
    "heartbeat",
    "command",
    "command_ack",
    "command_result",
    "deployment_event",
    "error",
]


class WorkerAgentWsEnvelope(APIModel):
    type: WorkerAgentWsMessageType
    message_id: str = Field(min_length=1)
    sent_at: datetime
    payload: dict[str, Any]


class WorkerAgentHelloPayload(APIModel):
    protocol_version: str = Field(min_length=1)
    worker_agent_id: UUID
    capabilities: list[str] = Field(default_factory=list)
    max_concurrent_deployments: int = Field(ge=1)
    used_slots: int = Field(ge=0)
    running_deployments: list[UUID] = Field(default_factory=list)


class WorkerAgentHelloAckPayload(APIModel):
    session_id: str
    protocol_version: str
    heartbeat_interval_s: int
    accepted_capabilities: list[str]
    server_time: datetime


class WorkerAgentHeartbeatPayload(APIModel):
    worker_agent_id: UUID
    used_slots: int = Field(ge=0)
    running_deployments: list[UUID] = Field(default_factory=list)
    recent_errors: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Add registry**

Create `backend/src/onestep_control_plane_api/api/worker_agent_connection_registry.py`:

```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class WorkerAgentConnection:
    worker_agent_id: UUID
    session_id: str
    send_queue: asyncio.Queue[dict[str, object]]


class WorkerAgentConnectionRegistry:
    def __init__(self) -> None:
        self._connections: dict[UUID, WorkerAgentConnection] = {}
        self._lock = asyncio.Lock()

    async def register(
        self,
        *,
        worker_agent_id: UUID,
        session_id: str,
        send_queue: asyncio.Queue[dict[str, object]],
    ) -> WorkerAgentConnection:
        async with self._lock:
            connection = WorkerAgentConnection(
                worker_agent_id=worker_agent_id,
                session_id=session_id,
                send_queue=send_queue,
            )
            self._connections[worker_agent_id] = connection
            return connection

    async def unregister(self, *, worker_agent_id: UUID, session_id: str) -> None:
        async with self._lock:
            existing = self._connections.get(worker_agent_id)
            if existing is not None and existing.session_id == session_id:
                self._connections.pop(worker_agent_id, None)

    async def get(self, worker_agent_id: UUID) -> WorkerAgentConnection | None:
        async with self._lock:
            return self._connections.get(worker_agent_id)


worker_agent_connection_registry = WorkerAgentConnectionRegistry()
```

- [ ] **Step 5: Add router**

Create `backend/src/onestep_control_plane_api/api/routers/worker_agent_ws.py` with these behaviors:

```python
from __future__ import annotations

import asyncio
import secrets
import uuid

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.common import utcnow
from onestep_control_plane_api.api.schemas import (
    WorkerAgentHeartbeatPayload,
    WorkerAgentHelloAckPayload,
    WorkerAgentHelloPayload,
    WorkerAgentWsEnvelope,
)
from onestep_control_plane_api.api.security import hash_worker_agent_token
from onestep_control_plane_api.api.worker_agent_connection_registry import (
    worker_agent_connection_registry,
)
from onestep_control_plane_api.db.models import WorkerAgent, WorkerAgentSession
from onestep_control_plane_api.db.session import get_db_session

router = APIRouter(prefix="/api/v1/worker-agents", tags=["worker-agent-ws"])

SUPPORTED_WORKER_AGENT_PROTOCOL_VERSION = "1"


def _new_session_id() -> str:
    return f"worker_sess_{uuid.uuid4().hex}"


def _new_message_id() -> str:
    return f"msg_{uuid.uuid4().hex}"


def _bearer_token(websocket: WebSocket) -> str | None:
    authorization = websocket.headers.get("authorization")
    if authorization is None:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def _send_loop(websocket: WebSocket, send_queue: asyncio.Queue[dict[str, object]]) -> None:
    while True:
        await websocket.send_json(await send_queue.get())


@router.websocket("/ws")
async def worker_agent_ws(websocket: WebSocket, db: Session = Depends(get_db_session)) -> None:
    token = _bearer_token(websocket)
    if token is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="missing bearer token")
        return

    await websocket.accept()
    context_worker_agent_id = None
    context_session_id = None
    send_task = None
    try:
        raw = await websocket.receive_json()
        envelope = WorkerAgentWsEnvelope.model_validate(raw)
        if envelope.type != "hello":
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="hello required")
            return
        hello = WorkerAgentHelloPayload.model_validate(envelope.payload)
        if hello.protocol_version != SUPPORTED_WORKER_AGENT_PROTOCOL_VERSION:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="unsupported protocol")
            return
        agent = db.scalar(
            select(WorkerAgent).where(WorkerAgent.worker_agent_id == hello.worker_agent_id)
        )
        if agent is None or not secrets.compare_digest(
            agent.connection_token_hash,
            hash_worker_agent_token(token),
        ):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="invalid token")
            return

        now = utcnow()
        session_id = _new_session_id()
        db.execute(
            update(WorkerAgentSession)
            .where(
                WorkerAgentSession.worker_agent_id == hello.worker_agent_id,
                WorkerAgentSession.status == "active",
            )
            .values(status="disconnected", disconnected_at=now, updated_at=now)
        )
        db.add(
            WorkerAgentSession(
                session_id=session_id,
                worker_agent_id=hello.worker_agent_id,
                protocol_version=hello.protocol_version,
                status="active",
                capabilities_json=hello.capabilities,
                accepted_capabilities_json=hello.capabilities,
                connected_at=now,
                last_hello_at=now,
                last_message_at=now,
            )
        )
        agent.status = "online"
        agent.used_slots = hello.used_slots
        agent.last_seen_at = now
        db.commit()

        send_queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        await worker_agent_connection_registry.register(
            worker_agent_id=hello.worker_agent_id,
            session_id=session_id,
            send_queue=send_queue,
        )
        context_worker_agent_id = hello.worker_agent_id
        context_session_id = session_id
        send_task = asyncio.create_task(_send_loop(websocket, send_queue))

        await websocket.send_json(
            {
                "type": "hello_ack",
                "message_id": _new_message_id(),
                "sent_at": now.isoformat(),
                "payload": WorkerAgentHelloAckPayload(
                    session_id=session_id,
                    protocol_version=SUPPORTED_WORKER_AGENT_PROTOCOL_VERSION,
                    heartbeat_interval_s=30,
                    accepted_capabilities=hello.capabilities,
                    server_time=now,
                ).model_dump(mode="json"),
            }
        )

        while True:
            message = WorkerAgentWsEnvelope.model_validate(await websocket.receive_json())
            if message.type == "heartbeat":
                heartbeat = WorkerAgentHeartbeatPayload.model_validate(message.payload)
                occurred_at = utcnow()
                db.execute(
                    update(WorkerAgent)
                    .where(WorkerAgent.worker_agent_id == heartbeat.worker_agent_id)
                    .values(
                        status="online",
                        used_slots=heartbeat.used_slots,
                        last_seen_at=occurred_at,
                        updated_at=occurred_at,
                    )
                )
                db.execute(
                    update(WorkerAgentSession)
                    .where(WorkerAgentSession.session_id == session_id)
                    .values(last_message_at=occurred_at, updated_at=occurred_at)
                )
                db.commit()
    except WebSocketDisconnect:
        pass
    finally:
        if send_task is not None:
            send_task.cancel()
        if context_worker_agent_id is not None and context_session_id is not None:
            now = utcnow()
            await worker_agent_connection_registry.unregister(
                worker_agent_id=context_worker_agent_id,
                session_id=context_session_id,
            )
            db.execute(
                update(WorkerAgent)
                .where(WorkerAgent.worker_agent_id == context_worker_agent_id)
                .values(status="offline", updated_at=now)
            )
            db.execute(
                update(WorkerAgentSession)
                .where(WorkerAgentSession.session_id == context_session_id)
                .values(status="disconnected", disconnected_at=now, updated_at=now)
            )
            db.commit()
```

Update router imports:

```python
from onestep_control_plane_api.api.routers.worker_agent_ws import router as worker_agent_ws_router
```

Include it:

```python
router.include_router(worker_agent_ws_router)
```

- [ ] **Step 6: Run WS test**

Run:

```bash
uv run pytest backend/tests/test_worker_agent_ws.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/src/onestep_control_plane_api/api/worker_agent_connection_registry.py backend/src/onestep_control_plane_api/api/routers/worker_agent_ws.py backend/src/onestep_control_plane_api/api/schemas.py backend/src/onestep_control_plane_api/api/routers/__init__.py backend/tests/test_worker_agent_ws.py
git commit -m "feat: add worker agent control websocket"
```

---

### Task 4: Add Worker-Agent CLI Identity And Registration

**Files:**
- Create: `../onestep-worker-agent/pyproject.toml`
- Create: `../onestep-worker-agent/README.md`
- Create: `../onestep-worker-agent/src/onestep_worker_agent/__init__.py`
- Create: `../onestep-worker-agent/src/onestep_worker_agent/config.py`
- Create: `../onestep-worker-agent/src/onestep_worker_agent/identity.py`
- Create: `../onestep-worker-agent/src/onestep_worker_agent/client.py`
- Create: `../onestep-worker-agent/src/onestep_worker_agent/cli.py`
- Create: `../onestep-worker-agent/tests/test_identity.py`

- [ ] **Step 1: Write identity tests**

Create `../onestep-worker-agent/tests/test_identity.py`:

```python
from __future__ import annotations

from uuid import UUID

from onestep_worker_agent.identity import AgentIdentity, load_identity, save_identity


def test_save_and_load_identity(tmp_path) -> None:
    path = tmp_path / "identity.json"
    identity = AgentIdentity(
        worker_agent_id=UUID("11111111-1111-4111-8111-111111111111"),
        connection_token="secret-token",
    )

    save_identity(path, identity)

    loaded = load_identity(path)
    assert loaded == identity
```

- [ ] **Step 2: Run identity test to verify it fails**

Run:

```bash
cd ../onestep-worker-agent
uv run pytest tests/test_identity.py -q
```

Expected: FAIL because `onestep_worker_agent` does not exist.

- [ ] **Step 3: Add standalone package and pyproject wiring**

Create `../onestep-worker-agent/pyproject.toml`:

```toml
[project]
name = "onestep-worker-agent"
version = "0.1.0a0"
description = "Execution host agent for OneStep Control Plane deployments."
requires-python = ">=3.11"
dependencies = [
  "httpx>=0.28.0",
  "websockets>=12.0",
]

[project.optional-dependencies]
test = [
  "pytest>=8.4.0",
]
dev = [
  "pytest>=8.4.0",
  "ruff>=0.13.0",
]

[project.scripts]
onestep-worker-agent = "onestep_worker_agent.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/onestep_worker_agent"]

[tool.pytest.ini_options]
addopts = "-q"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP"]
```

Create `../onestep-worker-agent/README.md`:

~~~markdown
# onestep-worker-agent

Execution host agent for OneStep Control Plane deployments.

```bash
export ONESTEP_PLANE_URL=http://localhost:8000
export ONESTEP_AGENT_REGISTRATION_TOKEN=dev-token
onestep-worker-agent start
```
~~~

Create `../onestep-worker-agent/src/onestep_worker_agent/__init__.py`:

```python
__version__ = "0.1.0a0"
```

- [ ] **Step 4: Add identity module**

Create `../onestep-worker-agent/src/onestep_worker_agent/identity.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID


@dataclass(frozen=True)
class AgentIdentity:
    worker_agent_id: UUID
    connection_token: str


def load_identity(path: Path) -> AgentIdentity | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text())
    return AgentIdentity(
        worker_agent_id=UUID(payload["worker_agent_id"]),
        connection_token=str(payload["connection_token"]),
    )


def save_identity(path: Path, identity: AgentIdentity) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "worker_agent_id": str(identity.worker_agent_id),
                "connection_token": identity.connection_token,
            },
            indent=2,
            sort_keys=True,
        )
    )
```

- [ ] **Step 5: Add config, client, and CLI skeleton**

Create `../onestep-worker-agent/src/onestep_worker_agent/config.py`:

```python
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AgentConfig:
    plane_url: str
    registration_token: str
    work_dir: Path
    identity_path: Path
    display_name: str
    max_concurrent_deployments: int


def load_config_from_env() -> AgentConfig:
    plane_url = os.environ["ONESTEP_PLANE_URL"].rstrip("/")
    work_dir = Path(os.environ.get("ONESTEP_WORKER_AGENT_DIR", ".onestep-worker-agent"))
    return AgentConfig(
        plane_url=plane_url,
        registration_token=os.environ.get("ONESTEP_AGENT_REGISTRATION_TOKEN", ""),
        work_dir=work_dir,
        identity_path=work_dir / "identity.json",
        display_name=os.environ.get("ONESTEP_WORKER_AGENT_NAME", "worker-agent"),
        max_concurrent_deployments=int(os.environ.get("ONESTEP_WORKER_AGENT_MAX_CONCURRENCY", "1")),
    )
```

Create `../onestep-worker-agent/src/onestep_worker_agent/client.py`:

```python
from __future__ import annotations

import platform
import sys
from uuid import UUID

import httpx

from onestep_worker_agent import __version__
from onestep_worker_agent.config import AgentConfig
from onestep_worker_agent.identity import AgentIdentity


async def register_agent(config: AgentConfig) -> AgentIdentity:
    async with httpx.AsyncClient(base_url=config.plane_url, timeout=30.0) as client:
        response = await client.post(
            "/api/v1/worker-agents/register",
            json={
                "registration_token": config.registration_token,
                "display_name": config.display_name,
                "agent_version": __version__,
                "onestep_version": None,
                "python_version": platform.python_version(),
                "execution_mode": "subprocess",
                "max_concurrent_deployments": config.max_concurrent_deployments,
                "labels": {},
                "capabilities": ["deployment.start", "deployment.stop", "deployment.restart"],
                "platform": {
                    "system": platform.system(),
                    "machine": platform.machine(),
                    "python_executable": sys.executable,
                },
            },
        )
        response.raise_for_status()
        payload = response.json()
        return AgentIdentity(
            worker_agent_id=UUID(payload["worker_agent_id"]),
            connection_token=payload["connection_token"],
        )
```

Create `../onestep-worker-agent/src/onestep_worker_agent/cli.py`:

```python
from __future__ import annotations

import argparse
import asyncio

from onestep_worker_agent.client import register_agent
from onestep_worker_agent.config import load_config_from_env
from onestep_worker_agent.identity import load_identity, save_identity


async def start() -> None:
    config = load_config_from_env()
    identity = load_identity(config.identity_path)
    if identity is None:
        identity = await register_agent(config)
        save_identity(config.identity_path, identity)
    print(f"worker agent ready: {identity.worker_agent_id}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="onestep-worker-agent")
    parser.add_argument("command", choices=["start"])
    args = parser.parse_args()
    if args.command == "start":
        asyncio.run(start())
```

- [ ] **Step 6: Run identity test**

Run:

```bash
cd ../onestep-worker-agent
uv run pytest tests/test_identity.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd ../onestep-worker-agent
git add pyproject.toml README.md src/onestep_worker_agent tests/test_identity.py
git commit -m "feat: add worker agent cli identity bootstrap"
```

---

### Task 5: Add Package Handling And Subprocess Supervisor

**Files:**
- Create: `../onestep-worker-agent/src/onestep_worker_agent/packages.py`
- Create: `../onestep-worker-agent/src/onestep_worker_agent/supervisor.py`
- Create: `../onestep-worker-agent/tests/test_packages.py`
- Create: `../onestep-worker-agent/tests/test_supervisor.py`

- [ ] **Step 1: Write package tests**

Create `../onestep-worker-agent/tests/test_packages.py`:

```python
from __future__ import annotations

import hashlib
import io
import zipfile

import pytest

from onestep_worker_agent.packages import extract_package


def build_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("worker.yaml", "app:\n  name: demo\n")
    return buffer.getvalue()


def test_extract_package_verifies_checksum(tmp_path) -> None:
    content = build_zip()
    checksum = hashlib.sha256(content).hexdigest()
    target = tmp_path / "deployment"

    extract_package(content, checksum, target)

    assert (target / "worker.yaml").read_text() == "app:\n  name: demo\n"


def test_extract_package_rejects_checksum_mismatch(tmp_path) -> None:
    with pytest.raises(ValueError, match="package checksum mismatch"):
        extract_package(build_zip(), "0" * 64, tmp_path / "deployment")
```

- [ ] **Step 2: Write supervisor tests**

Create `../onestep-worker-agent/tests/test_supervisor.py`:

```python
from __future__ import annotations

import asyncio

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
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
cd ../onestep-worker-agent
uv run pytest tests/test_packages.py tests/test_supervisor.py -q
```

Expected: FAIL because modules do not exist.

- [ ] **Step 4: Add package extraction**

Create `../onestep-worker-agent/src/onestep_worker_agent/packages.py`:

```python
from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path


def extract_package(content: bytes, checksum_sha256: str, target_dir: Path) -> None:
    actual = hashlib.sha256(content).hexdigest()
    if actual != checksum_sha256:
        raise ValueError("package checksum mismatch")
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        archive.extractall(target_dir)
```

- [ ] **Step 5: Add supervisor**

Create `../onestep-worker-agent/src/onestep_worker_agent/supervisor.py`:

```python
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DeploymentSpec:
    deployment_id: str
    worker_agent_id: str
    runtime_instance_id: str
    package_dir: Path
    env: dict[str, str]


class SubprocessSupervisor:
    def __init__(self, *, work_dir: Path, max_concurrent_deployments: int) -> None:
        self.work_dir = work_dir
        self.max_concurrent_deployments = max_concurrent_deployments
        self._reserved: set[str] = set()
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    def reserve_slot(self, deployment_id: str) -> None:
        if deployment_id in self._reserved:
            return
        if len(self._reserved) >= self.max_concurrent_deployments:
            raise RuntimeError("no deployment slots available")
        self._reserved.add(deployment_id)

    def release_slot(self, deployment_id: str) -> None:
        self._reserved.discard(deployment_id)
        self._processes.pop(deployment_id, None)

    def build_environment(self, spec: DeploymentSpec) -> dict[str, str]:
        env = dict(os.environ)
        env.update(spec.env)
        env["ONESTEP_DEPLOYMENT_ID"] = spec.deployment_id
        env["ONESTEP_WORKER_AGENT_ID"] = spec.worker_agent_id
        env["ONESTEP_RUNTIME_INSTANCE_ID"] = spec.runtime_instance_id
        return env

    async def check(self, spec: DeploymentSpec) -> int:
        process = await asyncio.create_subprocess_exec(
            "onestep",
            "check",
            "worker.yaml",
            cwd=spec.package_dir,
            env=self.build_environment(spec),
        )
        return await process.wait()

    async def start(self, spec: DeploymentSpec) -> asyncio.subprocess.Process:
        self.reserve_slot(spec.deployment_id)
        process = await asyncio.create_subprocess_exec(
            "onestep",
            "run",
            "worker.yaml",
            cwd=spec.package_dir,
            env=self.build_environment(spec),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._processes[spec.deployment_id] = process
        return process

    async def stop(self, deployment_id: str, *, grace_seconds: float = 10.0) -> int | None:
        process = self._processes.get(deployment_id)
        if process is None:
            self.release_slot(deployment_id)
            return None
        process.terminate()
        try:
            return await asyncio.wait_for(process.wait(), timeout=grace_seconds)
        except TimeoutError:
            process.kill()
            return await process.wait()
        finally:
            self.release_slot(deployment_id)
```

- [ ] **Step 6: Run tests**

Run:

```bash
cd ../onestep-worker-agent
uv run pytest tests/test_packages.py tests/test_supervisor.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd ../onestep-worker-agent
git add src/onestep_worker_agent/packages.py src/onestep_worker_agent/supervisor.py tests/test_packages.py tests/test_supervisor.py
git commit -m "feat: add worker agent package extraction and supervisor"
```

---

### Task 6: Wire Worker-Agent Commands To Local Execution

**Files:**
- Modify: `../onestep-worker-agent/src/onestep_worker_agent/client.py`
- Modify: `../onestep-worker-agent/src/onestep_worker_agent/cli.py`
- Modify: `../onestep-worker-agent/src/onestep_worker_agent/supervisor.py`
- Create: `../onestep-worker-agent/tests/test_client_messages.py`

- [ ] **Step 1: Write message-building tests**

Create `../onestep-worker-agent/tests/test_client_messages.py`:

```python
from __future__ import annotations

from uuid import UUID

from onestep_worker_agent.client import build_hello_message, build_heartbeat_message


def test_build_hello_message() -> None:
    message = build_hello_message(
        worker_agent_id=UUID("11111111-1111-4111-8111-111111111111"),
        max_concurrent_deployments=2,
        used_slots=1,
        running_deployments=["deployment-1"],
    )

    assert message["type"] == "hello"
    assert message["payload"]["protocol_version"] == "1"
    assert message["payload"]["worker_agent_id"] == "11111111-1111-4111-8111-111111111111"
    assert message["payload"]["used_slots"] == 1


def test_build_heartbeat_message() -> None:
    message = build_heartbeat_message(
        worker_agent_id=UUID("11111111-1111-4111-8111-111111111111"),
        used_slots=0,
        running_deployments=[],
    )

    assert message["type"] == "heartbeat"
    assert message["payload"]["running_deployments"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd ../onestep-worker-agent
uv run pytest tests/test_client_messages.py -q
```

Expected: FAIL because message builders do not exist.

- [ ] **Step 3: Add message builders**

Add to `../onestep-worker-agent/src/onestep_worker_agent/client.py`:

```python
from datetime import UTC, datetime
from uuid import uuid4


def _message_id() -> str:
    return f"msg_{uuid4().hex}"


def _sent_at() -> str:
    return datetime.now(UTC).isoformat()


def build_hello_message(
    *,
    worker_agent_id: UUID,
    max_concurrent_deployments: int,
    used_slots: int,
    running_deployments: list[str],
) -> dict[str, object]:
    return {
        "type": "hello",
        "message_id": _message_id(),
        "sent_at": _sent_at(),
        "payload": {
            "protocol_version": "1",
            "worker_agent_id": str(worker_agent_id),
            "capabilities": ["deployment.start", "deployment.stop", "deployment.restart"],
            "max_concurrent_deployments": max_concurrent_deployments,
            "used_slots": used_slots,
            "running_deployments": running_deployments,
        },
    }


def build_heartbeat_message(
    *,
    worker_agent_id: UUID,
    used_slots: int,
    running_deployments: list[str],
) -> dict[str, object]:
    return {
        "type": "heartbeat",
        "message_id": _message_id(),
        "sent_at": _sent_at(),
        "payload": {
            "worker_agent_id": str(worker_agent_id),
            "used_slots": used_slots,
            "running_deployments": running_deployments,
            "recent_errors": [],
        },
    }
```

- [ ] **Step 4: Add WS run loop**

Add a `run_control_loop` function in `../onestep-worker-agent/src/onestep_worker_agent/client.py` that connects to `/api/v1/worker-agents/ws`, sends hello, sends heartbeats every 30 seconds, and dispatches command messages to the supervisor. Use `websockets.connect` with `additional_headers={"Authorization": f"Bearer {identity.connection_token}"}`.

The command dispatch branch must support:

```python
if command_kind == "start_deployment":
    await command_handler.start_deployment(payload)
elif command_kind == "stop_deployment":
    await command_handler.stop_deployment(payload)
elif command_kind == "restart_deployment":
    await command_handler.restart_deployment(payload)
elif command_kind == "sync_agent_state":
    await command_handler.sync_agent_state(payload)
```

Return `command_ack` before long-running work and `command_result` after the command reaches a terminal or running state.

- [ ] **Step 5: Update CLI start**

Update `../onestep-worker-agent/src/onestep_worker_agent/cli.py` so `start()` creates `SubprocessSupervisor` and calls the control loop after registration:

```python
from onestep_worker_agent.client import register_agent, run_control_loop
from onestep_worker_agent.supervisor import SubprocessSupervisor
```

```python
    supervisor = SubprocessSupervisor(
        work_dir=config.work_dir,
        max_concurrent_deployments=config.max_concurrent_deployments,
    )
    await run_control_loop(config=config, identity=identity, supervisor=supervisor)
```

- [ ] **Step 6: Run worker-agent tests**

Run:

```bash
cd ../onestep-worker-agent
uv run pytest tests -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd ../onestep-worker-agent
git add src/onestep_worker_agent/client.py src/onestep_worker_agent/cli.py src/onestep_worker_agent/supervisor.py tests/test_client_messages.py
git commit -m "feat: connect worker agent control loop"
```

---

### Task 7: Add End-To-End Deployment Integration Test

**Files:**
- Create: `backend/tests/test_worker_agent_e2e.py`
- Modify: `backend/src/onestep_control_plane_api/api/worker_agent_service.py`
- Modify: `backend/src/onestep_control_plane_api/api/routers/worker_agent_ws.py`

- [ ] **Step 1: Write integration test**

Create `backend/tests/test_worker_agent_e2e.py`:

```python
from __future__ import annotations

import hashlib
import io
import zipfile
from uuid import UUID

from onestep_control_plane_api.db.models import WorkerDeployment
from sqlalchemy import select

def register_agent(client) -> dict[str, object]:
    response = client.post(
        "/api/v1/worker-agents/register",
        json={
            "registration_token": "test-worker-registration-token",
            "display_name": "dev-agent",
            "agent_version": "0.1.0",
            "onestep_version": "1.4.2",
            "python_version": "3.11.14",
            "execution_mode": "subprocess",
            "max_concurrent_deployments": 2,
            "labels": {"env": "dev"},
            "capabilities": ["deployment.start", "deployment.stop", "deployment.restart"],
            "platform": {"system": "Darwin", "machine": "arm64"},
        },
    )
    assert response.status_code == 200
    return response.json()


def package_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("worker.yaml", "app:\n  name: demo\nresources: {}\ntasks: []\n")
    return buffer.getvalue()


def test_create_deployment_prepares_start_command_for_online_agent(client, db_session) -> None:
    registered = register_agent(client)
    worker_agent_id = registered["worker_agent_id"]
    token = registered["connection_token"]
    content = package_bytes()
    checksum = hashlib.sha256(content).hexdigest()

    upload = client.post(
        "/api/v1/workflow-packages",
        data={
            "workflow_id": "11111111-1111-4111-8111-111111111111",
            "version": "1",
            "entrypoint": "worker.yaml",
        },
        files={"file": ("workflow.zip", content, "application/zip")},
    )
    assert upload.status_code == 200

    with client.websocket_connect(
        "/api/v1/worker-agents/ws",
        headers={"Authorization": f"Bearer {token}"},
    ) as websocket:
        websocket.send_json(
            {
                "type": "hello",
                "message_id": "msg_1",
                "sent_at": "2026-06-15T12:00:00Z",
                "payload": {
                    "protocol_version": "1",
                    "worker_agent_id": worker_agent_id,
                    "capabilities": ["deployment.start", "deployment.stop"],
                    "max_concurrent_deployments": 2,
                    "used_slots": 0,
                    "running_deployments": [],
                },
            }
        )
        websocket.receive_json()

        deployment_response = client.post(
            "/api/v1/worker-deployments",
            json={
                "workflow_package_id": upload.json()["package_id"],
                "worker_agent_id": worker_agent_id,
                "params": {},
                "env": {},
                "credential_refs": [],
            },
        )
        assert deployment_response.status_code == 200
        command = websocket.receive_json()

    assert command["type"] == "command"
    assert command["payload"]["kind"] == "start_deployment"
    assert command["payload"]["args"]["package_checksum"] == checksum

    deployment = db_session.scalar(
        select(WorkerDeployment).where(
            WorkerDeployment.deployment_id == UUID(deployment_response.json()["deployment_id"])
        )
    )
    assert deployment is not None
    assert deployment.observed_status == "assigned"
```

- [ ] **Step 2: Run integration test to verify it fails**

Run:

```bash
uv run pytest backend/tests/test_worker_agent_e2e.py -q
```

Expected: FAIL because deployment creation does not enqueue a `start_deployment` command.

- [ ] **Step 3: Implement command enqueue on deployment creation**

In `worker_agent_service.create_worker_deployment`, after committing deployment, create a `WorkerAgentCommand`:

```python
command = WorkerAgentCommand(
    command_id=uuid4(),
    worker_agent_id=agent.worker_agent_id,
    deployment_id=deployment.deployment_id,
    kind="start_deployment",
    args_json={
        "deployment_id": str(deployment.deployment_id),
        "workflow_package_id": str(package.package_id),
        "package_checksum": package.checksum_sha256,
        "entrypoint": package.entrypoint,
        "params": params,
        "env": env,
        "credential_refs": credential_refs,
    },
    timeout_s=60,
    status="pending",
)
db.add(command)
db.commit()
```

Add this helper to `worker_agent_service.py` and call it after the command is
committed:

```python
async def dispatch_worker_command_if_online(
    db: Session,
    *,
    command: WorkerAgentCommand,
) -> None:
    connection = await worker_agent_connection_registry.get(command.worker_agent_id)
    if connection is None:
        return
    now = utcnow()
    command.session_id = connection.session_id
    command.status = "dispatched"
    command.dispatched_at = now
    command.updated_at = now
    db.commit()
    await connection.send_queue.put(
        {
            "type": "command",
            "message_id": f"msg_{uuid4().hex}",
            "sent_at": now.isoformat(),
            "payload": {
                "command_id": str(command.command_id),
                "kind": command.kind,
                "args": command.args_json,
                "timeout_s": command.timeout_s,
                "created_at": command.created_at.isoformat(),
            },
        }
    )
```

Convert the deployment router handler to `async def` and call
`await dispatch_worker_command_if_online(db, command=command)` before returning
the deployment response.

- [ ] **Step 4: Run integration test**

Run:

```bash
uv run pytest backend/tests/test_worker_agent_e2e.py -q
```

Expected: PASS.

- [ ] **Step 5: Run related backend tests**

Run:

```bash
uv run pytest backend/tests/test_worker_agent_api.py backend/tests/test_worker_agent_ws.py backend/tests/test_worker_agent_e2e.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/tests/test_worker_agent_e2e.py backend/src/onestep_control_plane_api/api/worker_agent_service.py backend/src/onestep_control_plane_api/api/routers/worker_agent_ws.py
git commit -m "feat: dispatch worker deployments to online agents"
```

---

### Task 8: Final Validation And Documentation

**Files:**
- Modify: `README.md`
- Modify: `backend/tests/test_migrations.py`
- Modify: `docs/superpowers/specs/2026-06-15-onestep-worker-agent-design.md`
- Modify: `../onestep-worker-agent/README.md`

- [ ] **Step 1: Add README usage section**

Add a short section to control-plane `README.md`:

~~~markdown
## Worker Agent Registry

The control plane owns worker-agent registration, workflow package storage,
deployment desired state, and worker-agent control commands. The
`onestep-worker-agent` CLI lives in the separate `onestep-worker-agent`
repository.
~~~

Add this usage section to `../onestep-worker-agent/README.md`:

~~~markdown
## Local Start

Start a local worker agent after configuring a control-plane URL and registration token:

```bash
export ONESTEP_PLANE_URL=http://localhost:8000
export ONESTEP_AGENT_REGISTRATION_TOKEN=dev-token
export ONESTEP_WORKER_AGENT_DIR=.onestep-worker-agent
export ONESTEP_WORKER_AGENT_MAX_CONCURRENCY=2
onestep-worker-agent start
```

The agent registers once, stores its identity under `ONESTEP_WORKER_AGENT_DIR`,
connects to the control plane, and runs assigned workflow packages with
`onestep check worker.yaml` followed by `onestep run worker.yaml`.
~~~

- [ ] **Step 2: Run full backend and worker-agent tests**

Run:

```bash
uv run pytest backend/tests -q
cd ../onestep-worker-agent
uv run pytest tests -q
```

Expected: PASS.

- [ ] **Step 3: Run lint**

Run:

```bash
uv run ruff check backend/src backend/tests
cd ../onestep-worker-agent
uv run ruff check src tests
```

Expected: PASS.

- [ ] **Step 4: Verify migration head**

Run:

```bash
uv run pytest backend/tests/test_migrations.py -q
```

Expected: PASS with `HEAD_REVISION = "202606150002"`.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/superpowers/specs/2026-06-15-onestep-worker-agent-design.md backend/tests/test_migrations.py
git commit -m "docs: document worker agent mvp"
cd ../onestep-worker-agent
git add README.md
git commit -m "docs: document worker agent startup"
```

---

## Self-Review Checklist

- Spec coverage:
  - agent registration and identity: Tasks 1, 2, 4
  - package storage and checksum: Tasks 1, 2, 5
  - deployment model and state: Tasks 1, 2, 7
  - worker-agent control channel: Tasks 3, 6
  - subprocess execution: Tasks 5, 6
  - fixed concurrency slots: Tasks 1, 5
  - end-to-end validation: Tasks 7, 8
- Type consistency:
  - `worker_agent_id`, `deployment_id`, `workflow_package_id`, and `runtime_instance_id` are UUIDs in API/model layers.
  - Worker-agent local supervisor accepts string IDs because subprocess environment variables are strings.
  - `execution_mode` is fixed to `subprocess` for MVP.
- Known boundaries:
  - Docker execution, resource scheduling, external package storage, and task-level placement are not implemented in this plan.
