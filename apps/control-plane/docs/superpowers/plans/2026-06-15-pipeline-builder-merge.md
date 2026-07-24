# Pipeline Builder Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a design-time Pipeline Builder to `onestep-control-plane` so users can create, validate, and export OneStep worker definitions without running them in the control plane.

**Architecture:** Add a backend `pipeline_builder` package for graph schemas, connector descriptors, compile validation, credential encryption, and worker export. Add authenticated `/api/v1/pipelines`, `/api/v1/pipeline-credentials`, and `/api/v1/connectors` routes backed by new SQLAlchemy models and Alembic tables. Add frontend pipeline API/types/queries, navigation, list page, and editor page by adapting `onestep-web` editor components while removing local run/start/stop/log behavior.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Pydantic, pytest, React 19, React Router 7, TanStack Query 5, Vite, Vitest, `@xyflow/react`, `@monaco-editor/react`, `onestep[yaml]`, PyYAML, cryptography.

---

## File Structure

Backend files to create:

- `backend/src/onestep_control_plane_api/pipeline_builder/__init__.py`: package marker and public exports.
- `backend/src/onestep_control_plane_api/pipeline_builder/schemas.py`: graph, connector, pipeline, credential, validation, and export response schemas.
- `backend/src/onestep_control_plane_api/pipeline_builder/connectors.py`: connector catalog adapted from `onestep-web`.
- `backend/src/onestep_control_plane_api/pipeline_builder/compiler.py`: graph validation and generated handler/predicate code adapted from `onestep-web`.
- `backend/src/onestep_control_plane_api/pipeline_builder/onestep_adapter.py`: OneStep YAML config generation adapted from `onestep-web`.
- `backend/src/onestep_control_plane_api/pipeline_builder/exporter.py`: zip worker bundle generation.
- `backend/src/onestep_control_plane_api/pipeline_builder/credentials.py`: Fernet encryption, masking, and merge helpers using control-plane settings.
- `backend/src/onestep_control_plane_api/api/routers/pipelines.py`: authenticated pipeline, connector, credential, validate, and export endpoints.
- `backend/alembic/versions/202606150001_add_pipeline_builder_tables.py`: migration for `pipelines` and `pipeline_credentials`.
- `backend/tests/test_pipeline_builder_api.py`: route-level CRUD, validation, export, and credential tests.
- `backend/tests/test_pipeline_builder_compiler.py`: focused compiler/export tests.

Backend files to modify:

- `pyproject.toml`: add backend dependencies.
- `uv.lock`: refresh after dependency update.
- `backend/src/onestep_control_plane_api/core/settings.py`: add `pipeline_credentials_fernet_key`.
- `backend/src/onestep_control_plane_api/db/models.py`: add `Pipeline` and `PipelineCredential`.
- `backend/src/onestep_control_plane_api/api/routers/__init__.py`: include pipeline router.
- `backend/tests/test_migrations.py`: assert new tables and columns.
- `README.md`: document `ONESTEP_CP_PIPELINE_CREDENTIALS_FERNET_KEY`.

Frontend files to create:

- `frontend/src/features/pipelines/queries.ts`: TanStack Query hooks and mutations.
- `frontend/src/pages/pipelines-list/PipelinesListPage.tsx`: pipeline list, create, delete, status display.
- `frontend/src/pages/pipelines-list/PipelinesListPage.test.tsx`: list page tests.
- `frontend/src/pages/pipeline-editor/PipelineEditorPage.tsx`: editor shell, validation, export, persistence.
- `frontend/src/pages/pipeline-editor/PipelineEditorPage.test.tsx`: editor route tests.
- `frontend/src/features/pipelines/components/PipelineEditor.tsx`: adapted graph editor.
- `frontend/src/features/pipelines/components/NodePalette.tsx`: adapted node palette.
- `frontend/src/features/pipelines/components/PropertyPanel.tsx`: adapted property panel.
- `frontend/src/features/pipelines/components/CredentialManager.tsx`: adapted credential manager.
- `frontend/src/features/pipelines/templates.ts`: default pipeline templates.

Frontend files to modify:

- `frontend/package.json`: add `@xyflow/react` and `@monaco-editor/react`.
- `pnpm-lock.yaml`: refresh after dependency update.
- `frontend/src/lib/api/types.ts`: add pipeline types.
- `frontend/src/lib/api/client.ts`: add pipeline API functions and zip export helper.
- `frontend/src/app/router.tsx`: add `/pipelines` and `/pipelines/:pipelineId`.
- `frontend/src/components/layout/AppShell.tsx`: add `Pipelines` nav item.
- `frontend/src/lib/i18n.ts`: add pipeline UI copy.
- `frontend/src/styles.css` or `frontend/src/styles/nothing-signal-console.css`: add scoped pipeline builder layout.
- `frontend/src/components/layout/AppShell.test.tsx`: assert navigation includes `Pipelines`.

Do not modify:

- agent WebSocket protocol files.
- runtime reporter payload fields.
- service/task/instance command behavior.
- `onestep-web` repository contents.

---

### Task 1: Backend Pipeline Builder Package

**Files:**
- Create: `backend/src/onestep_control_plane_api/pipeline_builder/__init__.py`
- Create: `backend/src/onestep_control_plane_api/pipeline_builder/schemas.py`
- Create: `backend/src/onestep_control_plane_api/pipeline_builder/connectors.py`
- Create: `backend/src/onestep_control_plane_api/pipeline_builder/compiler.py`
- Create: `backend/src/onestep_control_plane_api/pipeline_builder/onestep_adapter.py`
- Create: `backend/src/onestep_control_plane_api/pipeline_builder/exporter.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Test: `backend/tests/test_pipeline_builder_compiler.py`

- [x] **Step 1: Add dependencies**

Run:

```bash
uv add 'onestep[yaml]' PyYAML cryptography
```

Expected: `pyproject.toml` contains these dependencies and `uv.lock` is updated.

If local development should use the sibling runtime checkout, also add this source block to `pyproject.toml`:

```toml
[tool.uv.sources]
onestep = { path = "../onestep", editable = true }
```

- [x] **Step 2: Write compiler/export failing tests**

Create `backend/tests/test_pipeline_builder_compiler.py` with:

```python
from __future__ import annotations

import zipfile
from io import BytesIO

import pytest
import yaml

