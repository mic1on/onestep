# onestep Local Handler Loop Design

## Summary

P2 adds a local diagnostic loop for running one task delivery, replaying a
captured production failure, checking resource connectivity, and optionally
capturing runtime failures to versioned files.

The implementation must reuse production delivery execution semantics without
changing the existing remote manual-run behavior. Python owns handler logic;
YAML remains a wiring and runtime-policy layer.

The committed CLI surface is:

```text
onestep task run <target> --task <name> --input <json-file> [--send] [--json]
onestep task replay <target> --task <name> --envelope <capture-file> [--send] [--json]
onestep check <target> --connect [--connect-timeout 10] [--json]
```

## Current State

`TaskRunner` currently owns both polling/concurrency control and single-delivery
execution. Its `_handle_delivery()` method invokes task hooks, the handler,
conditional sink routes, sink sends, acknowledgement, retry, dead-letter, and
task-event emission.

`OneStepApp.run_task_once()` already creates a synthetic delivery and calls the
same `TaskRunner` path. The control-plane WebSocket implementation calls this
method directly for remote manual runs, so its signature, return value,
exceptions, eligibility checks, and event behavior are compatibility
commitments for this work.

The current gaps are:

- there is no public local task-run or replay CLI;
- `run_task_once()` is coupled to remote manual-run eligibility;
- local execution cannot suppress framework-managed sink writes while retaining
  handler, hook, and route behavior;
- there is no versioned, redacted envelope capture format;
- `check` validates configuration but does not test resource connectivity;
- task events do not contain delivery bodies, so failure capture cannot be
  implemented safely as a generic event observer.

## Goals

- Support Python `module:app` and YAML worker targets equally.
- Execute one synthetic delivery through the same handler, task hooks, route
  selection, timeout, and retry-policy decision logic as production.
- Default local execution to dry-run for framework-managed sinks.
- Make real sink sends an explicit `--send` opt-in.
- Replay a versioned capture without requiring a running worker or control plane.
- Capture terminal runtime failures by default, with an option to capture every
  failed attempt.
- Redact resource secrets and configured payload paths before persistence.
- Check all configured resources for open/close connectivity without fetching
  deliveries or starting task runners.
- Preserve existing stable APIs, delivery ordering, task-event semantics,
  reporter payloads, remote controls, and WebSocket behavior.

## Non-Goals

- No general side-effect sandbox for handler or hook code.
- No multi-delivery batch replay.
- No YAML transformation or workflow DSL.
- No Control Plane capture download or replay UI.
- No delivery/attempt correlation fields, source lag metrics, recovery audit
  records, or reporter protocol changes. Those belong to P3.
- No change to the existing remote `run_task_once()` contract.
- No modification of `Envelope` or `TaskEvent` fields in P2.

## Architecture

Extract single-delivery execution into an internal `DeliveryExecutor`. Keep
fetching and worker lifecycle behavior in `TaskRunner`.

```text
Production Source
      |
      v
  TaskRunner --------------------------+
  fetch/concurrency/stop controls      |
                                       v
CLI input/capture -> DiagnosticRunner -> DeliveryExecutor
                                       | handler
                                       | task hooks
                                       | timeout
                                       | route selection
                                       | retry decision
                                       |
                         +-------------+-------------+
                         |                           |
                  production policy          diagnostic policy
                  real delivery actions      synthetic actions
                  app.emit_event()            local event collector
                  real sink sends             collect or --send
```

### Compatibility Facade

`TaskRunner` remains importable from `onestep.runtime`. Preserve:

- `TaskRunner(app, task)` construction;
- the existing runner lifecycle properties and methods;
- the current `_handle_delivery()` behavior as a delegating compatibility
  entry point;
- production event ordering and delivery action ordering.

`OneStepApp.run_task_once()` retains its current public signature and result
shape. It may reuse the extracted executor internally, but this refactor must be
covered by characterization tests before behavior moves.

### DeliveryExecutor

`DeliveryExecutor` is internal and is not exported from `onestep`.

It accepts explicit collaborators for:

- event emission;
- sink dispatch;
- delivery actions;
- optional failure capture.

Production collaborators retain the current behavior. Diagnostic collaborators
collect local events and intended delivery actions without invoking the
reporter or source acknowledgement callbacks.

