# Service Description Field Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a service-level description field that can be configured by workers, reported through the control-plane protocol, persisted by the plane, returned by APIs, and displayed in the UI.

**Architecture:** Treat this as an additive protocol field. Runtime config uses `service_description`; wire payloads carry `service.description`; the control plane stores it on `services.description`; API/UI models expose it as `description`. Ship the control-plane receiver before runtime reporters because current plane schemas reject unknown fields.

**Tech Stack:** Python 3.11, Pydantic v2, SQLAlchemy/Alembic, FastAPI, onestep reporter plugin, React/TypeScript/Vite.

---

## Scope

In scope:
- Runtime reporter config: YAML, Python constructor, environment variable.
- WebSocket hello and telemetry service descriptors.
- Control-plane schema, ingestion, storage, query API, and UI display.
- Worker Builder compilation so existing `Worker.description` becomes runtime service description.
- Focused tests and docs.

Not in scope:
- Editable service descriptions directly in the Service Directory UI. Runtime config remains the source of truth.
- Worker-agent protocol changes. The worker agent only downloads packages and runs `worker.yaml`.
- Renaming existing task-level `description`.
- Backfilling descriptions for old services unless a worker reports one.

## Field Contract

Use these names consistently:

```text
runtime YAML/Python config: reporter.service_description
runtime env var:           ONESTEP_SERVICE_DESCRIPTION
wire payload:              service.description
control-plane DB/API/UI:   description
```

Null and missing semantics:
- Missing `service.description` means "do not change existing DB value".
- Explicit `null` or blank string means "clear description".
- Non-empty strings are stripped before storage.

Data flow:

```text
Worker Builder / worker.yaml / Python app
        |
        v
ControlPlaneReporterConfig.service_description
        |
        v
service descriptor in hello/sync/heartbeat
        |
        v
ServiceDescriptor.description
        |
        v
services.description
        |
        v
ServiceSummary.description
        |
        v
frontend Service.description
        |
        v
Service Directory + Service Detail header
```

## File Structure

Runtime repo: `/Users/miclon/development/onestep/onestep`

- Modify `plugins/onestep-control-plane/src/onestep_control_plane/reporter.py`
  - Add `service_description` config, env parsing, service descriptor output, reporter summary.
- Modify `plugins/onestep-control-plane/src/onestep_control_plane/__init__.py`
  - Add `service_description` to allowed YAML reporter fields and build context.
- Modify `src/onestep/cli.py`
  - Include service description in `onestep check` reporter summary when present.
- Modify `tests/test_cli.py`
  - Update fake reporter allowed fields and add YAML strict/report summary coverage.
- Modify `plugins/onestep-control-plane/tests/test_control_plane_reporter.py`
  - Cover env parsing and sync/heartbeat payloads.
- Modify `plugins/onestep-control-plane/tests/test_control_plane_ws.py`
  - Cover WS hello carrying the new service descriptor field.
- Modify docs: `README.md`, `README.zh-CN.md`, `docs/yaml-task-definition.md`, `docs/control-plane/index.md`, `CHANGELOG.md`.

Control-plane repo: `/Users/miclon/development/onestep/onestep-control-plane`

- Modify `backend/src/onestep_control_plane_api/db/models.py`
  - Add `Service.description`.
- Create `backend/alembic/versions/202607200001_add_service_description_to_services.py`
  - Add nullable `services.description`.
- Modify `backend/tests/test_migrations.py`
  - Update `HEAD_REVISION`, expected `services` columns, and legacy reconciliation coverage.
- Modify `backend/src/onestep_control_plane_api/api/schemas.py`
  - Add and normalize `ServiceDescriptor.description`; expose `ServiceSummary.description`.
- Modify `backend/src/onestep_control_plane_api/api/common.py`
  - Add helper to apply service metadata only when the field was present.
- Modify `backend/src/onestep_control_plane_api/api/agent_ingestion_service.py`
  - Persist description from newer sync/heartbeat payloads.
- Modify `backend/src/onestep_control_plane_api/api/routers/agent_ws.py`
  - Persist description from hello payloads.
- Modify `backend/src/onestep_control_plane_api/api/query_support.py`
  - Return description in all service summaries.
- Modify `backend/src/onestep_control_plane_api/api/worker_compiler.py`
  - Emit `reporter.service_description` when worker description is non-empty.
- Modify `backend/src/onestep_control_plane_api/api/worker_service.py`
  - Pass `worker.description` into `compile_worker_yaml`.
- Modify backend tests:
  - `backend/tests/test_agent_ingestion.py`
  - `backend/tests/test_agent_ws.py`
  - `backend/tests/test_query_api.py`
  - `backend/tests/test_workers_api.py`
  - `backend/tests/test_e2e_workflow.py`