from onestep_control_plane_api.pipeline_builder.compiler import (
    PipelineCompileError,
    PipelineCompiler,
)
from onestep_control_plane_api.pipeline_builder.exporter import WorkerExporter
from onestep_control_plane_api.pipeline_builder.schemas import (
    GraphEdge,
    GraphNode,
    PipelineGraph,
)


def minimal_graph() -> PipelineGraph:
    return PipelineGraph(
        nodes=[
            GraphNode(
                id="source",
                type="interval_source",
                kind="source",
                config={"seconds": 60, "payload": '{"ok": true}'},
                position={"x": 0, "y": 0},
            ),
            GraphNode(
                id="handler",
                type="handler",
                kind="handler",
                mode="visual",
                mapping={"ok": "{{ payload.ok }}"},
                position={"x": 240, "y": 0},
            ),
            GraphNode(
                id="sink",
                type="http_sink",
                kind="sink",
                config={"url": "https://example.invalid/hook"},
                position={"x": 480, "y": 0},
            ),
        ],
        edges=[
            GraphEdge(from_="source", to="handler"),
            GraphEdge(from_="handler", to="sink"),
        ],
    )


def test_compiler_accepts_minimal_source_handler_sink_graph() -> None:
    compiled = PipelineCompiler().compile(minimal_graph(), credentials={})

    assert compiled.order == ["source", "handler", "sink"]
    assert "handler" in compiled.generated_handlers
    assert compiled.required_credentials == []


def test_compiler_rejects_empty_graph() -> None:
    with pytest.raises(PipelineCompileError, match="at least one node"):
        PipelineCompiler().compile(PipelineGraph(), credentials={})


def test_exporter_builds_worker_zip_with_yaml_and_handlers() -> None:
    exported = WorkerExporter().export(
        "pipe_test",
        "Daily Sync",
        minimal_graph(),
        credentials={},
    )

    with zipfile.ZipFile(BytesIO(exported.content)) as archive:
        names = set(archive.namelist())
        assert "daily_sync/worker.yaml" in names
        assert "daily_sync/pyproject.toml" in names
        assert "daily_sync/src/daily_sync/handlers.py" in names
        worker_yaml = yaml.safe_load(archive.read("daily_sync/worker.yaml"))

    assert worker_yaml["apiVersion"] == "onestep/v1alpha1"
    assert worker_yaml["kind"] == "App"
    assert worker_yaml["app"]["name"] == "daily_sync"
    assert worker_yaml["tasks"][0]["name"] == "handler"
```

- [x] **Step 3: Run tests to verify they fail**

Run:

```bash
uv run pytest backend/tests/test_pipeline_builder_compiler.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'onestep_control_plane_api.pipeline_builder'`.

- [x] **Step 4: Create schemas and connector catalog**

Create `backend/src/onestep_control_plane_api/pipeline_builder/schemas.py` by adapting `../onestep-web/src/onestep_web/schemas.py` with these first-phase changes:

```python
PipelineStatus = Literal["draft", "valid", "invalid"]

class ValidationResult(APIModel):
    ok: bool
    message: str

class ExportedWorker(APIModel):
    filename: str
```

Keep `GraphNode`, `GraphEdge`, `PipelineGraph`, `ConnectorField`, and `ConnectorDescriptor` field names compatible with `onestep-web`, including `GraphEdge.from_ = Field(alias="from")`.

Create `backend/src/onestep_control_plane_api/pipeline_builder/connectors.py` by adapting `../onestep-web/src/onestep_web/connectors.py`. Update imports to:

```python
from onestep_control_plane_api.pipeline_builder.schemas import (
    ConnectorDescriptor,
    ConnectorField,
)
```

- [x] **Step 5: Create compiler**

Create `backend/src/onestep_control_plane_api/pipeline_builder/compiler.py` by adapting `../onestep-web/src/onestep_web/compiler.py`.

Update imports to:

```python
from onestep_control_plane_api.pipeline_builder.connectors import CONNECTOR_BY_TYPE
from onestep_control_plane_api.pipeline_builder.schemas import GraphEdge, GraphNode, PipelineGraph
```

Keep these public names:

```python
class PipelineCompileError(ValueError):
    pass

@dataclass(frozen=True)
class CompiledPipeline:
    graph: PipelineGraph
    order: list[str]
    generated_handlers: dict[str, str]
    generated_predicates: dict[str, str]
    required_credentials: list[str]

class PipelineCompiler:
    def compile(self, graph: PipelineGraph, credentials: dict[str, dict[str, Any]]) -> CompiledPipeline:
        self.validate_graph(graph, credentials)
        return CompiledPipeline(
            graph=graph,
            order=self.topological_order(graph),
            generated_handlers={
                node.id: self.generate_handler_code(node)
                for node in graph.nodes
                if (node.kind or self.infer_kind(node)) == "handler"
            },
            generated_predicates={
                predicate_name(edge): self.generate_predicate_code(edge)
                for edge in graph.edges
                if _edge_condition(edge)
            },
            required_credentials=sorted(
                {
                    node.credential_ref
                    for node in graph.nodes
                    if node.credential_ref is not None
                }
            ),
        )
```

- [x] **Step 6: Create OneStep adapter and exporter**

Create `backend/src/onestep_control_plane_api/pipeline_builder/onestep_adapter.py` by adapting `../onestep-web/src/onestep_web/onestep_adapter.py`.

Update imports to:

```python
from onestep_control_plane_api.pipeline_builder.compiler import (
    PipelineCompileError,
    PipelineCompiler,
    predicate_name,
)
from onestep_control_plane_api.pipeline_builder.schemas import GraphEdge, GraphNode, PipelineGraph
```

Create `backend/src/onestep_control_plane_api/pipeline_builder/exporter.py` by adapting `../onestep-web/src/onestep_web/exporter.py`.

Update imports to:

```python
from onestep_control_plane_api.pipeline_builder.compiler import PipelineCompiler
from onestep_control_plane_api.pipeline_builder.onestep_adapter import (
    build_env_example,
    build_onestep_config,
    build_requirements,
)
from onestep_control_plane_api.pipeline_builder.schemas import PipelineGraph
```

The exporter dataclass remains:

```python
@dataclass(frozen=True)
class ExportedWorker:
    filename: str
    content: bytes
