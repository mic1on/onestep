# Worker Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Lambda-style Worker Builder — operators define a worker via forms (source + handler + sinks), the plane stores structured config, and on deploy compiles a complete `worker.yaml`, merges it with the handler zip, and dispatches via the existing package+deployment pipeline.

**Architecture:** New `workers` table holds structured config (name, handler ref, source config, sink configs). A pure `worker_compiler.py` compiles a worker + decrypted connectors into a `worker.yaml` string. The deploy endpoint merges that YAML into the handler zip, creates a workflow package, and creates a deployment — reusing all existing infrastructure. Frontend adds a source/sink catalog, a list page, and a three-section editor page.

**Tech Stack:** FastAPI + SQLAlchemy + Alembic + PyYAML (backend); React + React Query + react-i18next (frontend).

**Spec:** `docs/superpowers/specs/2026-06-17-worker-builder-design.md`

---

## File Structure

### Backend — Create
- `backend/src/onestep_control_plane_api/api/worker_compiler.py` — pure YAML compiler + package merge
- `backend/src/onestep_control_plane_api/api/routers/workers.py` — REST router
- `backend/alembic/versions/202606170002_add_workers_table.py` — migration
- `backend/tests/test_worker_compiler.py` — compiler unit tests
- `backend/tests/test_workers_api.py` — API tests

### Backend — Modify
- `backend/src/onestep_control_plane_api/db/models.py` — append `Worker` model
- `backend/src/onestep_control_plane_api/api/schemas.py` — worker request/response models
- `backend/src/onestep_control_plane_api/api/routers/__init__.py` — register workers router
- `backend/tests/test_migrations.py` — add workers table to expected set + HEAD_REVISION

### Frontend — Create
- `frontend/src/features/workers/catalog.ts` — source/sink type schema
- `frontend/src/features/workers/queries.ts` — React Query hooks
- `frontend/src/pages/workers/WorkersListPage.tsx` — list page
- `frontend/src/pages/workers/WorkerEditorPage.tsx` — three-section editor
- `frontend/src/pages/workers/WorkersListPage.test.tsx` — list test
- `frontend/src/pages/workers/WorkerEditorPage.test.tsx` — editor test

### Frontend — Modify
- `frontend/src/lib/api/types.ts` — worker types
- `frontend/src/lib/api/client.ts` — worker API functions
- `frontend/src/app/router.tsx` — routes
- `frontend/src/components/layout/AppShell.tsx` — nav link
- `frontend/src/lib/i18n.ts` — strings (en + zh)
- `frontend/src/components/layout/AppShell.test.tsx` — assert nav link

---

## Task 1: Worker.yaml compiler (pure function)

**Files:**
- Create: `backend/src/onestep_control_plane_api/api/worker_compiler.py`
- Test: `backend/tests/test_worker_compiler.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_worker_compiler.py`:

```python
from __future__ import annotations

import io
import zipfile

import yaml

from onestep_control_plane_api.api.worker_compiler import (
    compile_worker_yaml,
    merge_package,
)


def _worker(name="order-sync", handler_ref="myworker.handlers:sync", source=None, sinks=None):
    return {
        "name": name,
        "handler_ref": handler_ref,
        "source": source or {
            "type": "mysql_incremental",
            "connector_id": "c1",
            "fields": {"table": "orders", "key": "id", "cursor": ["updated_at", "id"]},
        },
        "sinks": sinks or [
            {
                "type": "mysql_table_sink",
                "connector_id": "c1",
                "fields": {"table": "synced", "mode": "upsert", "keys": ["id"]},
            }
        ],
    }


def _connectors():
    return {
        "c1": {"type": "mysql", "config": {}, "secret": {"dsn": "mysql://user:pass@host/db"}},
    }


def test_compile_single_source_single_sink():
    yml = compile_worker_yaml(_worker(), _connectors())
    data = yaml.safe_load(yml)
    assert data["app"]["name"] == "order-sync"
    assert data["apiVersion"] == "onestep/v1alpha1"
    assert data["kind"] == "App"
    # One connector resource (shared by source + sink).
    conn_resources = {k: v for k, v in data["resources"].items() if v["type"] == "mysql"}
    assert len(conn_resources) == 1
    conn = list(conn_resources.values())[0]
    assert conn["dsn"] == "mysql://user:pass@host/db"
    # Source resource references the connector.
    src = [v for v in data["resources"].values() if v["type"] == "mysql_incremental"][0]
    assert src["connector"] in conn_resources
    assert src["table"] == "orders"
    # Task: single source, emit list with one sink.
    task = data["tasks"][0]
    assert task["name"] == "main"
    assert task["source"] in data["resources"]
    assert task["emit"] == [k for k in data["resources"] if data["resources"][k]["type"] == "mysql_table_sink"]
    assert task["handler"]["ref"] == "myworker.handlers:sync"


def test_compile_multiple_sinks_uses_emit_list():
    worker = _worker(sinks=[
        {"type": "mysql_table_sink", "connector_id": "c1", "fields": {"table": "t1"}},
        {"type": "mysql_table_sink", "connector_id": "c1", "fields": {"table": "t2"}},
    ])
    yml = compile_worker_yaml(worker, _connectors())
    data = yaml.safe_load(yml)
    task = data["tasks"][0]
    assert len(task["emit"]) == 2


def test_compile_builtin_source_has_no_connector_key():
    worker = _worker(source={
        "type": "interval",
        "connector_id": None,
        "fields": {"minutes": 5},
    })
    yml = compile_worker_yaml(worker, {})
    data = yaml.safe_load(yml)
    src = [v for v in data["resources"].values() if v["type"] == "interval"][0]
    assert "connector" not in src
    assert src["minutes"] == 5


def test_compile_shared_connector_dedup():
    # Source and sink reference the same connector_id — one connector resource.
    yml = compile_worker_yaml(_worker(), _connectors())
    data = yaml.safe_load(yml)
    conn_keys = [k for k, v in data["resources"].items() if v["type"] == "mysql"]
    assert len(conn_keys) == 1


def test_merge_package_adds_worker_yaml():
    # Handler zip with code but no worker.yaml.
    handler_buf = io.BytesIO()
    with zipfile.ZipFile(handler_buf, "w") as zf:
        zf.writestr("src/myworker/handlers.py", "def sync(): pass")
    handler_bytes = handler_buf.getvalue()

    merged = merge_package(handler_bytes, "app:\n  name: test\n")
    with zipfile.ZipFile(io.BytesIO(merged), "r") as zf:
        names = zf.namelist()
    assert "worker.yaml" in names
    assert "src/myworker/handlers.py" in names


def test_merge_package_overwrites_existing_worker_yaml():
    handler_buf = io.BytesIO()
    with zipfile.ZipFile(handler_buf, "w") as zf:
        zf.writestr("worker.yaml", "OLD")
        zf.writestr("code.py", "x = 1")
    handler_bytes = handler_buf.getvalue()

    merged = merge_package(handler_bytes, "app:\n  name: new\n")
    with zipfile.ZipFile(io.BytesIO(merged), "r") as zf:
        assert zf.read("worker.yaml").decode() == "app:\n  name: new\n"
        assert zf.read("code.py").decode() == "x = 1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd onestep-control-plane && uv run pytest backend/tests/test_worker_compiler.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Write the compiler**

Create `backend/src/onestep_control_plane_api/api/worker_compiler.py`:

```python
from __future__ import annotations

