# Single Control Plane Image Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `onestep-control-plane` as one Docker image that serves the API, WebSocket endpoints, SSE, and built React console from a single ASGI process.

**Architecture:** Keep PostgreSQL as a separate service. Build the React app in a Node build stage, copy `frontend/dist` into the Python runtime image, and let FastAPI serve `/app-config.js`, `/assets/*`, and the SPA fallback after all API routes. Keep migrations as a one-shot compose service that uses the same image.

**Tech Stack:** FastAPI, Uvicorn, Vite/React, Docker BuildKit, Docker Compose, GitHub Actions, pytest, Vitest, Playwright.

---

## What Already Exists

- `Dockerfile` already has separate `api`, `frontend-build`, and `frontend` stages.
- `docker-compose.yml`, `docker-compose.deploy.yml`, and `docker-compose.nodb.yml` already model `api`, `frontend`, `migrate`, and optional `postgres`.
- `.github/workflows/ci.yml` already builds and publishes separate API/frontend images.
- `scripts/run-smoke.sh` already validates API readiness and frontend static assets.
- `docker/nginx/default.conf` currently proxies HTTP paths but does not configure WebSocket `Upgrade` headers.
- `frontend/index.html` already loads `/app-config.js` before the application bundle.
- `frontend/src/lib/runtime-config.ts` already reads `window.__APP_CONFIG__.apiBaseUrl`.

## NOT In Scope

- Do not bundle PostgreSQL into the app image. Database lifecycle, persistence, backup, and restore stay outside the plane image.
- Do not add Nginx, Supervisor, or another process manager inside the image. One container should run one Uvicorn process.
- Do not change agent, worker-agent, SSE, or console API paths. Existing clients should keep using the same URLs.
- Do not change authentication semantics. Session cookies, bearer ingest tokens, and worker-agent registration tokens stay unchanged.
- Do not redesign the frontend. This is packaging and serving only.

## Target Runtime Shape

