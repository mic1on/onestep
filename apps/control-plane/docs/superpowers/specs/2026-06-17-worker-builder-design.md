# Worker Builder Design

## Summary

Add a Lambda-style **Worker Builder** to the control plane: a form-based
designer where operators define a worker by choosing a source (data in), a
handler package (code), and one or more sinks (data out). The plane stores the
worker as a structured configuration, and on deploy compiles it into a complete
`worker.yaml`, merges it with the handler package, and ships it to a worker
agent via the existing workflow-package + deployment channel.

This is phase 2 of the three-phase worker platform. Phase 1 (Connectors
management) provides the workspace-level connector entities that this builder's
source/sink dropdowns reference. Phase 3 (already built) is the deployment +
agent install pipeline.

The removed pipeline builder's exporter compiled a visual graph into a
worker.zip. This phase restores that compilation capability — but driven by a
form, not a graph, and producing a real multi-resource onestep YAML instead of
a simplified single-task one.

## Goals

- Operators define a worker entirely through forms: name, handler package +
  entry ref, one source, multiple sinks. No YAML editing.
- Source/sink types and their fields are enumerated from the real onestep
  plugin catalog, so the form renders the correct fields per type.
- Saving a worker stores structured config only — no YAML synthesis, no deploy.
  Workers can be edited repeatedly before deployment.
- Deploying a worker compiles a complete `worker.yaml` (resources + tasks),
  merges it with the handler zip, creates a workflow package, and dispatches a
  deployment to the chosen agent — reusing the existing install/check/run
  pipeline (including dependency installation).
- Multi-sink workers compile to `emit: [sink_0, sink_1, ...]` per onestep's YAML
  spec.

## Non-Goals

- No multi-source workers (single source only, per prior decision).
- No worker version history chain (each deploy creates a new workflow package;
  the worker entity itself is not versioned).
- No handler schema validation or zip parsing — the handler ref is a free-text
  string (default `handler:handler`); correctness is discovered at agent
  `onestep check` time.
- No runtime monitoring in the builder — reuses the existing deployment-events
  page.
- No retry/concurrency/timeout policy UI (onestep defaults apply; can be added
  later).
- No state-store configuration UI for incremental/binlog sources (the `state:`
  field is omitted; sources that need it will use onestep defaults). Noted as a
  future enhancement.

## Data Model

### `workers` table

```
workers
  id                  UUID PK
  name                VARCHAR(255), NOT NULL, UNIQUE  (= onestep app name)
  description         TEXT, NOT NULL, default ""
  handler_package_id  UUID, FK → workflow_packages.id, NULLABLE
                      (null until a handler zip is uploaded/selected)
  handler_ref         VARCHAR(255), NOT NULL, default "handler:handler"
  source_config       JSON, NOT NULL  (see shape below)
  sink_configs        JSON, NOT NULL, default []  (list, see shape below)
  status              VARCHAR(32), NOT NULL, default "draft"
                      ("draft" while editing, "ready" when deployable)
  created_at          TIMESTAMPTZ
  updated_at          TIMESTAMPTZ
```

`source_config` shape:
```json
{
  "type": "mysql_incremental",
  "connector_id": "<uuid-of-workspace-connector>",
  "fields": { "table": "orders", "key": "id", "cursor": ["updated_at", "id"] }
}
```
For built-in sources that need no connector (interval, cron, webhook),
`connector_id` is null and all config lives in `fields`.

`sink_configs` shape (list, can be empty for handler-only workers):
```json
[
  {
    "type": "mysql_table_sink",
    "connector_id": "<uuid>",
    "fields": { "table": "synced", "mode": "upsert", "keys": ["id"] }
  }
]
```

### Relationship to existing entities

- `handler_package_id` → `workflow_packages.id`: the handler zip uploaded via
  the existing workflow-package upload endpoint. The worker references it; it
  is not owned exclusively (could be reused, though typically one-per-worker).
