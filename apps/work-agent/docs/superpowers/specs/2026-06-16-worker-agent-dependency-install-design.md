# Worker Agent Dependency Installation Design

## Summary

Extend `onestep-worker-agent` so a deployed workflow package can declare and
install its own Python dependencies in an isolated virtualenv, instead of being
limited to whatever is pre-installed in the agent's global Python environment.

Today the supervisor downloads a package zip, extracts it, and runs
`onestep check <entrypoint>` followed by `onestep run <entrypoint>` using the
global `onestep`. Any worker that needs third-party dependencies (or that ships
as an installable Python package) fails with `ModuleNotFoundError`, because the
agent never installs anything.

The new behavior: after extraction the agent auto-detects a dependency
declaration (`pyproject.toml` or `requirements.txt`) in the package, builds (or
reuses) a virtualenv keyed by the package checksum, installs the dependencies
into it, and runs `check`/`run` using that venv's `onestep`. A new
`installing` lifecycle state makes the install phase visible on the deployment
timeline.

## Goals

- Let a worker package ship its own dependencies and have them installed on
  the agent without manual host setup.
- Keep dependency sets isolated between unrelated workers (no shared
  environment conflicts).
- Make redeploys of the same package fast by reusing a venv keyed on the
  package checksum.
- Require zero configuration: the agent decides whether to install based on
  files present in the package, not on a flag the user must remember to set.
- Stay forward-compatible with the control plane: the plane stores
  `observed_status` as an opaque string, so a new `installing` value works
  without a protocol migration.

## Non-Goals

- No dependency-install retry with backoff. A failed install fails the
  deployment immediately.
- No new field on the plane's dispatch args (no `install_deps` flag). Detection
  is purely a package-content decision made by the agent.
- No prebuilt wheelhouse or offline artifact cache. pip's own cache is the
  first performance lever; an explicit wheelhouse is deferred.
- No credential/secret injection into the subprocess. That remains deferred as
  noted by the existing supervisor comments.
- No change to the package storage, download, or command-ack protocol.
- No Docker-based isolation mode.

## Current Flow (baseline)

`client.py:_handle_start_deployment_command`:

1. Reserve a slot.
2. Send `preparing` event ("downloading workflow package").
3. Download the zip, `extract_package` into `package_dir`.
4. Send `checking` event ("running onestep check").
5. `supervisor.check(spec)` → `onestep check <entrypoint>` with `cwd=package_dir`.
6. On non-zero, release slot, send `failed`.
7. `supervisor.start(spec)` → `onestep run <entrypoint>`, send `running`.

`supervisor.py` hardcodes the `onestep` executable name in both `check` and
`start`, and `build_environment` extends `os.environ` with only the deployment
identity vars (`ONESTEP_DEPLOYMENT_ID`, etc.). No venv, no install.

The smoke test (`scripts/run_smoke.py`) ships a package with only
`worker.yaml` + a stdlib-only `smoke_tasks.py`, so it never exercises
dependencies.

## Proposed Flow

Insert an optional **install phase** between download and check.

1. Reserve a slot.
2. `preparing` event ("downloading workflow package").
3. Download + extract (unchanged).
4. **Detect dependency declaration** in `package_dir`:
   - `pyproject.toml` present → install mode `pip install .`
   - else `requirements.txt` present → install mode `pip install -r requirements.txt`
   - neither → no install; run with the global `onestep` (unchanged path).
5. **Only if install mode is set:**
   1. `installing` event ("installing dependencies into virtualenv").
   2. Resolve the shared venv dir `work_dir/venvs/<package_checksum>/venv`.
   3. If `work_dir/venvs/<package_checksum>/.installed` marker exists → skip
      install (reuse).
   4. Else create the venv (`python -m venv`) and run the install command
      (`<venv>/bin/python -m pip install ...`).
   5. On non-zero exit → send `failed` ("dependency install failed"), release
      slot, **keep the venv** (so a retrigger can reuse partial progress), stop.
   6. On success → write `.installed` marker.
6. `checking` event → `onestep check` using the venv's `onestep` if a venv
   exists, else the global one.
7. On non-zero → `failed` (unchanged semantics), release slot.
8. `supervisor.start` → `onestep run` via the venv; send `running`.

### Failure handling

- `pip install` failure → `observed_status="failed"`, event_type `failed`,
  release slot. The venv and any partial `.installed`-absent state remain on
  disk so the next attempt at the same checksum can retry; pip is idempotent.
