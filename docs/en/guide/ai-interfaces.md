---
title: AI & Automation Interfaces | Guide
outline: deep
---

# AI & Automation Interfaces

OneStep's command-line output is a stable contract for programs (scripts, CI,
AI agents) to consume — not just human-readable text. This page lists those
contracts: exit codes, `--json` output structures, the JSON Schema, and how
strict validation reports errors.

## Exit Codes

Every subcommand follows the same exit-code semantics. Decide "did the command
succeed" from the exit code; do not parse the output text.

| Exit code | Meaning | Typical cases |
|-----------|---------|---------------|
| `0` | Success | `check` passed; `run` finished normally |
| `1` | Runtime failure | `run` raised; `check --connect` probe failed; `task run/replay` execution failed |
| `2` | Invalid input or config | YAML strict validation failed; target failed to load; unknown argument or subcommand; `task`'s `validation_failed` |

The key distinction: **`2` means "the input is broken — retrying will not
help", `1` means "the input was fine, but execution failed"**. Automation
should treat them differently: on `2` fix the config, on `1` consider
retrying.

## The `--json` Output Contract

`check`, `catalog`, `build`, `task run`, and `task replay` all support
`--json`. Every JSON output carries `schema` and `version` fields so consumers
fail loudly when the structure changes instead of silently misreading it:

```json
{
  "schema": "onestep/check-summary",
  "version": 1,
  "target": "worker.yaml",
  "name": "demo",
  "tasks": []
}
```

Known contract identifiers:

| `schema` | Command | Description |
|----------|---------|-------------|
| `onestep/check-summary` | `check --json` | App and task summary |
| `onestep/cli-error` | any `--json` | Error envelope on failure |
| `onestep/diagnostic-result` | `task run` / `task replay` | Single-delivery diagnostic result |
| `onestep/connectivity-result` | `check --connect --json` | Resource connectivity probe result |

### JSON on Failure Too

On failure, `--json` prints the error envelope to **stdout** (the human-readable
line still goes to stderr):

```json
{
  "schema": "onestep/cli-error",
  "version": 1,
  "command": "check",
  "target": "worker.yaml",
  "ok": false,
  "error": {
    "type": "AppConfigValidationError",
    "message": "3 validation problem(s): ...",
    "issues": [
      {"path": "config", "message": "unsupported fields for config: bogusTop", "kind": "unknown_field"},
      {"path": "resources.tick", "message": "...", "kind": "unknown_field"},
      {"path": "tasks[0]", "message": "...", "kind": "missing_field"}
    ]
  }
}
```

Values of `issues[].kind`: `unknown_field`, `missing_field`, `invalid_type`,
`invalid_value`, `unknown_resource`. `issues[].path` uses the same path
notation as the error text (`tasks[0].retry`, `resources.queue.dsn`) so you can
jump straight to the offending line.

## Strict Validation: All Problems in One Report

`check --strict` has two reporting modes for YAML:

- **Human path (default)**: fail-fast, reports only the first error. Suited for
  fixing issues one at a time in a terminal.
- **`--json` path**: collects and reports **all** problems — one call returns
  the complete list.

For an AI agent or CI, `--json` compresses "N rounds of fix → re-run" into
one:

```bash
onestep check --strict --json worker.yaml
```

The first error is guaranteed to be identical on both paths (pinned by a
contract test), so the human and the script never see different first errors.

## JSON Schema

The `onestep/v1alpha1` YAML structure has a JSON Schema you can use for editor
completion, validation, or letting an AI self-validate generated configs:

```bash
onestep schema                                   # print to stdout
onestep schema --out docs/public/schema/v1alpha1.json
```

The Schema is **derived from the runtime's strict-validation contract and the
installed connector catalog** — it is not a hand-written copy. Therefore:

- When a connector registers a new resource type or field, the Schema includes
  it automatically.
- The Schema accepts exactly the fields `check --strict` allows. For example,
  the resource catalog advertises `host`/`username` for `mysql`, but strict
  validation only accepts `dsn`/`engine_options` — the Schema agrees with
  strict validation.

Reference it with `$schema` in your YAML (the key is documentation-only and
does not affect the runtime):

```yaml
$schema: https://onestep.code05.com/schema/v1alpha1.json
apiVersion: onestep/v1alpha1
kind: App

app:
  name: demo

resources:
  tick:
    type: interval
    minutes: 5
  out:
    type: memory
    maxsize: 10

tasks:
  - name: forward
    source: tick
    emit: out
```

The `yaml-language-server` comment form also works:

```yaml
# yaml-language-server: $schema=https://onestep.code05.com/schema/v1alpha1.json
```

::: tip About required
The Schema deliberately declares **no** `required` fields. The resource
catalog marks more fields required than strict validation does (e.g. `dsn` and
`path` are marked required, while `check --strict` actually accepts their
absence). Enforcing requiredness in the Schema would reject valid configs, so
requiredness is defined by `onestep check --strict`.
:::

## Structured Logs

`--log-format json` emits every log line (including task lifecycle events) as
single-line JSON for Loki/ELK indexing:

```bash
onestep run worker.yaml --log-format json
```

## Related Docs

- [YAML Task Definition](/en/yaml-task-definition): full field reference and strict-validation boundary.
- [SKILL](/en/skill/): workflow notes for AI coding agents.
- [Core Reliability](/en/core-reliability): delivery semantics and failure recovery.
