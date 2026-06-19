# Worker Reporting Config Design

Date: 2026-06-19
Status: Ready for user review
Owner: Codex

## Summary

Add first-class Worker data reporting configuration in the Control Plane.
Reporting is enabled by default for Workers deployed through the platform. In
the default platform mode, users do not enter a reporting endpoint or token:
the worker-agent injects the Control Plane URL and its connection token at
runtime, and the compiled `worker.yaml` only needs `reporter: true`.

Users can also switch to a custom reporting endpoint and token from the Worker
configuration page. Custom tokens are stored encrypted and never returned in API
responses. They are injected only into the deployment runtime environment.

## Goals

- Make data reporting a visible Worker setting instead of hidden YAML compiler
  behavior.
- Enable reporting by default for platform-deployed Workers.
- Avoid asking users to manually fill platform reporting URL/token environment
  variables.
- Allow advanced users to specify a custom endpoint URL and token.
- Store custom reporting tokens encrypted and never expose token plaintext in
  read APIs.
- Keep deployment behavior compatible with the existing worker-agent runtime
  environment injection.

## Non-Goals

- No change to the runtime reporter wire protocol.
- No new ingestion authentication scheme.
- No redesign of the Worker editor beyond the reporting setting controls.
- No token rotation workflow or generated token management UI.
- No change to connector secrets beyond reusing the existing Fernet-backed
  secret storage pattern.

## Data Model

The `workers` table gains:

- `reporting_enabled boolean not null default true`
- `reporting_config_json json not null default {}`
- `reporting_secret_encrypted text null`

`reporting_config_json` stores non-secret reporting configuration:

```json
{
  "mode": "platform",
  "endpoint_url": null
}
```

`mode` is either `platform` or `custom`. `endpoint_url` is required only for
custom mode. `reporting_secret_encrypted` stores encrypted custom reporting
secret data, currently `{ "token": "..." }`. Platform mode does not store a
token.

API summaries include `reporting_enabled`, `reporting_config`, and a derived
`reporting_token_configured` boolean. They do not include token plaintext.

Create/update requests accept:

- `reporting_enabled?: boolean`
- `reporting_config?: { mode: "platform" | "custom"; endpoint_url?: string }`
- `reporting_secret?: { token?: string }`

For updates, omitting `reporting_secret.token` keeps the existing token.
Sending a new non-empty token replaces it. Switching back to platform mode
clears the custom secret.

## Backend Behavior

Worker creation defaults reporting to enabled platform mode when the request
omits reporting fields.

Worker update validates:

- disabled reporting can use any stored config but emits no reporter config.
- platform mode ignores endpoint URL and clears custom token.
- custom mode requires a non-empty endpoint URL.
- custom mode requires a token on create, or an existing encrypted token on
  update.

The Worker compiler changes from unconditional `reporter: true` to:

- reporting disabled: omit `reporter`
- enabled platform mode: `reporter: true`
- enabled custom mode:

```yaml
reporter:
  base_url: https://example.invalid
  token: "${ONESTEP_WORKER_REPORTING_TOKEN}"
```

During Worker deployment, the Control Plane builds the deployment environment:

- platform mode: do not add reporting URL/token; worker-agent already injects
  `ONESTEP_CONTROL_PLANE_URL` and `ONESTEP_CONTROL_PLANE_TOKEN`.
- custom mode: decrypt the custom token and add
  `ONESTEP_WORKER_REPORTING_TOKEN` to the deployment env.

User-provided env vars continue to work, but deployment-owned reporting env
vars take precedence for the custom reporting token to prevent a stale manual
value from silently overriding the Worker setting.

## Frontend Behavior

The Worker editor adds a reporting section in the configuration surface:

- An enabled toggle, default on for new Workers.
- A mode control:
  - Current Control Plane
  - Custom Endpoint
- Custom endpoint URL input.
- Custom token input that never displays the saved token. When a token is
  already configured, the UI shows a configured state and lets users replace it.

The existing environment variables UI remains for application-specific env
vars. It should not ask users to enter platform reporting URL/token values.

## Security

Custom reporting tokens are encrypted with the existing Fernet-backed secret
storage pattern. Plaintext is used only while handling the create/update request
or when building a deployment command environment.

API responses return only token presence, never plaintext. Tests should assert
that the token is not present in the serialized Worker response, database
config JSON, deployment summaries, or generated `worker.yaml`.

## Compatibility

Existing Workers behave as reporting-enabled platform Workers after migration.
This preserves the current local compiler behavior, which already emits
`reporter: true` in the uncommitted Worker compiler changes.

Existing worker-agents already inject platform reporter URL/token and the
Control Plane already accepts worker-agent connection tokens for ingestion, so
platform mode does not require additional runtime protocol changes.

## Verification

- Backend:
  - Worker create/update/list/get tests for default platform reporting.
  - Worker custom reporting tests covering encrypted token storage, masked
    responses, token preservation on update, and platform-mode secret clearing.
  - Worker compiler tests for disabled/platform/custom YAML output.
  - Deploy tests confirming custom token env injection and platform mode relying
    on worker-agent injection.
- Frontend:
  - Worker editor tests for default enabled reporting, switching to custom
    endpoint, saving a replacement token, and not showing saved token plaintext.
  - Frontend build.
- Existing suites:
  - `uv run pytest backend/tests/test_workers_api.py backend/tests/test_worker_compiler.py`
  - `pnpm --dir frontend exec vitest run src/pages/workers/WorkerEditorPage.test.tsx`
  - `pnpm --dir frontend build`

## Scope Boundary

This change spans Worker schemas, Worker persistence, Worker YAML compilation,
deployment env assembly, and the Worker editor. It should not change task
telemetry ingestion semantics, agent WebSocket protocol behavior, connector
catalogs, or unrelated page styling.
