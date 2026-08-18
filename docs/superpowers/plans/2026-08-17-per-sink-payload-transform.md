# Per-Sink Payload Transform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one task declare multiple output sinks that each receive either the handler result or a sink-specific Python payload projection, while preserving complete static topology and current delivery semantics.

**Architecture:** Add immutable EmitBinding beside EmitRoute and preserve YAML order in TaskSpec.emit_targets. The executor selects targets, prepares every binding body before the first Sink.send, and then sends prepared envelopes serially. TaskSpec.sinks remains the flattened lifecycle and compatibility view; task descriptions gain additive binding metadata.

**Tech Stack:** Python 3.9+, dataclasses, asyncio, pytest, OneStep YAML loader, VitePress Markdown.

---

## Fixed invariants

- Existing Python emit=Sink, emit=[Sink, ...], EmitRoute, YAML sink names, and conditional routes remain valid.
- New mappings are exactly {sink, transform?}; sink names one Sink and transform uses the existing callable-ref contract.
- For a non-None handler result, target selection and transforms follow YAML order. Every selected transform finishes before any sink send.
- Transform exceptions use a distinct transform stage and current retry/dead-letter behavior. timeout_s remains handler-only.
- Sink writes remain serial, at-least-once, and non-transactional. A prior success may replay after a later failure.
- No transformed conditional branches, parallel sends, per-Sink retry policy, or durable per-Sink completion in this release.

## File map

- src/onestep/task.py: binding model and ordered target normalization.
- src/onestep/config.py: binding YAML validation and resolution.
- src/onestep/runtime/executor.py: select, prepare, then send.
- src/onestep/app.py and src/onestep/cli.py: topology and summary details.
- tests/contract/test_runtime_contract.py: model and delivery contracts.
- tests/test_cli.py: YAML, strict validation, descriptions, CLI output.
- tests/test_diagnostics.py: transform-stage and inventory diagnostics.
- docs/yaml-task-definition.md, skills/onestep/references/yaml-task-definition.md, CHANGELOG.md: public contract.

## Task 1: Add the ordered output-binding model

**Files:**
- Modify: src/onestep/task.py:12-121
- Test: tests/contract/test_runtime_contract.py:1235-1267

- [ ] **Step 1: Write a failing model test.** Import EmitBinding; create emit=[audit, EmitBinding(sink=projected, transform=to_projected, transform_ref="tests.transforms:to_projected")]; assert target order is an audit EmitRoute followed by the binding, legacy emit_routes contains only the audit route, flattened emit_bindings contains audit then projected, and sinks == (audit, projected).

- [ ] **Step 2: Verify red.** Run:

  ~~~text
  uv run pytest tests/contract/test_runtime_contract.py::test_task_spec_keeps_emit_binding_order_and_legacy_route_view -q
  ~~~

  Expected: import or attribute failure because EmitBinding and TaskSpec.emit_targets do not exist.

- [ ] **Step 3: Implement the immutable model.** Add:

  ~~~python
  TaskTransform = Callable[["TaskContext", Any, Any], Any]

  @dataclass(frozen=True)
  class EmitBinding:
      sink: Sink
      transform: TaskTransform | None = None
      transform_ref: str | None = None
  ~~~

  Extend EmitTarget with EmitBinding. Add ordered TaskSpec.emit_targets and flattened emit_bindings. Normalize a bare Sink to the same unconditional EmitRoute used today; preserve explicit bindings/routes. Derive legacy emit_routes, flattened bindings, and sinks without changing declaration order. Keep a TypeError for every other entry type.

- [ ] **Step 4: Verify green and compatibility.** Run:

  ~~~text
  uv run pytest tests/contract/test_runtime_contract.py -q -k "emit_binding_model or task_spec_normalizes_unconditional_sink_to_emit_route or task_spec_flattens_conditional_route_sinks_for_compatibility"
  ~~~

  Expected: PASS.

