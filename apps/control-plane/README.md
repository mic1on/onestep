# OneStep Control Plane

Deployable control-plane application in the OneStep monorepo. From the repository root,
enter this component with `cd apps/control-plane`. It contains:

- `backend`: agent WS ingress and query API for the monitoring console
- `frontend`: web console

## macOS Desktop Rewrite

The macOS desktop app is a new Electron workbench UI that reuses the FastAPI
control plane backend but does not reuse the old web console frontend. See
[`docs/macos-desktop-rewrite.md`](docs/macos-desktop-rewrite.md).

## API skeleton

Current scope:

- FastAPI application skeleton
- settings/config bootstrap
- `/healthz` and `/readyz` endpoints
- task-oriented SQLAlchemy data model for services, instances, task windows, and task events
- bearer-protected agent WS endpoint at `/api/v1/agents/ws`
- query endpoints under `/api/v1/services*` for the future UI
- design-time Pipeline Builder routes and UI for validating and exporting worker definitions
- `frontend` monitoring console scaffold for service, task, and instance views

When a saved Worker has a description, the Worker Builder emits it into the
generated runtime YAML as `reporter.service_description`.

## Data model

`backend` now defines these core tables:

- `services`: logical service identity keyed by `(name, environment)`
- `instances`: runtime instance snapshot keyed by `instance_id`
- `task_definitions`: current service-level task topology/config snapshot
- `task_metric_windows`: task-level aggregated metric windows
- `task_events`: discrete task lifecycle events with idempotent `event_id`
- `agent_sessions`: active and historical WS sessions per runtime instance
- `agent_commands`: control commands and lifecycle state per runtime instance

`services.description` stores optional service-level descriptions reported by
runtimes. Missing reporter fields leave the existing value unchanged; explicit
`null` or blank values clear it.

Production database configuration is supplied via `ONESTEP_CP_DATABASE_URL`, for example:

```bash
export ONESTEP_CP_DATABASE_URL='postgresql+psycopg://postgres:postgres@localhost:5432/onestep_control_plane'
```

Ingestion endpoints require bearer auth via `ONESTEP_CP_INGEST_TOKENS`, for example:

```bash
export ONESTEP_CP_INGEST_TOKENS='dev-token'
export ONESTEP_CP_INGEST_TOKENS='token-a,token-b'
export ONESTEP_CP_INGEST_TOKENS='["token-a","token-b"]'
```

Prometheus can scrape `GET /metrics` with the same bearer token. Workers do not
need to expose scrape ports; they push telemetry outbound to the plane, and the
plane exports retained runtime and custom handler metrics in Prometheus text
format.

In `dev`, the monitoring console can optionally use a single shared username/password pair
via `ONESTEP_CP_CONSOLE_AUTH_USERNAME` and `ONESTEP_CP_CONSOLE_AUTH_PASSWORD`. In
production-style environments, prefer database-backed local users instead of the shared
credential fallback.

Browser access to the query API is controlled by `ONESTEP_CP_CORS_ALLOW_ORIGINS`. It defaults to
no allowed browser origins, so configure it explicitly when the UI is hosted separately. For local
or demo environments you can allow all origins:

```bash
export ONESTEP_CP_CORS_ALLOW_ORIGINS='*'
```

Or set an explicit frontend origin:

```bash
export ONESTEP_CP_CORS_ALLOW_ORIGINS='http://192.168.1.214:5173'
export ONESTEP_CP_CORS_ALLOW_ORIGINS='http://localhost:5173,http://192.168.1.214:5173'
```

If console auth is enabled and the browser talks to the API across different origins,
use explicit origins instead of `*` so the session cookie can be sent with requests.

`/healthz` and `/readyz` expose `ingestion_auth_configured` so you can tell whether
ingestion bearer auth is configured before hitting an ingestion endpoint.

Instance online/offline status is derived server-side from `last_seen_at` with a default
timeout of `90` seconds. Override it with `ONESTEP_CP_INSTANCE_OFFLINE_AFTER_S` if needed.
Service health summaries use a separate participation window so long-stale historical
instances eventually stop counting toward fleet health; the default is `3600` seconds and
can be overridden with `ONESTEP_CP_INSTANCE_HEALTH_PARTICIPATION_WINDOW_S`.

## Environment Variables

The table below centralizes the `ONESTEP_CP_*` variables used by the current backend,
Docker Compose files, deploy flow, and `scripts/start-local.sh`.

