# Lambda Workflow Package Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Phase 1 of Lambda-style workflow deployments: store workflow config, upload handler code, validate it, and generate immutable runnable workflow packages.

**Architecture:** Add a workflow design layer above the existing worker-agent package storage. `Workflow` stores UI-authored source/handler/sink/runtime/env config, `WorkflowCodeUpload` stores raw handler zip uploads, and a new workflow service builds generated packages by combining the saved config with uploaded code and a generated `worker.yaml`. The existing `WorkflowPackage` table, package download API, and worker-agent deployment path remain the package execution boundary.

**Tech Stack:** FastAPI, SQLAlchemy ORM, Alembic, Pydantic, pytest, PyYAML, existing worker-agent package service.

---

## Scope

This plan implements only Phase 1 from `docs/superpowers/specs/2026-06-17-lambda-style-workflow-deployments-design.md`.

Included:

- backend workflow and code-upload persistence
- workflow validation
- code archive validation
- generated `worker.yaml`
- immutable workflow package creation from workflow config plus uploaded code
- backend tests

Deferred to later plans:

- frontend Workflow editor
- deploy dialog
- worker-agent protocol changes
- secret credential injection
- automatic agent matching

## File Structure

Create:

- `backend/alembic/versions/202606170001_add_workflow_design_tables.py`: adds `workflows` and `workflow_code_uploads`.
- `backend/src/onestep_control_plane_api/api/workflow_service.py`: owns workflow CRUD, archive validation, YAML rendering, and package generation.
- `backend/src/onestep_control_plane_api/api/routers/workflows.py`: console-authenticated workflow, upload, validate, and package endpoints.
- `backend/tests/test_workflow_package_generation.py`: API and package-generation tests.

Modify:

- `backend/src/onestep_control_plane_api/db/models.py`: adds `Workflow` and `WorkflowCodeUpload` ORM models.
- `backend/src/onestep_control_plane_api/api/schemas.py`: adds workflow request/response schemas.
- `backend/src/onestep_control_plane_api/api/worker_agent_service.py`: lets `create_workflow_package` accept metadata.
- `backend/src/onestep_control_plane_api/api/routers/__init__.py`: includes the workflows router.
- `backend/tests/test_migrations.py`: updates expected schema and head revision.

---

### Task 1: Add Migration Expectations

**Files:**
- Modify: `backend/tests/test_migrations.py`

- [ ] **Step 1: Update migration head expectation**

Change:

```python
HEAD_REVISION = "202606150002"
```

to:

```python
HEAD_REVISION = "202606170001"
```

- [ ] **Step 2: Add new tables to expected table set**

In `test_alembic_upgrade_head_creates_expected_schema`, add `workflows` and
`workflow_code_uploads` to the expected table set:

```python
        "workflow_code_uploads",
        "workflow_packages",
        "workflows",
```

- [ ] **Step 3: Add column assertions**

Add these assertions after the `workflow_packages` assertion:

```python
    assert {column["name"] for column in inspector.get_columns("workflows")} == {
        "id",
        "workflow_id",
        "name",
        "description",
        "status",
        "source_config_json",
        "handler_config_json",
        "sink_config_json",
        "task_policy_json",
        "env_json",
        "credential_refs_json",
        "latest_package_id",
        "created_by",
        "created_at",
        "updated_at",
    }
    assert {column["name"] for column in inspector.get_columns("workflow_code_uploads")} == {
        "id",
        "code_upload_id",
        "workflow_id",
        "filename",
        "content_type",
        "checksum_sha256",
        "size_bytes",
        "storage_path",
        "detected_dependency_mode",
        "created_by",
        "created_at",
    }
```

- [ ] **Step 4: Add JSON type assertions**

Add these local column maps near the other maps:

```python
    workflow_columns = {
        column["name"]: column for column in inspector.get_columns("workflows")
    }
```

Add these assertions near the other JSON assertions:

```python
    assert isinstance(workflow_columns["source_config_json"]["type"], sa.JSON)
    assert isinstance(workflow_columns["handler_config_json"]["type"], sa.JSON)
    assert isinstance(workflow_columns["sink_config_json"]["type"], sa.JSON)
    assert isinstance(workflow_columns["task_policy_json"]["type"], sa.JSON)
    assert isinstance(workflow_columns["env_json"]["type"], sa.JSON)
    assert isinstance(workflow_columns["credential_refs_json"]["type"], sa.JSON)
```

- [ ] **Step 5: Add index assertions**

Add these assertions near the other index assertions:

```python
    assert {index["name"] for index in inspector.get_indexes("workflows")} == {
        "ix_workflows_status_updated_at",
    }
    assert {index["name"] for index in inspector.get_indexes("workflow_code_uploads")} == {
        "ix_workflow_code_uploads_workflow_created_at",
    }
```

- [ ] **Step 6: Run migration test and verify it fails**

Run:

```bash
uv run pytest backend/tests/test_migrations.py::test_alembic_upgrade_head_creates_expected_schema -q
```

Expected: FAIL because revision `202606170001`, `workflows`, and `workflow_code_uploads` do not exist yet.

- [ ] **Step 7: Commit failing migration expectation**

```bash
git add backend/tests/test_migrations.py
git commit -m "test: expect workflow design tables"
```

