# Lambda-Style Workflow Deployments Design

Date: 2026-06-17
Status: Draft
Owner: Codex

## Summary

Add a Lambda-style deployment flow to `onestep-control-plane`: users upload
handler code, configure runtime inputs and outputs in the console, save the
result as a workflow, and deploy a packaged version to a selected
`onestep-worker-agent`.

This design builds on the existing worker-agent architecture. The worker agent
continues to be a long-lived execution host that downloads an immutable package,
installs dependencies when needed, runs `onestep check`, then starts
`onestep run`. The control plane becomes the source of truth for workflow
configuration, package versions, environment variables, credential references,
agent selection, and deployment audit.

The Lambda comparison is about product shape, not execution semantics:

- Like Lambda, users provide code and runtime configuration.
- Unlike Lambda, the first OneStep deployment runs as a long-lived worker
  process because OneStep sources may poll queues, schedules, databases, or
  webhooks.

## Design Inputs

The following decisions were validated in conversation:

- Users should upload code, configure environment variables, and choose which
  agent runs the workload.
- The console should configure where data comes from, which handler processes
  it, and where data goes.
- The uploaded zip should be handler code and dependencies, not the durable
  source of runtime wiring.
- The control plane should generate the runnable OneStep package from the saved
  configuration.
- The first release should stay simple and use the existing worker-agent
  subprocess execution model.

## Goals

1. Make `onestep-control-plane` the place where a user can define, package, and
   deploy a workflow.
2. Keep the uploaded code package small and understandable: Python handler code
   plus optional dependency declarations.
3. Keep workflow wiring in control-plane data so the UI, audit log, package
   build, and runtime deployment all agree.
4. Let users manually choose a worker agent for MVP deployments.
5. Preserve the current OneStep runtime boundary: YAML wires sources, handlers,
   sinks, reporter, retries, and task policy; Python owns business logic.
6. Preserve the current worker-agent boundary: the agent executes packages but
   does not own workflow design, scheduling policy, or durable deployment truth.

## Non-Goals

- No automatic scheduler in the first release.
- No multi-agent DAG placement.
- No task-level placement across agents.
- No per-request serverless cold-start model.
- No untrusted multi-tenant sandbox in the first release.
- No browser-based code editor as the primary authoring path.
- No support for zip-provided `worker.yaml` in the default path.
- No hot update of a running workflow. A new deployment version replaces or
  restarts an existing deployment explicitly.

## Current State

`onestep-control-plane` already has the shape needed for this:

- Pipeline Builder design introduced a design-time surface for graph editing,
  validation, and worker bundle export.
- Worker Agent design introduced package storage, worker agent registry,
  deployment assignment, deployment state, and a worker-agent websocket.
- `onestep-worker-agent` already downloads a package, verifies checksum,
  extracts safely, detects `pyproject.toml` or `requirements.txt`, creates a
  checksum-keyed virtualenv, runs `onestep check`, and starts `onestep run`.

The missing product layer is the Lambda-style bridge:

- Upload handler code.
- Configure runtime wiring in the console.
- Build an immutable workflow package from both.
- Deploy that package to a chosen agent.

## Recommended Approach

Use control-plane-generated packages.

The console stores workflow configuration as structured control-plane data. When
the user publishes or deploys, the backend combines that data with the uploaded
code archive and generates a normalized workflow package containing:

- generated `worker.yaml`
- uploaded Python source code
- dependency declarations from the upload, if present
- package manifest metadata

The uploaded zip does not provide the canonical `worker.yaml` in the default
path. This is the key product boundary: the UI must remain the source of truth
for runtime wiring.

### Why Not Zip-Provided `worker.yaml` First?

Letting the zip contain the full `worker.yaml` is faster, but it creates two
truth sources. The UI would show source, sink, environment, and agent selection,
while the runnable package might contain different wiring. That makes audit,
diffs, validation, credential injection, and future visual editing weaker.

Zip-provided `worker.yaml` can be added later as an advanced "bring your own
worker" mode, but it should not be the default product path.

## Core Object Model

### Workflow

A workflow is the design-time object users edit in the console.

Suggested fields:

- `workflow_id`
- `name`
- `description`
- `status`: `draft`, `valid`, `invalid`, `published`
- `source_config_json`
- `handler_config_json`
- `sink_config_json`
- `task_policy_json`
- `env_json`
- `credential_refs_json`
- `latest_package_id`
- `created_by`
- `created_at`
- `updated_at`

The workflow does not run by itself. It is editable and versionable.

