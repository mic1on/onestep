# Worker Agent Monorepo Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import `mic1on/onestep-worker-agent` into `mic1on/onestep` at `apps/work-agent` while preserving source history, its standalone Python distribution, and its release boundary.

**Architecture:** Rewrite a temporary clone of the worker-agent repository so every historical path lives below `apps/work-agent`, then merge that history into a migration branch. Keep the app as an independently locked Python project, but move active CI and release automation to root workflows and add it to the runtime/control-plane contract gate.

**Tech Stack:** Git history filtering, uv, Python 3.11, pytest, Ruff, Hatchling, GitHub Actions.

---

## Scope

Included:

- Preserve worker-agent history through source commit `828e7fb9b932602651bc1f3bfe9666b537fee344`.
- Place imported files below `apps/work-agent/`.
- Keep the `onestep-worker-agent` distribution and `onestep-agent` CLI names.
- Keep the worker-agent `uv.lock` independent from the root workspace lock.
- Move actionable GitHub Actions configuration to the monorepo root.
- Update the smoke test and documentation to use `apps/control-plane`.
- Extend the cross-component contract workflow to install and test the current worker-agent checkout.

Not included:

- Fixing subprocess exit monitoring, PID identity validation, or host service management.
- Implementing `sync_agent_state`, runtime params, or credential resolution.
- Restoring the control-plane worker-agent UI.
- Publishing a package, creating a release tag, pushing the branch, or archiving the source repository.

## File Structure

- Create `apps/work-agent/**`: source repository content with rewritten history.
- Create `.github/workflows/work-agent.yml`: path-scoped test, lint, build, and optional PyPI publish workflow.
- Modify `.github/workflows/control-plane-contract.yml`: include worker-agent paths and tests.
- Modify `apps/work-agent/README.md`: describe the monorepo development and smoke commands.
- Modify `apps/work-agent/scripts/run_smoke.py`: resolve the migrated control-plane path by default.
- Modify `README.md`: list the worker agent in the repository map.
- Create `docs/superpowers/plans/2026-07-24-worker-agent-monorepo-migration.md`: record migration scope and verification.

### Task 1: Import Source History

- [ ] **Step 1: Verify both repositories are clean and record source commits**

Run:

```bash
git -C /Users/miclon/development/onestep/onestep status --short
git -C /Users/miclon/development/onestep/onestep-worker-agent status --short
git -C /Users/miclon/development/onestep/onestep-worker-agent rev-parse HEAD
```

Expected: both status commands are empty and the source SHA is `828e7fb9b932602651bc1f3bfe9666b537fee344`.

- [ ] **Step 2: Filter a temporary clone into the target prefix**

Run:

```bash
tmp_dir=$(mktemp -d)
git clone --no-local /Users/miclon/development/onestep/onestep-worker-agent "$tmp_dir/source"
uvx git-filter-repo --source "$tmp_dir/source" --target "$tmp_dir/filtered" --to-subdirectory-filter apps/work-agent
```

Expected: the filtered repository retains all source commits and its tip tree contains only `apps/work-agent/**`.

- [ ] **Step 3: Merge the filtered history**

Run:

```bash
git remote add worker-agent-import "$tmp_dir/filtered"
git fetch worker-agent-import main
git merge --allow-unrelated-histories --no-ff worker-agent-import/main -m "chore: import worker agent history"
git remote remove worker-agent-import
```

Expected: the merge has two parents and imports the worker-agent tree without changing existing files.

### Task 2: Integrate Repository Automation

- [ ] **Step 1: Replace the inert nested release workflow**

Delete `apps/work-agent/.github/workflows/release.yml` and create `.github/workflows/work-agent.yml` with:

- pull request and `main` push path filters for `apps/work-agent/**`;
- Python 3.11, frozen dependency sync, pytest, Ruff, build, metadata check, and wheel smoke install;
- manual `workflow_dispatch` publishing through PyPI Trusted Publishing;
- `working-directory: apps/work-agent` for app-local commands.

- [ ] **Step 2: Extend the cross-component contract gate**

Modify `.github/workflows/control-plane-contract.yml` so worker-agent source and lock changes trigger the workflow. Install the worker-agent project, replace its installed runtime with the current root checkout, and run its tests alongside the current control-plane protocol tests.

- [ ] **Step 3: Verify workflow syntax**

Run:

```bash
ruby -e 'require "yaml"; Dir[".github/workflows/*.yml"].each { |path| YAML.load_file(path, aliases: true) }'
```

Expected: exit code 0.

### Task 3: Update Monorepo Paths And Documentation

- [ ] **Step 1: Update the smoke test default path**

Change the default control-plane directory from a sibling repository to `<repo>/apps/control-plane`. Keep `--control-plane-dir` as an override.

- [ ] **Step 2: Update worker-agent documentation**

Document commands from the monorepo root and from `apps/work-agent`, including the explicit smoke command. Do not claim the known background-process smoke regression is fixed.

- [ ] **Step 3: Add the app to the root repository map**

Add `apps/work-agent` next to `apps/control-plane`, describing it as the host execution agent published as `onestep-worker-agent`.

### Task 4: Verify The Migration

- [ ] **Step 1: Run worker-agent checks**

Run:

```bash
uv sync --project apps/work-agent --frozen --extra test
uv run --project apps/work-agent pytest -q
uv run --project apps/work-agent ruff check .
uv build --project apps/work-agent
uvx twine check apps/work-agent/dist/*
```

Expected: all unit tests pass, Ruff reports no findings, and wheel/sdist metadata is valid.

- [ ] **Step 2: Smoke-install the built wheel**

Run the wheel in a temporary virtual environment and verify `onestep-agent --help` exits successfully.

- [ ] **Step 3: Verify history and scope**

Run:

```bash
git log --follow --oneline -- apps/work-agent/src/onestep_worker_agent/client.py
git diff --check origin/main...HEAD
git status --short
```

Expected: source history is visible, the diff has no whitespace errors, and only planned migration changes are present.

- [ ] **Step 4: Record the known smoke result**

Run the end-to-end smoke once. If it still fails because `start` backgrounds the real agent while the smoke monitors the launcher, record that as a pre-existing follow-up rather than changing runtime behavior in this migration.