---

### Task 2: Add Workflow Models And Migration

**Files:**
- Create: `backend/alembic/versions/202606170001_add_workflow_design_tables.py`
- Modify: `backend/src/onestep_control_plane_api/db/models.py`
- Test: `backend/tests/test_migrations.py`

- [ ] **Step 1: Add ORM models**

Add these model classes after `WorkerAgentSession` and before `WorkflowPackage` in
`backend/src/onestep_control_plane_api/db/models.py`:

```python
class Workflow(Base):
    __tablename__ = "workflows"
    __table_args__ = (
        sa.UniqueConstraint("workflow_id", name="uq_workflows_workflow_id"),
        sa.Index("ix_workflows_status_updated_at", "status", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workflow_id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    description: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="draft")
    source_config_json: Mapped[dict[str, object]] = mapped_column(
        JSON_TYPE,
        nullable=False,
        default=dict,
    )
    handler_config_json: Mapped[dict[str, object]] = mapped_column(
        JSON_TYPE,
        nullable=False,
        default=dict,
    )
    sink_config_json: Mapped[dict[str, object]] = mapped_column(
        JSON_TYPE,
        nullable=False,
        default=dict,
    )
    task_policy_json: Mapped[dict[str, object]] = mapped_column(
        JSON_TYPE,
        nullable=False,
        default=dict,
    )
    env_json: Mapped[dict[str, str]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    credential_refs_json: Mapped[list[str]] = mapped_column(
        JSON_TYPE,
        nullable=False,
        default=list,
    )
    latest_package_id: Mapped[UUID | None] = mapped_column(sa.Uuid(as_uuid=True))
    created_by: Mapped[str] = mapped_column(sa.String(255), nullable=False, default="system")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    code_uploads: Mapped[list[WorkflowCodeUpload]] = relationship(
        back_populates="workflow",
        cascade="all, delete-orphan",
    )
```

```python
class WorkflowCodeUpload(Base):
    __tablename__ = "workflow_code_uploads"
    __table_args__ = (
        sa.UniqueConstraint("code_upload_id", name="uq_workflow_code_uploads_code_upload_id"),
        sa.Index(
            "ix_workflow_code_uploads_workflow_created_at",
            "workflow_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    code_upload_id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    workflow_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("workflows.workflow_id", ondelete="CASCADE"),
        nullable=False,
    )
    filename: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    storage_path: Mapped[str] = mapped_column(sa.Text, nullable=False)
    detected_dependency_mode: Mapped[str] = mapped_column(
        sa.String(32),
        nullable=False,
        default="none",
    )
    created_by: Mapped[str] = mapped_column(sa.String(255), nullable=False, default="system")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)

    workflow: Mapped[Workflow] = relationship(back_populates="code_uploads")
```

- [ ] **Step 2: Create migration**

Create `backend/alembic/versions/202606170001_add_workflow_design_tables.py`:

```python
"""add workflow design tables

Revision ID: 202606170001
Revises: 202606150002
Create Date: 2026-06-17 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from onestep_control_plane_api.db.types import UTCDateTime

revision: str = "202606170001"
down_revision: str | Sequence[str] | None = "202606150002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


JSON_TYPE = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "workflows",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("workflow_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("source_config_json", JSON_TYPE, nullable=False, server_default="{}"),
        sa.Column("handler_config_json", JSON_TYPE, nullable=False, server_default="{}"),
        sa.Column("sink_config_json", JSON_TYPE, nullable=False, server_default="{}"),
        sa.Column("task_policy_json", JSON_TYPE, nullable=False, server_default="{}"),
        sa.Column("env_json", JSON_TYPE, nullable=False, server_default="{}"),
        sa.Column("credential_refs_json", JSON_TYPE, nullable=False, server_default="[]"),
        sa.Column("latest_package_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False, server_default="system"),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", name="uq_workflows_workflow_id"),
    )
    op.create_index(
        "ix_workflows_status_updated_at",
        "workflows",
        ["status", "updated_at"],
        unique=False,
    )
    op.create_table(
        "workflow_code_uploads",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("code_upload_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("workflow_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column(
            "detected_dependency_mode",
            sa.String(length=32),
            nullable=False,
            server_default="none",
        ),
        sa.Column("created_by", sa.String(length=255), nullable=False, server_default="system"),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.workflow_id"],
            name="fk_workflow_code_uploads_workflow_id_workflows",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_upload_id", name="uq_workflow_code_uploads_code_upload_id"),
    )
    op.create_index(
        "ix_workflow_code_uploads_workflow_created_at",
        "workflow_code_uploads",
        ["workflow_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workflow_code_uploads_workflow_created_at",
        table_name="workflow_code_uploads",
    )
    op.drop_table("workflow_code_uploads")
    op.drop_index("ix_workflows_status_updated_at", table_name="workflows")
    op.drop_table("workflows")
```

- [ ] **Step 3: Run migration test**

```bash
uv run pytest backend/tests/test_migrations.py::test_alembic_upgrade_head_creates_expected_schema -q
```

Expected: PASS.

- [ ] **Step 4: Commit migration and models**

```bash
git add backend/alembic/versions/202606170001_add_workflow_design_tables.py backend/src/onestep_control_plane_api/db/models.py backend/tests/test_migrations.py
git commit -m "feat: add workflow design persistence"
```