import io
import zipfile
from typing import Any

import yaml

#: Source/sink types that do NOT need a connector resource (built-in).
BUILTIN_SOURCE_TYPES = frozenset({"interval", "cron", "webhook", "memory"})
BUILTIN_SINK_TYPES = frozenset({"memory"})

#: Sink types that have no connector dependency.
NO_CONNECTOR_SINK_TYPES = frozenset({"http_sink"})


def _needs_connector(source_or_sink: dict[str, Any]) -> bool:
    typ = source_or_sink["type"]
    if source_or_sink.get("connector_id"):
        return True
    return typ not in BUILTIN_SOURCE_TYPES and typ not in NO_CONNECTOR_SINK_TYPES


def compile_worker_yaml(
    worker: dict[str, Any],
    connectors: dict[str, dict[str, Any]],
) -> str:
    """Compile a worker config + resolved connectors into a worker.yaml string.

    ``worker`` shape: {name, handler_ref, source: {type, connector_id, fields},
    sinks: [{type, connector_id, fields}, ...]}
    ``connectors`` shape: {connector_id: {type, config: {...}, secret: {...}}}
    """
    source = worker["source"]
    sinks = worker.get("sinks", [])

    resources: dict[str, dict[str, Any]] = {}
    # Map connector_id → resource key (dedup: same connector_id = same resource).
    conn_key_map: dict[str, str] = {}

    def resolve_connector(connector_id: str) -> str:
        if connector_id in conn_key_map:
            return conn_key_map[connector_id]
        conn = connectors[connector_id]
        key = f"conn_{len(conn_key_map)}"
        resource: dict[str, Any] = {"type": conn["type"]}
        resource.update(conn.get("config", {}))
        resource.update(conn.get("secret", {}))
        resources[key] = resource
        conn_key_map[connector_id] = key
        return key

    # Source resource.
    src_resource: dict[str, Any] = {"type": source["type"]}
    if _needs_connector(source) and source.get("connector_id"):
        src_resource["connector"] = resolve_connector(source["connector_id"])
    src_resource.update(source.get("fields", {}))
    resources["source_0"] = src_resource

    # Sink resources.
    for index, sink in enumerate(sinks):
        sink_resource: dict[str, Any] = {"type": sink["type"]}
        if _needs_connector(sink) and sink.get("connector_id"):
            sink_resource["connector"] = resolve_connector(sink["connector_id"])
        sink_resource.update(sink.get("fields", {}))
        resources[f"sink_{index}"] = sink_resource

    # Single task.
    task: dict[str, Any] = {
        "name": "main",
        "source": "source_0",
        "handler": {"ref": worker["handler_ref"]},
    }
    if sinks:
        task["emit"] = [f"sink_{i}" for i in range(len(sinks))]

    doc = {
        "apiVersion": "onestep/v1alpha1",
        "kind": "App",
        "app": {"name": worker["name"]},
        "resources": resources,
        "tasks": [task],
    }
    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)


def merge_package(handler_zip_bytes: bytes, worker_yaml_str: str) -> bytes:
    """Merge a compiled worker.yaml into a handler zip, overwriting any existing one."""
    out_buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(handler_zip_bytes), "r") as src_zip:
        with zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as out_zip:
            for item in src_zip.infolist():
                if item.filename == "worker.yaml":
                    continue  # overwrite with the compiled one
                out_zip.writestr(item, src_zip.read(item.filename))
            out_zip.writestr("worker.yaml", worker_yaml_str)
    return out_buf.getvalue()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd onestep-control-plane && uv run pytest backend/tests/test_worker_compiler.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/onestep_control_plane_api/api/worker_compiler.py backend/tests/test_worker_compiler.py
git commit -m "feat: add worker.yaml compiler + package merge"
```

---

## Task 2: Worker model + migration

**Files:**
- Modify: `backend/src/onestep_control_plane_api/db/models.py`
- Create: `backend/alembic/versions/202606170002_add_workers_table.py`
- Modify: `backend/tests/test_migrations.py`

- [ ] **Step 1: Add the Worker model**

Append to `backend/src/onestep_control_plane_api/db/models.py` (after `Connector`):

```python
class Worker(Base):
    __tablename__ = "workers"
    __table_args__ = (
        sa.UniqueConstraint("name", name="uq_workers_name"),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    description: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    handler_package_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), nullable=True
    )
    handler_ref: Mapped[str] = mapped_column(sa.String(255), nullable=False, default="handler:handler")
    source_config: Mapped[dict[str, object]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    sink_configs: Mapped[list[object]] = mapped_column(JSON_TYPE, nullable=False, default=list)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )
```

- [ ] **Step 2: Write the migration**

Create `backend/alembic/versions/202606170002_add_workers_table.py`:

```python
"""Add workers table.

Revision ID: 202606170002
Revises: 202606170001
Create Date: 2026-06-17 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202606170002"
down_revision: str | None = "202606170001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if not _has_table("workers"):
        op.create_table(
            "workers",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("handler_package_id", sa.Uuid(), nullable=True),
            sa.Column("handler_ref", sa.String(length=255), nullable=False),
            sa.Column("source_config", json_type, nullable=False),
            sa.Column("sink_configs", json_type, nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("name", name="uq_workers_name"),
        )


def downgrade() -> None:
    if _has_table("workers"):
        op.drop_table("workers")
```

- [ ] **Step 3: Update test_migrations.py**

In `backend/tests/test_migrations.py`:
- Change `HEAD_REVISION = "202606170001"` → `HEAD_REVISION = "202606170002"`.
- Add `"workers"` to the expected table set (alphabetically, after the existing entries, before or after `"worker_agents"` — it sorts as `workers` < `worker_agents` alphabetically since `s` < `_`... actually check: add `"workers",` in the set literal near `"connectors"`).

- [ ] **Step 4: Run migration tests**

Run: `cd onestep-control-plane && uv run pytest backend/tests/test_migrations.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/onestep_control_plane_api/db/models.py backend/alembic/versions/202606170002_add_workers_table.py backend/tests/test_migrations.py
git commit -m "feat: add Worker model + workers table migration"
```

---

## Task 3: Worker API schemas

**Files:**
- Modify: `backend/src/onestep_control_plane_api/api/schemas.py`

- [ ] **Step 1: Add worker schemas**

Append to `backend/src/onestep_control_plane_api/api/schemas.py`:

```python
class WorkerSourceConfig(APIModel):
    type: str
    connector_id: str | None = None
    fields: dict[str, object] = Field(default_factory=dict)


class WorkerSinkConfig(APIModel):
    type: str
    connector_id: str | None = None
    fields: dict[str, object] = Field(default_factory=dict)


class WorkerSummary(APIModel):
    id: str
    name: str
    description: str
    handler_package_id: str | None = None
    handler_ref: str
    source_config: WorkerSourceConfig
    sink_configs: list[WorkerSinkConfig]
    status: str
    created_at: str | None = None
    updated_at: str | None = None


class WorkerListResponse(APIModel):
    items: list[WorkerSummary]
    total: int


class WorkerCreateRequest(APIModel):
    name: str
    description: str = ""
    handler_package_id: str | None = None
    handler_ref: str = "handler:handler"
    source_config: WorkerSourceConfig
    sink_configs: list[WorkerSinkConfig] = Field(default_factory=list)


class WorkerUpdateRequest(APIModel):
    name: str | None = None
    description: str | None = None
    handler_package_id: str | None = None
    handler_ref: str | None = None
    source_config: WorkerSourceConfig | None = None
    sink_configs: list[WorkerSinkConfig] | None = None
    status: str | None = None


class WorkerDeployRequest(APIModel):
    worker_agent_id: str
    desired_status: str = "running"
```

- [ ] **Step 2: Verify import**

Run: `cd onestep-control-plane && uv run python -c "from onestep_control_plane_api.api.schemas import WorkerSummary, WorkerDeployRequest; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add backend/src/onestep_control_plane_api/api/schemas.py
git commit -m "feat: add worker API schemas"
```

---

## Task 4: Worker service + router

**Files:**
- Create: `backend/src/onestep_control_plane_api/api/worker_service.py`
- Create: `backend/src/onestep_control_plane_api/api/routers/workers.py`
- Modify: `backend/src/onestep_control_plane_api/api/routers/__init__.py`

- [ ] **Step 1: Write the worker service**

Create `backend/src/onestep_control_plane_api/api/worker_service.py`:

```python
from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.connector_service import (
    build_connector_summary,
    get_connector_or_404,
)
from onestep_control_plane_api.api.schemas import (
    WorkerCreateRequest,
    WorkerDeployRequest,
    WorkerSinkConfig,
    WorkerSourceConfig,
    WorkerUpdateRequest,
)
from onestep_control_plane_api.api.worker_agent_service import (
    create_worker_deployment,
    create_workflow_package,
    get_workflow_package_path_or_404,
)
from onestep_control_plane_api.api.worker_compiler import compile_worker_yaml, merge_package
from onestep_control_plane_api.db.models import Worker

WORKER_FIELDS = {
    "id",
    "name",
    "description",
    "handler_package_id",
    "handler_ref",
    "source_config",
    "sink_configs",
    "status",
    "created_at",
    "updated_at",
}


def _serialize(worker: Worker) -> dict[str, object]:
    return {
        "id": str(worker.id),
        "name": worker.name,
        "description": worker.description,
        "handler_package_id": str(worker.handler_package_id) if worker.handler_package_id else None,
        "handler_ref": worker.handler_ref,
        "source_config": worker.source_config,
        "sink_configs": worker.sink_configs,
        "status": worker.status,
        "created_at": worker.created_at.isoformat() if worker.created_at else None,
        "updated_at": worker.updated_at.isoformat() if worker.updated_at else None,
    }


def list_workers(db: Session) -> list[dict[str, object]]:
    rows = db.execute(select(Worker).order_by(Worker.updated_at.desc())).scalars().all()
    return [_serialize(w) for w in rows]


def get_worker_or_404(db: Session, worker_id: UUID) -> Worker:
    worker = db.get(Worker, worker_id)
    if worker is None:
        raise HTTPException(status_code=404, detail="worker not found")
    return worker


def create_worker(db: Session, request: WorkerCreateRequest) -> dict[str, object]:
    existing = db.scalar(select(Worker).where(Worker.name == request.name))
    if existing is not None:
        raise HTTPException(status_code=409, detail="worker name already exists")
    worker = Worker(
        id=uuid4(),
        name=request.name,
        description=request.description,
        handler_package_id=UUID(request.handler_package_id) if request.handler_package_id else None,
        handler_ref=request.handler_ref,
        source_config=request.source_config.model_dump(),
        sink_configs=[s.model_dump() for s in request.sink_configs],
        status="draft",
    )
    db.add(worker)
    db.commit()
    db.refresh(worker)
    return _serialize(worker)


def update_worker(db: Session, worker_id: UUID, request: WorkerUpdateRequest) -> dict[str, object]:
    worker = get_worker_or_404(db, worker_id)
    if request.name is not None:
        dup = db.scalar(select(Worker).where(Worker.name == request.name, Worker.id != worker_id))
        if dup is not None:
            raise HTTPException(status_code=409, detail="worker name already exists")
        worker.name = request.name
    if request.description is not None:
        worker.description = request.description
    if request.handler_package_id is not None:
        worker.handler_package_id = UUID(request.handler_package_id) if request.handler_package_id else None
    if request.handler_ref is not None:
        worker.handler_ref = request.handler_ref
    if request.source_config is not None:
        worker.source_config = request.source_config.model_dump()
    if request.sink_configs is not None:
        worker.sink_configs = [s.model_dump() for s in request.sink_configs]
    if request.status is not None:
        worker.status = request.status
    db.commit()
    db.refresh(worker)
    return _serialize(worker)


def delete_worker(db: Session, worker_id: UUID) -> None:
    worker = get_worker_or_404(db, worker_id)
    db.delete(worker)
    db.commit()


def _resolve_connectors(db: Session, worker: Worker) -> dict[str, dict[str, object]]:
    """Collect and decrypt all connectors referenced by source + sinks."""
    connector_ids: set[str] = set()
    src = worker.source_config
    if isinstance(src, dict) and src.get("connector_id"):
        connector_ids.add(src["connector_id"])
    for sink in worker.sink_configs:
        if isinstance(sink, dict) and sink.get("connector_id"):
            connector_ids.add(sink["connector_id"])

    resolved: dict[str, dict[str, object]] = {}
    for cid in connector_ids:
        connector = get_connector_or_404(db, UUID(cid))
        summary = build_connector_summary(connector, include_cleartext_secret=True)
        resolved[cid] = {
            "type": summary["type"],
            "config": summary["config"],
            "secret": summary["secret"],
        }
    return resolved


def deploy_worker(db: Session, worker_id: UUID, request: WorkerDeployRequest) -> dict[str, object]:
    worker = get_worker_or_404(db, worker_id)
    if worker.handler_package_id is None:
        raise HTTPException(status_code=422, detail="worker has no handler package")

    from onestep_control_plane_api.api.worker_agent_service import get_workflow_package_or_404

    handler_pkg = get_workflow_package_or_404(db, worker.handler_package_id)
    handler_path = get_workflow_package_path_or_404(handler_pkg)
    handler_bytes = handler_path.read_bytes()

    connectors = _resolve_connectors(db, worker)
    worker_dict = {
        "name": worker.name,
        "handler_ref": worker.handler_ref,
        "source": worker.source_config,
        "sinks": worker.sink_configs,
    }
    yaml_str = compile_worker_yaml(worker_dict, connectors)
    merged_bytes = merge_package(handler_bytes, yaml_str)

    merged_pkg = create_workflow_package(
        db,
        workflow_id=uuid4(),
        version=worker.updated_at.isoformat() if worker.updated_at else "deploy",
        filename=f"{worker.name}.zip",
        content_type="application/zip",
        content=merged_bytes,
        entrypoint="worker.yaml",
        created_by="worker-builder",
    )
    from onestep_control_plane_api.api.schemas import WorkerDeploymentCreateRequest

    deployment = create_worker_deployment(
        db,
        WorkerDeploymentCreateRequest(
            workflow_package_id=str(merged_pkg.package_id),
            worker_agent_id=request.worker_agent_id,
            desired_status=request.desired_status,
            params={},
            env={},
            credential_refs=[],
        ),
        created_by="worker-builder",
    )
    from onestep_control_plane_api.api.worker_agent_service import build_worker_deployment_summary

    return build_worker_deployment_summary(deployment)
```

- [ ] **Step 2: Write the router**

Create `backend/src/onestep_control_plane_api/api/routers/workers.py`:

```python
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.schemas import (
    WorkerCreateRequest,
    WorkerDeployRequest,
    WorkerListResponse,
    WorkerSummary,
    WorkerUpdateRequest,
)
from onestep_control_plane_api.api.security import require_console_auth
from onestep_control_plane_api.api.worker_service import (
    create_worker,
    delete_worker,
    deploy_worker,
    list_workers,
    update_worker,
)
from onestep_control_plane_api.db.session import get_db_session
from onestep_control_plane_api.api.worker_agent_service import build_worker_deployment_summary

router = APIRouter(prefix="/api/v1", tags=["workers"])


@router.get(
    "/workers",
    response_model=WorkerListResponse,
    dependencies=[Depends(require_console_auth)],
)
def list_workers_endpoint(db: Session = Depends(get_db_session)) -> WorkerListResponse:
    items = list_workers(db)
    return WorkerListResponse(items=items, total=len(items))


@router.post(
    "/workers",
    response_model=WorkerSummary,
    dependencies=[Depends(require_console_auth)],
)
def create_worker_endpoint(
    request: WorkerCreateRequest,
    db: Session = Depends(get_db_session),
) -> WorkerSummary:
    return WorkerSummary(**create_worker(db, request))


@router.get(
    "/workers/{worker_id}",
    response_model=WorkerSummary,
    dependencies=[Depends(require_console_auth)],
)
def get_worker_endpoint(
    worker_id: UUID,
    db: Session = Depends(get_db_session),
) -> WorkerSummary:
    from onestep_control_plane_api.api.worker_service import get_worker_or_404, _serialize

    return WorkerSummary(**_serialize(get_worker_or_404(db, worker_id)))


@router.put(
    "/workers/{worker_id}",
    response_model=WorkerSummary,
    dependencies=[Depends(require_console_auth)],
)
def update_worker_endpoint(
    worker_id: UUID,
    request: WorkerUpdateRequest,
    db: Session = Depends(get_db_session),
) -> WorkerSummary:
    return WorkerSummary(**update_worker(db, worker_id, request))


@router.delete(
    "/workers/{worker_id}",
    status_code=204,
    dependencies=[Depends(require_console_auth)],
)
def delete_worker_endpoint(
    worker_id: UUID,
    db: Session = Depends(get_db_session),
) -> None:
    delete_worker(db, worker_id)


@router.post(
    "/workers/{worker_id}/deploy",
    dependencies=[Depends(require_console_auth)],
)
def deploy_worker_endpoint(
    worker_id: UUID,
    request: WorkerDeployRequest,
    db: Session = Depends(get_db_session),
) -> dict:
    return deploy_worker(db, worker_id, request)
```

- [ ] **Step 3: Register the router**

In `backend/src/onestep_control_plane_api/api/routers/__init__.py`, add the import:

```python
from onestep_control_plane_api.api.routers.workers import router as workers_router
```

And add:

```python
router.include_router(workers_router)
```

- [ ] **Step 4: Verify routes registered**

Run: `cd onestep-control-plane && uv run python -c "from onestep_control_plane_api.api.routers import router; print([r.path for r in router.routes if 'worker' in r.path and 'agent' not in r.path])"`
Expected: prints the 6 worker paths (list/create/get/update/delete/deploy).

- [ ] **Step 5: Commit**

```bash
git add backend/src/onestep_control_plane_api/api/worker_service.py backend/src/onestep_control_plane_api/api/routers/workers.py backend/src/onestep_control_plane_api/api/routers/__init__.py
git commit -m "feat: add worker service + REST router"
```

---

## Task 5: Worker API tests

**Files:**
- Create: `backend/tests/test_workers_api.py`

- [ ] **Step 1: Write the tests**

Create `backend/tests/test_workers_api.py`:

```python
from __future__ import annotations


def _create_worker_payload(name="order-sync"):
    return {
        "name": name,
        "description": "sync orders",
        "handler_ref": "myworker.handlers:sync",
        "source_config": {
            "type": "interval",
            "connector_id": None,
            "fields": {"minutes": 5},
        },
        "sink_configs": [],
    }


def test_create_and_list_worker(client):
    response = client.post("/api/v1/workers", json=_create_worker_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "order-sync"
    assert body["handler_ref"] == "myworker.handlers:sync"
    assert body["status"] == "draft"
    assert body["source_config"]["type"] == "interval"

    listing = client.get("/api/v1/workers")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["name"] == "order-sync"


def test_update_worker(client):
    create = client.post("/api/v1/workers", json=_create_worker_payload())
    worker_id = create.json()["id"]

    response = client.put(
        f"/api/v1/workers/{worker_id}",
        json={"description": "updated desc", "status": "ready"},
    )
    assert response.status_code == 200
    assert response.json()["description"] == "updated desc"
    assert response.json()["status"] == "ready"


def test_delete_worker(client):
    create = client.post("/api/v1/workers", json=_create_worker_payload())
    worker_id = create.json()["id"]

    response = client.delete(f"/api/v1/workers/{worker_id}")
    assert response.status_code == 204

    gone = client.get(f"/api/v1/workers/{worker_id}")
    assert gone.status_code == 404


def test_create_rejects_duplicate_name(client):
    client.post("/api/v1/workers", json=_create_worker_payload())
    response = client.post("/api/v1/workers", json=_create_worker_payload())
    assert response.status_code == 409


def test_deploy_without_handler_package_returns_422(client):
    create = client.post("/api/v1/workers", json=_create_worker_payload())
    worker_id = create.json()["id"]

    response = client.post(
        f"/api/v1/workers/{worker_id}/deploy",
        json={"worker_agent_id": "11111111-1111-1111-1111-111111111111"},
    )
    assert response.status_code == 422
```

- [ ] **Step 2: Run tests**

Run: `cd onestep-control-plane && uv run pytest backend/tests/test_workers_api.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_workers_api.py
git commit -m "test: worker API CRUD + deploy validation"
```

---

## Task 6: Frontend types + API client

**Files:**
- Modify: `frontend/src/lib/api/types.ts`
- Modify: `frontend/src/lib/api/client.ts`

- [ ] **Step 1: Add worker types**

Append to `frontend/src/lib/api/types.ts`:

```ts
export interface WorkerSourceConfig {
  type: string;
  connector_id: string | null;
  fields: JsonObject;
}

export interface WorkerSinkConfig {
  type: string;
  connector_id: string | null;
  fields: JsonObject;
}

export interface WorkerSummary {
  id: string;
  name: string;
  description: string;
  handler_package_id: string | null;
  handler_ref: string;
  source_config: WorkerSourceConfig;
  sink_configs: WorkerSinkConfig[];
  status: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface WorkerListResponse {
  items: WorkerSummary[];
  total: number;
}

export interface WorkerCreateRequest {
  name: string;
  description?: string;
  handler_package_id?: string | null;
  handler_ref?: string;
  source_config: WorkerSourceConfig;
  sink_configs?: WorkerSinkConfig[];
}

export interface WorkerUpdateRequest {
  name?: string;
  description?: string;
  handler_package_id?: string | null;
  handler_ref?: string;
  source_config?: WorkerSourceConfig;
  sink_configs?: WorkerSinkConfig[];
  status?: string;
}

export interface WorkerDeployRequest {
  worker_agent_id: string;
  desired_status?: string;
}
```

- [ ] **Step 2: Add API client functions**

In `frontend/src/lib/api/client.ts`, add the type imports:

```ts
  WorkerCreateRequest,
  WorkerDeployRequest,
  WorkerListResponse,
  WorkerSummary,
  WorkerUpdateRequest,
```

Append the functions at end of file:

```ts
export function listWorkers() {
  return request<WorkerListResponse>("/api/v1/workers");
}

export function createWorker(payload: WorkerCreateRequest) {
  return request<WorkerSummary>("/api/v1/workers", { method: "POST", body: payload });
}

export function updateWorker(workerId: string, payload: WorkerUpdateRequest) {
  return request<WorkerSummary>(`/api/v1/workers/${encodeURIComponent(workerId)}`, {
    method: "PUT",
    body: payload,
  });
}

export function deleteWorker(workerId: string) {
  return request<void>(`/api/v1/workers/${encodeURIComponent(workerId)}`, {
    method: "DELETE",
  });
}

export function deployWorker(workerId: string, payload: WorkerDeployRequest) {
  return request<{ deployment_id: string }>(
    `/api/v1/workers/${encodeURIComponent(workerId)}/deploy`,
    { method: "POST", body: payload },
  );
}
```

- [ ] **Step 3: Verify typecheck**

Run: `cd onestep-control-plane/frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/api/types.ts frontend/src/lib/api/client.ts
git commit -m "feat: add worker API client + types"
```

---

## Task 7: Frontend source/sink catalog

**Files:**
- Create: `frontend/src/features/workers/catalog.ts`

- [ ] **Step 1: Write the catalog**

Create `frontend/src/features/workers/catalog.ts`:

```ts
export interface SourceSinkField {
  name: string;
  label: string;
  type: "text" | "number" | "password" | "list" | "select";
  required: boolean;
  options?: string[];
  placeholder?: string;
}

export interface SourceSinkTypeSchema {
  label: string;
  needsConnector: boolean;
  fields: SourceSinkField[];
}

export const sourceTypeSchemas: Record<string, SourceSinkTypeSchema> = {
  interval: {
    label: "Interval (schedule)",
    needsConnector: false,
    fields: [
      { name: "minutes", label: "Minutes", type: "number", required: false },
      { name: "seconds", label: "Seconds", type: "number", required: false },
      { name: "immediate", label: "Immediate", type: "text", required: false },
    ],
  },
  cron: {
    label: "Cron (schedule)",
    needsConnector: false,
    fields: [
      { name: "expression", label: "Cron Expression", type: "text", required: true, placeholder: "0 */5 * * *" },
    ],
  },
  webhook: {
    label: "Webhook (HTTP)",
    needsConnector: false,
    fields: [
      { name: "path", label: "Path", type: "text", required: true, placeholder: "/hook" },
      { name: "port", label: "Port", type: "number", required: false },
    ],
  },
  rabbitmq_queue: {
    label: "RabbitMQ Queue",
    needsConnector: true,
    fields: [
      { name: "queue", label: "Queue", type: "text", required: true },
      { name: "exchange", label: "Exchange", type: "text", required: false },
    ],
  },
  redis_stream: {
    label: "Redis Stream",
    needsConnector: true,
    fields: [
      { name: "stream", label: "Stream", type: "text", required: true },
      { name: "group", label: "Consumer Group", type: "text", required: false },
    ],
  },
  sqs_queue: {
    label: "AWS SQS Queue",
    needsConnector: true,
    fields: [
      { name: "url", label: "Queue URL", type: "text", required: true },
    ],
  },
  mysql_incremental: {
    label: "MySQL Incremental",
    needsConnector: true,
    fields: [
      { name: "table", label: "Table", type: "text", required: true },
      { name: "key", label: "Key Column", type: "text", required: true },
      { name: "cursor", label: "Cursor Columns", type: "list", required: true },
    ],
  },
  mysql_table_queue: {
    label: "MySQL Table Queue",
    needsConnector: true,
    fields: [
      { name: "table", label: "Table", type: "text", required: true },
      { name: "key", label: "Key Column", type: "text", required: true },
    ],
  },
  postgres_incremental: {
    label: "PostgreSQL Incremental",
    needsConnector: true,
    fields: [
      { name: "table", label: "Table", type: "text", required: true },
      { name: "key", label: "Key Column", type: "text", required: true },
      { name: "cursor", label: "Cursor Columns", type: "list", required: true },
    ],
  },
  postgres_table_queue: {
    label: "PostgreSQL Table Queue",
    needsConnector: true,
    fields: [
      { name: "table", label: "Table", type: "text", required: true },
      { name: "key", label: "Key Column", type: "text", required: true },
    ],
  },
};

export const sinkTypeSchemas: Record<string, SourceSinkTypeSchema> = {
  http_sink: {
    label: "HTTP Sink",
    needsConnector: false,
    fields: [
      { name: "url", label: "URL", type: "text", required: true, placeholder: "https://..." },
      { name: "method", label: "Method", type: "select", required: false, options: ["POST", "PUT", "PATCH", "DELETE"] },
    ],
  },
  rabbitmq_queue: {
    label: "RabbitMQ Queue",
    needsConnector: true,
    fields: [
      { name: "queue", label: "Queue", type: "text", required: true },
    ],
  },
  redis_stream: {
    label: "Redis Stream",
    needsConnector: true,
    fields: [
      { name: "stream", label: "Stream", type: "text", required: true },
    ],
  },
  sqs_queue: {
    label: "AWS SQS Queue",
    needsConnector: true,
    fields: [
      { name: "url", label: "Queue URL", type: "text", required: true },
    ],
  },
  mysql_table_sink: {
    label: "MySQL Table Sink",
    needsConnector: true,
    fields: [
      { name: "table", label: "Table", type: "text", required: true },
      { name: "mode", label: "Mode", type: "select", required: false, options: ["insert", "upsert"] },
      { name: "keys", label: "Upsert Keys", type: "list", required: false },
    ],
  },
  postgres_table_sink: {
    label: "PostgreSQL Table Sink",
    needsConnector: true,
    fields: [
      { name: "table", label: "Table", type: "text", required: true },
      { name: "mode", label: "Mode", type: "select", required: false, options: ["insert", "upsert"] },
      { name: "keys", label: "Upsert Keys", type: "list", required: false },
    ],
  },
};

export const sourceTypeOrder = Object.keys(sourceTypeSchemas).sort();
export const sinkTypeOrder = Object.keys(sinkTypeSchemas).sort();
```

- [ ] **Step 2: Verify typecheck**

Run: `cd onestep-control-plane/frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/workers/catalog.ts
git commit -m "feat: add source/sink type catalog"
```

---

## Task 8: Frontend worker queries

**Files:**
- Create: `frontend/src/features/workers/queries.ts`

- [ ] **Step 1: Write the queries**

Create `frontend/src/features/workers/queries.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createWorker,
  deleteWorker,
  deployWorker,
  listWorkers,
  updateWorker,
} from "../../lib/api/client";
import type {
  WorkerCreateRequest,
  WorkerDeployRequest,
  WorkerUpdateRequest,
} from "../../lib/api/types";

export const WORKERS_QUERY_KEY = ["workers"];

export function useWorkersQuery() {
  return useQuery({
    queryKey: WORKERS_QUERY_KEY,
    queryFn: () => listWorkers(),
  });
}

export function useCreateWorkerMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: WorkerCreateRequest) => createWorker(payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: WORKERS_QUERY_KEY }),
  });
}

export function useUpdateWorkerMutation(workerId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: WorkerUpdateRequest) => updateWorker(workerId, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: WORKERS_QUERY_KEY }),
  });
}

export function useDeleteWorkerMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (workerId: string) => deleteWorker(workerId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: WORKERS_QUERY_KEY }),
  });
}

export function useDeployWorkerMutation(workerId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: WorkerDeployRequest) => deployWorker(workerId, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: WORKERS_QUERY_KEY }),
  });
}
```

- [ ] **Step 2: Verify typecheck**

Run: `cd onestep-control-plane/frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/workers/queries.ts
git commit -m "feat: add worker React Query hooks"
```

---

## Task 9: Frontend workers list page

**Files:**
- Create: `frontend/src/pages/workers/WorkersListPage.tsx`

- [ ] **Step 1: Write the list page**

Create `frontend/src/pages/workers/WorkersListPage.tsx`:

```tsx
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { EmptyState } from "../../components/ui/EmptyState";
import { SignalConsoleHeader } from "../../components/ui/SignalConsoleHeader";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { useWorkersQuery } from "../../features/workers/queries";
import { formatRelativeTime } from "../../lib/formatters";

export function WorkersListPage() {
  const { t } = useTranslation();
  const workersQuery = useWorkersQuery();
  const workers = workersQuery.data?.items ?? [];

  return (
    <div className="ref-console-page workers-list-page">
      <SignalConsoleHeader
        kicker={t("workersList.eyebrow")}
        title={t("workersList.title")}
        description={<p className="signal-console-hero-note">{t("workersList.subtitle")}</p>}
        side={
          <Link to="/workers/new">
            <button type="button">{t("workersList.newWorker")}</button>
          </Link>
        }
      />

      {workersQuery.error ? (
        <EmptyState title={t("workersList.loadErrorTitle")} body={String(workersQuery.error)} />
      ) : null}
      {workersQuery.isPending ? (
        <div className="loading-block">{t("workersList.loading")}</div>
      ) : null}
      {!workersQuery.isPending && !workersQuery.error && workers.length === 0 ? (
        <EmptyState title={t("workersList.emptyTitle")} body={t("workersList.emptyBody")} />
      ) : null}

      {workers.length > 0 ? (
        <section className="ref-table-card">
          <div className="ref-table-head">
            <span>{t("workersList.tableHeaderName")}</span>
            <span>{t("workersList.tableHeaderStatus")}</span>
            <span>{t("workersList.tableHeaderSource")}</span>
            <span>{t("workersList.tableHeaderSinks")}</span>
            <span>{t("workersList.tableHeaderUpdated")}</span>
          </div>
          <div className="ref-table-body">
            {workers.map((worker) => (
              <article className="ref-table-row" key={worker.id}>
                <div className="ref-service-cell">
                  <Link className="ref-service-link" to={`/workers/${encodeURIComponent(worker.id)}`}>
                    <strong>{worker.name}</strong>
                    <span>{worker.description}</span>
                  </Link>
                </div>
                <div className="ref-meta-cell">
                  <StatusBadge value={worker.status === "ready" ? "active" : "pending"} />
                </div>
                <div className="ref-meta-cell">
                  <strong>{worker.source_config.type}</strong>
                </div>
                <div className="ref-meta-cell">
                  <strong>{worker.sink_configs.length}</strong>
                </div>
                <div className="ref-meta-cell">
                  <strong>{formatRelativeTime(worker.updated_at)}</strong>
                </div>
              </article>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 2: Verify typecheck**

Run: `cd onestep-control-plane/frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/workers/WorkersListPage.tsx
git commit -m "feat: add workers list page"
```

---

## Task 10: Frontend worker editor page

**Files:**
- Create: `frontend/src/pages/workers/WorkerEditorPage.tsx`

- [ ] **Step 1: Write the editor page**

Create `frontend/src/pages/workers/WorkerEditorPage.tsx`:

```tsx
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { EmptyState } from "../../components/ui/EmptyState";
import { SignalConsoleHeader } from "../../components/ui/SignalConsoleHeader";
import {
  sinkTypeSchemas,
  sinkTypeOrder,
  sourceTypeOrder,
  sourceTypeSchemas,
} from "../../features/workers/catalog";
import {
  useConnectorsQuery,
} from "../../features/connectors/queries";
import {
  useCreateWorkerMutation,
  useDeployWorkerMutation,
  useUpdateWorkerMutation,
  useWorkersQuery,
} from "../../features/workers/queries";
import type { WorkerSinkConfig, WorkerSourceConfig } from "../../lib/api/types";

type Draft = {
  name: string;
  description: string;
  handlerRef: string;
  source: WorkerSourceConfig;
  sinks: WorkerSinkConfig[];
};

function emptyDraft(): Draft {
  return {
    name: "",
    description: "",
    handlerRef: "handler:handler",
    source: { type: "interval", connector_id: null, fields: {} },
    sinks: [],
  };
}

export function WorkerEditorPage() {
  const { workerId } = useParams<{ workerId: string }>();
  const isEditing = Boolean(workerId && workerId !== "new");
  const navigate = useNavigate();
  const { t } = useTranslation();
  const workersQuery = useWorkersQuery();
  const connectorsQuery = useConnectorsQuery();
  const createMutation = useCreateWorkerMutation();
  const updateMutation = useUpdateWorkerMutation(workerId ?? "");
  const deployMutation = useDeployWorkerMutation(workerId ?? "");

  const existing = isEditing
    ? workersQuery.data?.items.find((w) => w.id === workerId)
    : undefined;

  const [draft, setDraft] = useState<Draft>(
    existing
      ? {
          name: existing.name,
          description: existing.description,
          handlerRef: existing.handler_ref,
          source: existing.source_config,
          sinks: existing.sink_configs,
        }
      : emptyDraft(),
  );

  const connectors = connectorsQuery.data?.items ?? [];

  function setSourceField(name: string, value: string) {
    setDraft({
      ...draft,
      source: { ...draft.source, fields: { ...draft.source.fields, [name]: value } },
    });
  }

  function setSinkField(index: number, name: string, value: string) {
    const sinks = [...draft.sinks];
    sinks[index] = {
      ...sinks[index],
      fields: { ...sinks[index].fields, [name]: value },
    };
    setDraft({ ...draft, sinks });
  }

  function addSink() {
    setDraft({
      ...draft,
      sinks: [...draft.sinks, { type: "http_sink", connector_id: null, fields: {} }],
    });
  }

  function removeSink(index: number) {
    setDraft({ ...draft, sinks: draft.sinks.filter((_, i) => i !== index) });
  }

  async function handleSave() {
    if (isEditing && workerId) {
      await updateMutation.mutateAsync({
        name: draft.name,
        description: draft.description,
        handler_ref: draft.handlerRef,
        source_config: draft.source,
        sink_configs: draft.sinks,
      });
    } else {
      await createMutation.mutateAsync({
        name: draft.name,
        description: draft.description,
        handler_ref: draft.handlerRef,
        source_config: draft.source,
        sink_configs: draft.sinks,
      });
      navigate("/workers");
    }
  }

  async function handleDeploy() {
    if (!workerId) return;
    const agentId = window.prompt(t("workerEditor.chooseAgentPrompt"));
    if (!agentId) return;
    await deployMutation.mutateAsync({ worker_agent_id: agentId });
    navigate(`/agents/${encodeURIComponent(agentId)}`);
  }

  if (isEditing && workersQuery.isPending) {
    return <div className="loading-block">{t("workerEditor.loading")}</div>;
  }
  if (isEditing && !existing) {
    return <EmptyState title={t("workerEditor.notFoundTitle")} body={t("workerEditor.notFoundBody")} />;
  }

  const sourceSchema = sourceTypeSchemas[draft.source.type];

  return (
    <div className="ref-console-page worker-editor-page">
      <SignalConsoleHeader
        kicker={t("workerEditor.eyebrow")}
        title={isEditing ? draft.name : t("workerEditor.newTitle")}
        description={<p className="signal-console-hero-note">{t("workerEditor.subtitle")}</p>}
        side={<span />}
      />

      <section className="ref-table-card">
        <h3>{t("workerEditor.identity")}</h3>
        <label className="ref-inline-control">
          <span>{t("workerEditor.name")}</span>
          <input
            type="text"
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
          />
        </label>
        <label className="ref-inline-control">
          <span>{t("workerEditor.description")}</span>
          <input
            type="text"
            value={draft.description}
            onChange={(e) => setDraft({ ...draft, description: e.target.value })}
          />
        </label>
        <label className="ref-inline-control">
          <span>{t("workerEditor.handlerRef")}</span>
          <input
            type="text"
            value={draft.handlerRef}
            onChange={(e) => setDraft({ ...draft, handlerRef: e.target.value })}
          />
        </label>

        <h3>{t("workerEditor.source")}</h3>
        <label className="ref-inline-control ref-inline-control-select">
          <span>{t("workerEditor.sourceType")}</span>
          <select
            value={draft.source.type}
            onChange={(e) =>
              setDraft({ ...draft, source: { type: e.target.value, connector_id: null, fields: {} } })
            }
          >
            {sourceTypeOrder.map((typ) => (
              <option key={typ} value={typ}>
                {sourceTypeSchemas[typ].label}
              </option>
            ))}
          </select>
        </label>
        {sourceSchema?.needsConnector ? (
          <label className="ref-inline-control ref-inline-control-select">
            <span>{t("workerEditor.connector")}</span>
            <select
              value={draft.source.connector_id ?? ""}
              onChange={(e) => setDraft({ ...draft, source: { ...draft.source, connector_id: e.target.value } })}
            >
              <option value="">{t("workerEditor.selectConnector")}</option>
              {connectors.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} ({c.type})
                </option>
              ))}
            </select>
          </label>
        ) : null}
        {sourceSchema?.fields.map((field) => (
          <label className="ref-inline-control" key={field.name}>
            <span>{field.label}</span>
            <input
              type={field.type === "number" ? "number" : "text"}
              placeholder={field.placeholder}
              value={(draft.source.fields[field.name] as string) ?? ""}
              onChange={(e) => setSourceField(field.name, e.target.value)}
            />
          </label>
        ))}

        <h3>{t("workerEditor.sinks")}</h3>
        {draft.sinks.map((sink, index) => {
          const sinkSchema = sinkTypeSchemas[sink.type];
          return (
            <div className="ref-accordion-item" key={index}>
              <label className="ref-inline-control ref-inline-control-select">
                <span>{t("workerEditor.sinkType")} {index + 1}</span>
                <select
                  value={sink.type}
                  onChange={(e) => {
                    const sinks = [...draft.sinks];
                    sinks[index] = { type: e.target.value, connector_id: null, fields: {} };
                    setDraft({ ...draft, sinks });
                  }}
                >
                  {sinkTypeOrder.map((typ) => (
                    <option key={typ} value={typ}>
                      {sinkTypeSchemas[typ].label}
                    </option>
                  ))}
                </select>
              </label>
              {sinkSchema?.needsConnector ? (
                <label className="ref-inline-control ref-inline-control-select">
                  <span>{t("workerEditor.connector")}</span>
                  <select
                    value={sink.connector_id ?? ""}
                    onChange={(e) => {
                      const sinks = [...draft.sinks];
                      sinks[index] = { ...sinks[index], connector_id: e.target.value };
                      setDraft({ ...draft, sinks });
                    }}
                  >
                    <option value="">{t("workerEditor.selectConnector")}</option>
                    {connectors.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name} ({c.type})
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
              {sinkSchema?.fields.map((field) => (
                <label className="ref-inline-control" key={field.name}>
                  <span>{field.label}</span>
                  <input
                    type={field.type === "number" ? "number" : "text"}
                    value={(sink.fields[field.name] as string) ?? ""}
                    onChange={(e) => setSinkField(index, field.name, e.target.value)}
                  />
                </label>
              ))}
              <button type="button" onClick={() => removeSink(index)}>
                {t("workerEditor.removeSink")}
              </button>
            </div>
          );
        })}
        <button type="button" onClick={addSink}>
          {t("workerEditor.addSink")}
        </button>

        <div className="ref-page-actions">
          <button
            type="button"
            disabled={createMutation.isPending || updateMutation.isPending}
            onClick={() => void handleSave()}
          >
            {t("workerEditor.save")}
          </button>
          {isEditing ? (
            <button
              type="button"
              disabled={deployMutation.isPending}
              onClick={() => void handleDeploy()}
            >
              {t("workerEditor.deploy")}
            </button>
          ) : null}
        </div>
      </section>
    </div>
  );
}
```

- [ ] **Step 2: Verify typecheck**

Run: `cd onestep-control-plane/frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/workers/WorkerEditorPage.tsx
git commit -m "feat: add worker editor page (Lambda-style form)"
```

---

## Task 11: Frontend route + nav + i18n

**Files:**
- Modify: `frontend/src/app/router.tsx`
- Modify: `frontend/src/components/layout/AppShell.tsx`
- Modify: `frontend/src/lib/i18n.ts`
- Modify: `frontend/src/components/layout/AppShell.test.tsx`

- [ ] **Step 1: Add routes**

In `frontend/src/app/router.tsx`, add imports:

```ts
import { WorkerEditorPage } from "../pages/workers/WorkerEditorPage";
import { WorkersListPage } from "../pages/workers/WorkersListPage";
```

Add routes inside `AppShell` children (after the `connectors` route):

```tsx
          {
            path: "workers",
            element: <WorkersListPage />,
          },
          {
            path: "workers/:workerId",
            element: <WorkerEditorPage />,
          },
```

- [ ] **Step 2: Add nav link**

In `frontend/src/components/layout/AppShell.tsx`, add a Workers NavLink (after Connectors, before Agents):

```tsx
            <NavLink
              className={({ isActive }) =>
                isActive ? "shell-nav-link active" : "shell-nav-link"
              }
              to="/workers"
            >
              {t("app.workersNav")}
            </NavLink>
```

- [ ] **Step 3: Add i18n keys (en + zh)**

In `frontend/src/lib/i18n.ts`, add to both `app` blocks:

```ts
        workersNav: "Workers",   # en
        workersNav: "工作流",     # zh
```

Add `workersList` + `workerEditor` namespaces. En:

```ts
      workersList: {
        eyebrow: "Build",
        title: "Workers",
        subtitle: "Lambda-style workers: source in, handler, sinks out.",
        loading: "Loading workers...",
        loadErrorTitle: "Unable to load workers",
        emptyTitle: "No workers yet",
        emptyBody: "Create a worker to define a source, handler, and sinks.",
        newWorker: "New worker",
        tableHeaderName: "Name",
        tableHeaderStatus: "Status",
        tableHeaderSource: "Source",
        tableHeaderSinks: "Sinks",
        tableHeaderUpdated: "Updated",
      },
      workerEditor: {
        eyebrow: "Build",
        newTitle: "New worker",
        subtitle: "Configure a source, handler, and sinks.",
        loading: "Loading worker...",
        notFoundTitle: "Worker not found",
        notFoundBody: "The requested worker does not exist.",
        identity: "Identity",
        name: "Name",
        description: "Description",
        handlerRef: "Handler ref",
        source: "Source",
        sourceType: "Source type",
        sinks: "Sinks",
        sinkType: "Sink type",
        connector: "Connector",
        selectConnector: "Select a connector",
        addSink: "Add sink",
        removeSink: "Remove",
        save: "Save",
        deploy: "Deploy to agent",
        chooseAgentPrompt: "Enter the worker agent ID to deploy to:",
      },
```

Zh equivalents:

```ts
      workersList: {
        eyebrow: "构建",
        title: "Worker",
        subtitle: "Lambda 式 worker：数据进入、handler 处理、数据输出。",
        loading: "正在加载 worker...",
        loadErrorTitle: "无法加载 worker",
        emptyTitle: "暂无 worker",
        emptyBody: "创建一个 worker 来定义 source、handler 和 sink。",
        newWorker: "新建 worker",
        tableHeaderName: "名称",
        tableHeaderStatus: "状态",
        tableHeaderSource: "Source",
        tableHeaderSinks: "Sink 数",
        tableHeaderUpdated: "更新时间",
      },
      workerEditor: {
        eyebrow: "构建",
        newTitle: "新建 worker",
        subtitle: "配置 source、handler 和 sink。",
        loading: "正在加载 worker...",
        notFoundTitle: "Worker 不存在",
        notFoundBody: "请求的 worker 不存在。",
        identity: "标识",
        name: "名称",
        description: "描述",
        handlerRef: "Handler 引用",
        source: "Source",
        sourceType: "Source 类型",
        sinks: "Sink",
        sinkType: "Sink 类型",
        connector: "连接器",
        selectConnector: "选择连接器",
        addSink: "添加 sink",
        removeSink: "移除",
        save: "保存",
        deploy: "部署到 agent",
        chooseAgentPrompt: "输入要部署到的 worker agent ID：",
      },
```

- [ ] **Step 4: Update AppShell test**

In `frontend/src/components/layout/AppShell.test.tsx`, add:

```tsx
    expect(screen.getByRole("link", { name: "Workers" })).toBeInTheDocument();
```

- [ ] **Step 5: Verify typecheck + test**

Run: `cd onestep-control-plane/frontend && npx tsc --noEmit && npx vitest run src/components/layout/AppShell.test.tsx`
Expected: no type errors; test PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/router.tsx frontend/src/components/layout/AppShell.tsx frontend/src/lib/i18n.ts frontend/src/components/layout/AppShell.test.tsx
git commit -m "feat: add workers route, nav link, i18n"
```

---

## Task 12: Frontend tests

**Files:**
- Create: `frontend/src/pages/workers/WorkersListPage.test.tsx`
- Create: `frontend/src/pages/workers/WorkerEditorPage.test.tsx`

- [ ] **Step 1: Write list page test**

Create `frontend/src/pages/workers/WorkersListPage.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { WorkersListPage } from "./WorkersListPage";

const mockUseWorkersQuery = vi.fn();

vi.mock("../../features/workers/queries", () => ({
  useWorkersQuery: () => mockUseWorkersQuery(),
}));

function renderPage() {
  const queryClient = new QueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <WorkersListPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("WorkersListPage", () => {
  it("renders the header and worker rows", () => {
    mockUseWorkersQuery.mockReturnValue({
      data: {
        items: [
          {
            id: "11111111-1111-1111-1111-111111111111",
            name: "order-sync",
            description: "sync orders",
            handler_package_id: null,
            handler_ref: "handler:handler",
            source_config: { type: "interval", connector_id: null, fields: {} },
            sink_configs: [],
            status: "draft",
            created_at: "2026-06-17T00:00:00Z",
            updated_at: "2026-06-17T00:00:00Z",
          },
        ],
        total: 1,
      },
      isPending: false,
      error: null,
    });

    renderPage();

    expect(screen.getByRole("heading", { name: "Workers" })).toBeInTheDocument();
    expect(screen.getByText("order-sync")).toBeInTheDocument();
  });

  it("renders empty state when no workers", () => {
    mockUseWorkersQuery.mockReturnValue({
      data: { items: [], total: 0 },
      isPending: false,
      error: null,
    });

    renderPage();

    expect(screen.getByText("No workers yet")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Write editor page test**

Create `frontend/src/pages/workers/WorkerEditorPage.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { WorkerEditorPage } from "./WorkerEditorPage";

vi.mock("../../features/workers/queries", () => ({
  useWorkersQuery: () => ({ data: { items: [] }, isPending: false, error: null }),
  useCreateWorkerMutation: () => ({ isPending: false, mutateAsync: vi.fn() }),
  useUpdateWorkerMutation: () => ({ isPending: false, mutateAsync: vi.fn() }),
  useDeployWorkerMutation: () => ({ isPending: false, mutateAsync: vi.fn() }),
}));

vi.mock("../../features/connectors/queries", () => ({
  useConnectorsQuery: () => ({ data: { items: [] }, isPending: false, error: null }),
}));

function renderPage(route = "/workers/new") {
  const queryClient = new QueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]}>
        <WorkerEditorPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("WorkerEditorPage", () => {
  it("renders the three-section form for a new worker", () => {
    renderPage();

    expect(screen.getByText("Identity")).toBeInTheDocument();
    expect(screen.getByText("Source")).toBeInTheDocument();
    expect(screen.getByText("Sinks")).toBeInTheDocument();
    expect(screen.getByText("Add sink")).toBeInTheDocument();
  });

  it("has a handler ref field defaulting to handler:handler", () => {
    renderPage();

    const handlerInput = screen.getByDisplayValue("handler:handler");
    expect(handlerInput).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run tests**

Run: `cd onestep-control-plane/frontend && npx vitest run src/pages/workers/`
Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/workers/WorkersListPage.test.tsx frontend/src/pages/workers/WorkerEditorPage.test.tsx
git commit -m "test: workers list + editor page"
```

---

## Task 13: Full validation

- [ ] **Step 1: Backend full test suite**

Run: `cd onestep-control-plane && uv run pytest backend/tests/ -q`
Expected: all PASS (including 6 compiler + 5 worker API tests).

- [ ] **Step 2: Frontend typecheck + full test suite**

Run: `cd onestep-control-plane/frontend && npx tsc --noEmit && npx vitest run`
Expected: no type errors; all tests PASS.

- [ ] **Step 3: Frontend production build**

Run: `cd onestep-control-plane/frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Commit if anything remains**

```bash
git add -A
git commit -m "chore: worker builder — full validation pass"
```
