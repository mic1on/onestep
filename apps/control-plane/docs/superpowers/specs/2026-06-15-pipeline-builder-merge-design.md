# Pipeline Builder Merge Design

Date: 2026-06-15
Status: Ready for user review
Owner: Codex

## Summary

Move the useful orchestration surface from `onestep-web` into `onestep-control-plane` as a first-class Pipeline Builder.

This first phase makes `onestep-control-plane` the place where users create, edit, validate, and export pipeline definitions. It does not add remote deployment, agent-side installation, or new WebSocket protocol behavior.

## Design Inputs

The following decisions were validated in conversation:

- Do not keep `onestep-web` as a separate product line for production control-plane use.
- Start with option A: merge orchestration and build capabilities into `onestep-control-plane`.
- Do not include deployment scheduling in this phase.
- Keep execution outside the control plane. Workers and agents still run `onestep`; the control plane only builds and exports definitions in this phase.

## Goals

1. Give `onestep-control-plane` a first-class Pipeline Builder surface.
2. Preserve the useful `onestep-web` capabilities: graph editing, connector configuration, validation, and worker export.
3. Keep design-time pipelines separate from runtime service/task telemetry.
4. Avoid control-plane protocol changes until deployment is designed explicitly.
5. Keep the change large enough to remove product ambiguity, but small enough to ship without coordinating agent behavior.

## Non-Goals

- No remote deployment to agents.
- No new agent WebSocket command kinds.
- No control-plane-owned task execution.
- No migration of `onestep-web` local start/stop runtime behavior.
- No local runtime log streaming in `onestep-control-plane`.
- No automatic binding between a design-time pipeline and a runtime `Service`.
- No retirement or deletion of the `onestep-web` repository in this implementation step.

## Current State

`onestep-web` owns the design-time pipeline experience:

- pipeline CRUD
- graph schema for source, handler, sink nodes
- connector descriptors
- credential masking and encryption
- compiler validation
- OneStep YAML generation
- worker bundle export
- local start/stop runtime and log streaming

`onestep-control-plane` owns the runtime control surface:

- services, instances, task definitions, task events, and metric windows
- agent WebSocket sessions
- agent command lifecycle
- service, task, instance, command, and notification UI

The product problem is that both projects contain task-oriented concepts. Users should not have to decide whether tasks are created in `onestep-web` or observed in `onestep-control-plane`.

## Recommended Approach

Add a new design-time Pipeline domain to `onestep-control-plane`.

This domain should reuse and adapt the stable pieces of `onestep-web`, but not import the local runtime model. The first version is a builder and exporter, not a deployment orchestrator.

Why this approach:

- It makes `onestep-control-plane` the single control-console entry point.
- It keeps runtime telemetry models clean and agent-driven.
- It avoids changing the agent protocol before deployment semantics are ready.
- It lets the existing `onestep-web` compiler/exporter behavior carry over with focused tests.

## Backend Design

### Data Model

Add tables for design-time pipeline state:

- `pipelines`
- `pipeline_credentials`

`pipelines` stores:

- `id`
- `name`
- `description`
- `graph_json`
- `status`
- `created_at`
- `updated_at`

`status` is a design-time lifecycle with exactly these first-phase values:

- `draft`
- `valid`
- `invalid`

Do not reuse runtime statuses like `running` or `stopped`, because this phase does not run pipelines.

`pipeline_credentials` stores:

- `id`
- `name`
- `connector_type`
- encrypted connector config
- encrypted environment variables
- `created_at`
- `updated_at`

Use the control-plane database conventions:

- SQLAlchemy models in `backend/src/onestep_control_plane_api/db/models.py`
- JSON type compatible with SQLite and PostgreSQL
- Alembic migration for the new tables
- UUID primary keys

### API

Add authenticated console APIs under `/api/v1`:

- `GET /api/v1/connectors`
- `GET /api/v1/pipelines`
- `POST /api/v1/pipelines`
- `GET /api/v1/pipelines/{pipeline_id}`
- `PUT /api/v1/pipelines/{pipeline_id}`
- `DELETE /api/v1/pipelines/{pipeline_id}`
- `POST /api/v1/pipelines/{pipeline_id}/validate`
- `POST /api/v1/pipelines/{pipeline_id}/export`
- `GET /api/v1/pipeline-credentials`
- `POST /api/v1/pipeline-credentials`
- `PUT /api/v1/pipeline-credentials/{credential_id}`
- `DELETE /api/v1/pipeline-credentials/{credential_id}`