- Modify frontend files:
  - `frontend/src/api.ts`
  - `frontend/src/types.ts`
  - `frontend/src/components/ServicesList.tsx`
  - `frontend/src/components/ServicesList.test.tsx`
  - `frontend/src/api.controlPlaneData.test.ts`
  - `frontend/src/initialData.ts`
  - `frontend/e2e/app.spec.ts`
- Modify docs/changelog if present in control-plane repo.

## Task 1: Control-Plane Migration

**Files:**
- Create: `onestep-control-plane/backend/alembic/versions/202607200001_add_service_description_to_services.py`
- Modify: `onestep-control-plane/backend/src/onestep_control_plane_api/db/models.py`
- Modify: `onestep-control-plane/backend/tests/test_migrations.py`

- [ ] **Step 1: Write migration test expectations**

Modify `backend/tests/test_migrations.py`:

```python
HEAD_REVISION = "202607200001"
```

Add `"description"` to the expected `services` column set in `test_alembic_upgrade_head_creates_expected_schema`.

Add this test:

```python
def test_alembic_upgrade_head_reconciles_missing_service_description_column(tmp_path) -> None:
    db_path = tmp_path / "missing-service-description.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url)

    metadata = sa.MetaData()
    sa.Table(
        "services",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("latest_deployment_version", sa.String(length=128), nullable=False),
        sa.Column("latest_topology_hash", sa.String(length=255), nullable=True),
        sa.Column("latest_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "alembic_version",
        metadata,
        sa.Column("version_num", sa.String(length=32), nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:version_num)"),
            {"version_num": "202603120001"},
        )
    engine.dispose()

    command.upgrade(make_alembic_config(database_url), "head")

    upgraded_engine = create_engine(database_url)
    inspector = inspect(upgraded_engine)

    assert "description" in {column["name"] for column in inspector.get_columns("services")}

    with upgraded_engine.connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

    assert version == HEAD_REVISION
    upgraded_engine.dispose()
```

- [ ] **Step 2: Run migration tests and verify failure**

Run:

```bash
cd /Users/miclon/development/onestep/onestep-control-plane
uv run pytest backend/tests/test_migrations.py -q
```

Expected: failure because revision `202607200001` and `services.description` do not exist.

- [ ] **Step 3: Add migration file**

Create `backend/alembic/versions/202607200001_add_service_description_to_services.py`:

```python
"""Add description to services.

Revision ID: 202607200001
Revises: 202607190001
Create Date: 2026-07-20 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607200001"
down_revision: str | None = "202607190001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not _has_table(table_name):
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if _has_table("services") and not _has_column("services", "description"):
        op.add_column("services", sa.Column("description", sa.Text(), nullable=True))


def downgrade() -> None:
    if _has_table("services") and _has_column("services", "description"):
        op.drop_column("services", "description")
```

- [ ] **Step 4: Add ORM field**

In `backend/src/onestep_control_plane_api/db/models.py`, add this field to `class Service` after `environment`:

```python
description: Mapped[str | None] = mapped_column(sa.Text)
```

- [ ] **Step 5: Run migration tests**

Run:

```bash
cd /Users/miclon/development/onestep/onestep-control-plane
uv run pytest backend/tests/test_migrations.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
cd /Users/miclon/development/onestep/onestep-control-plane
git add backend/alembic/versions/202607200001_add_service_description_to_services.py backend/src/onestep_control_plane_api/db/models.py backend/tests/test_migrations.py
git commit -m "feat: add service description storage"
```

## Task 2: Control-Plane Schema And Ingestion

**Files:**
- Modify: `onestep-control-plane/backend/src/onestep_control_plane_api/api/schemas.py`
- Modify: `onestep-control-plane/backend/src/onestep_control_plane_api/api/common.py`
- Modify: `onestep-control-plane/backend/src/onestep_control_plane_api/api/agent_ingestion_service.py`
- Modify: `onestep-control-plane/backend/src/onestep_control_plane_api/api/routers/agent_ws.py`
- Test: `onestep-control-plane/backend/tests/test_agent_ingestion.py`
- Test: `onestep-control-plane/backend/tests/test_agent_ws.py`

- [ ] **Step 1: Write ingestion tests**

In `backend/tests/test_agent_ingestion.py`, update `make_service_payload`:

```python
DESCRIPTION_UNSET = object()


def make_service_payload(
    instance_id: str = "8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df",
    *,
    description: str | None | object = DESCRIPTION_UNSET,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "billing-sync",
        "environment": "prod",
        "node_name": "vm-prod-3",
        "instance_id": instance_id,
        "deployment_version": "1.0.0a0+c435c99",
    }
    if description is not DESCRIPTION_UNSET:
        payload["description"] = description
    return payload
```