```

- [x] **Step 7: Run compiler/export tests**

Run:

```bash
uv run pytest backend/tests/test_pipeline_builder_compiler.py -q
```

Expected: PASS.

- [x] **Step 8: Verify exported YAML with OneStep strict check**

Run:

```bash
tmpdir="$(mktemp -d)"
uv run python - <<'PY' "$tmpdir"
from pathlib import Path
from sys import argv
from zipfile import ZipFile

from backend.tests.test_pipeline_builder_compiler import minimal_graph
from onestep_control_plane_api.pipeline_builder.exporter import WorkerExporter

target = Path(argv[1])
exported = WorkerExporter().export("pipe_test", "Daily Sync", minimal_graph(), credentials={})
with ZipFile(__import__("io").BytesIO(exported.content)) as archive:
    archive.extractall(target)
print(target / "daily_sync" / "worker.yaml")
PY
uv run onestep check --strict "$tmpdir/daily_sync/worker.yaml"
```

Expected: PASS and no strict validation errors.

- [ ] **Step 9: Commit backend builder package**

Run:

```bash
git add pyproject.toml uv.lock backend/src/onestep_control_plane_api/pipeline_builder backend/tests/test_pipeline_builder_compiler.py
git commit -m "feat: add pipeline builder compiler"
```

Expected: commit succeeds.

---

### Task 2: Backend Persistence, Migration, and Pipeline API

**Files:**
- Modify: `backend/src/onestep_control_plane_api/db/models.py`
- Create: `backend/alembic/versions/202606150001_add_pipeline_builder_tables.py`
- Modify: `backend/src/onestep_control_plane_api/api/routers/__init__.py`
- Create: `backend/src/onestep_control_plane_api/api/routers/pipelines.py`
- Modify: `backend/tests/test_migrations.py`
- Test: `backend/tests/test_pipeline_builder_api.py`

- [x] **Step 1: Write failing API tests for pipeline CRUD and validation**

Create `backend/tests/test_pipeline_builder_api.py` with:

```python
from __future__ import annotations

from uuid import UUID


def minimal_graph_payload() -> dict[str, object]:
    return {
        "nodes": [
            {
                "id": "source",
                "type": "interval_source",
                "kind": "source",
                "config": {"seconds": 60},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "handler",
                "type": "handler",
                "kind": "handler",
                "mode": "visual",
                "mapping": {"ok": "{{ payload.ok }}"},
                "position": {"x": 240, "y": 0},
            },
            {
                "id": "sink",
                "type": "http_sink",
                "kind": "sink",
                "config": {"url": "https://example.invalid/hook"},
                "position": {"x": 480, "y": 0},
            },
        ],
        "edges": [
            {"from": "source", "to": "handler"},
            {"from": "handler", "to": "sink"},
        ],
    }