- `onestep check` failure → unchanged: `failed`, release slot.
- No retry loop. The operator (or the plane's restart command) re-triggers.

### venv reuse and lifecycle

- Keyed on `package_checksum` (the workflow package's sha256), **not** on
  `deployment_id`. Two deployments of the same package share one venv; a new
  package version gets a fresh venv.
- Reuse guard: a `.installed` marker file next to the venv makes the install
  step idempotent and crash-safe (if the marker is absent, the venv is treated
  as not-ready and reinstalled; an interrupted install leaves no marker).
- Cleanup:
  - On startup (`recover_running_deployments` area), GC venvs whose checksum is
    not referenced by any current package known to the agent. (Best-effort; a
    venv whose package was deleted from the plane is unreachable anyway.)
  - When a deployment stops, the venv is **not** deleted (other deployments of
    the same package may reuse it; cleanup is checksum-driven, not
    deployment-driven).

### supervisor changes

- `DeploymentSpec` gains `venv_dir: Path | None` and
  `onestep_executable: str` (resolved at prepare time: `<venv>/bin/onestep` if
  venv exists, else the global `onestep` discovered from `shutil.which`).
- `build_environment` prepends `<venv>/bin` to `PATH` when a venv is in use, so
  child processes and onestep's own subprocesses resolve the same interpreter.
- New method `install(spec, *, mode, timeout_s)` encapsulates venv creation +
  pip install; `check`/`start` switch from the hardcoded `"onestep"` to
  `spec.onestep_executable`.

### control-plane coordination

The plane already stores `observed_status` as a free string column (see
`worker_agent_service.py:266-267`). The agent sending
`observed_status="installing"` will persist and display without a migration.

The only plane-side change is adding the literal to the documented union type
so the type layer and frontend agree:

- `backend/.../api/schemas.py`: add `"installing"` to
  `WorkerDeploymentObservedStatus`.
- frontend `lib/api/types.ts`: add `"installing"` to
  `WorkerDeploymentObservedStatus`.
- frontend `StatusBadge.tsx`: add an `installing` → `badge-accent` mapping (and
  the i18n `status.installing` label) so the new state renders with a
  recognized style instead of falling through.

This is an additive, forward-compatible enum extension — the plane tolerates
unknown status strings already, so an agent upgraded before the plane still
works; a plane upgraded before the agent simply never sees the new value.

## Local Development

The whole point of this change is that local development and deployment become
the same operation. A complex worker project:

```
myworker/
├── worker.yaml          # complex topology, multiple sources/sinks
├── pyproject.toml       # installable package + its dependencies
└── src/myworker/
    └── handlers.py      # handler ref: myworker.handlers:xxx
```

Local development:

```bash
pip install -e .
onestep check worker.yaml
onestep run worker.yaml
```

Deployment: zip the project root (same structure as local), upload via the
agent page's package-upload flow (or a pipeline export), and the agent runs
`pip install .` into an isolated venv, then `onestep check/run` with that venv.
What you validate locally is what the agent runs.

## Testing

- `tests/test_supervisor.py` (new or extended): unit-test `install` with a
  stubbed venv/pip to assert the marker is written on success, the slot is
  released and `failed` returned on non-zero pip exit, and reuse skips install
  when `.installed` exists.
- `tests/test_client.py`: extend the deployment-command handling test to cover
  the install phase — package with `requirements.txt` triggers `installing`
  then `checking`; package without declaration skips straight to `checking`
  (preserves the smoke-shaped fast path).
- The existing `scripts/run_smoke.py` continues to pass because its package has
  no dependency declaration and therefore takes the no-install path unchanged.

## Risks

- **Install latency**: a first-time install can take tens of seconds to
  minutes for heavy dependency trees. Mitigated by checksum-based reuse and
  pip's cache; surfaced via the `installing` state so it is not silent.
- **Disk growth**: each distinct package checksum leaves a venv on disk.
  Mitigated by startup GC of unreferenced checksums.
- **pip/venv availability**: assumes `python -m venv` and `pip` exist in the
  agent's environment. The venv is created from the same interpreter that runs
  the agent (`sys.executable`), so `python -m venv` resolves to that
  interpreter's venv module. The supervisor should fail fast with a clear
  `failed` message if venv creation is unavailable, rather than crashing.
