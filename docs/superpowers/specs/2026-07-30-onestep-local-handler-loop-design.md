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
onestep task run <target> --task <name> --input <json-file> [--send] [--timeout 60] [--json]
onestep task replay <target> --task <name> --envelope <capture-file> [--send] [--timeout 60] [--json]
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
- Attempt to capture terminal runtime failures by default, with an option to
  attempt capture for every failed attempt.
- Redact resource secrets and configured payload paths before persistence.
- Include every configured resource in connectivity reports, probe resources
  with an explicit lifecycle without fetching, reading, or sending, and clearly
  identify resources that cannot be probed safely.
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
CLI supervisor -> diagnostic worker -> DiagnosticRunner -> DeliveryExecutor
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

### Diagnostic Process And Overall Timeout

`task run` and `task replay` execute the diagnostic session in a fresh child
process supervised by the CLI parent. The child uses the `spawn` start method so
it does not inherit event-loop, app, thread, or connector state. Process
isolation is required because a blocking synchronous handler, hook, route
predicate, plugin call, or native library cannot be reliably interrupted by an
asyncio timeout in the same process.

The parent creates two private one-way pipes for each spawned child: a control
pipe from parent to child and a status pipe from child to parent. Pipe messages
are UTF-8 JSON frames sent with `Connection.send_bytes()` and
`Connection.recv_bytes()`, not pickled Python objects and not stdout. Every
frame contains the internal schema `onestep/diagnostic-ipc`, version `1`, and a
monotonically increasing sequence for its direction. The status pipe supports:

- `checkpoint`: the child sends this immediately before each potentially
  blocking phase and after each externally visible send or cleanup transition.
  It contains the current phase, transition (`entered` or `completed`), elapsed
  time, and only safe partial result fields accumulated so far;
- `final`: the complete versioned diagnostic result, sent only after normal
  cleanup finishes.

The control pipe accepts only a `cancel` frame. Unknown schemas, versions,
kinds, non-monotonic sequences, or malformed JSON are protocol failures. The
parent discards the invalid frame, terminates the child, and synthesizes
`child_failed` from the last previously validated checkpoint.

Target, request, capture, and task validation run only in the supervised child.
A validation error produces a complete `final` report with
`completion: validation_failed`; the parent maps that result to exit code `2`.
The parent never imports a replay target or decodes its capture before starting
the deadline, so import side effects occur once and target loading cannot bypass
the overall timeout.

Checkpoint phases cover child startup, target loading, validation, before hook,
handler, success/failure hook, routing, each sink send, dead-letter publication,
synthetic delivery action, and cleanup. The partial-field allowlist is limited
to app/task/resource identifiers, selected sink names, completion booleans,
elapsed time, and cleanup status. Checkpoints must not contain envelope bodies,
exception messages, credentials, or arbitrary object representations.
The parent continuously drains and validates the status pipe while supervising
the deadline, retaining the highest valid checkpoint sequence. Child stdout and
stderr are forwarded to parent stderr; stdout is reserved for the parent's final
human or JSON report and is never used as IPC.

This IPC is local and private to a parent and child from the same installed
onestep version. It is not an exported API, reporter payload, WebSocket message,
or Control Plane protocol.

`--timeout` is a positive number of seconds and defaults to `60`. Its monotonic
deadline begins when the child starts and covers target loading, task
validation, handler and hook execution, routing, real sends, dead-letter work,
and normal cleanup. The task's existing `timeout_s` still applies to the handler
using production semantics; whichever deadline expires first wins.

On overall timeout, the parent sends a `cancel` control frame. A child-side
control listener schedules cancellation on the diagnostic event loop when it is
responsive, after which the child attempts reverse-order cleanup and may return
a final timeout report. The parent allows up to five additional seconds for
this cooperative path. If the child is still blocked, the parent forcibly
terminates it and reports cleanup as incomplete. This makes the maximum expected
wall time `--timeout + 5` seconds while acknowledging that forced termination
cannot run arbitrary plugin cleanup code.