### Code Upload

A code upload is the raw user-provided source archive before normalization.

Suggested fields:

- `code_upload_id`
- `workflow_id`
- `filename`
- `checksum_sha256`
- `size_bytes`
- `storage_path`
- `detected_dependency_mode`: `none`, `requirements`, `package`
- `created_by`
- `created_at`

Validation rules:

- Accept zip archives only in MVP.
- Reject unsafe archive paths.
- Enforce maximum size.
- Enforce a maximum file count.
- Reject nested archive formats in MVP.
- Reject top-level `worker.yaml` in the default path, or ignore it with a clear
  warning.

### Workflow Package

A workflow package is an immutable runnable version.

Suggested fields match and extend the existing worker-agent package model:

- `workflow_package_id`
- `workflow_id`
- `code_upload_id`
- `version`
- `filename`
- `content_type`
- `checksum_sha256`
- `size_bytes`
- `storage_path`
- `entrypoint`: defaults to `worker.yaml`
- `manifest_json`
- `created_by`
- `created_at`

Package creation is the build boundary. After creation, a package never changes.

### Deployment

A deployment records the intent to run one package on one worker agent.

Use the existing worker deployment model, with these additions or clarifications:

- `workflow_id`
- `workflow_package_id`
- `worker_agent_id`
- `desired_status`
- `observed_status`
- `runtime_instance_id`
- `env_json`
- `credential_refs_json`
- `params_json`
- `package_checksum`
- `last_error_code`
- `last_error_message`
- audit fields

The control plane owns desired state. The worker agent reports observed state.

## UI Flow

### Workflows List

Add a `Workflows` console section.

The list page shows:

- workflow name
- validation status
- latest package version
- active deployment count
- last updated time
- primary actions: edit, upload code, validate, deploy

### Workflow Editor

The editor should be dense and operational, not a marketing-style builder.

MVP sections:

1. Source
   - connector type
   - connector config
   - polling or schedule config where relevant
2. Handler
   - uploaded code archive
   - handler entrypoint, for example `worker.tasks:handle`
   - optional handler params
3. Sink
   - sink type
   - sink config
4. Runtime
   - task name
   - concurrency
   - timeout
   - retry policy
   - dead-letter behavior if supported by the selected connector
5. Environment
   - non-secret environment variables
   - secret credential references
6. Deploy
   - selected worker agent
   - package version
   - deploy or restart action

The page should make the state clear:

- Draft configuration has not been packaged.
- Valid configuration can be packaged.
- Packaged versions are immutable.
- Deployments run a specific package version.

### Agent Selection

MVP agent selection is manual.

The deploy dialog should show:

- agent display name
- online/offline status
- used slots and max slots
- execution mode
- labels
- installed agent version
- last seen time

Disable deployment when the selected agent is offline or has no slots. The
backend still enforces this because UI state can be stale.

## Generated Package Format

The control plane builds a zip with a normalized structure:

```text
worker.yaml
manifest.json
src/
  ...
requirements.txt        # optional
pyproject.toml          # optional
```

`worker.yaml` is generated from the workflow configuration:

- `app.name` comes from the workflow name or a normalized runtime name.
- `reporter` is enabled for control-plane deployments.
- `resources` are generated from source and sink config.
- `tasks[]` contains one MVP task.
- `tasks[].handler.ref` points at the configured handler entrypoint.
- `tasks[].config` contains task config from the UI.
- retry, timeout, concurrency, and dead-letter policy map to OneStep YAML
  fields.

`manifest.json` records build metadata:

```json
{
  "schema_version": "onestep.workflow_package.v1",
  "workflow_id": "...",
  "workflow_package_id": "...",
  "code_upload_id": "...",
  "generated_at": "...",
  "entrypoint": "worker.yaml",
  "handler_ref": "worker.tasks:handle",
  "source_kind": "mysql_incremental",
  "sink_kind": "http_sink"
}
```

## Environment And Credentials

Environment variables split into two classes.

### Plain Environment Variables

Plain environment variables are non-secret values shown in the console and sent
to the worker agent as deployment `env`.

Examples:

- `APP_ENV=prod`
- `BATCH_SIZE=100`
- `LOG_LEVEL=INFO`

### Secret Credentials

Secrets are stored as credential references in the control plane.

MVP options:

1. Store encrypted credentials in the control-plane database and resolve them at
   deployment dispatch.
2. Store only references to an external secret system and require the worker
   host to resolve them.

Recommendation for MVP: use the existing control-plane encrypted credential
model if available, then send resolved values to the worker agent only at
deployment start.