| Variable | Scope | Default / Example | Meaning |
| --- | --- | --- | --- |
| `ONESTEP_CP_APP_ENV` | Backend | `dev` | Runtime environment label. Also affects API auth cookie behavior, such as whether cookies are marked `Secure` in production. |
| `ONESTEP_CP_DATABASE_URL` | Backend | `postgresql+psycopg://...` | SQLAlchemy DSN for the control plane database. In `.env.deploy`, leave it empty to use the bundled PostgreSQL container. |
| `ONESTEP_CP_INGEST_TOKENS` | Backend ingress | empty | Bearer tokens accepted by ingestion endpoints and the agent WS endpoint. Supports a single token, comma-separated string, or JSON array. |
| `ONESTEP_CP_CONNECTOR_SECRET` | Backend connectors | empty / `my-dev-secret` | Plain string used to derive the encryption key for connector secrets at rest. Required when using the Connectors feature to create, read, update, or compile connectors with stored secrets. |
| `ONESTEP_CP_CONSOLE_AUTH_USERNAME` | Backend auth | empty | Shared username for the monitoring console. Must be set together with `ONESTEP_CP_CONSOLE_AUTH_PASSWORD`. |
| `ONESTEP_CP_CONSOLE_AUTH_PASSWORD` | Backend auth | empty | Shared password for the monitoring console. Must be set together with `ONESTEP_CP_CONSOLE_AUTH_USERNAME`. |
| `ONESTEP_CP_CONSOLE_AUTH_SESSION_TTL_S` | Backend auth | `604800` | Console session lifetime in seconds. Advanced option for login cookie expiry. |
| `ONESTEP_CP_CONSOLE_LOGIN_MAX_FAILURES` | Backend auth | `5` | Failed console logins allowed for one username before a temporary lockout. |
| `ONESTEP_CP_CONSOLE_LOGIN_FAILURE_WINDOW_S` | Backend auth | `900` | Time window in seconds used to count failed console logins. |
| `ONESTEP_CP_CONSOLE_LOGIN_LOCKOUT_S` | Backend auth | `900` | Temporary console-login lockout duration in seconds after reaching the failure limit. |
| `ONESTEP_CP_PROMETHEUS_CACHE_TTL_S` | Backend metrics | `15` | Maximum in-process cache lifetime in seconds for the Prometheus scrape response. Set to `0` to disable caching. |
| `ONESTEP_CP_CONSOLE_BASE_URL` | Backend notifications | empty / `https://cp.example.com` | Optional public base URL for the monitoring console. When set, webhook notifications render clickable absolute detail links instead of relative paths. |
| `ONESTEP_CP_PIPELINE_CREDENTIALS_FERNET_KEY` | Backend pipeline builder | empty | Fernet key used to encrypt Pipeline Builder credentials. Required for production persistence across restarts and replicas. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. |
| `ONESTEP_CP_CORS_ALLOW_ORIGINS` | Backend CORS | empty | Browser origins allowed to call the query API. Use explicit origins instead of `*` when console auth is enabled across origins. |
| `ONESTEP_CP_INSTANCE_OFFLINE_AFTER_S` | Backend health | `90` | Threshold after which an instance with no fresh heartbeat is considered `offline`. |
| `ONESTEP_CP_INSTANCE_HEALTH_PARTICIPATION_WINDOW_S` | Backend health | `3600` | How long a recently seen or recently synced instance stays in the service health denominator. Must be greater than or equal to `ONESTEP_CP_INSTANCE_OFFLINE_AFTER_S`. |
| `ONESTEP_CP_RETENTION_TASK_EVENTS_DAYS` | Backend retention | `30` | Retention window for raw `task_events`, based on `occurred_at`. Older rows become cleanup candidates. |
| `ONESTEP_CP_RETENTION_TASK_METRIC_WINDOWS_DAYS` | Backend retention | `90` | Retention window for aggregated `task_metric_windows`, based on `window_ended_at`. |
| `ONESTEP_CP_RETENTION_AGENT_COMMANDS_DAYS` | Backend retention | `30` | Retention window for terminal `agent_commands`, based on `updated_at`. Pending or otherwise non-terminal commands are not deleted. |
| `ONESTEP_CP_RETENTION_DELETE_BATCH_SIZE` | Backend retention | `1000` | Maximum number of rows deleted per batch when retention runs in execute mode. |
| `ONESTEP_CP_RETENTION_RUN_INTERVAL_S` | Backend retention | `86400` | How often the API retention background worker executes automatic cleanup. The worker still participates in leader election so only one replica deletes rows at a time. |
| `ONESTEP_CP_API_RESPONSE_TIMEZONE` | Backend API | unset | Explicit timezone for datetime fields returned by the API. If unset, the backend falls back to container `TZ`, then `UTC`. |
| `ONESTEP_CP_DEBUG` | Backend API | `false` | Enables FastAPI debug mode. Advanced troubleshooting option. |
| `ONESTEP_CP_TIMEZONE` | Compose / deploy helper | `Asia/Shanghai` | Compose-level helper used to set container `TZ`. This indirectly affects API timestamp rendering when `ONESTEP_CP_API_RESPONSE_TIMEZONE` is unset. |
| `ONESTEP_CP_IMAGE` | Compose / deploy | `onestep-control-plane:latest` | Unified control plane image used by Compose services. |
| `ONESTEP_CP_PORT` | Compose / deploy / `scripts/start-local.sh` | `4173` | Host port bound to the unified control plane service. |
| `ONESTEP_CP_UI_DIST_DIR` | Packaged runtime | `frontend/dist` locally / `/app/frontend/dist` in Docker | Directory containing the built Vite console assets served by FastAPI. |
| `ONESTEP_CP_UI_API_BASE_URL` | Frontend runtime | `/` | Runtime API base path served from `/app-config.js` by the packaged control plane image. Not used by `pnpm dev`. |
| `ONESTEP_CP_POSTGRES_DB` | Bundled PostgreSQL | `onestep_control_plane` | Database name for the bundled PostgreSQL container used by local/deploy Compose files. |
| `ONESTEP_CP_POSTGRES_USER` | Bundled PostgreSQL | `postgres` | Database user for the bundled PostgreSQL container. |
| `ONESTEP_CP_POSTGRES_PASSWORD` | Bundled PostgreSQL | `postgres` locally | Database password for the bundled PostgreSQL container. Change this in real deployments. |
| `ONESTEP_CP_POSTGRES_PORT` | Bundled PostgreSQL | `5432` | Host port exposed for the bundled PostgreSQL container. If you change it locally, update `ONESTEP_CP_DATABASE_URL` to match. |
| `ONESTEP_CP_HOST` | `scripts/start-local.sh` | `0.0.0.0` | Bind host used by the local helper script. |
| `ONESTEP_CP_SQLITE_PATH` | `scripts/start-local.sh` | `.data/control-plane-dev.db` | SQLite file path used by the local helper script when `ONESTEP_CP_DATABASE_URL` is not set. |