`validate` returns structured validation success or compiler errors. It updates the pipeline status to `valid` on success and `invalid` on compiler failure.

`export` returns the generated worker bundle as a zip response. It should use the same underlying YAML generation path as validation so users do not validate one representation and export another.

### Reused Code

Move or adapt these `onestep-web` backend concepts into the control-plane backend:

- graph schemas
- connector descriptors
- compiler
- OneStep config adapter
- worker exporter
- credential masking/encryption helpers

Do not move:

- local runtime pool
- pipeline start/stop endpoints
- runtime log WebSocket
- local debug runtime behavior unless a later implementation plan identifies a narrow need

Prefer package-local names that match the control plane, for example:

- `onestep_control_plane_api.api.routers.pipelines`
- `onestep_control_plane_api.pipeline_builder.compiler`
- `onestep_control_plane_api.pipeline_builder.exporter`

## Frontend Design

Add a `Pipelines` entry to the console navigation and two main routes:

- `/pipelines`
- `/pipelines/:pipelineId`

The list page supports:

- list pipelines
- create pipeline
- delete pipeline
- show validation status
- open editor

The editor page supports:

- source, handler, and sink graph editing
- connector palette
- node property editing
- credential selection
- visual handler mapping and code handler mode where already supported by `onestep-web`
- validate action
- export action

The editor must not show Start, Stop, Run, or Deploy actions in this phase. The available actions should communicate that this surface creates a runnable worker definition; it does not execute it.

## Product Boundary

Runtime service data remains agent-reported:

- `services`
- `instances`
- `task_definitions`
- `task_events`
- `agent_sessions`
- `agent_commands`

Design-time pipeline data is user-authored:

- `pipelines`
- `pipeline_credentials`

Do not force a relationship between these two worlds in phase one. A later deployment design can add an explicit relationship such as `pipeline_deployments` or `service.pipeline_id` if the product needs it.

## Error Handling

Validation and export should fail with clear 422 responses for user-fixable graph errors:

- empty graph
- unknown node type
- missing connector credentials
- invalid handler code
- invalid mapping expression
- invalid conditional edge expression
- cycles
- disconnected graph

Unexpected server errors remain 500s and should not leak credential values.

Credential reads return masked values. Writes preserve masked existing environment variables using the same behavior currently used by `onestep-web`.

## Testing

Backend tests:

1. Pipeline CRUD persists and returns graph JSON.
2. Duplicate or invalid graph data is rejected by schema validation.
3. `validate` returns success for a minimal valid source-handler-sink pipeline.
4. `validate` returns a 422-style user error for compiler failures.
5. `export` returns a zip containing `worker.yaml`, `pyproject.toml`, and handler module files.
6. Exported `worker.yaml` can pass `onestep check --strict` for representative built-in connectors.
7. Credential responses mask secret fields and preserve masked values on update.

Frontend tests:

1. Navigation exposes `Pipelines`.
2. Pipeline list renders empty, loading, and populated states.
3. Create pipeline flow opens the editor.
4. Editor renders palette, canvas, properties, validate, and export controls.
5. Start, Stop, Run, and Deploy controls are absent in this phase.

## Success Criteria

The phase is complete when:

1. `onestep-control-plane` can create, edit, validate, and export a pipeline without running it.
2. The exported worker bundle matches the existing `onestep-web` export capability closely enough for equivalent graphs.
3. Existing service/task/instance/command behavior remains unchanged.
4. No agent WebSocket protocol or runtime reporter payload fields change.
5. The UI makes `onestep-control-plane` the obvious place to build pipelines.

## Implementation Notes

This is a substantial merge, so implementation should be split into small commits:

1. backend pipeline builder domain and tests
2. backend export and credential tests
3. frontend route/navigation shell
4. frontend editor integration
5. final validation and documentation updates

Avoid opportunistic redesign of existing service, task, instance, or command pages. This project is about adding the missing design-time pipeline surface to the control plane.