The executor tracks the active failure stage:

- `before_hook`;
- `handler`;
- `after_success_hook`;
- `route`;
- `sink`;
- `ack`;
- `dead_letter`;
- `delivery_action`.

This stage is diagnostic metadata only. It does not add task-event kinds or
change retry classification.

### DiagnosticRunner

`DiagnosticRunner` is internal and is used by the CLI.

It:

- resolves a task by name;
- creates a synthetic delivery from input or a capture;
- executes one attempt through `DeliveryExecutor`;
- collects the handler result, selected sinks, local task events, resolved
  retry decision, failure stage, and duration;
- never starts source polling;
- never invokes app-level startup or shutdown hooks;
- does not attach, start, or flush a reporter;
- opens a selected sink immediately before a real `--send` and closes opened
  resources in reverse order.

Task-level before, success, and failure hooks do execute. Handler and hook code
may perform external writes directly; the CLI cannot sandbox those effects and
must warn before every task run or replay.

## Local Execution Semantics

Local diagnostics execute exactly one attempt. They do not loop or sleep for a
configured retry delay.

On failure, the executor evaluates the existing retry policy with the captured
attempt count and reports one of:

- `would_retry`;
- `would_dead_letter`;
- `would_fail`.

This makes a production attempt reproducible without making the CLI wait through
production backoff windows. Existing remote `run_task_once()` keeps its current
multi-attempt behavior.

### Dry-Run

Dry-run is the default.

- Handler and task hooks execute.
- Conditional route predicates execute.
- Selected sink names and output envelopes are recorded.
- Configured emit and dead-letter sinks are not opened or called.
- Synthetic delivery actions are recorded rather than sent to a source.

### Send Mode

`--send` opts into framework-managed external writes.

- Selected emit sinks are opened, called, and closed.
- If execution resolves to dead-letter, configured dead-letter sinks may be
  opened and called.
- The CLI prints an explicit side-effect warning even when `--json` is used;
  machine-readable output carries the same warning as structured data.
- Synthetic source `ack()`, `retry()`, and `fail()` remain local observations;
  no source delivery exists to mutate.

## CLI Contract

### `task run`

```text
onestep task run <target> --task <name> --input <json-file> [--send] [--json]
```

The input file must contain one JSON value. That value becomes `Envelope.body`.
The synthetic envelope uses empty metadata and `attempts=0`.

### `task replay`

```text
onestep task replay <target> --task <name> --envelope <capture-file> [--send] [--json]
```

Replay validates the capture schema and version, verifies that its app and task
match the selected target, then reconstructs body, metadata, and attempts.
Cross-task replay is rejected in P2.

### `check --connect`

```text
onestep check <target> --connect [--connect-timeout 10] [--json]
```

Connectivity checking:

- performs the normal target load and optional strict YAML validation first;
- deduplicates shared resource objects by identity;
- calls each resource's `open()` and `close()` without fetching or sending;
- checks resources sequentially to avoid connection storms and preserve clear
  attribution;
- applies the timeout independently to each resource;
- continues after a resource failure so the report includes every resource;
- always attempts reverse-order cleanup for successfully opened resources;
- does not run app hooks or task runners.

### Output And Exit Codes

Human-readable output is the default. `--json` returns a versioned report:

```json
{
  "schema": "onestep/diagnostic-result",
  "version": 1,
  "operation": "run",
  "app": "billing-sync",
  "task": "sync_users",
  "mode": "dry-run",
  "completion": "succeeded",
  "attempts": 0,
  "selected_sinks": ["warehouse"],
  "delivery_action": "would_ack",
  "events": [],
  "warning": "handler and task hooks may perform external side effects"
}
```

Exit codes retain the existing CLI convention:

- `0`: successful execution or all connectivity checks passed;
- `1`: handler execution, sink send, cleanup, or connectivity failure;
- `2`: argument, target, configuration, input, capture-version, or capture-task
  validation failure.

## Failure Capture

### Configuration

Failure capture is disabled by default.

Python applications opt in with an additive stable API:

```python
from onestep import FailureCaptureConfig, OneStepApp

app = OneStepApp(
    "billing-sync",
    failure_capture=FailureCaptureConfig(
        directory=".onestep/captures",
        mode="terminal",
        max_bytes=1_048_576,
        redact_paths=("/body/password",),
    ),
)
```

YAML applications use runtime policy under `app`:

```yaml
app:
  name: billing-sync
  failure_capture:
    directory: .onestep/captures
    mode: terminal
    max_bytes: 1048576
    redact_paths:
      - /body/password
```

`mode` is either:

- `terminal`: capture only after the runtime confirms the delivery will not be
  retried;
- `all`: capture every failed attempt that produces a retry or terminal
  decision.

### Capture Format

Capture files do not reuse or modify the `Envelope` dataclass schema.

```json
{
  "schema": "onestep/envelope-capture",
  "version": 1,
  "captured_at": "2026-07-30T10:00:00Z",
  "app": "billing-sync",
  "task": "sync_users",
  "stage": "handler",
  "terminal": true,
  "failure": {
    "kind": "error",
    "exception_type": "ValueError",
    "message": "invalid record"
  },
  "envelope": {
    "body": {},
    "meta": {},
    "attempts": 2
  },
  "redacted_paths": []
}
```

The codec supports JSON values plus tagged datetime, UUID, and bytes values.
Unsupported values fail capture explicitly. The writer must not persist a lossy
record that cannot reproduce the delivery.

### Capture Timing

Capture occurs inside the delivery executor because task events deliberately do
not carry payload bodies.

For terminal mode, capture only after the resolved delivery action is known:

- a successful dead-letter publish followed by a successful fail action is
  terminal;
- a dead-letter publish failure that retries the original delivery is not
  terminal;
- a `delivery.fail()` failure that falls back to retry is not terminal.

All mode records these retrying failures as non-terminal captures.

Capture persistence runs through `asyncio.to_thread()` so filesystem work does
not block the event loop. The delivery waits for the capture result so a reported
successful capture is durable before the runtime proceeds.

## Security

- Capture directories are created with owner-only permissions where supported.
- Capture files use mode `0600` and a temporary file plus atomic replacement.
- Existing symbolic-link directories or file targets are rejected.
- File names use capture time and a random identifier, never payload values.
- Common case-insensitive credential keys are redacted by default, including
  password, secret, token, authorization, cookie, API key, and DSN variants.
- User JSON Pointer paths provide workload-specific payload redaction.
- Redacted paths are recorded in the capture metadata without recording the
  removed values.
- Captures remain sensitive operational artifacts even after redaction. The
  documentation must state that payload PII may remain and that capture
  directories require normal retention and access controls.
- Official connector connectivity errors use normalized public error fields.
- Unknown plugin exceptions expose the exception type but hide the raw message
  by default, because it may contain credentials or DSNs.

## Error Handling

| Codepath | Failure | Behavior | User visibility |
| --- | --- | --- | --- |
| Input load | invalid JSON or unreadable file | exit 2 | exact file and validation error |
| Replay load | unsupported schema/version | exit 2 | supported version and received version |
| Replay validation | app/task mismatch | exit 2 | expected and captured identity |
| Handler/hook/route | exception or timeout | compute one retry decision | local failure report with stage |
| Dry-run sink | output cannot be represented | exit 1 | serialization failure without lossy output |
| Send sink | normalized connector failure | preserve error kind | redacted backend/operation/kind |
| Connectivity open | timeout or connector failure | record failure, continue | per-resource result |
| Resource close | close failure | record failure, continue cleanup | per-resource cleanup result |
| Capture encode | unsupported value or size limit | do not write | ERROR log with app/task/stage |
| Capture persist | permissions, disk full, atomic-write error | do not change delivery action | ERROR log with safe path context |

Capture failure must never change production acknowledgement, retry, fail, or
dead-letter decisions. It must also never be silently ignored.

## Compatibility

This is an additive core feature suitable for a minor release.

- Existing `OneStepApp` constructor calls continue to work because
  `failure_capture` is keyword-only and defaults to `None`.
- Existing YAML remains valid; `app.failure_capture` is additive.
- Existing `run`, `check`, `init`, `build`, and `catalog` command behavior is
  unchanged.