Add these tests:

```python
def test_sync_ingestion_persists_service_description(db_session) -> None:
    payload = make_sync_payload()
    payload["service"] = make_service_payload(
        description="  Reconciles billing data into the warehouse.  "
    )

    ingest_sync(db_session, payload)

    service = db_session.scalar(select(Service))
    assert service is not None
    assert service.description == "Reconciles billing data into the warehouse."


def test_missing_service_description_does_not_clear_existing_value(db_session) -> None:
    ingest_sync(db_session, make_sync_payload())
    service = db_session.scalar(select(Service))
    assert service is not None
    service.description = "Existing description"
    db_session.commit()

    payload = make_sync_payload()
    payload["sent_at"] = "2026-03-08T17:31:06Z"
    payload["sequence"] = 6
    ingest_sync(db_session, payload)

    db_session.refresh(service)
    assert service.description == "Existing description"


def test_explicit_null_service_description_clears_existing_value(db_session) -> None:
    ingest_sync(db_session, make_sync_payload())
    service = db_session.scalar(select(Service))
    assert service is not None
    service.description = "Existing description"
    db_session.commit()

    payload = make_sync_payload()
    payload["service"] = make_service_payload(description=None)
    payload["sent_at"] = "2026-03-08T17:31:06Z"
    payload["sequence"] = 6
    ingest_sync(db_session, payload)

    db_session.refresh(service)
    assert service.description is None
```

In `backend/tests/test_agent_ws.py`, add `"description": "Billing warehouse sync service."` to `make_service_payload()` and assert after the WS hello/sync test:

```python
service = db_session.scalar(select(Service).where(Service.name == "billing-sync"))
assert service is not None
assert service.description == "Billing warehouse sync service."
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
cd /Users/miclon/development/onestep/onestep-control-plane
uv run pytest backend/tests/test_agent_ingestion.py backend/tests/test_agent_ws.py -q
```

Expected: schema rejects `service.description` or DB field is not populated.

- [ ] **Step 3: Add schema field and normalization**

In `backend/src/onestep_control_plane_api/api/schemas.py`, update `ServiceDescriptor`:

```python
class ServiceDescriptor(APIModel):
    name: str = Field(min_length=1, max_length=255)
    environment: Environment
    description: str | None = None
    node_name: str = Field(min_length=1, max_length=255)
    instance_id: UUID
    deployment_version: str = Field(min_length=1, max_length=128)

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None
```

- [ ] **Step 4: Add service metadata helper**

In `backend/src/onestep_control_plane_api/api/common.py`, add:

```python
def apply_service_metadata(service: Service, identity: ServiceDescriptor) -> None:
    if "description" in identity.model_fields_set:
        service.description = identity.description
```

- [ ] **Step 5: Apply metadata on hello, newer sync, and newer heartbeat**

In `backend/src/onestep_control_plane_api/api/routers/agent_ws.py`, import `apply_service_metadata` and call it after `ensure_service`:

```python
service = ensure_service(db, message.payload.service, update_existing_version=True)
apply_service_metadata(service, message.payload.service)
```

In `backend/src/onestep_control_plane_api/api/agent_ingestion_service.py`, import `apply_service_metadata` and call it inside the `apply_sync` and newer heartbeat blocks:

```python
if apply_sync:
    apply_service_metadata(service, request.service)
    service.latest_deployment_version = request.service.deployment_version
```

```python
if is_newer_heartbeat(instance, sent_at=sent_at, sequence=request.sequence):
    apply_service_metadata(service, request.service)
    service.latest_deployment_version = request.service.deployment_version
```

- [ ] **Step 6: Run focused tests**

Run:

```bash
cd /Users/miclon/development/onestep/onestep-control-plane
uv run pytest backend/tests/test_agent_ingestion.py backend/tests/test_agent_ws.py -q
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
cd /Users/miclon/development/onestep/onestep-control-plane
git add backend/src/onestep_control_plane_api/api/schemas.py backend/src/onestep_control_plane_api/api/common.py backend/src/onestep_control_plane_api/api/agent_ingestion_service.py backend/src/onestep_control_plane_api/api/routers/agent_ws.py backend/tests/test_agent_ingestion.py backend/tests/test_agent_ws.py
git commit -m "feat: ingest service descriptions"
```

## Task 3: Control-Plane Query API