- Deployment creates a **new** workflow package (the merged zip) distinct from
  the handler package. So: handler_package (code only) → deploy → merged
  package (code + worker.yaml) → deployment.

## Worker.yaml Compiler

`api/worker_compiler.py` — the core synthesis logic.

### Input

A worker entity + its resolved connectors (decrypted) + handler package.

### Output

A `worker.yaml` string (plus the handler zip bytes, merged into a new zip).

### Compilation rules

1. **app.name** = `worker.name`.
2. **resources** — for each referenced connector (deduplicated by connector_id),
   emit a connector resource:
   ```yaml
   <conn_key>:
     type: <connector.type>      # mysql, redis, rabbitmq, etc.
     <...decrypted config + secret fields...>
   ```
   Connector resource keys are deterministic: `conn_<index>` (by order of first
   reference). If source and a sink share the same connector_id, they share one
   resource.
3. **resources** — for the source, emit a source resource:
   ```yaml
   <source_key>:
     type: <source_config.type>
     connector: <conn_key>        # omitted for built-in (interval/cron/webhook)
     <...source_config.fields...>
   ```
4. **resources** — for each sink, emit a sink resource:
   ```yaml
   <sink_key>:
     type: <sink.type>
     connector: <conn_key>        # omitted if the sink needs none
     <...sink.fields...>
   ```
5. **tasks** — single task:
   ```yaml
   tasks:
     - name: main
       source: <source_key>
       emit: [<sink_key_0>, <sink_key_1>, ...]   # omitted if no sinks
       handler:
         ref: <worker.handler_ref>
   ```
6. YAML output is a string; strict-mode header (`apiVersion: onestep/v1alpha1`,
   `kind: App`) is included.

### Package merge

