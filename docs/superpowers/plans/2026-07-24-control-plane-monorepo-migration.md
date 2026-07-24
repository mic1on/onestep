# Control Plane Monorepo Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import `mic1on/onestep-control-plane` into `mic1on/onestep` with full Git history while preserving independent build, dependency, and release boundaries.

**Architecture:** The deployable control plane lives at `apps/control-plane/`. It keeps its own Python project, `uv.lock`, pnpm workspace, Dockerfile, and Compose files, and is deliberately excluded from the root uv workspace because the server and reporter plugin currently share the `onestep-control-plane` distribution name and require different Python versions. Root GitHub Actions workflows provide path-scoped component CI plus a cross-component contract gate.

**Tech Stack:** Git history filtering, uv, Python 3.9-3.12, FastAPI, pytest, pnpm, React/Vite, Playwright, Docker Compose, GitHub Actions.

---

## Scope

Included:

- Preserve the control-plane `main` history ending at `8f55bc56fd8b2c38911a6fcc249b1db33ab4ff98`.
- Place all imported files below `apps/control-plane/`.
- Move actionable GitHub Actions configuration to the monorepo root.
- Keep the control-plane image name `ghcr.io/mic1on/onestep-control-plane`.
- Keep root and control-plane dependency locks independent.
- Add a current-checkout contract check for runtime, reporter, and server compatibility.
- Update current documentation and local commands to the monorepo layout.

Not included:

- Renaming either Python distribution named `onestep-control-plane`.
- Changing WebSocket messages, reporter payloads, runtime identity, or command semantics.
- Moving `onestep-worker-agent`.
- Publishing packages or images.
- Archiving or deleting `mic1on/onestep-control-plane`.
- Rewriting historical issue or pull-request links.

## Task 1: Create the migration branch and import full history

**Files:**

- Create: `apps/control-plane/**` from the filtered source repository.
- Create: `docs/superpowers/plans/2026-07-24-control-plane-monorepo-migration.md`.

- [x] **Step 1: Record clean source commits**

Run:

```bash
git -C /Users/miclon/development/onestep/onestep rev-parse origin/main
git -C /Users/miclon/development/onestep/onestep-control-plane rev-parse origin/main
git -C /Users/miclon/development/onestep/onestep-control-plane status --short
```

Expected: both SHAs are printed and the source control-plane worktree is clean.

- [x] **Step 2: Rewrite a temporary control-plane clone below the target prefix**

Run:

```bash
git clone --no-hardlinks /Users/miclon/development/onestep/onestep-control-plane /tmp/onestep-control-plane-import
cd /tmp/onestep-control-plane-import
uvx git-filter-repo --to-subdirectory-filter apps/control-plane --force
```

Expected: every tracked source path is below `apps/control-plane/`, and 200 source commits remain reachable.

- [x] **Step 3: Merge the rewritten history**

Run from the migration worktree:

```bash
git remote add control-plane-import /tmp/onestep-control-plane-import
git fetch control-plane-import main
git merge --allow-unrelated-histories --no-ff control-plane-import/main \
  -m "chore: import control plane history"
git remote remove control-plane-import
```

Expected: the merge has two parents, the imported source tip is an ancestor, and all imported files are below `apps/control-plane/`.

## Task 2: Preserve independent project boundaries

**Files:**

- Modify: `.gitignore`.
- Delete: `apps/control-plane/.github/workflows/ci.yml` after its behavior moves to a root workflow.

- [x] **Step 1: Keep the control-plane project out of the root uv workspace**

Verify that root `pyproject.toml` does not add `apps/control-plane` to `[tool.uv.workspace].members`.

Expected: root remains compatible with Python 3.9 and the control-plane server continues using Python 3.11 or newer.

- [x] **Step 2: Merge component-specific ignore rules**

The imported `apps/control-plane/.gitignore` already scopes these generated paths,
so no root ignore entries are needed:

```gitignore
apps/control-plane/.data/
apps/control-plane/desktop/out/
apps/control-plane/desktop/release/
apps/control-plane/desktop/backend-build/
apps/control-plane/desktop/backend-dist/
apps/control-plane/desktop/test-results/
```

- [x] **Step 3: Check both lockfiles independently**

Run:

```bash
uv lock --check
uv lock --project apps/control-plane --check
```

Expected: both commands succeed without changing either lockfile.

## Task 3: Move control-plane CI to the monorepo root

**Files:**

- Create: `.github/workflows/control-plane.yml`.
- Create: `.github/workflows/control-plane-contract.yml`.
- Delete: `apps/control-plane/.github/workflows/ci.yml`.

- [x] **Step 1: Add path-scoped full control-plane CI**

The workflow must trigger for `apps/control-plane/**` and its own workflow file. Each shell step runs from `apps/control-plane`, while action cache paths and Docker contexts use repository-relative paths.

Expected jobs: backend lint/tests, frontend tests/build/E2E, Docker build, Compose smoke, and main-branch GHCR publish.