---

### Task 3: Add Workflow API Schemas

**Files:**
- Modify: `backend/src/onestep_control_plane_api/api/schemas.py`

- [ ] **Step 1: Add literals**

Near the worker-agent literals, add:

```python
WorkflowStatus = Literal["draft", "valid", "invalid", "published"]
WorkflowDependencyMode = Literal["none", "requirements", "package"]
```

- [ ] **Step 2: Add request and response models**

Add these classes before `WorkflowPackageSummary`:

```python
class WorkflowConfig(APIModel):
    source: dict[str, Any] = Field(default_factory=dict)
    handler: dict[str, Any] = Field(default_factory=dict)
    sink: dict[str, Any] = Field(default_factory=dict)
    task_policy: dict[str, Any] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    credential_refs: list[str] = Field(default_factory=list)


class WorkflowCreateRequest(APIModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    config: WorkflowConfig = Field(default_factory=WorkflowConfig)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value


class WorkflowUpdateRequest(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    config: WorkflowConfig | None = None

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value


class WorkflowSummary(APIModel):
    workflow_id: UUID
    name: str
    description: str
    status: WorkflowStatus
    latest_package_id: UUID | None = None
    created_by: str
    created_at: datetime
    updated_at: datetime


class WorkflowDetail(WorkflowSummary):
    config: WorkflowConfig


class WorkflowListResponse(PaginatedResponse):
    items: list[WorkflowSummary]


class WorkflowCodeUploadSummary(APIModel):
    code_upload_id: UUID
    workflow_id: UUID
    filename: str
    content_type: str
    checksum_sha256: str
    size_bytes: int = Field(ge=0)
    detected_dependency_mode: WorkflowDependencyMode
    created_by: str
    created_at: datetime


class WorkflowCodeUploadListResponse(PaginatedResponse):
    items: list[WorkflowCodeUploadSummary]


class WorkflowValidationIssue(APIModel):
    field: str
    code: str
    message: str


class WorkflowValidationResponse(APIModel):
    valid: bool
    issues: list[WorkflowValidationIssue] = Field(default_factory=list)


class WorkflowPackageCreateRequest(APIModel):
    code_upload_id: UUID
    version: str = Field(min_length=1, max_length=128)
```

- [ ] **Step 3: Run schema import check**

```bash
uv run python -c "from onestep_control_plane_api.api.schemas import WorkflowCreateRequest, WorkflowValidationResponse; print(WorkflowCreateRequest(name='x'))"
```

Expected: prints a `WorkflowCreateRequest` object and exits 0.

- [ ] **Step 4: Commit schemas**

```bash
git add backend/src/onestep_control_plane_api/api/schemas.py
git commit -m "feat: add workflow API schemas"
```

---

### Task 4: Add Workflow Service And Package Builder

**Files:**
- Create: `backend/src/onestep_control_plane_api/api/workflow_service.py`
- Modify: `backend/src/onestep_control_plane_api/api/worker_agent_service.py`
- Test: `backend/tests/test_workflow_package_generation.py`

- [ ] **Step 1: Add failing service tests**

Create `backend/tests/test_workflow_package_generation.py` with:

