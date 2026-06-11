# Onestep Worker Runtime Image Design

## Summary

Add an official multi-platform worker runtime image for `onestep` so adopters can start a worker by supplying only:

- a `worker.yaml`
- Python task code
- optional business dependencies

The design uses one shared runtime image that supports two consumption modes:

- `A`: run the official image directly by mounting a workspace
- `B`: build a project-specific image with `FROM ghcr.io/${GITHUB_REPOSITORY_OWNER}/onestep-worker`

The first version intentionally keeps the surface small:

- publish one official runtime image
- include `onestep[all]`
- support `linux/amd64` and `linux/arm64`
- use `ONESTEP_TARGET` as the single required entrypoint contract
- automatically add `/workspace` and `/workspace/src` to `PYTHONPATH`
- optionally install project dependencies from the mounted workspace before startup

## Problem

`onestep` already supports a project shape where runtime wiring lives in `worker.yaml` and business logic lives in Python modules. That makes local development straightforward, but there is no official container runtime that turns this shape into a standardized deployment product.

Today a user who wants to run a YAML worker in a container still has to decide all of the following on their own:

- which base image to use
- which `onestep` extras to install
- how to expose project code to the runtime
- how to set `PYTHONPATH`
- how to validate the target before starting
- how to publish a multi-platform image if they want to inherit from a shared base

That gap makes adoption heavier than it should be for the intended YAML worker flow.

## Goals

- Provide an official runtime image for YAML-oriented `onestep` workers.
- Support both direct-run and derived-image usage with one shared runtime product.
- Keep the worker startup contract simple and explicit.
- Publish official images to GHCR as part of the normal release flow.
- Support both `linux/amd64` and `linux/arm64`.
- Make startup failures diagnosable from container logs.

## Non-Goals

- No attempt to containerize the control plane in this design.
- No automatic code generation inside the runtime container.
- No workflow DSL, YAML transform language, or embedded business logic expansion.
- No runtime dependency cache service or shared package volume management.
- No first-version support for additional architectures such as `linux/arm/v7`.
- No separate public images for direct-run and derived-image scenarios.

## User Model

The official image should feel like a runtime product rather than a container recipe.

Users interact with one image and choose one of two modes:

### Mode A: Mounted workspace

The user runs the official image directly and mounts a project directory into `/workspace`.

That project directory contains:

- `worker.yaml`
- Python task modules
- optional `requirements.txt` or `pyproject.toml`

The user sets `ONESTEP_TARGET` to the YAML file or Python target to run.

Example mental model:

```bash
docker run --rm \
  -e ONESTEP_TARGET=/workspace/worker.yaml \
  -v "$PWD:/workspace" \
  ghcr.io/repository-owner/onestep-worker:1.2.62
```

### Mode B: Derived image

The user builds their own image from the official runtime image and bakes in code, config, and optionally dependencies.

Example mental model:

```dockerfile
FROM ghcr.io/repository-owner/onestep-worker:1.2.62

WORKDIR /workspace
COPY . /workspace
ENV ONESTEP_TARGET=/workspace/worker.yaml
```

The same startup contract applies in both modes. The only difference is whether project files arrive at runtime or at image build time.

## Recommended Approach

Publish one official runtime image with a single entrypoint contract.

Why this approach:

- one runtime product is easier to document and support
- A and B become two usage patterns rather than two products
- versioning stays aligned with the Python package release
- the startup path can be validated in one place

Rejected alternatives:

- two public images, one for mounted workspaces and one for derived images
  - clearer role separation, but doubles release and support burden
- a very thin image with no startup automation
  - smaller implementation, but fails the goal of "write YAML and task code, then run"

## Image Scope

The official image should contain only the runtime pieces needed to execute a worker reliably:

- Python runtime
- `onestep[all]`
- container entrypoint script
- minimal system packages needed by the runtime and package installation flow
- default `WORKDIR /workspace`

It should not contain:

- example projects
- test fixtures
- control plane services
- repository-only build tooling that is not required at runtime

The image is a worker runtime, not a general project development environment.

## Runtime Contract

The runtime contract is intentionally narrow and explicit.

### Required input

- `ONESTEP_TARGET`

`ONESTEP_TARGET` points to the worker entrypoint and can be either:

- a YAML file path such as `/workspace/worker.yaml`
- a Python import target such as `your_pkg.tasks:app`

The runtime must not guess between multiple targets. If `ONESTEP_TARGET` is missing or invalid, startup fails immediately with a clear error.

### Working directory and import path

The image defaults to:

- `WORKDIR=/workspace`

At startup, the runtime appends these paths to `PYTHONPATH`:

- `/workspace`
- `/workspace/src`

This supports the common `src/` project shape without requiring users to manually inject `PYTHONPATH`.

### Optional dependency discovery

Before running the worker, the entrypoint checks for project-local dependency declarations in this order:

1. `/workspace/requirements.txt`
2. `/workspace/pyproject.toml`

If `requirements.txt` exists, install from it.

Otherwise, if `pyproject.toml` exists, install the project dependencies from it.

If neither exists, skip dependency installation.

This behavior exists primarily to make Mode A usable when business logic depends on packages beyond `onestep[all]`.

## Startup Flow

The entrypoint performs four steps:

1. Preflight validation
2. Project dependency installation
3. `onestep check`
4. `onestep run`