- [ ] **Step 5: Commit.**

  ~~~text
  git add src/onestep/task.py tests/contract/test_runtime_contract.py
  git commit -m "feat: add emit binding task model"
  ~~~

## Task 2: Parse and strictly validate binding YAML

**Files:**
- Modify: src/onestep/config.py:89-92,678-728,919-952
- Test: tests/test_cli.py:1483-1685

- [ ] **Step 1: Write valid and invalid YAML tests.** A valid entry uses {"sink": "projected", "transform": {"ref": "testsupport_yaml_emit_binding:to_projected", "params": {"factor": 2}}}. Register callables, load strict config, and assert EmitBinding, preserved transform_ref, copied params, and mixed legacy/binding order. Add failures for missing sink, unknown key, missing transform.ref, non-Sink resource, and mixing sink with when/then.

- [ ] **Step 2: Verify red.** Run uv run pytest tests/test_cli.py -q -k "emit_binding". Expected: the mapping is misclassified as a conditional route.

- [ ] **Step 3: Implement strict dispatch.** Add _STRICT_EMIT_BINDING_FIELDS = frozenset({"sink", "transform"}). In _validate_emit, mappings with sink use _validate_emit_binding; all others retain route validation. Permit only those two keys, require a non-empty sink string, and validate transform through _validate_ref_entry.

- [ ] **Step 4: Implement resolution.** Rename _resolve_optional_emit_routes to _resolve_optional_emit_targets. Non-mappings still become unconditional routes; mappings with sink resolve one Sink and optional callable through _resolve_callable_ref; other mappings retain _resolve_emit_route. Return the ordered EmitBinding-or-EmitRoute tuple and pass it to app.task.

- [ ] **Step 5: Verify YAML and legacy routes.** Run:

  ~~~text
  uv run pytest tests/test_cli.py -q -k "emit_binding or conditional_emit or strict_rejects_emit_route"
  ~~~

  Expected: PASS.

- [ ] **Step 6: Commit.**

  ~~~text
  git add src/onestep/config.py tests/test_cli.py
  git commit -m "feat: parse per-sink emit transforms"
  ~~~

## Task 3: Prepare every output before sending any Sink

**Files:**
- Modify: src/onestep/runtime/executor.py:129-149,226-244
- Test: tests/contract/test_runtime_contract.py:1213-1382

- [ ] **Step 1: Write runtime success and barrier tests.** One unchanged binding and one async transform must receive different bodies. A second transform that raises must leave a recording first sink empty, proving preparation happens before the first side effect.

- [ ] **Step 2: Verify red.** Run uv run pytest tests/contract/test_runtime_contract.py -q -k "emit_binding". Expected: incorrect shared payloads or missing transform execution.

- [ ] **Step 3: Select ordered bindings.** Replace _select_emit_sinks with _select_emit_bindings. Iterate task.emit_targets; select explicit bindings as-is, evaluate route predicates with current sync/async behavior, and wrap chosen branch sinks as unchanged EmitBinding values.

- [ ] **Step 4: Add preparation.** Add _prepare_emit_envelopes(bindings, ctx, payload, result). For each binding, use result when transform is absent; otherwise call invoke_callback(transform, ctx, payload, result) and await when necessary. Return ordered (sink, Envelope(body=body)) pairs without sending.

- [ ] **Step 5: Integrate execution stages.** Keep route selection and selected_sinks checkpoints. Set active_stage to transform, checkpoint, prepare all pairs, then enter the current serial sink loop. Do not use asyncio.wait_for around transforms.

- [ ] **Step 6: Pin partial success.** Add a test where the first transformed sink succeeds and the second sink raises; assert the first write remains and the source is not acknowledged. This mirrors current non-transactional fan-out.

- [ ] **Step 7: Verify regressions.** Run:

  ~~~text
  uv run pytest tests/contract/test_runtime_contract.py -q -k "emit_binding or multi_sink_send_is_not_transactional or conditional_emit"
  ~~~

  Expected: PASS.