```python
from __future__ import annotations

import io
import zipfile

import yaml


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _create_workflow(client) -> dict[str, object]:
    response = client.post(
        "/api/v1/workflows",
        json={
            "name": "daily user sync",
            "description": "sync users once per day",
            "config": {
                "source": {"name": "tick", "type": "interval", "minutes": 1440},
                "handler": {"ref": "worker.tasks:handle"},
                "sink": {"name": "out", "type": "memory"},
                "task_policy": {
                    "task_name": "sync_users",
                    "concurrency": 2,
                    "timeout_s": 120,
                    "retry": {"type": "max_attempts", "max_attempts": 3, "delay_s": 5},
                },
                "env": {"APP_ENV": "test"},
                "credential_refs": ["mysql-main"],
            },
        },
    )
    assert response.status_code == 200
    return response.json()


def test_workflow_upload_and_package_generation(client) -> None:
    workflow = _create_workflow(client)
    code = _zip_bytes(
        {
            "worker/__init__.py": "",
            "worker/tasks.py": "def handle(ctx, payload):\n    return payload\n",
            "requirements.txt": "",
        }
    )

    upload = client.post(
        f"/api/v1/workflows/{workflow['workflow_id']}/code-uploads",
        params={"filename": "handler.zip"},
        headers={"content-type": "application/zip"},
        content=code,
    )
    assert upload.status_code == 200
    upload_payload = upload.json()
    assert upload_payload["detected_dependency_mode"] == "requirements"

    package_response = client.post(
        f"/api/v1/workflows/{workflow['workflow_id']}/packages",
        json={
            "code_upload_id": upload_payload["code_upload_id"],
            "version": "2026.06.17.1",
        },
    )
    assert package_response.status_code == 200
    package = package_response.json()
    assert package["workflow_id"] == workflow["workflow_id"]
    assert package["entrypoint"] == "worker.yaml"
    assert package["metadata"]["handler_ref"] == "worker.tasks:handle"
    assert package["metadata"]["code_upload_id"] == upload_payload["code_upload_id"]

    download = client.get(
        f"/api/v1/workflow-packages/{package['package_id']}/download",
        headers={"Authorization": "Bearer not-a-worker-token"},
    )
    assert download.status_code == 401


def test_generated_package_contains_worker_yaml(client) -> None:
    workflow = _create_workflow(client)
    code = _zip_bytes({"worker/tasks.py": "def handle(ctx, payload):\n    return payload\n"})
    upload = client.post(
        f"/api/v1/workflows/{workflow['workflow_id']}/code-uploads",
        params={"filename": "handler.zip"},
        headers={"content-type": "application/zip"},
        content=code,
    )
    assert upload.status_code == 200

    package_response = client.post(
        f"/api/v1/workflows/{workflow['workflow_id']}/packages",
        json={
            "code_upload_id": upload.json()["code_upload_id"],
            "version": "2026.06.17.2",
        },
    )
    assert package_response.status_code == 200

    package_path = package_response.json()["metadata"]["storage_path"]
    with zipfile.ZipFile(package_path) as archive:
        names = set(archive.namelist())
        assert "worker.yaml" in names
        assert "manifest.json" in names
        assert "worker/tasks.py" in names
        worker_yaml = yaml.safe_load(archive.read("worker.yaml"))

    assert worker_yaml["apiVersion"] == "onestep/v1alpha1"
    assert worker_yaml["kind"] == "App"
    assert worker_yaml["app"]["name"] == "daily-user-sync"
    assert worker_yaml["reporter"] is True
    assert worker_yaml["resources"]["tick"] == {
        "type": "interval",
        "minutes": 1440,
    }
    assert worker_yaml["resources"]["out"] == {"type": "memory"}
    assert worker_yaml["tasks"][0]["name"] == "sync_users"
    assert worker_yaml["tasks"][0]["handler"]["ref"] == "worker.tasks:handle"
    assert worker_yaml["tasks"][0]["concurrency"] == 2
    assert worker_yaml["tasks"][0]["timeout_s"] == 120


def test_upload_rejects_unsafe_zip_path(client) -> None:
    workflow = _create_workflow(client)
    code = _zip_bytes({"../escape.py": "print('bad')\n"})

    response = client.post(
        f"/api/v1/workflows/{workflow['workflow_id']}/code-uploads",
        params={"filename": "handler.zip"},
        headers={"content-type": "application/zip"},
        content=code,
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "zip entry escapes archive root: ../escape.py"


def test_validation_reports_missing_handler(client) -> None:
    response = client.post(
        "/api/v1/workflows",
        json={
            "name": "broken",
            "config": {
                "source": {"name": "tick", "type": "interval", "minutes": 1},
                "handler": {},
                "sink": {},
                "task_policy": {"task_name": "broken"},
            },
        },
    )
    assert response.status_code == 200

    validation = client.post(f"/api/v1/workflows/{response.json()['workflow_id']}/validate")

    assert validation.status_code == 200
    assert validation.json() == {
        "valid": False,
        "issues": [
            {
                "field": "handler.ref",
                "code": "missing_handler_ref",
                "message": "handler.ref is required",
            }
        ],
    }
```

- [ ] **Step 2: Run tests and verify they fail**

```bash
uv run pytest backend/tests/test_workflow_package_generation.py -q
```

Expected: FAIL because `/api/v1/workflows` routes do not exist.

- [ ] **Step 3: Allow package metadata**

Modify `create_workflow_package` in
`backend/src/onestep_control_plane_api/api/worker_agent_service.py`:

```python
def create_workflow_package(
    db: Session,
    *,
    workflow_id: UUID,
    version: str,
    filename: str,
    content_type: str,
    content: bytes,
    entrypoint: str,
    created_by: str,
    metadata: dict[str, object] | None = None,
) -> WorkflowPackage:
```

Change the `WorkflowPackage` construction from:

```python
        metadata_json={},
```

to:

```python
        metadata_json=metadata or {},
```

- [ ] **Step 4: Create workflow service**

Create `backend/src/onestep_control_plane_api/api/workflow_service.py`:

```python
from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from pathlib import PurePosixPath
from uuid import UUID, uuid4

import yaml
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.common import utcnow
from onestep_control_plane_api.api.schemas import (
    WorkflowCodeUploadSummary,
    WorkflowConfig,
    WorkflowCreateRequest,
    WorkflowDetail,
    WorkflowPackageCreateRequest,
    WorkflowSummary,
    WorkflowUpdateRequest,
    WorkflowValidationIssue,
    WorkflowValidationResponse,
)
from onestep_control_plane_api.api.worker_agent_service import (
    build_workflow_package_summary,
    create_workflow_package,
)
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db.models import Workflow, WorkflowCodeUpload

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_UPLOAD_FILES = 256


def _storage_root() -> str:
    return settings.worker_package_storage_dir


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "workflow"


def _workflow_config(workflow: Workflow) -> WorkflowConfig:
    return WorkflowConfig(
        source=workflow.source_config_json,
        handler=workflow.handler_config_json,
        sink=workflow.sink_config_json,
        task_policy=workflow.task_policy_json,
        env=workflow.env_json,
        credential_refs=workflow.credential_refs_json,
    )


def build_workflow_summary(workflow: Workflow) -> WorkflowSummary:
    return WorkflowSummary(
        workflow_id=workflow.workflow_id,
        name=workflow.name,
        description=workflow.description,
        status=workflow.status,
        latest_package_id=workflow.latest_package_id,
        created_by=workflow.created_by,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
    )


def build_workflow_detail(workflow: Workflow) -> WorkflowDetail:
    return WorkflowDetail(
        **build_workflow_summary(workflow).model_dump(),
        config=_workflow_config(workflow),
    )


def build_code_upload_summary(upload: WorkflowCodeUpload) -> WorkflowCodeUploadSummary:
    return WorkflowCodeUploadSummary(
        code_upload_id=upload.code_upload_id,
        workflow_id=upload.workflow_id,
        filename=upload.filename,
        content_type=upload.content_type,
        checksum_sha256=upload.checksum_sha256,
        size_bytes=upload.size_bytes,
        detected_dependency_mode=upload.detected_dependency_mode,
        created_by=upload.created_by,
        created_at=upload.created_at,
    )


def get_workflow_or_404(db: Session, workflow_id: UUID) -> Workflow:
    workflow = db.scalar(select(Workflow).where(Workflow.workflow_id == workflow_id))
    if workflow is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"workflow {workflow_id} was not found",
        )
    return workflow


def create_workflow(
    db: Session,
    request: WorkflowCreateRequest,
    *,
    created_by: str,
) -> Workflow:
    now = utcnow()
    workflow = Workflow(
        workflow_id=uuid4(),
        name=request.name,
        description=request.description,
        status="draft",
        source_config_json=request.config.source,
        handler_config_json=request.config.handler,
        sink_config_json=request.config.sink,
        task_policy_json=request.config.task_policy,
        env_json=request.config.env,
        credential_refs_json=request.config.credential_refs,
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    db.add(workflow)
    db.commit()
    db.refresh(workflow)
    return workflow


def update_workflow(
    db: Session,
    workflow: Workflow,
    request: WorkflowUpdateRequest,
) -> Workflow:
    if request.name is not None:
        workflow.name = request.name
    if request.description is not None:
        workflow.description = request.description
    if request.config is not None:
        workflow.source_config_json = request.config.source
        workflow.handler_config_json = request.config.handler
        workflow.sink_config_json = request.config.sink
        workflow.task_policy_json = request.config.task_policy
        workflow.env_json = request.config.env
        workflow.credential_refs_json = request.config.credential_refs
        workflow.status = "draft"
    workflow.updated_at = utcnow()
    db.commit()
    db.refresh(workflow)
    return workflow


def list_workflows(db: Session, *, limit: int, offset: int) -> tuple[int, list[Workflow]]:
    total = db.scalar(select(func.count()).select_from(Workflow)) or 0
    items = db.scalars(
        select(Workflow)
        .order_by(Workflow.updated_at.desc(), Workflow.workflow_id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return total, list(items)


def validate_workflow_config(workflow: Workflow) -> WorkflowValidationResponse:
    issues: list[WorkflowValidationIssue] = []
    if not isinstance(workflow.handler_config_json.get("ref"), str) or not workflow.handler_config_json.get("ref", "").strip():
        issues.append(
            WorkflowValidationIssue(
                field="handler.ref",
                code="missing_handler_ref",
                message="handler.ref is required",
            )
        )
    if not isinstance(workflow.source_config_json.get("type"), str) or not workflow.source_config_json.get("type", "").strip():
        issues.append(
            WorkflowValidationIssue(
                field="source.type",
                code="missing_source_type",
                message="source.type is required",
            )
        )
    return WorkflowValidationResponse(valid=not issues, issues=issues)


def _check_zip_member(member: zipfile.ZipInfo) -> None:
    path = PurePosixPath(member.filename)
    if path.is_absolute() or ".." in path.parts:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"zip entry escapes archive root: {member.filename}",
        )


def _detect_dependency_mode(names: set[str]) -> str:
    if "pyproject.toml" in names:
        return "package"
    if "requirements.txt" in names:
        return "requirements"
    return "none"


def store_code_upload(
    db: Session,
    workflow: Workflow,
    *,
    filename: str,
    content_type: str,
    content: bytes,
    created_by: str,
) -> WorkflowCodeUpload:
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"code upload exceeds {MAX_UPLOAD_BYTES} bytes",
        )
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="code upload must be a valid zip archive",
        ) from exc
    with archive:
        members = archive.infolist()
        if len(members) > MAX_UPLOAD_FILES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"code upload contains more than {MAX_UPLOAD_FILES} files",
            )
        for member in members:
            _check_zip_member(member)
        names = {member.filename for member in members}
        dependency_mode = _detect_dependency_mode(names)

    code_upload_id = uuid4()
    checksum = hashlib.sha256(content).hexdigest()
    from pathlib import Path

    storage_dir = Path(_storage_root()).expanduser() / "code-uploads"
    storage_dir.mkdir(parents=True, exist_ok=True)
    storage_path = storage_dir / f"{code_upload_id}.zip"
    storage_path.write_bytes(content)
    upload = WorkflowCodeUpload(
        code_upload_id=code_upload_id,
        workflow_id=workflow.workflow_id,
        filename=filename,
        content_type=content_type,
        checksum_sha256=checksum,
        size_bytes=len(content),
        storage_path=str(storage_path),
        detected_dependency_mode=dependency_mode,
        created_by=created_by,
    )
    db.add(upload)
    db.commit()
    db.refresh(upload)
    return upload


def _worker_yaml(workflow: Workflow) -> dict[str, object]:
    source_name = str(workflow.source_config_json.get("name") or "source")
    sink_name = workflow.sink_config_json.get("name")
    task_name = str(workflow.task_policy_json.get("task_name") or _slugify(workflow.name).replace("-", "_"))
    resources: dict[str, object] = {
        source_name: {
            key: value
            for key, value in workflow.source_config_json.items()
            if key != "name"
        }
    }
    if sink_name:
        resources[str(sink_name)] = {
            key: value
            for key, value in workflow.sink_config_json.items()
            if key != "name"
        }
    task: dict[str, object] = {
        "name": task_name,
        "source": source_name,
        "handler": {"ref": workflow.handler_config_json["ref"]},
    }
    if sink_name:
        task["emit"] = [str(sink_name)]
    for key in ("concurrency", "timeout_s", "retry"):
        if key in workflow.task_policy_json:
            task[key] = workflow.task_policy_json[key]
    return {
        "apiVersion": "onestep/v1alpha1",
        "kind": "App",
        "app": {"name": _slugify(workflow.name)},
        "reporter": True,
        "resources": resources,
        "tasks": [task],
    }


def build_package_from_workflow(
    db: Session,
    workflow: Workflow,
    request: WorkflowPackageCreateRequest,
    *,
    created_by: str,
):
    upload = db.scalar(
        select(WorkflowCodeUpload).where(
            WorkflowCodeUpload.workflow_id == workflow.workflow_id,
            WorkflowCodeUpload.code_upload_id == request.code_upload_id,
        )
    )
    if upload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"code upload {request.code_upload_id} was not found",
        )
    validation = validate_workflow_config(workflow)
    if not validation.valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=[issue.model_dump() for issue in validation.issues],
        )

    from pathlib import Path

    source_path = Path(upload.storage_path)
    source_content = source_path.read_bytes()
    output = io.BytesIO()
    manifest = {
        "schema_version": "onestep.workflow_package.v1",
        "workflow_id": str(workflow.workflow_id),
        "code_upload_id": str(upload.code_upload_id),
        "entrypoint": "worker.yaml",
        "handler_ref": workflow.handler_config_json["ref"],
        "source_kind": workflow.source_config_json.get("type"),
        "sink_kind": workflow.sink_config_json.get("type"),
    }
    with zipfile.ZipFile(io.BytesIO(source_content)) as source_archive:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for member in source_archive.infolist():
                _check_zip_member(member)
                if member.filename == "worker.yaml":
                    continue
                target.writestr(member, source_archive.read(member.filename))
            target.writestr("worker.yaml", yaml.safe_dump(_worker_yaml(workflow), sort_keys=False))
            target.writestr("manifest.json", json.dumps(manifest, sort_keys=True, indent=2))

    package = create_workflow_package(
        db,
        workflow_id=workflow.workflow_id,
        version=request.version,
        filename=f"{_slugify(workflow.name)}-{request.version}.zip",
        content_type="application/zip",
        content=output.getvalue(),
        entrypoint="worker.yaml",
        created_by=created_by,
        metadata={
            **manifest,
            "storage_path": "",
            "dependency_mode": upload.detected_dependency_mode,
        },
    )
    package.metadata_json = {**package.metadata_json, "storage_path": package.storage_path}
    workflow.latest_package_id = package.package_id
    workflow.status = "published"
    workflow.updated_at = utcnow()
    db.commit()
    db.refresh(package)
    return build_workflow_package_summary(package)
```