**Files:**
- Modify: `onestep-control-plane/backend/src/onestep_control_plane_api/api/schemas.py`
- Modify: `onestep-control-plane/backend/src/onestep_control_plane_api/api/query_support.py`
- Test: `onestep-control-plane/backend/tests/test_query_api.py`

- [ ] **Step 1: Write query API tests**

In `backend/tests/test_query_api.py`, update the relevant service seed in `test_list_services_and_get_service_summary`:

```python
prod_service.description = "Moves billing changes into downstream systems."
```

Add assertions:

```python
assert payload["items"][0]["description"] == "Moves billing changes into downstream systems."
assert detail.json()["description"] == "Moves billing changes into downstream systems."
```

In a dashboard or task-detail test with embedded `payload["service"]`, add:

```python
assert payload["service"]["description"] == "Moves billing changes into downstream systems."
```

- [ ] **Step 2: Run query tests and verify failure**

Run:

```bash
cd /Users/miclon/development/onestep/onestep-control-plane
uv run pytest backend/tests/test_query_api.py::test_list_services_and_get_service_summary -q
```

Expected: response does not include `description`.

- [ ] **Step 3: Add API response field**

In `backend/src/onestep_control_plane_api/api/schemas.py`, update `ServiceSummary`:

```python
class ServiceSummary(APIModel):
    name: str
    environment: Environment
    description: str | None = None
    latest_deployment_version: str
```

- [ ] **Step 4: Populate response field**

In `backend/src/onestep_control_plane_api/api/query_support.py`, update `build_service_summary`:

```python
description=service.description,
```

Insert it immediately after the existing `environment=service.environment` line so the response model construction stays aligned with `ServiceSummary`.

- [ ] **Step 5: Run query tests**

Run:

```bash
cd /Users/miclon/development/onestep/onestep-control-plane
uv run pytest backend/tests/test_query_api.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
cd /Users/miclon/development/onestep/onestep-control-plane
git add backend/src/onestep_control_plane_api/api/schemas.py backend/src/onestep_control_plane_api/api/query_support.py backend/tests/test_query_api.py
git commit -m "feat: expose service descriptions in query API"
```

## Task 4: Frontend API Mapping And UI Display

**Files:**
- Modify: `onestep-control-plane/frontend/src/api.ts`
- Modify: `onestep-control-plane/frontend/src/types.ts`
- Modify: `onestep-control-plane/frontend/src/components/ServicesList.tsx`
- Modify: `onestep-control-plane/frontend/src/components/ServicesList.test.tsx`
- Modify: `onestep-control-plane/frontend/src/api.controlPlaneData.test.ts`
- Modify: `onestep-control-plane/frontend/src/initialData.ts`
- Modify: `onestep-control-plane/frontend/e2e/app.spec.ts`

- [ ] **Step 1: Write frontend API mapping test**

In `frontend/src/api.controlPlaneData.test.ts`, add this field to the shared `serviceSummary` fixture:

```ts
description: 'Shows the local control-plane demo worker.',
```

In `loadControlPlaneData` assertions, add:

```ts
expect(data.services[0]).toEqual(
  expect.objectContaining({
    description: 'Shows the local control-plane demo worker.',
  }),
);
```

- [ ] **Step 2: Write ServicesList render/search test**

In `frontend/src/components/ServicesList.test.tsx`, update `service()` default:

```ts
description: null,
```

Add:

```ts
it('renders and searches service descriptions', async () => {
  const user = userEvent.setup();
  renderServicesList([
    service({
      id: 'billing-sync:prod',
      name: 'billing-sync / prod',
      description: 'Reconciles invoices into the warehouse',
    }),
    service({
      id: 'audit-sync:prod',
      name: 'audit-sync / prod',
      description: 'Ships audit events',
    }),
  ]);

  expect(screen.getByText('Reconciles invoices into the warehouse')).toBeTruthy();

  await user.type(screen.getByPlaceholderText('Search by service name or ID'), 'invoices');

  expect(screen.getByText('billing-sync / prod')).toBeTruthy();
  expect(screen.queryByText('audit-sync / prod')).toBeNull();
});
```

- [ ] **Step 3: Run frontend tests and verify failure**

Run:

```bash
cd /Users/miclon/development/onestep/onestep-control-plane/frontend
pnpm test -- --run src/api.controlPlaneData.test.ts src/components/ServicesList.test.tsx
```

Expected: TypeScript or runtime failure because `description` is not in the service model and the list does not search it.

- [ ] **Step 4: Add TypeScript fields and mapping**

In `frontend/src/api.ts`, update `interface ServiceSummary`:

```ts
description: string | null;
```

In `mapService`, add:

```ts
description: service.description,
```

In `frontend/src/types.ts`, update `Service`:

```ts
description: string | null;
```