- `TaskRunner` retains its constructor and compatibility entry point.
- `run_task_once()` retains its current remote-control contract.
- Existing `Envelope` and `TaskEvent` fields and semantics are unchanged.
- Reporter payloads, lifecycle event kinds, WebSocket messages, runtime identity,
  and Control Plane storage are unchanged.
- Capture is disabled unless configured, so existing workers gain no filesystem
  writes or new failure-path latency.

Strict YAML files using `app.failure_capture` require a P2-capable runtime. Older
runtimes will reject the unknown field in strict mode; this is expected forward
configuration incompatibility, not a change to existing configurations.

## Testing Strategy

### Production Characterization

Before extracting the executor, add or identify tests that lock down:

- handler and task-hook ordering;
- conditional route selection;
- sink send before source acknowledgement;
- success event after acknowledgement;
- handler, hook, predicate, sink, timeout, and acknowledgement failure paths;
- retry, dead-letter, fail, cancellation, and fallback-retry behavior;
- event kinds, attempts, duration, failure classification, and metadata.

The same tests must pass unchanged after extraction.

### DiagnosticRunner

Cover:

- Python and YAML targets;
- dry-run does not open or call emit/dead-letter sinks;
- `--send` opens, sends, and closes selected sinks;
- synchronous and asynchronous handlers and task hooks;
- conditional true/false routes and multiple selected sinks;
- one-attempt execution and `would_retry`/`would_dead_letter`/`would_fail`;
- captured attempts participating in retry-policy resolution;
- local events never reaching an attached reporter;
- exception, timeout, cancellation, and reverse-order cleanup paths.

### Capture Codec And Writer

Cover:

- JSON, datetime, UUID, and bytes round trips;
- unsupported and oversized values;
- built-in nested credential-key redaction;
- configured JSON Pointer paths, including lists and missing paths;
- owner-only directory and file permissions;
- atomic replacement, collisions, disk errors, and symbolic-link rejection;
- terminal versus all capture modes;
- dead-letter and fail-action fallback-to-retry classification;
- capture failures preserving the original delivery action.

### CLI And Connectivity

Cover:

- command parsing and help;
- human and JSON reports;
- exit codes 0, 1, and 2;
- invalid input, schema version, task identity, and target loading;
- all-resource connectivity results after partial failure;
- per-resource timeout and reverse-order cleanup;
- shared resource identity deduplication;
- normalized official-connector errors and hidden unknown-plugin messages.

### Validation Gates

```bash
uv run pytest -q tests/test_cli.py tests/test_diagnostics.py tests/test_failure_capture.py
uv run pytest -q tests/contract/test_runtime_contract.py
uv run pytest -q -m "not integration"
./scripts/run-reliability-checks.sh
```

## Documentation And Release

- Release core as a minor version, expected to be `1.8.0` if no earlier minor is
  released first.
- Update `CHANGELOG.md` with the new CLI, capture policy, compatibility statement,
  and side-effect warning.
- Document the commands in the English and Chinese READMEs.
- Extend `docs/yaml-task-definition.md` with `app.failure_capture`.
- Mark P2 complete in `docs/framework-evolution-roadmap.md` only after all exit
  gates pass.
- Update `uv.lock` as part of the core version bump.
- No plugin version bump is required because plugin contracts do not change.
- No Control Plane image rebuild is required because P2 changes no Control Plane
  code or baked assets.

## P3 Boundary

P3 begins only after the P2 capture and diagnostic APIs are proven. Its separate
design will cover stable delivery correlation across retries and dead-letter
replay, attempt identity, reporter payloads, Control Plane migration, API/UI
rendering, lag metrics, and audited recovery operations.

P3 must coordinate runtime, the control-plane reporter plugin, backend schema,
frontend types and views, and image rebuild/restart in one compatibility window.

## Success Criteria

P2 is complete when:

- both Python and YAML targets support local run and replay;
- dry-run never invokes framework-managed sinks;
- a captured terminal handler failure can be replayed without a worker or
  Control Plane;
- capture files are versioned, bounded, redacted, private, atomic, and lossless;
- connectivity checks report every configured resource without fetching work;
- local diagnostic events never reach the Control Plane;
- production delivery and remote manual-run contracts remain unchanged;
- focused, non-integration, and all-plugin reliability gates pass.