- [ ] **Step 5: Run targeted tests and verify failure moved to missing routes**

```bash
uv run pytest backend/tests/test_workflow_package_generation.py -q
```

Expected: FAIL because service exists but routes are still missing.

- [ ] **Step 6: Commit service layer**

```bash
git add backend/src/onestep_control_plane_api/api/workflow_service.py backend/src/onestep_control_plane_api/api/worker_agent_service.py backend/tests/test_workflow_package_generation.py
git commit -m "feat: add workflow package generation service"
```

---

### Task 5: Add Workflow Routes

**Files:**
- Create: `backend/src/onestep_control_plane_api/api/routers/workflows.py`
- Modify: `backend/src/onestep_control_plane_api/api/routers/__init__.py`
- Test: `backend/tests/test_workflow_package_generation.py`

- [ ] **Step 1: Create workflows router**

Create `backend/src/onestep_control_plane_api/api/routers/workflows.py`:

```python
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, Query
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.schemas import (
    WorkflowCodeUploadListResponse,
    WorkflowCodeUploadSummary,
    WorkflowCreateRequest,
    WorkflowDetail,
    WorkflowListResponse,
    WorkflowPackageCreateRequest,
    WorkflowPackageSummary,
    WorkflowSummary,
    WorkflowUpdateRequest,
    WorkflowValidationResponse,
)
from onestep_control_plane_api.api.security import require_console_auth
from onestep_control_plane_api.api.workflow_service import (
    build_code_upload_summary,
    build_package_from_workflow,
    build_workflow_detail,
    build_workflow_summary,
    create_workflow,
    get_workflow_or_404,
    list_workflows,
    store_code_upload,
    update_workflow,
    validate_workflow_config,
)
from onestep_control_plane_api.db.session import get_db_session

router = APIRouter(prefix="/api/v1", tags=["workflows"])


@router.get(
    "/workflows",
    response_model=WorkflowListResponse,
    dependencies=[Depends(require_console_auth)],
)
def list_workflows_endpoint(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db_session),
) -> WorkflowListResponse:
    total, items = list_workflows(db, limit=limit, offset=offset)
    return WorkflowListResponse(
        items=[build_workflow_summary(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/workflows", response_model=WorkflowDetail)
def create_workflow_endpoint(
    request: WorkflowCreateRequest,
    identity=Depends(require_console_auth),
    db: Session = Depends(get_db_session),
) -> WorkflowDetail:
    workflow = create_workflow(
        db,
        request,
        created_by=identity.username if identity is not None else "system",
    )
    return build_workflow_detail(workflow)


@router.get(
    "/workflows/{workflow_id}",
    response_model=WorkflowDetail,
    dependencies=[Depends(require_console_auth)],
)
def get_workflow_endpoint(
    workflow_id: UUID,
    db: Session = Depends(get_db_session),
) -> WorkflowDetail:
    return build_workflow_detail(get_workflow_or_404(db, workflow_id))


@router.put(
    "/workflows/{workflow_id}",
    response_model=WorkflowDetail,
    dependencies=[Depends(require_console_auth)],
)
def update_workflow_endpoint(
    workflow_id: UUID,
    request: WorkflowUpdateRequest,
    db: Session = Depends(get_db_session),
) -> WorkflowDetail:
    return build_workflow_detail(update_workflow(db, get_workflow_or_404(db, workflow_id), request))


@router.post(
    "/workflows/{workflow_id}/validate",
    response_model=WorkflowValidationResponse,
    dependencies=[Depends(require_console_auth)],
)
def validate_workflow_endpoint(
    workflow_id: UUID,
    db: Session = Depends(get_db_session),
) -> WorkflowValidationResponse:
    workflow = get_workflow_or_404(db, workflow_id)
    validation = validate_workflow_config(workflow)
    workflow.status = "valid" if validation.valid else "invalid"
    db.commit()
    return validation


@router.post(
    "/workflows/{workflow_id}/code-uploads",
    response_model=WorkflowCodeUploadSummary,
)
def upload_workflow_code_endpoint(
    workflow_id: UUID,
    content: Annotated[bytes, Body(media_type="application/zip")],
    filename: str = Query("handler.zip", min_length=1, max_length=255),
    content_type: Annotated[str | None, Header(alias="content-type")] = None,
    identity=Depends(require_console_auth),
    db: Session = Depends(get_db_session),
) -> WorkflowCodeUploadSummary:
    workflow = get_workflow_or_404(db, workflow_id)
    upload = store_code_upload(
        db,
        workflow,
        filename=filename,
        content_type=content_type or "application/zip",
        content=content,
        created_by=identity.username if identity is not None else "system",
    )
    return build_code_upload_summary(upload)


@router.get(
    "/workflows/{workflow_id}/code-uploads",
    response_model=WorkflowCodeUploadListResponse,
    dependencies=[Depends(require_console_auth)],
)
def list_workflow_code_uploads_endpoint(
    workflow_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db_session),
) -> WorkflowCodeUploadListResponse:
    workflow = get_workflow_or_404(db, workflow_id)
    uploads = workflow.code_uploads[offset : offset + limit]
    return WorkflowCodeUploadListResponse(
        items=[build_code_upload_summary(upload) for upload in uploads],
        total=len(workflow.code_uploads),
        limit=limit,
        offset=offset,
    )


@router.post(
    "/workflows/{workflow_id}/packages",
    response_model=WorkflowPackageSummary,
)
def create_package_from_workflow_endpoint(
    workflow_id: UUID,
    request: WorkflowPackageCreateRequest,
    identity=Depends(require_console_auth),
    db: Session = Depends(get_db_session),
) -> WorkflowPackageSummary:
    workflow = get_workflow_or_404(db, workflow_id)
    return build_package_from_workflow(
        db,
        workflow,
        request,
        created_by=identity.username if identity is not None else "system",
    )
```