### 1. Preflight validation

Validate:

- `ONESTEP_TARGET` is set
- when the target is a file path, the file exists and is readable

Log:

- current working directory
- target value
- effective `PYTHONPATH`

### 2. Project dependency installation

Install project dependencies when a recognized dependency file is present in `/workspace`.

Behavior should stay intentionally small:

- do not scan nested directories
- do not try to infer multiple project roots
- do not implement persistent cross-container caching behavior

### 3. `onestep check`

Run `onestep check "$ONESTEP_TARGET"` before startup.

This catches:

- YAML schema errors
- missing imports
- invalid app wiring

before the container enters the long-running execution path.

### 4. `onestep run`

Only after validation passes does the runtime execute:

```bash
onestep run "$ONESTEP_TARGET"
```

## Failure Handling

The runtime should fail fast and loudly.

Expected hard-failure cases:

- `ONESTEP_TARGET` missing
- target file missing
- dependency installation failure
- `onestep check` failure
- `onestep run` startup failure

Failure handling rules:

- exit non-zero
- print the failing phase
- print the relevant target or file path
- do not silently skip validation or fallback to guessed defaults

This is especially important for Mode A, where runtime behavior is more dynamic.

## Logging And Observability

The first version only needs basic container-level observability.

Required startup logs:

- target being used
- whether dependency installation ran, and from which file
- result of `onestep check`
- transition into `onestep run`

This keeps failures diagnosable in plain container logs without adding a larger observability subsystem.

## Versioning And Tagging

The image version should align with the `onestep` package release version.

Recommended public tags:

- full version tag, for example `1.2.62`
- minor convenience tag, for example `1.2`
- `latest`

The tag source of truth should remain the existing repository release flow. The image should not introduce a separate version lifecycle.

## Registry And Distribution

Publish the runtime image to GHCR:

- canonical image name: `onestep-worker`
- full image reference derived by release workflow: `ghcr.io/${GITHUB_REPOSITORY_OWNER}/onestep-worker`

First-version platform targets:

- `linux/amd64`
- `linux/arm64`

Use Docker Buildx to publish a multi-platform manifest under each released tag.

## Release Flow

The repository already has a release workflow that builds distributions, runs tests, and publishes to GitHub Releases and PyPI. The container image should be added to that same release path instead of introducing an independent image-release system.

Recommended release behavior:

- on release tags, build and push the runtime image to GHCR
- publish a multi-platform manifest for `amd64` and `arm64`
- tag the image with the same version as the Python package

Recommended CI behavior outside release:

- validate that the Dockerfile builds
- validate that the entrypoint starts and fails correctly in expected bad-input cases
- do not push release images from ordinary PR or branch CI

This preserves the current release discipline while adding the image as another official artifact.

## Validation Strategy

Validation should cover both failure paths and happy paths.

### 1. Image-level failure cases

Verify:

- container fails when `ONESTEP_TARGET` is missing
- container fails when the target file path does not exist
- container fails when `onestep check` fails because of invalid YAML
- container fails when import resolution fails

### 2. Mode A happy path

Use a mounted example worker project and verify that:

- `/workspace` import resolution works
- `/workspace/src` import resolution works
- optional dependency installation path behaves correctly
- the worker reaches the `onestep run` phase successfully

The repo's existing `example/yaml_project/` is the best first validation target.

### 3. Mode B happy path

Build a small derived image with:

- copied project files
- `ENV ONESTEP_TARGET=/workspace/worker.yaml`

Verify the derived image starts successfully with the same entrypoint contract.

### 4. Release-path validation

During release:

- build and push the multi-platform image
- ensure the GHCR publish step uses the release tag-derived version

## Documentation Changes

The feature is incomplete without container-focused docs.

Minimum required documentation:

- how to run a mounted workspace worker with the official image
- how to build a derived worker image with `FROM ghcr.io/repository-owner/onestep-worker`
- what `ONESTEP_TARGET` means
- how dependency discovery works in Mode A
- common failure cases and how to diagnose them

Likely doc touch points:

- `README.md`
- a dedicated runtime image or deployment document under `docs/` or `deploy/`

## Risks And Tradeoffs

### Larger base image

Shipping `onestep[all]` makes the image easier to adopt but larger than a narrower connector-specific image. This is an explicit tradeoff in favor of product simplicity for the first version.

### Slower Mode A startup

If Mode A installs project dependencies at container start, startup time becomes sensitive to project size and network availability. That is acceptable because Mode A is meant to optimize ease of use, while Mode B remains the production-friendly path for teams that want fixed artifacts.

### Ambiguous `pyproject.toml` installs

Project-local `pyproject.toml` handling can vary depending on packaging shape. The implementation should keep the install rule narrow and document the expected supported project layout rather than trying to handle every Python packaging style in the first version.

### Runtime script complexity

Supporting both A and B in one entrypoint increases script responsibility slightly. This is acceptable because it centralizes behavior and avoids a larger product split.

## Implementation Boundaries

Expected implementation areas:

- container build files for the runtime image
- an entrypoint script that owns the startup flow
- CI changes for Docker build validation
- release workflow changes for GHCR multi-platform publish
- user-facing docs and examples for A and B usage

The implementation should stay focused on the runtime image product. It should not expand into unrelated deployment refactors or control-plane packaging work.