In `frontend/src/initialData.ts`, add `description: null` or demo descriptions to each service fixture.

- [ ] **Step 5: Display and search descriptions**

In `frontend/src/components/ServicesList.tsx`, update search:

```ts
const description = svc.description?.toLowerCase() ?? '';
const matchesSearch =
  !query ||
  svc.name.toLowerCase().includes(query) ||
  svc.id.toLowerCase().includes(query) ||
  description.includes(query);
```

Under the existing service id line, render description when present:

```tsx
{svc.description && (
  <div className="mt-0.5 max-w-sm truncate text-[11px] font-medium text-slate-500">
    {svc.description}
  </div>
)}
```

In `frontend/src/App.tsx`, under the service-detail title row and only when no task is selected, render:

```tsx
{!selectedTask && selectedService.description && (
  <p className="ml-10 mt-1 max-w-3xl text-sm font-medium text-slate-500">
    {selectedService.description}
  </p>
)}
```

- [ ] **Step 6: Update e2e fixtures**

In `frontend/e2e/app.spec.ts`, add `description` to service fixtures:

```ts
description: "Synchronizes billing records into downstream systems.",
```

Assert it appears on list/detail:

```ts
await expect(page.getByText("Synchronizes billing records into downstream systems.")).toBeVisible();
```

- [ ] **Step 7: Run frontend checks**

Run:

```bash
cd /Users/miclon/development/onestep/onestep-control-plane/frontend
pnpm test -- --run src/api.controlPlaneData.test.ts src/components/ServicesList.test.tsx
pnpm build
```

Expected: pass.

- [ ] **Step 8: Commit**

```bash
cd /Users/miclon/development/onestep/onestep-control-plane
git add frontend/src/api.ts frontend/src/types.ts frontend/src/components/ServicesList.tsx frontend/src/components/ServicesList.test.tsx frontend/src/api.controlPlaneData.test.ts frontend/src/initialData.ts frontend/e2e/app.spec.ts
git commit -m "feat: show service descriptions in the UI"
```

## Task 5: Worker Builder Propagation

**Files:**
- Modify: `onestep-control-plane/backend/src/onestep_control_plane_api/api/worker_compiler.py`
- Modify: `onestep-control-plane/backend/src/onestep_control_plane_api/api/worker_service.py`
- Test: `onestep-control-plane/backend/tests/test_workers_api.py`
- Test: `onestep-control-plane/backend/tests/test_e2e_workflow.py`

- [ ] **Step 1: Write worker compiler tests**

In `backend/tests/test_workers_api.py::test_download_worker_package_includes_compiled_worker_yaml`, add:

```python
assert compiled["reporter"]["service_description"] == "sync orders"
```

In the custom reporting test, read the generated package or add a compiler-specific assertion:

```python
compiled = yaml.safe_load(worker_yaml)
assert compiled["reporter"] == {
    "base_url": "https://telemetry.example.com",
    "token": "${ONESTEP_WORKER_REPORTING_TOKEN}",
    "service_description": "sync orders",
}
```

In `backend/tests/test_e2e_workflow.py`, include description in the direct `compile_worker_yaml` worker dict:

```python
"description": "incremental sync from MySQL",
```

Assert:

```python
assert data["reporter"]["service_description"] == "incremental sync from MySQL"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
cd /Users/miclon/development/onestep/onestep-control-plane
uv run pytest backend/tests/test_workers_api.py backend/tests/test_e2e_workflow.py -q
```

Expected: compiled YAML has no `reporter.service_description`.

- [ ] **Step 3: Pass worker description into compiler**

In `backend/src/onestep_control_plane_api/api/worker_service.py`, add the field to `worker_dict`:

```python
worker_dict = {
    "name": worker.name,
    "description": worker.description,
    "handler_ref": worker.handler_ref,
    "source": worker.source_config,
    "sinks": worker.sink_configs,
    "reporting_enabled": worker.reporting_enabled,
    "reporting_config": _normalize_reporting_config(worker.reporting_config_json),
}
```

- [ ] **Step 4: Emit reporter service_description**

In `backend/src/onestep_control_plane_api/api/worker_compiler.py`, add:

```python
def _service_description(worker: dict[str, Any]) -> str | None:
    description = str(worker.get("description") or "").strip()
    return description or None
```

Replace `_reporter_config` with:

```python
def _reporter_config(worker: dict[str, Any]) -> bool | dict[str, str] | None:
    if worker.get("reporting_enabled", True) is False:
        return None
    description = _service_description(worker)
    reporting_config = worker.get("reporting_config")
    if not isinstance(reporting_config, dict):
        return {"service_description": description} if description is not None else True
    if reporting_config.get("mode", "platform") != "custom":
        return {"service_description": description} if description is not None else True
    endpoint_url = str(reporting_config.get("endpoint_url") or "").strip()
    if not endpoint_url:
        raise ValueError("custom reporting endpoint_url is required")
    reporter = {
        "base_url": endpoint_url,
        "token": f"${{{REPORTING_TOKEN_ENV}}}",
    }
    if description is not None:
        reporter["service_description"] = description
    return reporter
```

- [ ] **Step 5: Run worker tests**

Run:

```bash
cd /Users/miclon/development/onestep/onestep-control-plane
uv run pytest backend/tests/test_workers_api.py backend/tests/test_e2e_workflow.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
cd /Users/miclon/development/onestep/onestep-control-plane
git add backend/src/onestep_control_plane_api/api/worker_compiler.py backend/src/onestep_control_plane_api/api/worker_service.py backend/tests/test_workers_api.py backend/tests/test_e2e_workflow.py
git commit -m "feat: propagate worker descriptions to runtime services"
```

## Task 6: Runtime Reporter Config And Payloads

**Files:**
- Modify: `onestep/plugins/onestep-control-plane/src/onestep_control_plane/reporter.py`
- Modify: `onestep/plugins/onestep-control-plane/src/onestep_control_plane/__init__.py`
- Modify: `onestep/src/onestep/cli.py`
- Test: `onestep/plugins/onestep-control-plane/tests/test_control_plane_reporter.py`
- Test: `onestep/plugins/onestep-control-plane/tests/test_control_plane_ws.py`
- Test: `onestep/tests/test_cli.py`

- [ ] **Step 1: Write runtime reporter tests**

In `plugins/onestep-control-plane/tests/test_control_plane_reporter.py`, update `_make_config`:

```python
service_description="Synchronizes billing data.",
```

Assert heartbeat and sync payloads include:

```python
assert heartbeat_payload["service"]["description"] == "Synchronizes billing data."
assert sync_payload["service"]["description"] == "Synchronizes billing data."
```

In `test_reporter_config_from_env_uses_fallback_names`, add:

```python
monkeypatch.setenv("ONESTEP_SERVICE_DESCRIPTION", "Processes payment events.")
assert config.service_description == "Processes payment events."
```

In `plugins/onestep-control-plane/tests/test_control_plane_ws.py`, assert the hello service payload includes `description`.

- [ ] **Step 2: Write YAML/CLI tests**

In `tests/test_cli.py`, update fake allowed fields:

```python
allowed_fields=frozenset({"type", "base_url", "token", "service_name", "service_description"})
```

Add a YAML reporter mapping test:

```python
def test_yaml_target_reporter_mapping_accepts_service_description(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "reporter-description.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "yaml-reporter",
                "reporter": {
                    "base_url": "https://yaml-control-plane.example.com",
                    "service_name": "billing-sync-worker",
                    "service_description": "Synchronizes billing data.",
                },
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ONESTEP_CONTROL_TOKEN", "env-token")
    created = []
    install_fake_control_plane_reporter(monkeypatch, created)

    with registered_yaml_module():
        app = OneStepApp.load(str(config_path))

    assert created[0].config.service_description == "Synchronizes billing data."
    assert app.describe()["reporter"]["service_description"] == "Synchronizes billing data."
```

- [ ] **Step 3: Run runtime tests and verify failure**

Run:

```bash
cd /Users/miclon/development/onestep/onestep
uv run pytest plugins/onestep-control-plane/tests/test_control_plane_reporter.py plugins/onestep-control-plane/tests/test_control_plane_ws.py tests/test_cli.py -q
```

Expected: failures because config field and payload field do not exist.

- [ ] **Step 4: Add runtime config field**

In `plugins/onestep-control-plane/src/onestep_control_plane/reporter.py`, update `ControlPlaneReporterConfig`:

```python
service_description: str | None = None
```

Normalize in `__post_init__`:

```python
self.service_description = _normalize_optional_text(self.service_description)
```

Add helper near other normalizers:

```python
def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
```

Update the `from_env` signature, env parsing, and constructor call:

```python
service_description: str | None = None,
```

```python
service_description = service_description or _read_env("ONESTEP_SERVICE_DESCRIPTION")
```

```python
service_description=service_description,
```

- [ ] **Step 5: Add YAML plugin field**

In `plugins/onestep-control-plane/src/onestep_control_plane/__init__.py`:

```python
_CONTROL_PLANE_REPORTER_FIELDS = frozenset(
    {"type", "base_url", "token", "service_name", "service_description"}
)
```

Pass it to `from_env`:

```python
service_description=ctx.optional_string(spec, "service_description"),
```

- [ ] **Step 6: Emit payload and reporter summary**

In `ControlPlaneReporter.attach`, add to `app.set_reporter_summary` only when present:

```python
summary = {
    "type": "control_plane",
    "base_url": self.config.base_url,
    "service_name": self._service_name or app.name,
}
if self.config.service_description is not None:
    summary["service_description"] = self.config.service_description
app.set_reporter_summary(summary)
```

In `_service_descriptor`, add:

```python
descriptor = {
    "name": self._service_name,
    "environment": self.config.environment,
    "node_name": self._node_name,
    "instance_id": self._require_instance_id(),
    "deployment_version": self._deployment_version,
}
if self.config.service_description is not None:
    descriptor["description"] = self.config.service_description
return descriptor
```

In `src/onestep/cli.py`, update `_format_reporter`:

```python
service_description = reporter.get("service_description")
if service_description:
    parts.append(f"description={service_description!r}")
```

- [ ] **Step 7: Run runtime tests**

Run:

```bash
cd /Users/miclon/development/onestep/onestep
uv run pytest plugins/onestep-control-plane/tests/test_control_plane_reporter.py plugins/onestep-control-plane/tests/test_control_plane_ws.py tests/test_cli.py -q
```

Expected: pass.

- [ ] **Step 8: Commit**

```bash
cd /Users/miclon/development/onestep/onestep
git add plugins/onestep-control-plane/src/onestep_control_plane/reporter.py plugins/onestep-control-plane/src/onestep_control_plane/__init__.py src/onestep/cli.py plugins/onestep-control-plane/tests/test_control_plane_reporter.py plugins/onestep-control-plane/tests/test_control_plane_ws.py tests/test_cli.py
git commit -m "feat: report service descriptions"
```

## Task 7: Docs And Changelog

**Files:**
- Modify: `onestep/README.md`
- Modify: `onestep/README.zh-CN.md`
- Modify: `onestep/docs/yaml-task-definition.md`
- Modify: `onestep/docs/control-plane/index.md`
- Modify: `onestep/CHANGELOG.md`
- Modify: `onestep-control-plane/README.md`
- Modify: control-plane changelog file if one exists.

- [ ] **Step 1: Update onestep YAML docs**

In `docs/yaml-task-definition.md`, update the reporter section:

```yaml
reporter:
  base_url: https://control-plane.example.com
  token: ${ONESTEP_CONTROL_PLANE_TOKEN}
  service_name: billing-sync-worker
  service_description: Synchronizes billing data into the warehouse
```

Add bullets:

```markdown
- `service_description` is optional service-level metadata shown by the control plane.
- It can also be supplied with `ONESTEP_SERVICE_DESCRIPTION`.
- Task-level `tasks[].description` remains separate and describes an individual task.
```

- [ ] **Step 2: Update control-plane docs**

In `docs/control-plane/index.md`, add `ONESTEP_SERVICE_DESCRIPTION` to the env table:

```markdown
| `ONESTEP_SERVICE_DESCRIPTION` | Optional service-level description displayed in the control plane | `Syncs billing users into the warehouse` |
```

- [ ] **Step 3: Update README snippets**

In `README.md`, add this optional YAML example near reporter setup:

```yaml
reporter:
  service_description: Synchronizes billing data into the warehouse
```

In `README.zh-CN.md`, add the matching Chinese note:

```markdown
可选：设置 `reporter.service_description` 或 `ONESTEP_SERVICE_DESCRIPTION` 后，Control Plane 会在服务目录展示服务描述。
```

- [ ] **Step 4: Update changelogs**

In `onestep/CHANGELOG.md`, add under Unreleased or the next release section:

```markdown
- Adds `reporter.service_description` / `ONESTEP_SERVICE_DESCRIPTION` so workers can report service-level descriptions to the control plane.
```

In the control-plane changelog, add:

```markdown
- Stores, returns, and displays service-level descriptions reported by onestep workers.
```

- [ ] **Step 5: Commit**

```bash
cd /Users/miclon/development/onestep/onestep
git add README.md README.zh-CN.md docs/yaml-task-definition.md docs/control-plane/index.md CHANGELOG.md
git commit -m "docs: document service descriptions"
```

```bash
cd /Users/miclon/development/onestep/onestep-control-plane
git add README.md CHANGELOG.md
git commit -m "docs: document service descriptions"
```

If `CHANGELOG.md` does not exist in the control-plane repo, skip that file and commit only `README.md`.

## Task 8: Full Validation And Local Control-Plane Restart

**Files:**
- No source edits unless tests expose missed files.

- [ ] **Step 1: Run control-plane backend tests**