- [ ] **Step 2: Include router**

Modify `backend/src/onestep_control_plane_api/api/routers/__init__.py`:

```python
from onestep_control_plane_api.api.routers.workflows import router as workflows_router
```

Add it before `worker_agents_router`:

```python
router.include_router(workflows_router)
router.include_router(worker_agents_router)
```

- [ ] **Step 3: Run workflow tests**

```bash
uv run pytest backend/tests/test_workflow_package_generation.py -q
```

Expected: PASS.

- [ ] **Step 4: Run worker-agent API tests**

```bash
uv run pytest backend/tests/test_worker_agent_api.py backend/tests/test_worker_agent_ws.py -q
```

Expected: PASS. This confirms existing raw package upload and worker-agent deployment behavior still works.

- [ ] **Step 5: Commit routes**

```bash
git add backend/src/onestep_control_plane_api/api/routers/workflows.py backend/src/onestep_control_plane_api/api/routers/__init__.py backend/tests/test_workflow_package_generation.py
git commit -m "feat: add workflow package generation endpoints"
```

---

### Task 6: Tighten Package Generation Behavior

**Files:**
- Modify: `backend/src/onestep_control_plane_api/api/workflow_service.py`
- Modify: `backend/tests/test_workflow_package_generation.py`

- [ ] **Step 1: Add test for zip-provided worker.yaml being ignored**

