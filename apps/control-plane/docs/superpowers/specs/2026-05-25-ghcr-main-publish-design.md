# GHCR Main Branch Publish Design

Date: 2026-05-25
Status: Ready for user review
Owner: Codex

## Summary

Publish the existing `api` and `frontend` Docker targets to GitHub Container Registry after an
automatic `main` push pipeline or a manual `workflow_dispatch` run against `main`.

Scope is limited to GitHub Actions release automation and the related release documentation.

Primary files expected in scope:

- `.github/workflows/ci.yml`
- `README.md`
- `docs/runbooks/release.md`

No application code, Docker build logic, or deployment compose topology changes are expected.

## Design Inputs

The following decisions were validated in conversation:

- Publish both images, not just one target
- Reuse the existing `CI` workflow instead of creating a separate publish workflow
- Publish only after a `push` to `main`
- Also allow an operator to trigger the same release path manually from GitHub Actions
- Push both `latest` and commit-specific SHA tags
- Keep the change minimal and aligned with the current registry deployment flow

## Goals

1. Automatically publish release-ready API and frontend images to `ghcr.io` after `main` updates.
2. Allow a manual re-run path for publishing the selected `main` revision without requiring a new push.
3. Ensure images are only published after the existing test, build, and smoke gates succeed.
4. Produce stable tags for both "most recent release" usage and exact rollback / pinning.
5. Keep the release operator flow compatible with the existing `ONESTEP_CP_API_IMAGE` and `ONESTEP_CP_FRONTEND_IMAGE` deployment variables.

## Non-Goals

- No automatic deployment to a server
- No change to the Dockerfile targets or image contents
- No new registry beyond GHCR
- No semantic version tagging in this iteration
- No change to local development compose defaults
- No manual publishing of non-`main` refs in this iteration

## Current State

The repository already has the core pieces needed for registry publishing:

- `.github/workflows/ci.yml` runs backend checks, frontend checks, Docker builds, and smoke tests
- `Dockerfile` already defines separate `api` and `frontend` targets
- `docker-compose.deploy.yml` already accepts externally hosted image references through
  `ONESTEP_CP_API_IMAGE` and `ONESTEP_CP_FRONTEND_IMAGE`
- the README and release runbook already describe a registry-based deployment flow

What is missing is the final publish step that turns a green `main` pipeline into pushed GHCR images.

## Approach Options

### Recommended: Add a publish job to the existing CI workflow

Add one `publish` job to `.github/workflows/ci.yml` and gate it behind:

- successful completion of the current `smoke` job
- either a `push` event on `main` or a `workflow_dispatch` run against `main`

Why this approach:

- it is the smallest change
- it reuses the current test and smoke gates directly
- it keeps the publish path easy to inspect in one workflow
- it matches the repository's current CI structure
- it gives operators a safe retry path when a publish needs to be re-run without a fresh merge

### Alternative: Separate `publish-images.yml` on `push` to `main`

Why not now:

- splits the release signal across multiple workflows
- either duplicates validation or allows publishing before the current CI pipeline fully finishes

### Alternative: Separate publish workflow triggered by `workflow_run`

Why not now:

- clean in theory, but more complex to debug and maintain
- unnecessary indirection for the current scope

## Workflow Design

### Trigger And Gate

The existing `CI` workflow remains the single entry point.

Add `workflow_dispatch` to the workflow triggers so an operator can start the workflow from the
Actions UI.

Add a new `publish` job with:

- `needs: smoke`
- an `if:` guard that allows:
  - `push` events on `main`
  - `workflow_dispatch` runs whose selected ref is `main`

Result:

- pull requests still validate but never publish
- feature branch pushes still validate but never publish
- manually dispatched runs on non-`main` refs validate but do not publish
- only `main` revisions can reach GHCR

### Workflow Permissions

Set workflow-level permissions to the minimum required for GHCR publishing:

- `contents: read`
- `packages: write`

The design assumes GitHub Actions package publishing is allowed for the repository. If repository
or organization settings block package writes, the workflow will authenticate but push will fail.

### Registry Authentication

Authenticate to `ghcr.io` with the workflow `GITHUB_TOKEN`.

Do not introduce a Personal Access Token in this iteration. The built-in token keeps setup lighter
and avoids adding a second credential path unless repository policy requires it later.

## Image Naming And Tagging

Publish two images:

- `ghcr.io/${{ github.repository_owner }}/onestep-control-plane-api`
- `ghcr.io/${{ github.repository_owner }}/onestep-control-plane-frontend`

For each image, publish:

- `latest`
- `sha-<full git sha>`

The SHA-based tag uses the full Git commit SHA and is the deployable release record. `latest` is a
convenience tag for "most recent main release" flows.

The implementation should use Docker metadata generation rather than hand-built string concatenation
so the tag set is consistent between both image targets.

## Build And Push Behavior

The `publish` job should build and push the two targets independently from the shared `Dockerfile`:

1. `target: api`
2. `target: frontend`

Each build should:

- check out the repository
- log in to GHCR
- generate tags and labels
- run `docker/build-push-action` with `push: true`

Separate target pushes are preferred over trying to collapse both targets into one custom loop
because the standard action flow is clearer and easier to maintain.

## Documentation Changes

Update release documentation so operators know exactly what image references to use.

README changes:

- clarify that `main` pushes publish both images to GHCR
- clarify that the workflow can also be manually dispatched from GitHub Actions for `main`
- show example image references using the `sha-<full git sha>` tag form

Release runbook changes:

- note that the CI workflow publishes GHCR images after a successful `main` pipeline
- note that operators can manually dispatch the workflow to republish a `main` revision
- update the pull / deploy examples to use explicit GHCR image references when setting
  `ONESTEP_CP_API_IMAGE` and `ONESTEP_CP_FRONTEND_IMAGE`

The documentation should bias operators toward commit-pinned tags for actual deployments and
reserve `latest` for convenience or quick validation flows.

## Testing And Verification

This change should be verified at the workflow level by:

1. validating the workflow file syntax locally if a suitable tool is available
2. confirming the `publish` job is guarded to `push` on `main` or `workflow_dispatch` on `main`
3. confirming both image targets still build through the existing Docker and smoke gates
4. confirming documentation examples match the new tag format

No application test changes are expected because runtime behavior is unchanged.

## Success Criteria

- A push to `main` runs the existing CI pipeline and, after `smoke` passes, pushes:
  - `ghcr.io/<owner>/onestep-control-plane-api:latest`
  - `ghcr.io/<owner>/onestep-control-plane-api:sha-<full git sha>`
  - `ghcr.io/<owner>/onestep-control-plane-frontend:latest`
  - `ghcr.io/<owner>/onestep-control-plane-frontend:sha-<full git sha>`
- A manual `workflow_dispatch` run against `main` publishes the same four tags after `smoke` passes
- Pull requests do not publish images
- Feature branch pushes do not publish images
- Manual runs against non-`main` refs do not publish images
- Registry deployment docs show how to reference the new GHCR tags

## Implementation Boundaries

Keep the change surgical:

- modify the existing CI workflow rather than reorganizing all workflows
- avoid altering unrelated test steps
- avoid changing the Dockerfile unless the publish path reveals a concrete build issue
- update only the docs directly needed to explain the new release path