For Vite frontend development, `frontend/.env` uses `VITE_API_BASE_URL`, which is separate
from the `ONESTEP_CP_*` runtime variables above.

Schema changes are managed with Alembic:

```bash
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "add foo"
```

## Local PostgreSQL

For local debugging, the repository includes a `docker-compose.yml` with PostgreSQL 16.

Bring the database up:

```bash
cp .env.example .env
docker compose up -d postgres
```

Wait for the healthcheck to pass, then apply schema migrations:

```bash
docker compose run --rm --build migrate
```

If port `5432` is already occupied locally, change `ONESTEP_CP_POSTGRES_PORT` and
`ONESTEP_CP_DATABASE_URL` in `.env` together.

## Docker Compose

The repository includes a multi-stage `Dockerfile` and a full `docker-compose.yml`
for `postgres`, `plane`, and `migrate`. This is a local development stack: both
published host ports bind to `127.0.0.1`, and the supplied `.env.example` enables
development authentication behavior. Use `.env.deploy.example` with
`docker-compose.deploy.yml` for a reachable deployment.

Bring the full stack up:

```bash
cp .env.example .env
bash scripts/release-preflight.sh --compose-file docker-compose.yml --env-file .env
docker compose build plane
docker compose up -d postgres
docker compose run --rm migrate
docker compose up --build -d plane
bash scripts/run-smoke.sh --compose-file docker-compose.yml --env-file .env
```

If you plan to use the Connectors feature, set `ONESTEP_CP_CONNECTOR_SECRET` in
`.env` to any non-empty string such as `my-dev-secret`. The backend derives the
actual encryption key internally; you do not need to generate a Fernet key.

Endpoints after startup:

- control plane: `http://127.0.0.1:4173`
- PostgreSQL: `127.0.0.1:5432`

The packaged control plane image starts Uvicorn and serves the API, WebSocket endpoints,
SSE stream, `/healthz`, `/readyz`, `/docs`, and the built React console on one port.
Schema migrations run through the one-shot `migrate` compose service so a bad migration
can stop the release before the application restarts. FastAPI serves `/app-config.js`
from `ONESTEP_CP_UI_API_BASE_URL`, so you can reuse the same image across environments
without rebuilding it. The default runtime API base is `/`, which works with the unified
service port. If
`ONESTEP_CP_CONSOLE_AUTH_USERNAME` and
`ONESTEP_CP_CONSOLE_AUTH_PASSWORD` are set in `.env`, this local full-stack compose flow
also serves the login page and enforces console auth.
The local `migrate` service reuses the same `onestep-control-plane:latest` image as the
`plane` service, so rebuilding `plane` also refreshes the migration runner.

To create database-backed local console users, run the helper scripts from the
control-plane directory:

```bash
uv run python scripts/create_local_admin.py --username admin
uv run python scripts/create_local_user.py --username viewer1 --role viewer
uv run python scripts/create_local_user.py --username operator1 --role operator
```

When using the bundled Docker Compose stack, the same scripts are available inside the
control plane container after rebuild:

```bash
docker compose exec plane /app/.venv/bin/python /app/scripts/create_local_user.py --username viewer1 --role viewer
```

For the first production-style deployment, bootstrap a local admin before opening the
console to operators:

```bash
docker compose --env-file .env.deploy -f docker-compose.deploy.yml run --rm \
  plane /app/.venv/bin/python /app/scripts/create_local_admin.py --username admin
```

The API now runs retention cleanup automatically in the background every
`ONESTEP_CP_RETENTION_RUN_INTERVAL_S` seconds. Manual runs remain available from the
control-plane directory for dry-run review or one-off forced execution:

```bash
uv run python scripts/run-retention.py --dry-run
uv run python scripts/run-retention.py --execute
```

`--dry-run` is the default safe mode. Use `--batch-size` if you need to override
`ONESTEP_CP_RETENTION_DELETE_BATCH_SIZE` for a one-off cleanup run.

PostgreSQL backup and restore helpers are also available from the control-plane directory:

```bash
bash scripts/backup-postgres.sh --env-file .env.deploy
bash scripts/restore-postgres.sh --env-file .env.deploy --input backups/<file>.dump --yes
```

## Registry Deployment

If you have already pushed the control plane image to a registry, use
`docker-compose.deploy.yml` instead of rebuilding from source on the server.

The `Control Plane` workflow publishes the unified image to GitHub Container Registry as a multi-architecture
image for `linux/amd64` and `linux/arm64` after a successful `push` to `main`. If you need to
republish a `main` revision without creating a new merge commit, open GitHub Actions and manually
run the `Control Plane` workflow with `workflow_dispatch` against `main`.

Prepare the deployment env file:

```bash
cp .env.deploy.example .env.deploy
```

Set at least these values in `.env.deploy`:

- `ONESTEP_CP_IMAGE`
- `ONESTEP_CP_INGEST_TOKENS`
- `ONESTEP_CP_CONSOLE_AUTH_USERNAME`
- `ONESTEP_CP_CONSOLE_AUTH_PASSWORD`
- `ONESTEP_CP_UI_API_BASE_URL`
- `ONESTEP_CP_POSTGRES_PASSWORD`

Use `docker-compose.deploy.yml` with the bundled `postgres` service, or use
`docker-compose.nodb.yml` and set `ONESTEP_CP_DATABASE_URL` for an external PostgreSQL
database.

Prefer commit-pinned image tags for real deployments:

```bash
ONESTEP_CP_IMAGE=ghcr.io/mic1on/onestep-control-plane:sha-<full git sha>
```

`latest` is also published, but `sha-<full git sha>` is the safer rollback and
audit trail for production-style rollouts.

Deploy on the target machine:

```bash
docker login <registry>
bash scripts/release-preflight.sh --compose-file docker-compose.deploy.yml --env-file .env.deploy
docker compose --env-file .env.deploy -f docker-compose.deploy.yml pull plane
docker compose --env-file .env.deploy -f docker-compose.deploy.yml up -d postgres
docker compose --env-file .env.deploy -f docker-compose.deploy.yml run --rm migrate
docker compose --env-file .env.deploy -f docker-compose.deploy.yml up -d plane
```