- [ ] **Step 8: Commit.**

  ~~~text
  git add src/onestep/runtime/executor.py tests/contract/test_runtime_contract.py
  git commit -m "feat: dispatch transformed per-sink outputs"
  ~~~

## Task 4: Expose topology and transform diagnostics

**Files:**
- Modify: src/onestep/app.py:840-879
- Modify: src/onestep/cli.py:413-441,561-620
- Test: tests/test_cli.py:1587-1626
- Test: tests/test_diagnostics.py:1-160,276-304

- [ ] **Step 1: Write failing description tests.** Assert existing task["emit"] remains the flattened sink descriptors and new task["emit_bindings"] contains {sink, transform_ref} for every static binding. Assert text output contains transforms=projected:testsupport_yaml_emit_binding:to_projected.

- [ ] **Step 2: Write a failing stage test.** Run a diagnostic whose transform raises; assert failed completion, failure_stage == "transform", selected sink names, and current would-retry action.

- [ ] **Step 3: Extend inventory coverage.** Use an EmitBinding pointing to a shared resource and prove aliases/roles remain deduplicated and open/close happen once.

- [ ] **Step 4: Implement additive descriptions.** Retain exact emit output in OneStepApp.describe; add emit_bindings with sink descriptor and optional transform_ref. In CLI text mode, list only transformed entries on the detail line. Do not alter JSON emit or connectivity traversal because both continue using TaskSpec.sinks.

- [ ] **Step 5: Verify.** Run uv run pytest tests/test_cli.py tests/test_diagnostics.py -q. Expected: PASS.

- [ ] **Step 6: Commit.**

  ~~~text
  git add src/onestep/app.py src/onestep/cli.py tests/test_cli.py tests/test_diagnostics.py
  git commit -m "feat: describe per-sink output transforms"
  ~~~

## Task 5: Document and validate the release surface

**Files:**
- Modify: docs/yaml-task-definition.md:274-313
- Modify: skills/onestep/references/yaml-task-definition.md:116-154
- Modify: CHANGELOG.md:1-25

- [ ] **Step 1: Document the same example in both YAML references.** Use:

  ~~~yaml
  emit:
    - sink: entity_callback
    - sink: downstream_meta
      transform:
        ref: worker.transforms:to_meta_row
  ~~~

  Define transform(ctx, payload, result), async support, unchanged-result fallback, all-transforms-before-first-send, later-Sink duplicate risk, and the first-release ban on mixing binding keys with conditional-route keys.

- [ ] **Step 2: Add an unreleased changelog bullet.** State that declarative per-Sink transforms are additive and preserve at-least-once fan-out semantics.

- [ ] **Step 3: Run focused checks.**

  ~~~text
  uv run pytest tests/contract/test_runtime_contract.py tests/test_cli.py tests/test_diagnostics.py -q -m "not integration"
  pnpm --dir docs build
  git diff --check
  ~~~

  Expected: all tests/builds succeed and diff check has no output.

- [ ] **Step 4: Run reliability checks.** Execute ./scripts/run-reliability-checks.sh. Expect core and plugin suites to pass; if unrelated infrastructure fails, record the exact command and do not change plugin code.

- [ ] **Step 5: Commit docs.**

  ~~~text
  git add docs/yaml-task-definition.md skills/onestep/references/yaml-task-definition.md CHANGELOG.md
  git commit -m "docs: describe per-sink emit transforms"
  ~~~

## Final verification

- [ ] Review git log --oneline origin/main..HEAD; only the approved design, implementation, tests, and docs belong in the branch.
- [ ] Run git status --short; pre-existing untracked plans/specs and .lavish artifacts must not be staged.
- [ ] Re-read the design spec against code and tests; no deferred feature may have slipped in.
- [ ] Open a PR with Fixes #116, explain the prepare-before-send barrier and non-transactional at-least-once behavior, and include focused tests, reliability results, and docs-build evidence.