```text
browser / runtime agent / worker agent
        |
        v
onestep-control-plane container, port 8000
  |-- /api/*                 FastAPI routers
  |-- /api/v1/agents/ws      agent WebSocket, direct ASGI
  |-- /api/v1/worker-agents/ws worker-agent WebSocket, direct ASGI
  |-- /api/v1/ui/stream      SSE, direct ASGI
  |-- /healthz /readyz       health and readiness
  |-- /app-config.js         runtime UI config
  |-- /assets/*              Vite static assets
  `-- /*                     SPA fallback, except API/docs/health paths
        |
        v
PostgreSQL container or external PostgreSQL
```

## File Structure

- Create `backend/src/onestep_control_plane_api/ui.py`: FastAPI UI asset routes, runtime config route, and SPA fallback guard.
- Create `backend/tests/test_ui_assets.py`: backend tests for runtime config, assets, SPA fallback, and API 404 guard.
- Modify `backend/src/onestep_control_plane_api/core/settings.py`: add `ui_dist_dir` and `ui_api_base_url` settings.
- Modify `backend/src/onestep_control_plane_api/main.py`: include UI routes last, after API/docs/health routes.
- Modify `Dockerfile`: replace the two final runtime targets with one `plane` target that includes backend dependencies and built UI assets.
- Modify `docker-compose.yml`: replace `api` + `frontend` with `plane`; keep `postgres` and `migrate`.
- Modify `docker-compose.deploy.yml`: use one `${ONESTEP_CP_IMAGE}` and one exposed plane port.
- Modify `docker-compose.nodb.yml`: same single-image shape, without bundled postgres.
- Modify `.env.example` and `.env.deploy.example`: introduce `ONESTEP_CP_IMAGE` and `ONESTEP_CP_PORT`; keep old variables only if compatibility is intentionally retained.
- Modify `scripts/release-preflight.sh`: validate `ONESTEP_CP_IMAGE` for deploy compose files.
- Modify `scripts/run-smoke.sh`: build/start `plane`, run `migrate`, and validate all endpoints on one base URL.
- Modify `.github/workflows/ci.yml`: build and publish one multi-arch image.
- Modify `.github/workflows/release-smoke.yml`: no service-name changes needed if it delegates to `scripts/run-smoke.sh`.
- Modify `README.md`, `docs/runbooks/release.md`, `docs/runbooks/rollback.md`, `docs/runbooks/backup-restore.md`, and `docs/runbooks/alerts.md`: replace two-image release instructions with one-image instructions.
- Delete `docker/nginx/default.conf` and `docker/nginx/write-runtime-config.sh` after no Docker target references them.

---

### Task 1: Add Failing Backend Tests For UI Serving

**Files:**
- Create: `backend/tests/test_ui_assets.py`
- Modify: none
- Test: `backend/tests/test_ui_assets.py`

- [ ] **Step 1: Write the failing tests**

```python
from collections.abc import Generator
from contextlib import contextmanager

from onestep_control_plane_api.core.settings import settings


@contextmanager
def override_ui_settings(tmp_path) -> Generator[None, None, None]:
    original_dist_dir = settings.ui_dist_dir
    original_api_base_url = settings.ui_api_base_url
    dist_dir = tmp_path / "frontend-dist"
    assets_dir = dist_dir / "assets"
    assets_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text(
        "<!doctype html><html><body><div id=\"root\"></div></body></html>",
        encoding="utf-8",
    )
    (assets_dir / "app.js").write_text("console.log('ok');", encoding="utf-8")
    settings.ui_dist_dir = str(dist_dir)
    settings.ui_api_base_url = "/"
    try:
        yield
    finally:
        settings.ui_dist_dir = original_dist_dir
        settings.ui_api_base_url = original_api_base_url


def test_app_config_js_uses_runtime_api_base_url(client, tmp_path) -> None:
    with override_ui_settings(tmp_path):
        settings.ui_api_base_url = "/plane/"

        response = client.get("/app-config.js")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert 'apiBaseUrl": "/plane/"' in response.text


def test_serves_vite_asset_from_configured_dist_dir(client, tmp_path) -> None:
    with override_ui_settings(tmp_path):
        response = client.get("/assets/app.js")

    assert response.status_code == 200
    assert "console.log('ok')" in response.text


def test_spa_fallback_serves_index_for_console_routes(client, tmp_path) -> None:
    with override_ui_settings(tmp_path):
        response = client.get("/services/billing-sync?environment=prod")

    assert response.status_code == 200
    assert "<div id=\"root\"></div>" in response.text


def test_api_404_does_not_return_spa_index(client, tmp_path) -> None:
    with override_ui_settings(tmp_path):
        response = client.get("/api/v1/definitely-missing")

    assert response.status_code == 404
    assert "text/html" not in response.headers.get("content-type", "")


def test_missing_ui_asset_returns_404(client, tmp_path) -> None:
    with override_ui_settings(tmp_path):
        response = client.get("/assets/missing.js")

    assert response.status_code == 404
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run:

```bash
uv run pytest backend/tests/test_ui_assets.py -q
```

Expected: FAIL because `settings.ui_dist_dir`, `settings.ui_api_base_url`, and UI routes do not exist yet.

- [ ] **Step 3: Commit the failing tests**

```bash
git add backend/tests/test_ui_assets.py
git commit -m "test: cover packaged control plane UI serving"
```

---

### Task 2: Implement FastAPI UI Asset Routes

**Files:**
- Create: `backend/src/onestep_control_plane_api/ui.py`
- Modify: `backend/src/onestep_control_plane_api/core/settings.py`
- Modify: `backend/src/onestep_control_plane_api/main.py`
- Test: `backend/tests/test_ui_assets.py`

- [ ] **Step 1: Add UI settings**

In `backend/src/onestep_control_plane_api/core/settings.py`, add these fields to `Settings` near the other console/UI settings:

```python
    ui_dist_dir: str = "frontend/dist"
    ui_api_base_url: str = "/"
```

- [ ] **Step 2: Create the UI router**

Create `backend/src/onestep_control_plane_api/ui.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse, Response

from onestep_control_plane_api.core.settings import settings

router = APIRouter(include_in_schema=False)

RESERVED_FIRST_PATH_SEGMENTS = {
    "api",
    "docs",
    "healthz",
    "openapi.json",
    "readyz",
    "redoc",
}


def _ui_dist_dir() -> Path:
    return Path(settings.ui_dist_dir).resolve()


def _safe_file(root: Path, relative_path: str) -> Path:
    resolved_root = root.resolve()
    candidate = (resolved_root / relative_path).resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")
    if not candidate.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")
    return candidate


def _index_file() -> Path:
    return _safe_file(_ui_dist_dir(), "index.html")


@router.get("/app-config.js")
def app_config_js() -> Response:
    payload = json.dumps(
        {"apiBaseUrl": settings.ui_api_base_url or "/"},
        ensure_ascii=False,
    )
    return Response(
        content=f"window.__APP_CONFIG__ = {payload};\n",
        media_type="application/javascript",
    )


@router.get("/assets/{asset_path:path}")
def vite_asset(asset_path: str) -> FileResponse:
    return FileResponse(_safe_file(_ui_dist_dir() / "assets", asset_path))


@router.get("/")
def console_index() -> FileResponse:
    return FileResponse(_index_file())


@router.get("/{full_path:path}")
def console_spa_fallback(full_path: str) -> FileResponse:
    first_segment = full_path.split("/", 1)[0]
    if first_segment in RESERVED_FIRST_PATH_SEGMENTS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return FileResponse(_index_file())
```

- [ ] **Step 3: Include the UI router last**

In `backend/src/onestep_control_plane_api/main.py`, import the router:

```python
from onestep_control_plane_api.ui import router as ui_router
```

Then include it at the end of `create_app()`, after `/docs`, `/redoc`, and `/openapi.json` routes:

```python
    app.include_router(ui_router)

    return app
```

The UI router must be included last so it cannot shadow API, docs, health, readiness, WebSocket, or SSE routes.

- [ ] **Step 4: Run the focused backend tests**

Run:

```bash
uv run pytest backend/tests/test_ui_assets.py -q
```

Expected: PASS.

- [ ] **Step 5: Run broader backend regression tests**

Run:

```bash
uv run pytest backend/tests/test_health.py backend/tests/test_console_auth.py backend/tests/test_agent_ws.py backend/tests/test_worker_agent_ws.py backend/tests/test_ui_stream.py -q
```

Expected: PASS. This verifies health/readiness, auth cookies, WebSocket routes, and SSE routes still win over the SPA fallback.

- [ ] **Step 6: Commit the backend UI serving implementation**

```bash
git add backend/src/onestep_control_plane_api/core/settings.py backend/src/onestep_control_plane_api/main.py backend/src/onestep_control_plane_api/ui.py backend/tests/test_ui_assets.py
git commit -m "feat: serve control plane UI from the API process"
```

---

### Task 3: Collapse The Dockerfile To One Runtime Image

**Files:**
- Modify: `Dockerfile`
- Delete after references are removed: `docker/nginx/default.conf`
- Delete after references are removed: `docker/nginx/write-runtime-config.sh`

- [ ] **Step 1: Replace the Dockerfile with one final `plane` target**

Use this structure in `Dockerfile`:

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS api-base

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}"

COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY backend ./backend
COPY scripts ./scripts

RUN uv sync --frozen --no-dev


FROM node:22-alpine AS frontend-build

WORKDIR /app

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY frontend/package.json frontend/package.json

RUN corepack enable
RUN pnpm install --frozen-lockfile

COPY frontend ./frontend

RUN pnpm ui:build


FROM api-base AS plane

ENV ONESTEP_CP_UI_DIST_DIR=/app/frontend/dist \
    ONESTEP_CP_UI_API_BASE_URL=/

COPY --from=frontend-build /app/frontend/dist /app/frontend/dist

EXPOSE 8000

CMD ["uvicorn", "onestep_control_plane_api.main:app", "--app-dir", "backend/src", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Build the new image locally**

Run:

```bash
docker build --target plane -t onestep-control-plane:single .
```

Expected: build succeeds and the image contains both `/app/.venv` and `/app/frontend/dist`.

- [ ] **Step 3: Remove unused Nginx files**

Run this search before deleting:

```bash
rg -n "docker/nginx|nginx|frontend-build|target: frontend|onestep-control-plane-frontend" Dockerfile docker-compose*.yml .github scripts README.md docs .env* 
```

Expected before deletion: only references that will be removed by later tasks remain. Delete `docker/nginx/default.conf` and `docker/nginx/write-runtime-config.sh` only after compose and docs no longer reference them.

- [ ] **Step 4: Commit the Dockerfile change**

```bash
git add Dockerfile
git commit -m "build: produce a single control plane image"
```

---

### Task 4: Convert Local Compose To `plane + migrate + postgres`

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Test: `scripts/run-smoke.sh`

- [ ] **Step 1: Update `.env.example`**

Replace `ONESTEP_CP_API_PORT`, `ONESTEP_CP_FRONTEND_PORT`, and image split variables with one port/image shape:

```dotenv
ONESTEP_CP_IMAGE=onestep-control-plane:latest
ONESTEP_CP_PORT=4173
ONESTEP_CP_UI_API_BASE_URL=/
```

Keep database, auth, token, retention, and smoke variables unchanged.

- [ ] **Step 2: Update `docker-compose.yml`**

Use this service shape:

```yaml
x-plane-build: &plane-build
  context: .
  dockerfile: Dockerfile
  target: plane

services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${ONESTEP_CP_POSTGRES_DB:-onestep_control_plane}
      POSTGRES_USER: ${ONESTEP_CP_POSTGRES_USER:-postgres}
      POSTGRES_PASSWORD: ${ONESTEP_CP_POSTGRES_PASSWORD:-postgres}
    ports:
      - "${ONESTEP_CP_POSTGRES_PORT:-5432}:5432"
    healthcheck:
      test:
        [
          "CMD-SHELL",
          "pg_isready -U ${ONESTEP_CP_POSTGRES_USER:-postgres} -d ${ONESTEP_CP_POSTGRES_DB:-onestep_control_plane}",
        ]
      interval: 5s
      timeout: 5s
      retries: 10
      start_period: 5s
    volumes:
      - onestep-control-plane-postgres-data:/var/lib/postgresql/data

  plane:
    build: *plane-build
    image: ${ONESTEP_CP_IMAGE:-onestep-control-plane:latest}
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      ONESTEP_CP_APP_ENV: ${ONESTEP_CP_APP_ENV:-docker}
      ONESTEP_CP_DATABASE_URL: postgresql+psycopg://${ONESTEP_CP_POSTGRES_USER:-postgres}:${ONESTEP_CP_POSTGRES_PASSWORD:-postgres}@postgres:5432/${ONESTEP_CP_POSTGRES_DB:-onestep_control_plane}
      ONESTEP_CP_INGEST_TOKENS: ${ONESTEP_CP_INGEST_TOKENS:-dev-token}
      ONESTEP_CP_WORKER_AGENT_REGISTRATION_TOKENS: ${ONESTEP_CP_WORKER_AGENT_REGISTRATION_TOKENS:-}
      ONESTEP_CP_CONNECTOR_SECRET_KEY: ${ONESTEP_CP_CONNECTOR_SECRET_KEY:-}
      ONESTEP_CP_CONSOLE_AUTH_USERNAME: ${ONESTEP_CP_CONSOLE_AUTH_USERNAME:-}
      ONESTEP_CP_CONSOLE_AUTH_PASSWORD: ${ONESTEP_CP_CONSOLE_AUTH_PASSWORD:-}
      ONESTEP_CP_CORS_ALLOW_ORIGINS: ${ONESTEP_CP_CORS_ALLOW_ORIGINS:-}
      ONESTEP_CP_INSTANCE_OFFLINE_AFTER_S: ${ONESTEP_CP_INSTANCE_OFFLINE_AFTER_S:-90}
      ONESTEP_CP_INSTANCE_HEALTH_PARTICIPATION_WINDOW_S: ${ONESTEP_CP_INSTANCE_HEALTH_PARTICIPATION_WINDOW_S:-3600}
      ONESTEP_CP_UI_API_BASE_URL: ${ONESTEP_CP_UI_API_BASE_URL:-/}
      TZ: ${ONESTEP_CP_TIMEZONE:-Asia/Shanghai}
    ports:
      - "${ONESTEP_CP_PORT:-4173}:8000"
    healthcheck:
      test:
        [
          "CMD",
          "python",
          "-c",
          "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz').read()",
        ]
      interval: 10s
      timeout: 5s
      retries: 10
      start_period: 10s

  migrate:
    build: *plane-build
    image: ${ONESTEP_CP_IMAGE:-onestep-control-plane:latest}
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      ONESTEP_CP_APP_ENV: ${ONESTEP_CP_APP_ENV:-docker}
      ONESTEP_CP_DATABASE_URL: postgresql+psycopg://${ONESTEP_CP_POSTGRES_USER:-postgres}:${ONESTEP_CP_POSTGRES_PASSWORD:-postgres}@postgres:5432/${ONESTEP_CP_POSTGRES_DB:-onestep_control_plane}
      TZ: ${ONESTEP_CP_TIMEZONE:-Asia/Shanghai}
    command: ["/app/.venv/bin/python", "-m", "alembic", "-c", "/app/alembic.ini", "upgrade", "head"]
    restart: "no"
    profiles: ["tools"]

volumes:
  onestep-control-plane-postgres-data:
```

- [ ] **Step 3: Validate compose config**

Run:

```bash
cp .env.example .env
docker compose --env-file .env -f docker-compose.yml config >/tmp/onestep-compose-single.yml
```

Expected: command exits 0, and `/tmp/onestep-compose-single.yml` contains a `plane` service and no `frontend` service.

- [ ] **Step 4: Commit local compose changes**

```bash
git add docker-compose.yml .env.example
git commit -m "deploy: run local compose with one plane service"
```

---

### Task 5: Convert Deploy Compose And Preflight

**Files:**
- Modify: `docker-compose.deploy.yml`
- Modify: `docker-compose.nodb.yml`
- Modify: `.env.deploy.example`
- Modify: `scripts/release-preflight.sh`

- [ ] **Step 1: Update `.env.deploy.example`**

Use one image and one app port:

```dotenv
ONESTEP_CP_IMAGE=registry.example.com/onestep-control-plane:latest

ONESTEP_CP_APP_ENV=prod
ONESTEP_CP_INGEST_TOKENS=replace-me
ONESTEP_CP_CORS_ALLOW_ORIGINS=
ONESTEP_CP_INSTANCE_OFFLINE_AFTER_S=90
ONESTEP_CP_INSTANCE_HEALTH_PARTICIPATION_WINDOW_S=3600
ONESTEP_CP_RETENTION_RUN_INTERVAL_S=86400
ONESTEP_CP_TIMEZONE=Asia/Shanghai
ONESTEP_CP_PORT=4173
ONESTEP_CP_UI_API_BASE_URL=/

ONESTEP_CP_POSTGRES_DB=onestep_control_plane
ONESTEP_CP_POSTGRES_USER=postgres
ONESTEP_CP_POSTGRES_PASSWORD=change-me
ONESTEP_CP_POSTGRES_PORT=5432

# Required when using docker-compose.nodb.yml instead of the bundled postgres service.
# ONESTEP_CP_DATABASE_URL=postgresql+psycopg://postgres:change-me@db.example.com:5432/onestep_control_plane

# Recommended for production-style console access.
# ONESTEP_CP_CONSOLE_AUTH_USERNAME=admin
# ONESTEP_CP_CONSOLE_AUTH_PASSWORD=change-me
```

- [ ] **Step 2: Update deploy compose files**

In `docker-compose.deploy.yml` and `docker-compose.nodb.yml`, replace `api` and `frontend` services with a single `plane` service using `${ONESTEP_CP_IMAGE}`. Keep the `migrate` service and point it at the same image.

For deploy with bundled postgres, the `plane` service should bind:

```yaml
ports:
  - "${ONESTEP_CP_PORT:-4173}:8000"
```

For nodb, keep the external database URL behavior:

```yaml
ONESTEP_CP_DATABASE_URL: ${ONESTEP_CP_DATABASE_URL:?set ONESTEP_CP_DATABASE_URL}
```

- [ ] **Step 3: Update preflight validation**

In `scripts/release-preflight.sh`, replace the deploy image checks:

```sh
if [ "$(basename "$COMPOSE_PATH")" = "docker-compose.deploy.yml" ]; then
  : "${ONESTEP_CP_IMAGE:?set ONESTEP_CP_IMAGE in the deploy env file}"
  : "${ONESTEP_CP_INGEST_TOKENS:?set ONESTEP_CP_INGEST_TOKENS in the deploy env file}"
fi
```

Also apply the same `ONESTEP_CP_IMAGE` check when the basename is `docker-compose.nodb.yml`.

- [ ] **Step 4: Validate deploy compose config**

Run:

```bash
bash scripts/release-preflight.sh --compose-file docker-compose.deploy.yml --env-file .env.deploy.example
ONESTEP_CP_DATABASE_URL=postgresql+psycopg://postgres:change-me@db.example.com:5432/onestep_control_plane ONESTEP_CP_CONSOLE_AUTH_USERNAME=admin ONESTEP_CP_CONSOLE_AUTH_PASSWORD=change-me bash scripts/release-preflight.sh --compose-file docker-compose.nodb.yml --env-file .env.deploy.example
```

Expected: deploy preflight passes for bundled postgres, and nodb preflight passes when explicit external PostgreSQL and console auth values are supplied by the command environment.

- [ ] **Step 5: Commit deploy compose changes**

```bash
git add docker-compose.deploy.yml docker-compose.nodb.yml .env.deploy.example scripts/release-preflight.sh
git commit -m "deploy: use one published control plane image"
```

---

### Task 6: Update Smoke Script For One Service

**Files:**
- Modify: `scripts/run-smoke.sh`
- Test: `scripts/run-smoke.sh`

- [ ] **Step 1: Change the default URLs**

In `scripts/run-smoke.sh`, replace API/frontend URL defaults with one base URL:

```sh
BASE_URL="${ONESTEP_CP_SMOKE_BASE_URL:-http://127.0.0.1:${ONESTEP_CP_PORT:-4173}}"
API_URL="${ONESTEP_CP_SMOKE_API_URL:-$BASE_URL}"
FRONTEND_URL="${ONESTEP_CP_SMOKE_FRONTEND_URL:-$BASE_URL}"
```

- [ ] **Step 2: Change managed stack startup**

Replace:

```sh
compose build api frontend
compose up -d postgres
compose run --rm migrate
compose up -d api frontend
```

with:

```sh
compose build plane
compose up -d postgres
compose run --rm migrate
compose up -d plane
```

For `docker-compose.nodb.yml`, guard `compose up -d postgres` so it only runs when the compose file contains a `postgres` service:

```sh
if compose config --services | grep -qx postgres; then
  compose up -d postgres
fi
```

- [ ] **Step 3: Keep the endpoint assertions**

Keep the existing checks for:

```sh
$API_URL/healthz
$API_URL/readyz
$FRONTEND_URL/
$FRONTEND_URL/app-config.js
```

Expected: all four now hit the same `plane` container port.

- [ ] **Step 4: Run smoke with local compose**

Run:

```bash
cp .env.example .env
SMOKE_BUILD=1 SMOKE_MANAGE_STACK=1 SMOKE_CLEANUP=1 bash scripts/run-smoke.sh --compose-file docker-compose.yml --env-file .env
```

Expected: `smoke ok`, with both `api` and `frontend` URLs printed as the same base URL.

- [ ] **Step 5: Commit smoke changes**

```bash
git add scripts/run-smoke.sh
git commit -m "test: smoke the unified control plane service"
```

---

### Task 7: Publish One GHCR Image In CI

**Files:**
- Modify: `.github/workflows/ci.yml`
- Test: GitHub Actions syntax plus local Docker build

- [ ] **Step 1: Replace the docker job image builds**

In `.github/workflows/ci.yml`, replace separate API/frontend builds with:

```yaml
  docker:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build control plane image
        run: docker build --target plane -t onestep-control-plane:ci .
```

- [ ] **Step 2: Replace publish metadata and build-push steps**

Use one image name:

```yaml
      - name: Docker metadata
        id: meta
        uses: docker/metadata-action@v6
        with:
          images: ghcr.io/${{ github.repository_owner }}/onestep-control-plane
          tags: |
            type=raw,value=latest
            type=sha,format=long

      - name: Build and push control plane image
        uses: docker/build-push-action@v6
        with:
          context: .
          file: ./Dockerfile
          target: plane
          platforms: linux/amd64,linux/arm64
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
```

- [ ] **Step 3: Validate workflow syntax locally**

Run:

```bash
docker build --target plane -t onestep-control-plane:ci .
```

Expected: local build passes. GitHub workflow YAML remains valid.

- [ ] **Step 4: Commit CI publishing changes**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: publish one control plane image"
```

---

### Task 8: Update Release Documentation And Remove Nginx Artifacts

**Files:**
- Modify: `README.md`
- Modify: `docs/runbooks/release.md`
- Modify: `docs/runbooks/rollback.md`
- Modify: `docs/runbooks/backup-restore.md`
- Modify: `docs/runbooks/alerts.md`
- Delete: `docker/nginx/default.conf`
- Delete: `docker/nginx/write-runtime-config.sh`

- [ ] **Step 1: Replace image variable docs**

Replace all operator-facing references to:

```text
ONESTEP_CP_API_IMAGE
ONESTEP_CP_FRONTEND_IMAGE
ghcr.io/mic1on/onestep-control-plane
```

with:

```text
ONESTEP_CP_IMAGE
ghcr.io/mic1on/onestep-control-plane
```

- [ ] **Step 2: Replace compose command docs**

Replace commands like:

```bash
docker compose --env-file .env.deploy -f docker-compose.deploy.yml pull plane
docker compose --env-file .env.deploy -f docker-compose.deploy.yml up -d plane
```

with:

```bash
docker compose --env-file .env.deploy -f docker-compose.deploy.yml pull plane
docker compose --env-file .env.deploy -f docker-compose.deploy.yml up -d plane
```

Keep migration commands:

```bash
docker compose --env-file .env.deploy -f docker-compose.deploy.yml run --rm migrate
```

- [ ] **Step 3: Document the new one-port behavior**

Add this operator note to `README.md` under Docker Compose and Registry Deployment:

```markdown
The packaged control plane image serves the API, WebSocket endpoints, SSE stream, and React console on one port. `ONESTEP_CP_PORT` is the host port for the unified service. PostgreSQL remains a separate service or external database.
```

- [ ] **Step 4: Remove Nginx artifacts after references are gone**

Run:

```bash
rg -n "docker/nginx|nginx|ONESTEP_CP_FRONTEND_IMAGE|ONESTEP_CP_API_IMAGE|onestep-control-plane-frontend|onestep-control-plane-api" Dockerfile docker-compose*.yml .github scripts README.md docs/runbooks .env.example .env.deploy.example
```

Expected: no active deployment references remain. Then delete:

```text
docker/nginx/default.conf
docker/nginx/write-runtime-config.sh
```

- [ ] **Step 5: Commit docs and cleanup**

```bash
git add README.md docs/runbooks/release.md docs/runbooks/rollback.md docs/runbooks/backup-restore.md docs/runbooks/alerts.md Dockerfile docker-compose.yml docker-compose.deploy.yml docker-compose.nodb.yml .env.example .env.deploy.example scripts/release-preflight.sh scripts/run-smoke.sh .github/workflows/ci.yml
git rm docker/nginx/default.conf docker/nginx/write-runtime-config.sh
git commit -m "docs: document single-image control plane deployment"
```

---

### Task 9: Full Verification Pass

**Files:**
- No new files
- Test: full backend, frontend, Docker, compose, smoke

- [ ] **Step 1: Run backend tests**

```bash
uv run pytest
```

Expected: PASS.

- [ ] **Step 2: Run frontend tests and build**

```bash
pnpm install --frozen-lockfile
pnpm --dir frontend run test --run
pnpm ui:build
```

Expected: PASS.

- [ ] **Step 3: Run Playwright e2e**

```bash
pnpm --dir frontend exec playwright install --with-deps chromium
pnpm --dir frontend run e2e
```

Expected: PASS.

- [ ] **Step 4: Build the release image**

```bash
docker build --target plane -t onestep-control-plane:verify .
```

Expected: PASS.

- [ ] **Step 5: Run managed compose smoke**

```bash
cp .env.example .env
SMOKE_BUILD=1 SMOKE_MANAGE_STACK=1 SMOKE_CLEANUP=1 bash scripts/run-smoke.sh --compose-file docker-compose.yml --env-file .env
```

Expected: PASS with `smoke ok`.

- [ ] **Step 6: Manually verify WebSocket path is no longer behind Nginx**

Run:

```bash
docker compose --env-file .env -f docker-compose.yml up -d postgres
docker compose --env-file .env -f docker-compose.yml run --rm migrate
docker compose --env-file .env -f docker-compose.yml up -d plane
uv run pytest backend/tests/test_agent_ws.py backend/tests/test_worker_agent_ws.py -q
```

Expected: PASS. The tests exercise direct ASGI WebSocket routes.

- [ ] **Step 7: Commit any verification-only fixes**

If verification required small fixes, inspect and stage only the exact files printed by `git status --short`:

```bash
git status --short
git commit -m "fix: complete single-image verification"
```

Expected: if `git status --short` is empty, do not run `git commit`; if it lists files, run `git add` with those exact file paths before the commit.

---

## Failure Modes To Cover

```text
Path                         Failure                      Expected handling
/api/v1/missing              SPA fallback eats API 404     JSON 404, never index.html
/services/foo                Browser refresh on SPA route  index.html
/assets/missing.js           Missing built asset           404
/app-config.js               runtime API base changed      JS reflects env without rebuild
/api/v1/agents/ws            reverse-proxy Upgrade issue   avoided by direct ASGI route
/api/v1/ui/stream            SSE buffered by Nginx         avoided by direct ASGI route
/readyz                      DB/background not ready       503 from existing readiness
```

## Parallelization Strategy

Sequential implementation is safer for the first three tasks because `main.py`, settings, Dockerfile, and compose all depend on the same runtime shape.

After Task 4 lands, work can split:

| Lane | Work | Modules touched | Depends on |
| --- | --- | --- | --- |
| A | Deploy compose + preflight + smoke | root compose files, scripts | Task 4 |
| B | CI publish workflow | `.github/workflows` | Task 3 |
| C | Docs/runbooks cleanup | `README.md`, `docs/runbooks` | Task 4 |

Execution order: finish Tasks 1-4 sequentially, then run lanes A, B, and C in parallel worktrees. Merge A first because docs and CI should match the final service names from smoke.

## Success Criteria

- `docker build --target plane -t onestep-control-plane:verify .` succeeds.
- `docker compose` exposes one app service named `plane` plus `migrate` and optional `postgres`.
- `scripts/run-smoke.sh` passes against the unified service.
- `/`, `/app-config.js`, `/healthz`, `/readyz`, and `/api/v1/services` work on the same port.
- `/api/v1/agents/ws` and `/api/v1/worker-agents/ws` remain direct ASGI WebSocket routes.
- CI publishes only `ghcr.io/<owner>/onestep-control-plane:{latest,sha-*}`.
- Release and rollback docs mention one image variable: `ONESTEP_CP_IMAGE`.

## Self-Review

- Spec coverage: the plan covers static UI serving, one-image Docker build, local/deploy compose, CI publish, smoke, docs, and Nginx removal.
- Placeholder scan: no deferred implementation placeholders are present.
- Type consistency: settings names are `ui_dist_dir` and `ui_api_base_url`; environment variables are `ONESTEP_CP_UI_DIST_DIR` and `ONESTEP_CP_UI_API_BASE_URL`; the published image variable is `ONESTEP_CP_IMAGE`.