- [x] **Step 2: Add a focused cross-component contract workflow**

Trigger on:

```yaml
paths:
  - "src/**"
  - "plugins/onestep-control-plane/**"
  - "apps/control-plane/backend/**"
  - "apps/control-plane/pyproject.toml"
  - "apps/control-plane/uv.lock"
  - ".github/workflows/control-plane-contract.yml"
```

The job runs reporter/WebSocket tests from the root workspace, then runs server WebSocket, end-to-end, and resource-catalog tests with the current checkout's `src/` ahead of the published core package on `PYTHONPATH`.

Expected: a protocol-affecting PR cannot merge with only one side passing.

- [x] **Step 3: Validate workflow syntax and path references**

Run repository searches for stale root-relative `Dockerfile`, lockfile, pnpm cache, and script paths.

Expected: all control-plane workflow paths resolve below `apps/control-plane/`.

## Task 4: Update active documentation and commands

**Files:**

- Modify: `docs/control-plane/index.md`.
- Modify: `docs/agent-ws-protocol.md`.
- Modify: `docs/ws-cross-repo-collaboration.md`.
- Modify: `apps/control-plane/README.md`.
- Modify: `apps/control-plane/RELEASE.md`.
- Modify: `apps/control-plane/docs/protocols/agent-ws-protocol.md` only when repository links or ownership text are stale.

- [x] **Step 1: Replace clone instructions with monorepo paths**

Use:

```bash
git clone https://github.com/mic1on/onestep
cd onestep/apps/control-plane
```

Expected: every current deployment and contributor command starts from a valid directory.

- [x] **Step 2: Update protocol ownership language**

Keep one canonical protocol at `apps/control-plane/docs/protocols/agent-ws-protocol.md`, and replace two-repository branch coordination with one-PR contract validation.

Expected: documentation no longer instructs contributors to coordinate matching branches across repositories.

- [x] **Step 3: Keep historical plans unchanged unless they drive an active command**

Expected: historical design records retain their original context; only current operational documentation changes.

## Task 5: Verify the migrated repository

**Files:** none unless a verification failure reveals a migration defect.

- [x] **Step 1: Verify Git history and repository shape**

Run:

```bash
git status --short
git rev-list --count HEAD
git log --follow -- apps/control-plane/README.md
git ls-files apps/control-plane | wc -l
git tag --list | wc -l
```

Expected: clean intentional diff/commits, imported path history is visible, 321 control-plane files are represented before workflow relocation, and 80 core tags remain.

- [x] **Step 2: Run Python validation**

Run:

```bash
uv lock --check
uv run pytest -q -m "not integration"
uv lock --project apps/control-plane --check
uv run --project apps/control-plane ruff check
uv run --project apps/control-plane pytest
```

Expected: all commands pass.

- [x] **Step 3: Run frontend validation**

Run:

```bash
pnpm --dir apps/control-plane install --frozen-lockfile
pnpm --dir apps/control-plane/frontend run test --run
pnpm --dir apps/control-plane ui:build
pnpm --dir apps/control-plane/frontend run e2e
```

Expected: unit tests, production build, and Playwright E2E pass.

- [x] **Step 4: Rebuild and restart the control plane**

Run:

```bash
cd apps/control-plane
docker compose build plane
docker compose up -d plane
docker compose ps
```

Expected: the `plane` container is healthy and serves the rebuilt code.

- [x] **Step 5: Run release-shape checks**

Run:

```bash
docker compose -f apps/control-plane/docker-compose.yml config --quiet
docker build --target plane -f apps/control-plane/Dockerfile \
  -t onestep-control-plane:migration-check apps/control-plane
```

Expected: Compose configuration and the production image build succeed without changing the published image name.

## Rollback

Before this branch is merged, rollback is deleting the migration worktree and branch. After merge but before the old repository is archived, revert the import merge and workflow/documentation commits. The standalone repository remains the source of truth until this migration branch passes all checks and is merged.

## Execution Record

- Source control-plane commit: `8f55bc56fd8b2c38911a6fcc249b1db33ab4ff98`.
- Rewritten source tip: `412b1df101a53fdb3285d441eb69ecdb356ae31e`.
- Import merge: `a63ec18` with 200 rewritten source commits and path history preserved.
- Dependency validation: both frozen uv locks resolved successfully on Python 3.11.
- Runtime unit tests: 177 passed.
- Reporter and WebSocket client tests: 157 passed.
- Current-checkout server contract tests: 22 passed.
- Control-plane backend tests: 316 passed; Ruff passed.
- Frontend tests: 90 passed; production build passed.
- Desktop tests: 1 passed.
- Playwright E2E: 23 passed.
- Docker image build: `onestep-control-plane:latest` succeeded from `apps/control-plane`.
- Runtime validation: `onestep-control-plane-plane-1` rebuilt and healthy; smoke passed at `http://127.0.0.1:4173`.
- Pre-existing concern: orphan container `onestep-control-plane-api-1` remains unhealthy and was not modified or removed by this migration.