On deploy:
1. Download the handler package zip (from workflow-package storage).
2. Compile `worker.yaml` from the worker config.
3. Create a new zip = handler zip contents **plus** `worker.yaml` at root.
   If the handler zip already contains a `worker.yaml`, the compiled one
   overwrites it (the builder's config is authoritative).
4. Upload the merged zip as a new workflow package (`entrypoint: worker.yaml`).
5. Create a worker_deployment for the chosen agent.

This means the agent receives a self-contained zip and runs `onestep check` +
`onestep run worker.yaml` exactly as it does for any workflow package — including
the dependency-install phase (phase: dependency installation) if the handler
ships a `pyproject.toml` / `requirements.txt`.

## Backend

### Migration

New migration `add_workers_table`, chained off `202606170001` (connectors).
Creates the `workers` table with a unique index on `name`.

### `api/routers/workers.py` (new)

```
GET    /api/v1/workers                 list
POST   /api/v1/workers                 create (structured config)
GET    /api/v1/workers/:id             detail
PUT    /api/v1/workers/:id             update
DELETE /api/v1/workers/:id             delete
POST   /api/v1/workers/:id/deploy      { worker_agent_id, desired_status }
                                      → compile + merge + package + deploy
```

The deploy endpoint:
1. Loads the worker + resolves connectors (decrypt secrets).
2. Loads the handler package zip from storage.
3. `worker_compiler.compile(worker, connectors, handler_zip_bytes)` → merged zip.
4. `create_workflow_package(merged_zip, entrypoint="worker.yaml")` — reuses the
   existing endpoint's storage logic.
5. `create_worker_deployment(package_id, worker_agent_id, desired_status)` —
   reuses the existing deployment-creation + command-dispatch logic.
6. Returns the deployment summary.

### `api/schemas.py` (append)

Worker request/response models: `WorkerSummary`, `WorkerCreateRequest`,
`WorkerUpdateRequest`, `WorkerListResponse`, `WorkerDeployRequest`.

### `api/worker_compiler.py` (new)

Pure function `compile_worker_yaml(worker, connectors, handler_ref) -> str` and
`merge_package(handler_zip_bytes, worker_yaml_str) -> bytes`. No I/O, no DB —
testable in isolation.

## Frontend

### Source/Sink catalog (`features/workers/catalog.ts`)

A static catalog mirroring the onestep plugin inventory discovered in research:

- **Sources**: interval, cron, webhook, rabbitmq_queue, redis_stream, sqs_queue,
  mysql_incremental, mysql_table_queue, mysql_binlog, postgres_incremental,
  postgres_table_queue, feishu_bitable_incremental.
- **Sinks**: http_sink, rabbitmq_queue, redis_stream, sqs_queue,
  mysql_table_sink, postgres_table_sink, feishu_bitable_table_sink.

Each entry: `label`, `needsConnector: boolean`, `fields: SourceSinkField[]`
(name, label, type, required, enum options, placeholder). Plugin entries set
`needsConnector: true`; built-ins (interval/cron/webhook/http_sink) set false.

### Workers list page (`pages/workers/WorkersListPage.tsx`)

Table of workers: name, status, source type, sink count, updated_at. "New
worker" button. Each row links to the editor.

### Worker editor page (`pages/workers/WorkerEditorPage.tsx`)

Three-section form (Lambda layout):

1. **Identity**: name, description, handler package (file upload or reference to
   an existing workflow package), handler ref (default `handler:handler`).
2. **Source** (single): type dropdown (from source catalog) → if
   `needsConnector`, show connector dropdown (from `useConnectorsQuery`) →
   dynamic fields from the source's field schema.
3. **Sinks** (list, add/remove): each sink has the same type→connector→fields
   flow.

Footer: **Save** (creates/updates worker entity), **Deploy to agent** (opens the
existing DeployDialog flow, but instead of uploading a raw zip, calls the
worker-deploy endpoint).

### API client + queries

`lib/api/client.ts`: `listWorkers`, `getWorker`, `createWorker`, `updateWorker`,
`deleteWorker`, `deployWorker`. `features/workers/queries.ts`: corresponding
React Query hooks.

### Navigation + routes + i18n

- `app/router.tsx`: `workers` → `WorkersListPage`, `workers/:workerId` →
  `WorkerEditorPage`.
- `AppShell.tsx`: a `Workers` NavLink (between Connectors and Agents).
- `i18n.ts`: `app.workersNav` + `workers.*` namespace (en + zh).

## Testing

- **`test_worker_compiler.py`** — unit tests for the pure compiler:
  - single source + single sink → correct resource/task structure
  - single source + multiple sinks → `emit: [sink_0, sink_1]`
  - shared connector deduplication (source + sink same connector → one resource)
  - built-in source (interval, no connector) → no `connector:` key
  - handler ref appears in `tasks[0].handler.ref`
- **`test_workers_api.py`** — CRUD happy path + deploy endpoint (mock the
  package storage + deployment creation to assert the merge + dispatch happens).
- **Frontend**: `WorkerEditorPage.test.tsx` — renders the three-section form,
  source type dropdown shows catalog entries, adding a sink adds a row.
  Extend `AppShell.test.tsx` for the Workers nav link.
- Migration test: extend `test_migrations.py` with the `workers` table.

## Risks

- **Compiler correctness**: the synthesized YAML must pass `onestep check`. The
  compiler must produce valid resource references and field names. Mitigated by
  unit-testing the compiler output against known-good YAML shapes, and the
  agent's `onestep check` as a runtime backstop.
- **Connector secret exposure**: the compiler decrypts connector secrets to
  embed them in the YAML (the agent needs them to connect). The decrypted YAML
  exists only transiently in memory during deploy and inside the deployed zip
  (which is stored as a workflow package, same trust boundary as any uploaded
  package). No new surface beyond what the package storage already implies.
- **Catalog drift**: the source/sink catalog (frontend) must match the real
  onestep plugin field signatures. Same risk as the connector catalog; same
  mitigation (single source-of-truth file, update both sides together).
- **State store omission**: incremental/binlog sources may need a `state:`
  resource for cursor persistence. This phase omits it (onestep defaults apply).
  Workers using these sources may not resume correctly across restarts until
  state-store configuration is added in a future phase. Noted here so it is not
  forgotten.