Rules:

- Do not write resolved secret values into workflow package files.
- Do not persist resolved secret values in worker-agent deployment state.
- Do not include secret values in deployment events, logs, telemetry, or
  topology descriptors.
- If a deployment is restarted by the control plane, send the resolved secret
  values again in the command payload.
- If a worker-agent process restarts and only recovers an already-running child
  process, it should report the child as recovered without needing the secret
  values again.

The current worker-agent code records `credential_refs` for audit but does not
inject credential values. This design requires a coordinated follow-up: either
send resolved credential values in `env`, or add a separate sensitive env block
to the start-deployment command.

## API Design

Add authenticated console APIs under `/api/v1`.

Workflow APIs:

- `GET /api/v1/workflows`
- `POST /api/v1/workflows`
- `GET /api/v1/workflows/{workflow_id}`
- `PUT /api/v1/workflows/{workflow_id}`
- `DELETE /api/v1/workflows/{workflow_id}`
- `POST /api/v1/workflows/{workflow_id}/validate`

Code upload APIs:

- `POST /api/v1/workflows/{workflow_id}/code-uploads`
- `GET /api/v1/workflows/{workflow_id}/code-uploads`

Package APIs:

- `POST /api/v1/workflows/{workflow_id}/packages`
- `GET /api/v1/workflows/{workflow_id}/packages`
- `GET /api/v1/workflow-packages/{workflow_package_id}`
- `GET /api/v1/workflow-packages/{workflow_package_id}/download`

Deployment APIs:

- `POST /api/v1/workflow-packages/{workflow_package_id}/deployments`
- `GET /api/v1/workflow-deployments`
- `GET /api/v1/workflow-deployments/{deployment_id}`
- `POST /api/v1/workflow-deployments/{deployment_id}/stop`
- `POST /api/v1/workflow-deployments/{deployment_id}/restart`

Naming can reuse existing worker-agent package and deployment routes if those
already exist. The important product distinction is that the console deals with
workflows, packages, and deployments, not a generic `work` object.

## Validation

Validation happens in two layers.

### Workflow Validation

Runs before package creation.

Checks:

- required source fields are present
- required sink fields are present
- handler ref is syntactically valid
- selected credential refs exist
- plain env keys are valid environment variable names
- source and sink config can be rendered into OneStep YAML

### Package Validation

Runs during package creation or deploy.

Checks:

- upload archive is safe
- generated package contains `worker.yaml`
- dependency declarations are valid enough for the worker-agent install mode
- `onestep check --strict worker.yaml` passes before the deployment starts

The backend should expose user-fixable validation errors as structured 422
responses. Worker-agent `onestep check` failures should be attached to the
deployment timeline.

## Worker-Agent Protocol Changes

This feature affects the worker-agent protocol and must be coordinated with
`onestep-worker-agent`.

The existing `start_deployment` command already carries most required fields:

- `deployment_id`
- `package_checksum`
- `download_url`
- `entrypoint`
- `env`
- `params`
- `credential_refs`

Required changes or clarifications:

1. Treat `env` as the full process environment overlay for the deployment.
2. Decide how sensitive env values are represented. Either:
   - merge resolved secrets into `env` and mark them sensitive only in the
     control plane, or
   - add `secret_env` to the command payload and ensure it is never persisted or
     reported.
3. Preserve `params` for audit and future runtime support, but do not rely on it
   until OneStep has a runtime params entrypoint.
4. Include package manifest metadata in deployment events only when values are
   non-secret.
5. Keep unknown fields tolerated so newer control-plane commands do not break
   older agents immediately.

The first implementation should prefer additive fields over changing existing
field meaning.

## Runtime Reporter Behavior

Generated `worker.yaml` should enable the built-in OneStep reporter for
deployments launched by the control plane.

The worker agent injects runtime identity values already supported by the
runtime path:

- `ONESTEP_DEPLOYMENT_ID`
- `ONESTEP_WORKER_AGENT_ID`
- `ONESTEP_RUNTIME_INSTANCE_ID`

The generated reporter config or injected environment should also provide:

- control-plane URL
- reporter token or connection credential
- service name
- environment label

This lets the existing service, instance, task definition, task event, and
metric views continue to be populated by the runtime reporter instead of by the
worker-agent control channel.

## Security Boundary

MVP assumes trusted internal users and trusted worker hosts.

That must be explicit. Uploading and running arbitrary Python code is remote
code execution on the selected worker host.

MVP protections:

- console authentication required
- package upload authorization required
- deployment authorization required
- upload size and file count limits
- zip path traversal protection
- immutable package checksum verification
- secret redaction in logs and event payloads
- audit records for upload, package creation, deployment, stop, and restart

Out of scope for MVP:

- untrusted user isolation
- tenant isolation
- container sandboxing
- CPU and memory enforcement
- network egress policy
- syscall restriction

If OneStep is later offered as a multi-tenant service, container or sandboxed
execution becomes mandatory before accepting arbitrary uploads.

## Error Handling

Expected user-fixable errors:

- missing handler ref
- handler module cannot be imported
- invalid source or sink config
- missing credential reference
- invalid env var key
- package too large
- unsafe zip path
- dependency install failure
- `onestep check` failure
- selected agent offline
- no deployment slots available

Each error should have:

- stable error code
- short user-facing message
- detailed technical message where safe
- pointer to the config section that needs attention when applicable

Deployment failures should appear in the deployment timeline and on the
workflow detail page.

## Testing

Backend tests:

1. Workflow CRUD persists source, handler, sink, env, and credential refs.
2. Code upload rejects unsafe zip paths and oversized archives.
3. Workflow validation returns structured 422 errors for missing required
   config.
4. Package creation generates a zip with `worker.yaml`, `manifest.json`, and
   uploaded source files.
5. Generated `worker.yaml` passes `onestep check --strict` for a minimal
   interval-source handler workflow.
6. Package versions are immutable.
7. Deployment creation targets a specific online worker agent.
8. Secret values are never returned from read APIs.

Worker-agent tests:

1. Start-deployment command injects plain env into the subprocess.
2. Secret env, if added, is injected into the subprocess but not persisted in
   local deployment state.
3. Existing dependency install behavior still works for generated packages.
4. Deployment events redact sensitive values.
5. Older command payloads without new fields still work.

Frontend tests:

1. Workflows navigation item renders.
2. Workflow list renders empty, loading, and populated states.
3. Workflow editor captures source, handler, sink, env, and agent selection.
4. Upload flow shows archive validation failures.
5. Deploy dialog disables offline agents and full agents.
6. Deployment timeline renders check, install, running, failed, stopped states.

End-to-end smoke:

1. Start local control plane.
2. Start local worker agent.
3. Upload minimal handler zip.
4. Configure interval source and passthrough or simple handler.
5. Package workflow.
6. Deploy to the worker agent.
7. Wait for `running`.
8. Verify runtime reporter creates service/task/instance records.
9. Stop deployment.

## Implementation Phases

### Phase 1: Package From UI Config

- Add workflow and code upload models.
- Add backend package builder that generates `worker.yaml`.
- Add workflow validation and package creation APIs.
- Add focused backend tests.

No worker-agent protocol changes are required if deployments only use plain
`env` and no resolved credentials.

### Phase 2: Deploy From Workflow Package

- Add workflow deploy UI.
- Connect package versions to existing worker deployment creation.
- Show deployment status and timeline from existing worker deployment events.
- Add frontend tests.

This phase coordinates with worker-agent behavior already present.

### Phase 3: Credential Injection

- Decide `env` versus `secret_env` payload shape.
- Update control plane command dispatch.
- Update worker-agent injection and local state persistence rules.
- Add redaction and restart tests.

This phase requires control-plane and worker-agent coordination.

### Phase 4: Agent Matching

- Add agent labels and capability filters to deploy dialog.
- Recommend matching agents but keep manual override.
- Do not add automatic scheduling yet.

## Success Criteria

The feature is ready when:

1. A user can upload a Python handler zip from the console.
2. A user can configure source, handler, sink, env vars, and credential refs.
3. The backend can generate an immutable workflow package.
4. The generated package passes `onestep check --strict`.
5. A user can select an online worker agent and deploy the package.
6. The worker agent runs the deployment through the existing subprocess path.
7. Runtime reporter data appears in the existing service/task/instance views.
8. Stop and restart work from the control plane.
9. Secrets are not written into packages, read APIs, event payloads, or worker
   local state.

## Open Questions

1. Should the product label be `Workflows` or keep the existing `Pipelines`
   language?
2. Should MVP support only one task per workflow, or allow multiple UI-defined
   tasks before deployment?
3. Should secrets be sent as merged env or a separate `secret_env` command
   field?
4. Should dependency installation be allowed by default, or require an agent
   capability flag because it executes package-provided install logic?
5. Should code upload accept only installable Python packages, or also loose
   `src/` trees with `requirements.txt`?