Timeout produces a versioned diagnostic report with `completion` set to
`timed_out`, the last valid checkpoint retained by the parent, and cleanup
status. If the child never produced a checkpoint, the parent uses
`child_start`. A child exit or broken status pipe without a valid `final` frame
similarly produces a parent-authored `child_failed` report rather than partial
JSON. Timeout exits with code `1`; the parent always owns final rendering, so
`--json` emits exactly one valid JSON document even after forced termination.

In `--send` mode, timeout or forced termination may occur after an external sink
has accepted all or part of a write but before its `completed` checkpoint. That
outcome is inherently ambiguous and can leave partial fan-out, duplicates on a
later replay, or backend connections that close only when the operating system
reclaims the process. Any timeout with an entered but incomplete send sets
`side_effect_outcome` to `unknown` and repeats this warning in the result. This
is an explicit risk accepted by opting into `--send`; onestep cannot roll back
or prove the outcome of the interrupted external operation.

## Local Execution Semantics

Local diagnostics execute exactly one attempt. They do not loop or sleep for a
configured retry delay.

On failure, the executor evaluates the existing retry policy with the captured
attempt count and reports one of:

- `would_retry`;
- `would_dead_letter`;
- `would_fail`.

These values are diagnostic conclusions, not all equally observable outcomes.
In dry-run, `would_dead_letter` means that production would attempt dead-letter
publication; it becomes terminal only if every configured dead-letter publish
and the source delivery's `fail()` action succeed. The report therefore sets
`delivery_action_basis` to `predicted` and records that dead-letter publication
was not attempted.

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
- `would_dead_letter` is explicitly reported as a prediction conditional on
  successful dead-letter publication and source finalization.

### Send Mode

`--send` opts into framework-managed external writes.

- Selected emit sinks are opened, called, and closed.
- If execution resolves to dead-letter, configured dead-letter sinks may be
  opened and called.
- The CLI prints an explicit side-effect warning even when `--json` is used;
  machine-readable output carries the same warning as structured data.
- Synthetic source `ack()`, `retry()`, and `fail()` remain local observations;
  no source delivery exists to mutate.
- A successful dead-letter send is an observed result for this invocation, but
  the final source action remains synthetic. `--send` can confirm whether the
  configured dead-letter sinks accepted the capture in this run; it cannot
  guarantee that a production source's later `fail()` call would succeed.
- A timeout or forced child termination during a real send has an unknown
  external outcome. Retrying the diagnostic may duplicate a write that the
  backend committed before the timeout.

## CLI Contract

The current argument-normalization shim treats an unknown first token as the
legacy `run` shorthand. Implementation must add `task` to
`_normalize_argv()`'s known top-level command set before parsing nested
subcommands; otherwise `onestep task run ...` is silently rewritten as
`onestep run task run ...`. The legacy shorthand remains unchanged for other
unknown first tokens.

### `task run`

```text
onestep task run <target> --task <name> --input <json-file> [--send] [--timeout 60] [--json]
```

The input file must contain one JSON value. That value becomes `Envelope.body`.
The synthetic envelope uses empty metadata and `attempts=0`.

### `task replay`

```text
onestep task replay <target> --task <name> --envelope <capture-file> [--send] [--timeout 60] [--json]
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
- inventories named resources, app state, task sources, emit sinks, and
  dead-letter sinks, then deduplicates shared objects by identity while
  retaining every name and role in the result;
- probes only resources that expose callable `open()` and `close()` methods;
- calls `open()` and `close()` without fetching, reading, writing, or sending;
- reports state stores, cursor stores, and other resources without both
  lifecycle methods as `not_probeable`; this status is visible but does not
  make the command fail;
- never calls `load()`, `save()`, or `delete()` as a generic connectivity probe,
  because store operations may create schema or otherwise mutate a backend;
- checks resources sequentially to avoid connection storms and preserve clear
  attribution;
- applies `--connect-timeout` independently to each lifecycle operation;
- invokes synchronous lifecycle methods on a daemon call thread so a blocking
  method cannot pin the diagnostic event loop beyond that timeout;
- continues after a resource failure so the report includes every resource;
- always attempts bounded cleanup after an invoked `open()`, including an open
  failure or timeout that may have left a partially initialized resource;
- does not run app hooks or task runners.

Each resource result contains its aliases, roles, concrete type, probe kind
(`lifecycle` or `none`), status (`connected`, `failed`, or `not_probeable`), and
separate open/close outcomes when applicable. Exit code `0` means every
lifecycle probe passed; a report containing only `not_probeable` resources is
successful but carries an explicit warning that no connection was verified.

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
  "timeout_s": 60,
  "completion": "succeeded",
  "attempts": 0,
  "selected_sinks": ["warehouse"],
  "delivery_action": "would_ack",
  "delivery_action_basis": "predicted",
  "dead_letter": {"attempted": false, "published": null},
  "events": [],
  "warning": "handler and task hooks may perform external side effects"
}
```