Verify the rollout:

```bash
docker compose --env-file .env.deploy -f docker-compose.deploy.yml ps
bash scripts/run-smoke.sh --compose-file docker-compose.deploy.yml --env-file .env.deploy
```

When console auth is configured, the browser authenticates through the login page and
receives an `HttpOnly` session cookie from the same control plane service. Agent ingestion
remains separate and still authenticates with `ONESTEP_CP_INGEST_TOKENS` only. External
traffic should go through the `plane` service port or an upstream reverse proxy.

Release and rollback procedures are documented in:

- `docs/runbooks/release.md`
- `docs/runbooks/rollback.md`
- `docs/runbooks/backup-restore.md`
- `docs/runbooks/alerts.md`

## Development

Install dependencies with `uv`:

```bash
uv sync --extra test
```

Run the API locally:

```bash
uv run uvicorn onestep_control_plane_api.main:app --app-dir backend/src --reload
```

## Frontend

`frontend` is a Vite + React + TypeScript monitoring console with:

- React Router for page routing
- TanStack Query for API state
- feature-oriented source layout under `src/features`
- pages for service list, service detail, task detail, and instance detail

Install frontend dependencies:

```bash
pnpm install
```

Run the UI locally:

```bash
cp frontend/.env.example frontend/.env
pnpm run dev
```

The Vite dev server binds to `0.0.0.0:5173` by default so you can access it from other
machines on the same network.

Build the UI:

```bash
pnpm ui:build
```

For local `pnpm` development, you can still set `VITE_API_BASE_URL` in `frontend/.env` if
the API is not running on `http://127.0.0.1:8000`. In the Docker/Nginx image, runtime
`ONESTEP_CP_UI_API_BASE_URL` takes precedence over the compiled fallback, so you can point
the same image at different API origins after build. If you open the UI from another
machine, make sure `ONESTEP_CP_CORS_ALLOW_ORIGINS` allows that frontend origin.

Quick local start with SQLite:

```bash
./scripts/start-local.sh
```

The script will:

- create a local SQLite database under `.data/`
- run `alembic upgrade head`
- start the API on `127.0.0.1:8080`
- default `ONESTEP_CP_INGEST_TOKENS` to `dev-token` if not already set

Agent telemetry and control now enter only through `GET /api/v1/agents/ws`.
The WS handshake uses the same ingest token configured by `ONESTEP_CP_INGEST_TOKENS`.
Telemetry payloads still reuse the historical heartbeat/sync/metrics/events body shapes
inside the WS `telemetry` envelope.
When agents and the plane both support `telemetry.custom_metrics`, handler
counter/gauge samples are included in metrics telemetry and later exported from
`/metrics`.
On API startup, previously persisted `agent_sessions.status=active` rows are reconciled
to `disconnected` before new agents reconnect, so stale sessions from an earlier process
do not stay eligible for dashboards or command dispatch.

WS rollout and cutover notes live in `docs/migrations/ws-agent-cutover.md`.

Query API endpoints:

- `GET /api/v1/services`
- `GET /api/v1/services/{service_name}?environment=prod`
- `GET /api/v1/services/{service_name}/dashboard?environment=prod`
- `GET /api/v1/services/{service_name}/instances?environment=prod`
- `GET /api/v1/services/{service_name}/instances/{instance_id}?environment=prod`
- `GET /api/v1/services/{service_name}/tasks?environment=prod`
- `GET /api/v1/services/{service_name}/tasks/{task_name}?environment=prod`
- `GET /api/v1/services/{service_name}/metric-windows?environment=prod`
- `GET /api/v1/services/{service_name}/events?environment=prod`
- `GET /api/v1/services/{service_name}/commands?environment=prod`
- `GET /api/v1/services/{service_name}/sessions?environment=prod`
- `GET /api/v1/instances/{instance_id}/commands`

`GET /api/v1/services/{service_name}/tasks` now returns both runtime aggregates and the
latest service-level topology snapshot from `task_definitions`. `GET /api/v1/services/{service_name}/dashboard`
also exposes `topology_hashes` and `topology_consistent` to surface instance drift.
Service list, detail, and dashboard summaries include `description`; the
monitoring console displays it in the service list/detail views and includes it
in service-list search.

Run tests:

```bash
uv run pytest
```