def test_pipeline_crud_and_validate(client) -> None:
    create_response = client.post(
        "/api/v1/pipelines",
        json={
            "name": "Daily Sync",
            "description": "Build from UI",
            "graph": minimal_graph_payload(),
        },
    )
    assert create_response.status_code == 200
    created = create_response.json()
    UUID(created["id"])
    assert created["status"] == "draft"
    assert created["graph"]["nodes"][0]["id"] == "source"

    list_response = client.get("/api/v1/pipelines")
    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["name"] == "Daily Sync"

    validate_response = client.post(f"/api/v1/pipelines/{created['id']}/validate")
    assert validate_response.status_code == 200
    assert validate_response.json() == {"ok": True, "message": "pipeline is valid"}

    get_response = client.get(f"/api/v1/pipelines/{created['id']}")
    assert get_response.status_code == 200
    assert get_response.json()["status"] == "valid"

    update_response = client.put(
        f"/api/v1/pipelines/{created['id']}",
        json={"name": "Daily Sync v2"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["name"] == "Daily Sync v2"
    assert update_response.json()["status"] == "draft"

    delete_response = client.delete(f"/api/v1/pipelines/{created['id']}")
    assert delete_response.status_code == 204
    assert client.get(f"/api/v1/pipelines/{created['id']}").status_code == 404


def test_validate_invalid_pipeline_updates_status(client) -> None:
    create_response = client.post(
        "/api/v1/pipelines",
        json={"name": "Broken", "description": "", "graph": {"nodes": [], "edges": []}},
    )
    assert create_response.status_code == 200
    pipeline_id = create_response.json()["id"]

    validate_response = client.post(f"/api/v1/pipelines/{pipeline_id}/validate")
    assert validate_response.status_code == 422
    assert "at least one node" in validate_response.json()["detail"]

    get_response = client.get(f"/api/v1/pipelines/{pipeline_id}")
    assert get_response.status_code == 200
    assert get_response.json()["status"] == "invalid"
```

- [x] **Step 2: Run API tests to verify they fail**

Run:

```bash
uv run pytest backend/tests/test_pipeline_builder_api.py::test_pipeline_crud_and_validate -q
```

Expected: FAIL with route not found or model import errors.

- [x] **Step 3: Add database models**

Modify `backend/src/onestep_control_plane_api/db/models.py` to add:

```python
class Pipeline(Base):
    __tablename__ = "pipelines"
    __table_args__ = (
        sa.Index("ix_pipelines_updated_at", "updated_at"),
        sa.Index("ix_pipelines_status_updated_at", "status", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    description: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    graph_json: Mapped[dict[str, object]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )
```

Do not relate `Pipeline` to `Service` in this phase.

- [x] **Step 4: Add Alembic migration**

Create `backend/alembic/versions/202606150001_add_pipeline_builder_tables.py` with:

```python
"""Add pipeline builder tables.

Revision ID: 202606150001
Revises: 202605270001
Create Date: 2026-06-15 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202606150001"
down_revision: str | None = "202605270001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if not _has_table("pipelines"):
        op.create_table(
            "pipelines",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("graph_json", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_pipelines")),
        )
        op.create_index("ix_pipelines_updated_at", "pipelines", ["updated_at"])
        op.create_index("ix_pipelines_status_updated_at", "pipelines", ["status", "updated_at"])


def downgrade() -> None:
    if _has_table("pipelines"):
        op.drop_index("ix_pipelines_status_updated_at", table_name="pipelines")
        op.drop_index("ix_pipelines_updated_at", table_name="pipelines")
        op.drop_table("pipelines")
```

- [x] **Step 5: Add pipeline router**

Create `backend/src/onestep_control_plane_api/api/routers/pipelines.py`.

Core route signatures:

```python
router = APIRouter(
    prefix="/api/v1",
    tags=["pipelines"],
    dependencies=[Depends(require_console_auth)],
)

@router.get("/connectors", response_model=ConnectorList)
def list_connectors() -> ConnectorList:
    return ConnectorList(items=CONNECTORS)

@router.get("/pipelines", response_model=PipelineList)
def list_pipelines(db: Session = Depends(get_db_session)) -> PipelineList:
    rows = db.scalars(select(Pipeline).order_by(Pipeline.updated_at.desc())).all()
    return PipelineList(items=[_pipeline_read(row) for row in rows])

@router.post("/pipelines", response_model=PipelineRead)
def create_pipeline(request: PipelineCreate, db: Session = Depends(get_db_session)) -> PipelineRead:
    pipeline = Pipeline(
        name=request.name,
        description=request.description,
        graph_json=request.graph.model_dump(by_alias=True),
        status="draft",
    )
    db.add(pipeline)
    db.commit()
    db.refresh(pipeline)
    return _pipeline_read(pipeline)

@router.post("/pipelines/{pipeline_id}/validate", response_model=ValidationResult)
def validate_pipeline(pipeline_id: UUID, db: Session = Depends(get_db_session)) -> ValidationResult:
    pipeline = _get_pipeline_or_404(db, pipeline_id)
    graph = PipelineGraph.model_validate(pipeline.graph_json)
    try:
        PipelineCompiler().compile(graph, credentials=_credential_map(db))
    except PipelineCompileError as exc:
        pipeline.status = "invalid"
        db.commit()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    pipeline.status = "valid"
    db.commit()
    return ValidationResult(ok=True, message="pipeline is valid")
```

Implementation rules:

- Use `PipelineGraph.model_validate(pipeline.graph_json)` before compile/export.
- On create, set `status="draft"`.
- On update of `name`, `description`, or `graph`, set `status="draft"`.
- On validate success, set `status="valid"` and return `{"ok": True, "message": "pipeline is valid"}`.
- On `PipelineCompileError`, set `status="invalid"`, commit, and raise `HTTPException(status_code=422, detail=str(exc))`.

Add router include in `backend/src/onestep_control_plane_api/api/routers/__init__.py`:

```python
from onestep_control_plane_api.api.routers.pipelines import router as pipelines_router

router.include_router(pipelines_router)
```

- [x] **Step 6: Update migration test**

Modify `backend/tests/test_migrations.py`:

```python
HEAD_REVISION = "202606150001"
```

Add `"pipelines"` to the expected table set and assert columns:

```python
assert {column["name"] for column in inspector.get_columns("pipelines")} == {
    "id",
    "name",
    "description",
    "graph_json",
    "status",
    "created_at",
    "updated_at",
}
pipeline_columns = {column["name"]: column for column in inspector.get_columns("pipelines")}
assert isinstance(pipeline_columns["graph_json"]["type"], sa.JSON)
```

- [x] **Step 7: Run focused backend tests**

Run:

```bash
uv run pytest backend/tests/test_pipeline_builder_api.py backend/tests/test_migrations.py -q
```

Expected: PASS.

- [x] **Step 8: Commit persistence and API**

Run:

```bash
git add backend/src/onestep_control_plane_api/db/models.py backend/src/onestep_control_plane_api/api/routers/__init__.py backend/src/onestep_control_plane_api/api/routers/pipelines.py backend/alembic/versions/202606150001_add_pipeline_builder_tables.py backend/tests/test_pipeline_builder_api.py backend/tests/test_migrations.py
git commit -m "feat: add pipeline builder api"
```

Expected: commit succeeds.

---

### Task 3: Backend Credentials and Export API

**Files:**
- Modify: `backend/src/onestep_control_plane_api/core/settings.py`
- Modify: `backend/src/onestep_control_plane_api/db/models.py`
- Modify: `backend/alembic/versions/202606150001_add_pipeline_builder_tables.py`
- Create: `backend/src/onestep_control_plane_api/pipeline_builder/credentials.py`
- Modify: `backend/src/onestep_control_plane_api/api/routers/pipelines.py`
- Modify: `backend/tests/test_pipeline_builder_api.py`
- Modify: `README.md`

- [x] **Step 1: Add failing credential and export tests**

Append to `backend/tests/test_pipeline_builder_api.py`:

```python
def test_pipeline_credentials_mask_and_preserve_secret_values(client) -> None:
    create_response = client.post(
        "/api/v1/pipeline-credentials",
        json={
            "name": "demo mysql",
            "connector_type": "mysql",
            "config": {"host": "localhost"},
            "env_vars": {"MYSQL_PASSWORD": "secret"},
        },
    )
    assert create_response.status_code == 200
    created = create_response.json()
    assert created["env_vars"] == {"MYSQL_PASSWORD": "********"}

    update_response = client.put(
        f"/api/v1/pipeline-credentials/{created['id']}",
        json={"env_vars": {"MYSQL_PASSWORD": "********", "MYSQL_USER": "root"}},
    )
    assert update_response.status_code == 200
    assert update_response.json()["env_vars"] == {
        "MYSQL_PASSWORD": "********",
        "MYSQL_USER": "********",
    }


def test_export_pipeline_returns_worker_zip(client) -> None:
    create_response = client.post(
        "/api/v1/pipelines",
        json={
            "name": "Daily Sync",
            "description": "",
            "graph": minimal_graph_payload(),
        },
    )
    pipeline_id = create_response.json()["id"]

    export_response = client.post(f"/api/v1/pipelines/{pipeline_id}/export")

    assert export_response.status_code == 200
    assert export_response.headers["content-type"] == "application/zip"
    assert export_response.headers["content-disposition"].startswith(
        'attachment; filename="'
    )
    assert export_response.content.startswith(b"PK")
```

- [x] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest backend/tests/test_pipeline_builder_api.py::test_pipeline_credentials_mask_and_preserve_secret_values backend/tests/test_pipeline_builder_api.py::test_export_pipeline_returns_worker_zip -q
```

Expected: FAIL because credential endpoints and export route are incomplete.

- [x] **Step 3: Add settings**

Modify `backend/src/onestep_control_plane_api/core/settings.py`:

```python
pipeline_credentials_fernet_key: str = ""
```

Add a validator:

```python
@field_validator("pipeline_credentials_fernet_key")
@classmethod
def validate_pipeline_credentials_fernet_key(cls, value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return ""
    from cryptography.fernet import Fernet

    Fernet(stripped.encode("ascii"))
    return stripped
```

- [x] **Step 4: Add credential model and migration columns**

Add to `backend/src/onestep_control_plane_api/db/models.py`:

```python
class PipelineCredential(Base):
    __tablename__ = "pipeline_credentials"
    __table_args__ = (
        sa.UniqueConstraint("name", name="uq_pipeline_credentials_name"),
        sa.Index("ix_pipeline_credentials_connector_type_name", "connector_type", "name"),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    connector_type: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    config_encrypted: Mapped[str] = mapped_column(sa.Text, nullable=False)
    env_vars_encrypted: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )
```

Modify `backend/alembic/versions/202606150001_add_pipeline_builder_tables.py` to create and drop `pipeline_credentials` in the same revision. The table must include `config_encrypted` and `env_vars_encrypted` text columns.

- [x] **Step 5: Add credential helpers**

Create `backend/src/onestep_control_plane_api/pipeline_builder/credentials.py` adapted from `onestep-web`, but do not write a local key file.

Use:

```python
def load_cipher() -> CredentialCipher:
    if settings.pipeline_credentials_fernet_key:
        return CredentialCipher(settings.pipeline_credentials_fernet_key)
    return CredentialCipher()
```

This default lets tests and local dev work. Document that production deployments must set `ONESTEP_CP_PIPELINE_CREDENTIALS_FERNET_KEY` to keep encrypted credentials decryptable across restarts and replicas.

- [x] **Step 6: Add credential and export routes**

Extend `backend/src/onestep_control_plane_api/api/routers/pipelines.py` with:

```python
@router.get("/pipeline-credentials", response_model=CredentialList)
def list_pipeline_credentials(db: Session = Depends(get_db_session)) -> CredentialList:
    cipher = load_cipher()
    rows = db.scalars(select(PipelineCredential).order_by(PipelineCredential.name.asc())).all()
    return CredentialList(items=[_credential_read(row, cipher, masked=True) for row in rows])

@router.post("/pipeline-credentials", response_model=CredentialRead)
def create_pipeline_credential(
    request: CredentialCreate,
    db: Session = Depends(get_db_session),
) -> CredentialRead:
    cipher = load_cipher()
    credential = PipelineCredential(
        name=request.name,
        connector_type=request.connector_type,
        config_encrypted=cipher.encrypt_json(request.config),
        env_vars_encrypted=cipher.encrypt_json(request.env_vars),
    )
    db.add(credential)
    db.commit()
    db.refresh(credential)
    return _credential_read(credential, cipher, masked=True)

@router.put("/pipeline-credentials/{credential_id}", response_model=CredentialRead)
def update_pipeline_credential(
    credential_id: UUID,
    request: CredentialUpdate,
    db: Session = Depends(get_db_session),
) -> CredentialRead:
    cipher = load_cipher()
    credential = _get_credential_or_404(db, credential_id)
    if request.name is not None:
        credential.name = request.name
    if request.connector_type is not None:
        credential.connector_type = request.connector_type
    if request.config is not None:
        credential.config_encrypted = cipher.encrypt_json(request.config)
    if request.env_vars is not None:
        existing_env_vars = cipher.decrypt_json(credential.env_vars_encrypted)
        credential.env_vars_encrypted = cipher.encrypt_json(
            merge_masked_env_vars(request.env_vars, existing_env_vars)
        )
    db.commit()
    db.refresh(credential)
    return _credential_read(credential, cipher, masked=True)

@router.delete("/pipeline-credentials/{credential_id}", status_code=204)
def delete_pipeline_credential(credential_id: UUID, db: Session = Depends(get_db_session)) -> Response:
    credential = _get_credential_or_404(db, credential_id)
    db.delete(credential)
    db.commit()
    return Response(status_code=204)

@router.post("/pipelines/{pipeline_id}/export")
def export_pipeline(pipeline_id: UUID, db: Session = Depends(get_db_session)) -> Response:
    pipeline = _get_pipeline_or_404(db, pipeline_id)
    graph = PipelineGraph.model_validate(pipeline.graph_json)
    try:
        exported = WorkerExporter().export(
            str(pipeline.id),
            pipeline.name,
            graph,
            credentials=_credential_map(db),
        )
    except PipelineCompileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(
        content=exported.content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{exported.filename}"'},
    )
```

Build credential maps by decrypting saved credentials and keying by credential id as a string:

```python
def _credential_map(db: Session) -> dict[str, dict[str, object]]:
    cipher = load_cipher()
    credentials = db.scalars(select(PipelineCredential)).all()
    return {
        str(credential.id): {
            **cipher.decrypt_json(credential.config_encrypted),
            "env": cipher.decrypt_json(credential.env_vars_encrypted),
        }
        for credential in credentials
    }
```

- [x] **Step 7: Update README**

Add an environment variable row:

```markdown
| `ONESTEP_CP_PIPELINE_CREDENTIALS_FERNET_KEY` | Backend pipeline builder | empty | Fernet key used to encrypt Pipeline Builder credentials. Required for production persistence across restarts and replicas. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. |
```

- [x] **Step 8: Run focused tests**

Run:

```bash
uv run pytest backend/tests/test_pipeline_builder_api.py backend/tests/test_migrations.py -q
```

Expected: PASS.

- [x] **Step 9: Commit credentials and export**

Run:

```bash
git add README.md backend/src/onestep_control_plane_api/core/settings.py backend/src/onestep_control_plane_api/db/models.py backend/alembic/versions/202606150001_add_pipeline_builder_tables.py backend/src/onestep_control_plane_api/pipeline_builder/credentials.py backend/src/onestep_control_plane_api/api/routers/pipelines.py backend/tests/test_pipeline_builder_api.py backend/tests/test_migrations.py
git commit -m "feat: add pipeline credentials and export"
```

Expected: commit succeeds.

---

### Task 4: Frontend API, Navigation, and List Page

**Files:**
- Modify: `frontend/package.json`
- Modify: `pnpm-lock.yaml`
- Modify: `frontend/src/lib/api/types.ts`
- Modify: `frontend/src/lib/api/client.ts`
- Create: `frontend/src/features/pipelines/queries.ts`
- Modify: `frontend/src/app/router.tsx`
- Modify: `frontend/src/components/layout/AppShell.tsx`
- Modify: `frontend/src/components/layout/AppShell.test.tsx`
- Modify: `frontend/src/lib/i18n.ts`
- Create: `frontend/src/pages/pipelines-list/PipelinesListPage.tsx`
- Create: `frontend/src/pages/pipelines-list/PipelinesListPage.test.tsx`

- [x] **Step 1: Add frontend dependencies**

Run:

```bash
pnpm --dir frontend add @xyflow/react @monaco-editor/react
```

Expected: `frontend/package.json` and `pnpm-lock.yaml` are updated.

- [x] **Step 2: Write failing navigation and list tests**

Create `frontend/src/pages/pipelines-list/PipelinesListPage.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { PipelinesListPage } from "./PipelinesListPage";

const mockUsePipelinesQuery = vi.fn();

vi.mock("../../features/pipelines/queries", () => ({
  useCreatePipelineMutation: () => ({ isPending: false, mutateAsync: vi.fn() }),
  useDeletePipelineMutation: () => ({ isPending: false, mutateAsync: vi.fn() }),
  usePipelinesQuery: (...args: unknown[]) => mockUsePipelinesQuery(...args),
}));

function renderPage() {
  const queryClient = new QueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <PipelinesListPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("PipelinesListPage", () => {
  it("renders pipeline rows and create action", () => {
    mockUsePipelinesQuery.mockReturnValue({
      data: {
        items: [
          {
            id: "11111111-1111-1111-1111-111111111111",
            name: "Daily Sync",
            description: "Build from UI",
            graph: { nodes: [], edges: [] },
            status: "valid",
            created_at: "2026-06-15T00:00:00Z",
            updated_at: "2026-06-15T00:00:00Z",
          },
        ],
      },
      isPending: false,
      error: null,
    });

    renderPage();

    expect(screen.getByRole("heading", { name: "Pipelines" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create pipeline" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Daily Sync/ })).toBeInTheDocument();
    expect(screen.getByText("valid")).toBeInTheDocument();
  });
});
```

Update `frontend/src/components/layout/AppShell.test.tsx` to assert:

```tsx
expect(screen.getByRole("link", { name: "Pipelines" })).toBeInTheDocument();
```

- [x] **Step 3: Run frontend tests to verify failure**

Run:

```bash
pnpm --dir frontend test -- PipelinesListPage AppShell
```

Expected: FAIL because pipeline components and nav copy are missing.

- [x] **Step 4: Add frontend types**

Append to `frontend/src/lib/api/types.ts`:

```ts
export type PipelineStatus = "draft" | "valid" | "invalid";
export type PipelineNodeKind = "source" | "handler" | "sink";
export type PipelineHandlerMode = "visual" | "code";

export interface PipelineGraphNode {
  id: string;
  type: string;
  kind: PipelineNodeKind;
  credential_ref?: string | null;
  config: JsonObject;
  mode?: PipelineHandlerMode | null;
  mapping: Record<string, string>;
  code?: string | null;
  input_schema: JsonObject;
  position: { x: number; y: number };
}

export interface PipelineGraphEdge {
  from: string;
  to: string;
  condition?: string | null;
}

export interface PipelineGraph {
  nodes: PipelineGraphNode[];
  edges: PipelineGraphEdge[];
}

export interface Pipeline {
  id: string;
  name: string;
  description: string;
  graph: PipelineGraph;
  status: PipelineStatus;
  created_at: string;
  updated_at: string;
}

export interface PipelineListResponse {
  items: Pipeline[];
}

export interface PipelineValidationResult {
  ok: boolean;
  message: string;
}

export interface PipelineConnectorField {
  name: string;
  label: string;
  type: string;
  required: boolean;
}

export interface PipelineConnectorDescriptor {
  type: string;
  label: string;
  category: PipelineNodeKind;
  description: string;
  credential_type?: string | null;
  fields: PipelineConnectorField[];
}

export interface PipelineCredential {
  id: string;
  name: string;
  connector_type: string;
  config: JsonObject;
  env_vars: Record<string, string>;
  created_at: string;
  updated_at: string;
}
```

- [x] **Step 5: Add client functions and queries**

Add to `frontend/src/lib/api/client.ts`:

```ts
export function listPipelines() {
  return request<PipelineListResponse>("/api/v1/pipelines");
}

export function createPipeline(payload: {
  name: string;
  description: string;
  graph: PipelineGraph;
}) {
  return request<Pipeline>("/api/v1/pipelines", { method: "POST", body: payload });
}

export function updatePipeline(
  pipelineId: string,
  payload: Partial<Pick<Pipeline, "name" | "description" | "graph">>,
) {
  return request<Pipeline>(`/api/v1/pipelines/${encodeURIComponent(pipelineId)}`, {
    method: "PUT",
    body: payload,
  });
}

export function deletePipeline(pipelineId: string) {
  return request<void>(`/api/v1/pipelines/${encodeURIComponent(pipelineId)}`, {
    method: "DELETE",
  });
}

export function validatePipeline(pipelineId: string) {
  return request<PipelineValidationResult>(
    `/api/v1/pipelines/${encodeURIComponent(pipelineId)}/validate`,
    { method: "POST" },
  );
}
```

Create `frontend/src/features/pipelines/queries.ts` with TanStack hooks for list/create/update/delete/validate. Use query keys:

```ts
["pipelines"]
["pipeline", pipelineId]
["pipeline-connectors"]
["pipeline-credentials"]
```

- [x] **Step 6: Add routes, nav, and list page**

Modify `frontend/src/app/router.tsx` to import and route:

```tsx
import { PipelinesListPage } from "../pages/pipelines-list/PipelinesListPage";

{
  path: "pipelines",
  element: <PipelinesListPage />,
}
```

Modify `frontend/src/components/layout/AppShell.tsx` to add nav link:

```tsx
<NavLink
  className={({ isActive }) => (isActive ? "shell-nav-link active" : "shell-nav-link")}
  to="/pipelines"
>
  {t("app.pipelinesNav")}
</NavLink>
```

Add `app.pipelinesNav: "Pipelines"` to English and Chinese resource blocks in `frontend/src/lib/i18n.ts`.

Create `frontend/src/pages/pipelines-list/PipelinesListPage.tsx` with:

```tsx
export function PipelinesListPage() {
  const pipelinesQuery = usePipelinesQuery();
  const createMutation = useCreatePipelineMutation();
  const deleteMutation = useDeletePipelineMutation();
  const navigate = useNavigate();
  const pipelines = pipelinesQuery.data?.items ?? [];

  async function handleCreate() {
    const pipeline = await createMutation.mutateAsync({
      name: "Untitled pipeline",
      description: "",
      graph: { nodes: [], edges: [] },
    });
    navigate(`/pipelines/${pipeline.id}`);
  }

  return (
    <div className="ref-console-page pipeline-builder-list-page">
      <SignalConsoleHeader
        kicker="Builder"
        title="Pipelines"
        description={<p className="signal-console-hero-note">Create and export OneStep worker definitions.</p>}
        side={<button type="button" onClick={() => void handleCreate()}>Create pipeline</button>}
      />
      {pipelinesQuery.error ? <EmptyState title="Unable to load pipelines" body={String(pipelinesQuery.error)} /> : null}
      {pipelinesQuery.isPending ? <div className="loading-block">Loading pipelines...</div> : null}
      {!pipelinesQuery.isPending && !pipelinesQuery.error && pipelines.length === 0 ? (
        <EmptyState title="No pipelines yet" body="Create a pipeline to start building a worker definition." />
      ) : null}
      {pipelines.length > 0 ? (
        <section className="ref-table-card">
          <div className="ref-table-body">
            {pipelines.map((pipeline) => (
              <article className="ref-table-row" key={pipeline.id}>
                <Link to={`/pipelines/${pipeline.id}`}>{pipeline.name}</Link>
                <span>{pipeline.status}</span>
                <button type="button" onClick={() => void deleteMutation.mutateAsync(pipeline.id)}>
                  Delete
                </button>
              </article>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
```

- [x] **Step 7: Run frontend focused tests**

Run:

```bash
pnpm --dir frontend test -- PipelinesListPage AppShell
```

Expected: PASS.

- [x] **Step 8: Commit frontend shell**

Run:

```bash
git add frontend/package.json pnpm-lock.yaml frontend/src/lib/api/types.ts frontend/src/lib/api/client.ts frontend/src/features/pipelines/queries.ts frontend/src/app/router.tsx frontend/src/components/layout/AppShell.tsx frontend/src/components/layout/AppShell.test.tsx frontend/src/lib/i18n.ts frontend/src/pages/pipelines-list
git commit -m "feat: add pipeline builder navigation"
```

Expected: commit succeeds.

---

### Task 5: Frontend Editor Integration and Final Validation

**Files:**
- Create: `frontend/src/pages/pipeline-editor/PipelineEditorPage.tsx`
- Create: `frontend/src/pages/pipeline-editor/PipelineEditorPage.test.tsx`
- Create: `frontend/src/features/pipelines/components/PipelineEditor.tsx`
- Create: `frontend/src/features/pipelines/components/NodePalette.tsx`
- Create: `frontend/src/features/pipelines/components/PropertyPanel.tsx`
- Create: `frontend/src/features/pipelines/components/CredentialManager.tsx`
- Create: `frontend/src/features/pipelines/templates.ts`
- Modify: `frontend/src/features/pipelines/queries.ts`
- Modify: `frontend/src/lib/api/client.ts`
- Modify: `frontend/src/app/router.tsx`
- Modify: `frontend/src/styles.css` or `frontend/src/styles/nothing-signal-console.css`
- Modify: `README.md`

- [x] **Step 1: Write failing editor test**

Create `frontend/src/pages/pipeline-editor/PipelineEditorPage.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { PipelineEditorPage } from "./PipelineEditorPage";

vi.mock("../../features/pipelines/queries", () => ({
  usePipelineConnectorsQuery: () => ({
    data: { items: [{ type: "handler", label: "Python Handler", category: "handler", description: "", fields: [] }] },
    isPending: false,
    error: null,
  }),
  usePipelineCredentialsQuery: () => ({ data: { items: [] }, isPending: false, error: null }),
  usePipelineQuery: () => ({
    data: {
      id: "11111111-1111-1111-1111-111111111111",
      name: "Daily Sync",
      description: "",
      graph: { nodes: [], edges: [] },
      status: "draft",
      created_at: "2026-06-15T00:00:00Z",
      updated_at: "2026-06-15T00:00:00Z",
    },
    isPending: false,
    error: null,
  }),
  useUpdatePipelineMutation: () => ({ isPending: false, mutateAsync: vi.fn() }),
  useValidatePipelineMutation: () => ({ isPending: false, mutateAsync: vi.fn() }),
}));

vi.mock("../../features/pipelines/components/PipelineEditor", () => ({
  PipelineEditor: () => <div data-testid="pipeline-editor-canvas">canvas</div>,
}));

describe("PipelineEditorPage", () => {
  it("renders builder controls without run or deploy actions", () => {
    const queryClient = new QueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/pipelines/11111111-1111-1111-1111-111111111111"]}>
          <Routes>
            <Route path="/pipelines/:pipelineId" element={<PipelineEditorPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.getByRole("heading", { name: "Daily Sync" })).toBeInTheDocument();
    expect(screen.getByTestId("pipeline-editor-canvas")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Validate" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Start|Stop|Run|Deploy/ })).not.toBeInTheDocument();
  });
});
```

- [x] **Step 2: Run editor test to verify failure**

Run:

```bash
pnpm --dir frontend test -- PipelineEditorPage
```

Expected: FAIL because `PipelineEditorPage` is missing.

- [x] **Step 3: Extend API client for editor needs**

Add to `frontend/src/lib/api/client.ts`:

```ts
export function getPipeline(pipelineId: string) {
  return request<Pipeline>(`/api/v1/pipelines/${encodeURIComponent(pipelineId)}`);
}

export function listPipelineConnectors() {
  return request<{ items: PipelineConnectorDescriptor[] }>("/api/v1/connectors");
}

export function listPipelineCredentials() {
  return request<{ items: PipelineCredential[] }>("/api/v1/pipeline-credentials");
}

export async function exportPipeline(pipelineId: string) {
  const response = await fetch(buildApiUrl(`/api/v1/pipelines/${encodeURIComponent(pipelineId)}/export`), {
    credentials: "include",
    method: "POST",
  });
  if (!response.ok) {
    throw new ApiError(response.statusText, response.status);
  }
  return response.blob();
}
```

Add matching query hooks to `frontend/src/features/pipelines/queries.ts`.

- [x] **Step 4: Copy and adapt editor components**

Copy these files from `../onestep-web/frontend/src/` into `frontend/src/features/pipelines/components/`:

```bash
cp ../onestep-web/frontend/src/PipelineEditor.tsx frontend/src/features/pipelines/components/PipelineEditor.tsx
cp ../onestep-web/frontend/src/NodePalette.tsx frontend/src/features/pipelines/components/NodePalette.tsx
cp ../onestep-web/frontend/src/PropertyPanel.tsx frontend/src/features/pipelines/components/PropertyPanel.tsx
cp ../onestep-web/frontend/src/CredentialManager.tsx frontend/src/features/pipelines/components/CredentialManager.tsx
cp ../onestep-web/frontend/src/templates.ts frontend/src/features/pipelines/templates.ts
```

Then update imports from `./types` to:

```ts
import type {
  PipelineConnectorDescriptor as ConnectorDescriptor,
  PipelineCredential as Credential,
  PipelineGraph,
  PipelineGraphEdge as GraphEdge,
  PipelineGraphNode as GraphNode,
} from "../../../lib/api/types";
```

Remove or disable imports and UI calls for debug/test-connection/fetch-sample if the component references `api.testConnection`, `api.fetchSample`, `DebugResult`, `RuntimeStatus`, or log sockets. First phase supports edit, validate, and export only.

- [x] **Step 5: Add editor page and route**

Create `frontend/src/pages/pipeline-editor/PipelineEditorPage.tsx`:

```tsx
export function PipelineEditorPage() {
  const { pipelineId } = useParams<{ pipelineId: string }>();
  const { pushToast } = useToast();
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const pipelineQuery = usePipelineQuery(pipelineId ?? "");
  const connectorsQuery = usePipelineConnectorsQuery();
  const credentialsQuery = usePipelineCredentialsQuery();
  const updateMutation = useUpdatePipelineMutation(pipelineId ?? "");
  const validateMutation = useValidatePipelineMutation(pipelineId ?? "");

  if (!pipelineId) {
    return <EmptyState title="Missing pipeline" body="The route needs a pipeline id." />;
  }
  if (pipelineQuery.isPending || connectorsQuery.isPending || credentialsQuery.isPending) {
    return <div className="loading-block">Loading pipeline...</div>;
  }
  if (pipelineQuery.error) {
    return <EmptyState title="Unable to load pipeline" body={String(pipelineQuery.error)} />;
  }

  const pipeline = pipelineQuery.data;
  if (!pipeline) {
    return <EmptyState title="Pipeline not found" body="The requested pipeline does not exist." />;
  }

  async function handleGraphChange(graph: PipelineGraph) {
    await updateMutation.mutateAsync({ graph });
  }

  async function handleValidate() {
    const result = await validateMutation.mutateAsync();
    pushToast({ tone: "success", message: result.message });
  }

  async function handleExport() {
    const blob = await exportPipeline(pipeline.id);
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${pipeline.id}.zip`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="ref-console-page pipeline-builder-editor-page">
      <SignalConsoleHeader
        kicker="Pipeline Builder"
        title={pipeline.name}
        description={<p className="signal-console-hero-note">Status {pipeline.status}</p>}
        side={
          <div className="ref-page-actions">
            <button type="button" onClick={() => void handleValidate()}>Validate</button>
            <button type="button" onClick={() => void handleExport()}>Export</button>
          </div>
        }
      />
      <PipelineEditor
        connectors={connectorsQuery.data?.items ?? []}
        credentials={credentialsQuery.data?.items ?? []}
        graph={pipeline.graph}
        onGraphChange={(graph) => void handleGraphChange(graph)}
        onSelectedNodeChange={setSelectedNodeId}
        selectedNodeId={selectedNodeId}
      />
    </div>
  );
}
```

Modify `frontend/src/app/router.tsx`:

```tsx
import { PipelineEditorPage } from "../pages/pipeline-editor/PipelineEditorPage";

{
  path: "pipelines/:pipelineId",
  element: <PipelineEditorPage />,
}
```

- [x] **Step 6: Add scoped styles**

Add scoped classes to the active control-plane stylesheet:

```css
.pipeline-builder-editor-page {
  min-height: calc(100vh - 120px);
}

.pipeline-builder-editor-page .pipeline-editor-shell {
  display: grid;
  grid-template-columns: minmax(12rem, 16rem) minmax(0, 1fr) minmax(18rem, 24rem);
  gap: 1rem;
  min-height: 42rem;
}

.pipeline-builder-editor-page .react-flow {
  min-height: 42rem;
  border: 1px solid var(--ref-border-subtle);
  background: var(--ref-surface);
}
```

Keep styles scoped to pipeline builder classes so existing service and command pages do not shift.

- [x] **Step 7: Run frontend tests and build**

Run:

```bash
pnpm --dir frontend test -- PipelineEditorPage PipelinesListPage AppShell
pnpm --dir frontend build
```

Expected: PASS.

- [x] **Step 8: Run backend tests**

Run:

```bash
uv run pytest backend/tests/test_pipeline_builder_api.py backend/tests/test_pipeline_builder_compiler.py backend/tests/test_migrations.py -q
```

Expected: PASS.

- [x] **Step 9: Run full validation**

Run:

```bash
uv run pytest -q
pnpm --dir frontend build
```

Expected: PASS.

- [x] **Step 10: Commit editor integration**

Run:

```bash
git add frontend/src/pages/pipeline-editor frontend/src/features/pipelines frontend/src/lib/api/client.ts frontend/src/app/router.tsx frontend/src/styles.css frontend/src/styles/nothing-signal-console.css README.md
git commit -m "feat: add pipeline builder editor"
```

Expected: commit succeeds.

---

## Final Acceptance

- [ ] `uv run pytest -q` passes.
- [ ] A representative exported `worker.yaml` passes `uv run onestep check --strict`.
- [ ] `pnpm --dir frontend test -- PipelinesListPage PipelineEditorPage AppShell` passes.
- [ ] `pnpm --dir frontend build` passes.
- [ ] Manual API check can create, validate, and export a pipeline.
- [ ] The UI has `Pipelines`, `Validate`, and `Export`; it has no `Start`, `Stop`, `Run`, or `Deploy` action.
- [ ] No files under agent WebSocket protocol or runtime reporter payload handling changed.
- [ ] The last commit message summarizes the feature and the working tree is clean.