For `task run` and `task replay`, `delivery_action` always describes the
synthetic source transition and `delivery_action_basis` is therefore
`predicted`. The separate `dead_letter` object distinguishes an unattempted
dry-run (`published: null`) from an observed `--send` success or failure
(`published: true` or `false`). This prevents a successful sink publication
from being presented as proof that a real source delivery was finalized.

`completion: validation_failed` is reserved for target, request, capture, or
task validation performed by the supervised child. It is a structured
exit-code `2` result, not a handler execution failure.

Exit codes retain the existing CLI convention:

- `0`: successful execution or all attempted lifecycle connectivity probes
  passed;
- `1`: handler execution, overall timeout, sink send, cleanup, or lifecycle
  connectivity failure;
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

- `terminal`: attempt capture only after the runtime confirms the delivery will
  not be retried;
- `all`: attempt capture for every failed attempt that produces a retry or
  terminal decision.

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

The codec uses collision-safe recursive type tags rather than `default=str` or
`repr()`. It supports JSON values plus these lossless extensions:

- `datetime`, UUID, and bytes;
- `Decimal`, encoded with its exact string form so exponent, trailing zeros,
  signed zero, infinities, and NaN forms round trip;
- plain tuple;
- enum members, encoded with module, qualified class name, member name, and
  encoded value;
- namedtuple instances, encoded with module, qualified class name, field names,
  and encoded field values;
- set and frozenset, with members ordered by their canonical encoded JSON so
  captures are deterministic.

Only exact built-in scalar and container types are accepted by the plain JSON
branches. Subclasses of `int`, `str`, `dict`, `list`, and the other supported
built-ins are rejected unless they have an explicit lossless extension such as
enum or namedtuple; silently converting a custom subtype to its base type would
make the capture lossy.

Enum and namedtuple reconstruction is allowed only when the class is importable
from the already loaded target module graph and its recorded metadata still
matches. Local classes, dataclasses, arbitrary custom classes, generators,
open handles, and other unsupported values fail capture explicitly. The error
identifies the JSON Pointer and type but never includes `repr(value)`.

`mode=all` is therefore best-effort rather than a promise that every failed
attempt produces a file. Every encode failure emits an ERROR log with app,
task, stage, safe type name, and unsupported path. The writer must not persist a
lossy or partial record that cannot reproduce the delivery.

### Capture Timing

Capture occurs inside the delivery executor because task events deliberately do
not carry payload bodies.

For terminal mode, capture only after the resolved delivery action is known:

- a successful dead-letter publish followed by a successful fail action is
  terminal;
- a dead-letter publish failure that retries the original delivery is not
  terminal;
- a `delivery.fail()` failure that falls back to retry is not terminal.

