# GHCR Main Publish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add GHCR publishing for the existing `api` and `frontend` Docker targets after successful `main` CI runs, while also allowing safe manual re-publish runs from GitHub Actions.

**Architecture:** Extend the existing `.github/workflows/ci.yml` workflow instead of introducing a second workflow. Keep the current backend/frontend/docker/smoke gates unchanged, then add a guarded publish stage that logs into GHCR with `GITHUB_TOKEN` and pushes `latest` plus full-SHA tags for both Docker targets. Update the registry deployment docs to show the new release path and how to use the generated tags.

**Tech Stack:** GitHub Actions YAML, Docker official GitHub Actions (`login-action`, `metadata-action`, `build-push-action`), Markdown docs

---

### Task 1: Add manual trigger and GHCR publish stage to CI

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Add the manual trigger and workflow permissions**

Insert the top-level trigger and permission block so the workflow can be started manually and can write to GHCR packages:

```yaml
name: CI

on:
  workflow_dispatch:
  pull_request:
  push:
    branches:
      - main
      - feat/**

permissions:
  contents: read
  packages: write
```

- [ ] **Step 2: Add the guarded publish job after `smoke`**

Append a `publish` job that only pushes on `push` to `main` or `workflow_dispatch` against `main`:

```yaml
  publish:
    runs-on: ubuntu-latest
    needs:
      - smoke
    if: >
      github.ref == 'refs/heads/main' &&
      (github.event_name == 'push' || github.event_name == 'workflow_dispatch')
    steps:
      - uses: actions/checkout@v4

      - name: Log in to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Docker metadata (api)
        id: meta_api
        uses: docker/metadata-action@v6
        with:
          images: ghcr.io/${{ github.repository_owner }}/onestep-control-plane-api
          tags: |
            type=raw,value=latest
            type=sha,format=long

      - name: Build and push API image
        uses: docker/build-push-action@v6
        with:
          context: .
          file: ./Dockerfile
          target: api
          push: true
          tags: ${{ steps.meta_api.outputs.tags }}
          labels: ${{ steps.meta_api.outputs.labels }}

      - name: Docker metadata (frontend)
        id: meta_frontend
        uses: docker/metadata-action@v6
        with:
          images: ghcr.io/${{ github.repository_owner }}/onestep-control-plane-frontend
          tags: |
            type=raw,value=latest
            type=sha,format=long

      - name: Build and push frontend image
        uses: docker/build-push-action@v6
        with:
          context: .
          file: ./Dockerfile
          target: frontend
          push: true
          tags: ${{ steps.meta_frontend.outputs.tags }}
          labels: ${{ steps.meta_frontend.outputs.labels }}
```

- [ ] **Step 3: Verify the publish guard is narrow enough**

Read the resulting workflow and confirm these three cases:

```text
push to main              => publish runs
workflow_dispatch on main => publish runs
pull_request / feat/**    => publish skips
```

- [ ] **Step 4: Validate the workflow YAML**

Run:

```bash
ruby -e "require 'yaml'; YAML.load_file('.github/workflows/ci.yml'); puts 'YAML OK'"
```

Expected:

```text
YAML OK
```

- [ ] **Step 5: Commit the workflow change**

```bash
git add .github/workflows/ci.yml
git commit -m "feat(ci): publish ghcr images from main"
```

### Task 2: Document the GHCR release path in the README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add GHCR publishing guidance to the registry deployment section**

Update the registry deployment section to explain that:

- successful `main` pushes publish both images to GHCR
- the same workflow can be manually dispatched from the Actions tab for `main`
- deployments should prefer `sha-<full git sha>` tags over `latest`

Add example exports like:

```bash
export ONESTEP_CP_API_IMAGE=ghcr.io/mic1on/onestep-control-plane-api:sha-<full git sha>
export ONESTEP_CP_FRONTEND_IMAGE=ghcr.io/mic1on/onestep-control-plane-frontend:sha-<full git sha>
```

- [ ] **Step 2: Keep the deployment commands unchanged except for the image-source explanation**

Preserve the existing `docker compose ... pull`, `run --rm migrate`, and `up -d` commands so the
docs still match the current deployment topology.

- [ ] **Step 3: Review the updated section for operator clarity**

Confirm the section answers:

```text
Where do images come from?
How do I re-publish a main revision manually?
Which tag should I deploy?
```

- [ ] **Step 4: Commit the README change**

```bash
git add README.md
git commit -m "docs: document ghcr image publishing"
```

### Task 3: Update the release runbook for manual republish and pinned tags

**Files:**
- Modify: `docs/runbooks/release.md`

- [ ] **Step 1: Add the CI publish precondition**

Extend the preconditions / pull-images section to state that:

- `.github/workflows/ci.yml` publishes GHCR images after a successful `main` run
- operators may re-run the workflow with `workflow_dispatch` when they need to republish a `main` revision

- [ ] **Step 2: Add explicit image examples before the pull step**

Insert a short example block showing the `.env.deploy` image variables:

```bash
export ONESTEP_CP_API_IMAGE=ghcr.io/mic1on/onestep-control-plane-api:sha-<full git sha>
export ONESTEP_CP_FRONTEND_IMAGE=ghcr.io/mic1on/onestep-control-plane-frontend:sha-<full git sha>
```

Then keep the existing compose pull / migrate / rollout commands intact.

- [ ] **Step 3: Review the runbook for consistency with the README**

Confirm both documents agree on:

```text
registry = ghcr.io
images = api + frontend
recommended deploy tag = sha-<full git sha>
manual fallback = workflow_dispatch on main
```

- [ ] **Step 4: Commit the runbook change**

```bash
git add docs/runbooks/release.md
git commit -m "docs: update release runbook for ghcr publish"
```

### Task 4: Final verification and delivery

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`
- Modify: `docs/runbooks/release.md`

- [ ] **Step 1: Review the full diff**

Run:

```bash
git diff -- .github/workflows/ci.yml README.md docs/runbooks/release.md
```

Expected:

```text
Only CI publishing and registry deployment documentation changes appear.
```

- [ ] **Step 2: Re-run the lightweight checks**

Run:

```bash
ruby -e "require 'yaml'; YAML.load_file('.github/workflows/ci.yml'); puts 'YAML OK'"
git diff --check
```

Expected:

```text
YAML OK
No whitespace errors reported by git diff --check
```

- [ ] **Step 3: Summarize the exact publish behavior for handoff**

Document these final behavior guarantees in the close-out:

```text
push to main => build, smoke, publish to GHCR
workflow_dispatch on main => build, smoke, publish to GHCR
pull_request / feat/** / workflow_dispatch on non-main => no publish
tags => latest + sha-<full git sha> for both api and frontend
```

- [ ] **Step 4: Commit the final doc/workflow batch if using a single implementation commit**

```bash
git add .github/workflows/ci.yml README.md docs/runbooks/release.md
git commit -m "feat(ci): publish ghcr images from main"
```