Append to `backend/tests/test_workflow_package_generation.py`:

```python
def test_uploaded_worker_yaml_is_replaced_by_generated_yaml(client) -> None:
    workflow = _create_workflow(client)
    code = _zip_bytes(
        {
            "worker.yaml": "apiVersion: bad\nkind: Bad\n",
            "worker/tasks.py": "def handle(ctx, payload):\n    return payload\n",
        }
    )
    upload = client.post(
        f"/api/v1/workflows/{workflow['workflow_id']}/code-uploads",
        params={"filename": "handler.zip"},
        headers={"content-type": "application/zip"},
        content=code,
    )
    assert upload.status_code == 200

    package_response = client.post(
        f"/api/v1/workflows/{workflow['workflow_id']}/packages",
        json={
            "code_upload_id": upload.json()["code_upload_id"],
            "version": "2026.06.17.3",
        },
    )
    assert package_response.status_code == 200

    package_path = package_response.json()["metadata"]["storage_path"]
    with zipfile.ZipFile(package_path) as archive:
        worker_yaml = yaml.safe_load(archive.read("worker.yaml"))

    assert worker_yaml["apiVersion"] == "onestep/v1alpha1"
    assert worker_yaml["kind"] == "App"
```

- [ ] **Step 2: Run the new test**

```bash
uv run pytest backend/tests/test_workflow_package_generation.py::test_uploaded_worker_yaml_is_replaced_by_generated_yaml -q
```

Expected: PASS if Task 4 copied code correctly and skipped uploaded `worker.yaml`.

- [ ] **Step 3: Add test for package metadata**

Append:

```python
def test_package_metadata_records_source_and_sink_kinds(client) -> None:
    workflow = _create_workflow(client)
    code = _zip_bytes({"worker/tasks.py": "def handle(ctx, payload):\n    return payload\n"})
    upload = client.post(
        f"/api/v1/workflows/{workflow['workflow_id']}/code-uploads",
        params={"filename": "handler.zip"},
        headers={"content-type": "application/zip"},
        content=code,
    )
    assert upload.status_code == 200

    package_response = client.post(
        f"/api/v1/workflows/{workflow['workflow_id']}/packages",
        json={
            "code_upload_id": upload.json()["code_upload_id"],
            "version": "2026.06.17.4",
        },
    )

    assert package_response.status_code == 200
    metadata = package_response.json()["metadata"]
    assert metadata["source_kind"] == "interval"
    assert metadata["sink_kind"] == "memory"
    assert metadata["dependency_mode"] == "none"
```

- [ ] **Step 4: Run all workflow tests**

```bash
uv run pytest backend/tests/test_workflow_package_generation.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit behavior coverage**

```bash
git add backend/src/onestep_control_plane_api/api/workflow_service.py backend/tests/test_workflow_package_generation.py
git commit -m "test: cover generated workflow package semantics"
```

---

### Task 7: Final Verification

**Files:**
- All files touched by this plan

- [ ] **Step 1: Run focused backend test suite**

```bash
uv run pytest backend/tests/test_migrations.py backend/tests/test_workflow_package_generation.py backend/tests/test_worker_agent_api.py backend/tests/test_worker_agent_ws.py -q
```

Expected: PASS.

- [ ] **Step 2: Run formatting and type checks used by this repo**

Run:

```bash
uv run ruff check backend/src backend/tests
```

Expected: PASS.

Run:

```bash
uv run mypy backend/src
```

Expected: PASS. If this repo does not configure mypy for the backend, record that the command is unavailable and keep the pytest + ruff results as the required verification.

- [ ] **Step 3: Inspect git diff**

```bash
git diff --stat HEAD
git diff -- backend/src/onestep_control_plane_api/api/workflow_service.py backend/src/onestep_control_plane_api/api/routers/workflows.py
```

Expected: changes are limited to workflow package generation, schemas, models, migration, tests, and router registration.

- [ ] **Step 4: Commit any verification-only fixes**

Only if Task 7 found small lint/type fixes:

```bash
git add <fixed-files>
git commit -m "fix: polish workflow package generation checks"
```

---

## Handoff Notes

- The existing raw `POST /api/v1/workflow-packages` endpoint stays available for worker-agent smoke tests and low-level package upload.
- New user-facing package generation should go through `POST /api/v1/workflows/{workflow_id}/packages`.
- `metadata.storage_path` is included only to make Phase 1 tests inspect package content without worker-agent assignment. Remove it from API responses before exposing this endpoint to real users if local server paths are considered sensitive.
- This phase does not inject resolved credentials. `credential_refs` are stored on the workflow for later deployment planning.
- This phase does not add frontend UI. The next plan should build the Workflow list/editor around these APIs.