All mode attempts to record these retrying failures as non-terminal captures.

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
| Replay load | unsupported schema/version | child returns `validation_failed`; exit 2 | supported version and received version |
| Replay validation | target load or app/task mismatch | child returns `validation_failed`; exit 2 | report has safe exception type/stage; stderr retains validation detail |
| Handler/hook/route | exception or timeout | compute one retry decision | local failure report with stage |
| Overall diagnostic session | `--timeout` expires | cooperative cancel, bounded cleanup, then terminate if needed; exit 1 | `timed_out`, last stage, and cleanup status |
| Diagnostic child/IPC | child exits or status pipe breaks without `final` | parent synthesizes report; exit 1 | `child_failed` and last valid checkpoint |
| Dry-run sink | output cannot be represented | exit 1 | serialization failure without lossy output |
| Send sink | normalized connector failure | preserve error kind | redacted backend/operation/kind |
| Interrupted in-flight `--send` | external commit state cannot be known | do not claim rollback or retry safety | `side_effect_outcome: unknown` and duplicate/partial-write warning |
| Connectivity open | timeout or connector failure | record failure, continue | per-resource result |
| Resource close | close failure | record failure, continue cleanup | per-resource cleanup result |
| Resource without lifecycle | no callable `open()` and `close()` pair | do not perform a surrogate read/write probe | `not_probeable` with role and type |
| Capture encode | unsupported value or size limit | do not write | ERROR log with app/task/stage and safe type/path context |
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
- `_normalize_argv()` recognizes `task` as a top-level command so nested task
  commands bypass legacy shorthand rewriting. Consequently, the bare shorthand
  target name `task` becomes reserved; users of that exact target must write
  `onestep run task`. Other shorthand targets remain unchanged.
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
- predicted dry-run dead-letter versus observed `--send` publication, including
  publication failure and synthetic source finalization;
- captured attempts participating in retry-policy resolution;
- local events never reaching an attached reporter;
- task timeout, overall process timeout, cooperative cancellation, forced child
  termination, valid parent-rendered JSON, and reverse-order cleanup paths;
- blocking synchronous handler and hook fixtures that prove the CLI returns by
  `--timeout + 5` seconds;
- ordered IPC checkpoints for every blocking phase, malformed/truncated frame
  handling, child exit without `final`, and fallback to `child_start` when no
  checkpoint arrives;
- child stdout/stderr during `--json` without corruption of the single JSON
  document emitted on parent stdout;
- forced termination during `--send` reporting an unknown external side-effect
  outcome and the duplicate/partial-write warning.

### Capture Codec And Writer

Cover:

- JSON, datetime, UUID, bytes, Decimal, plain tuple, enum, namedtuple, set, and
  frozenset round trips;
- deterministic set ordering and collision-safe tags;
- unsupported local/custom classes and oversized values;
- codec failures logging safe type/path context without `repr()` or payload
  leakage;
- built-in nested credential-key redaction;
- configured JSON Pointer paths, including lists and missing paths;
- owner-only directory and file permissions;
- atomic replacement, collisions, disk errors, and symbolic-link rejection;
- terminal versus all capture modes;
- dead-letter and fail-action fallback-to-retry classification;
- capture failures preserving the original delivery action.

### CLI And Connectivity

Cover:

- command parsing and help, including `task` bypassing shorthand normalization,
  explicit `onestep run task`, and unchanged shorthand for other targets;
- human and JSON reports;
- exit codes 0, 1, and 2;
- invalid input, schema version, task identity, and target loading;
- all-resource inventory after partial lifecycle failure;
- `not_probeable` state/cursor stores, including proof that no store method is
  called and SQLAlchemy `auto_create` cannot run;
- per-operation connectivity timeout and bounded cleanup;
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
  the reserved bare `task` shorthand, and side-effect warning.
- Document the commands in the English and Chinese READMEs.
- Document that dry-run dead-letter actions are predictions, `--send` observes
  sink publication but not a real source finalization, and diagnostic commands
  have an overall timeout.
- Document that timeout or forced termination during `--send` has an unknown
  external outcome and may cause partial or duplicate writes on replay.
- Document the lossless codec's supported types and make the best-effort nature
  of `mode=all` explicit, including how unsupported custom types are reported.
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
- `task` commands bypass legacy argv rewriting while explicit `run` and other
  shorthand targets retain their behavior;
- dry-run never invokes framework-managed sinks;
- an unresponsive diagnostic child returns a parent-authored valid report with
  its last safe checkpoint within the documented deadline and cleanup grace;
- an interrupted in-flight `--send` is reported with an unknown external
  side-effect outcome rather than a false success or rollback claim;
- a captured terminal handler failure can be replayed without a worker or
  Control Plane;
- capture files are versioned, bounded, redacted, private, atomic, and lossless;
- connectivity checks represent every configured resource, probe only explicit
  lifecycle capabilities, and never use store reads or writes as a surrogate;
- local diagnostic events never reach the Control Plane;
- production delivery and remote manual-run contracts remain unchanged;
- focused, non-integration, and all-plugin reliability gates pass.