Run:

```bash
cd /Users/miclon/development/onestep/onestep-control-plane
uv run pytest
```

Expected: pass.

- [ ] **Step 2: Run frontend tests and build**

Run:

```bash
cd /Users/miclon/development/onestep/onestep-control-plane/frontend
pnpm test -- --run
pnpm build
```

Expected: pass.

- [ ] **Step 3: Run runtime/plugin tests**

Run:

```bash
cd /Users/miclon/development/onestep/onestep
uv run pytest tests/test_cli.py plugins/onestep-control-plane/tests -q
```

Expected: pass.

- [ ] **Step 4: Validate generated worker YAML**

Create a temporary worker YAML containing:

```yaml
apiVersion: onestep/v1alpha1
kind: App

app:
  name: billing-sync

reporter:
  service_description: Synchronizes billing data into the warehouse

resources:
  tick:
    type: interval
    seconds: 60
    immediate: false

tasks:
  - name: main
    source: tick
    handler:
      ref: example.handlers:main
```

Run:

```bash
cd /Users/miclon/development/onestep/onestep
uv run onestep check --strict /tmp/service-description-worker.yaml
```

Expected: strict check accepts `reporter.service_description`. If handler import fails because the example handler does not exist, replace `handler` with a real local test handler or rely on unit tests for strict field validation.

- [ ] **Step 5: Rebuild and restart local control plane**

Because control-plane backend/frontend code is baked into the image, run:

```bash
cd /Users/miclon/development/onestep/onestep-control-plane
docker compose build plane
docker compose up -d plane
docker compose ps
```

Expected: `plane` is healthy.

- [ ] **Step 6: Manual smoke**

Start or deploy a worker configured with:

```yaml
reporter:
  service_description: Synchronizes billing data into the warehouse
```

Verify:

```bash
curl -s "http://127.0.0.1:8080/api/v1/services?environment=dev" | jq '.items[0].description'
```

Expected:

```json
"Synchronizes billing data into the warehouse"
```

Open the UI and verify:
- Service Directory row shows the description.
- Searching by a word from the description keeps the service visible.
- Service Detail header shows the description.

## Release And Deployment Order

Because old control-plane schemas reject unknown payload fields, deploy in this order:

```text
1. Release/deploy onestep-control-plane receiver + UI.
2. Confirm plane accepts optional service.description.
3. Release onestep runtime reporter plugin.
4. Update Worker Builder generated packages or deployed workers.
```

If releasing packages:
- For core `onestep` package releases, follow repository release rules: bump root `pyproject.toml`, update `uv.lock`, update `CHANGELOG.md`, and tag `v<core-version>`.
- If only `plugins/onestep-control-plane` changes in the onestep repo, bump that plugin patch version and update `uv.lock`.
- If the plugin depends on a newly released core version, set a lower bound such as `onestep>=<core-version>` and do not add an upper bound unless explicitly requested.

## Failure Modes To Watch

- New runtime sends `service.description` to old plane: old plane rejects the frame because Pydantic models forbid unknown fields.
- Missing description from an old runtime clears DB value: prevent this with `model_fields_set` checks.
- Metrics/events roll back description out of order: do not update service description from metrics/events.
- Worker Builder emits `reporter: true` when description exists: runtime never sees the description.
- UI maps API response but `Service` type does not include the field: description silently disappears before render.

## Parallelization

```text
Lane A: control-plane DB/schema/ingestion -> query API
Lane B: runtime reporter config/payload
Lane C: frontend UI after query API response shape is agreed
Lane D: Worker Builder after runtime YAML field is agreed
```

Execution order:
- Start Lane A first because it owns compatibility.
- Lane B can be built in parallel but must not deploy before Lane A.
- Lane C can start once `ServiceSummary.description` is settled.
- Lane D can start once `reporter.service_description` is settled.

## Final Checklist

- [ ] `services.description` exists after Alembic upgrade.
- [ ] `ServiceDescriptor` accepts optional `description`.
- [ ] Missing `description` does not clear existing value.
- [ ] Explicit null/blank clears existing value.
- [ ] WS hello persists description.
- [ ] Newer sync/heartbeat persists description.
- [ ] Metrics/events do not update description.
- [ ] `/api/v1/services`, `/api/v1/services/{name}`, dashboard, task detail embedded service all include `description`.
- [ ] Frontend displays and searches description.
- [ ] Worker Builder emits `reporter.service_description`.
- [ ] Runtime reporter supports Python arg, YAML field, and env var.
- [ ] Docs explain service-level vs task-level description.
- [ ] Local plane image is rebuilt and container is healthy after control-plane code changes.
