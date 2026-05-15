# OneStep Control Plane

Monorepo for the OneStep control plane. This repository will host:

- `backend`: agent WS ingress and query API for the monitoring console
- `frontend`: web console

## API skeleton

Current scope:

- FastAPI application skeleton
- settings/config bootstrap
- `/healthz` and `/readyz` endpoints
- task-oriented SQLAlchemy data model for services, instances, task windows, and task events
- bearer-protected agent WS endpoint at `/api/v1/agents/ws`
- query endpoints under `/api/v1/services*` for the future UI
- `frontend` monitoring console scaffold for service, task, and instance views

## Data model

`backend` now defines these core tables:

- `services`: logical service identity keyed by `(name, environment)`
- `instances`: runtime instance snapshot keyed by `instance_id`
- `task_definitions`: current service-level task topology/config snapshot
- `task_metric_windows`: task-level aggregated metric windows
- `task_events`: discrete task lifecycle events with idempotent `event_id`
- `agent_sessions`: active and historical WS sessions per runtime instance
- `agent_commands`: control commands and lifecycle state per runtime instance

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
| `ONESTEP_CP_CONSOLE_AUTH_USERNAME` | Backend auth | empty | Shared username for the monitoring console. Must be set together with `ONESTEP_CP_CONSOLE_AUTH_PASSWORD`. |
| `ONESTEP_CP_CONSOLE_AUTH_PASSWORD` | Backend auth | empty | Shared password for the monitoring console. Must be set together with `ONESTEP_CP_CONSOLE_AUTH_USERNAME`. |
| `ONESTEP_CP_CONSOLE_AUTH_SESSION_TTL_S` | Backend auth | `604800` | Console session lifetime in seconds. Advanced option for login cookie expiry. |
| `ONESTEP_CP_CONSOLE_BASE_URL` | Backend notifications | empty / `https://cp.example.com` | Optional public base URL for the monitoring console. When set, webhook notifications render clickable absolute detail links instead of relative paths. |
| `ONESTEP_CP_CORS_ALLOW_ORIGINS` | Backend CORS | empty | Browser origins allowed to call the query API. Use explicit origins instead of `*` when console auth is enabled across origins. |
| `ONESTEP_CP_INSTANCE_OFFLINE_AFTER_S` | Backend health | `90` | Threshold after which an instance with no fresh heartbeat is considered `offline`. |
| `ONESTEP_CP_INSTANCE_HEALTH_PARTICIPATION_WINDOW_S` | Backend health | `3600` | How long a recently seen or recently synced instance stays in the service health denominator. Must be greater than or equal to `ONESTEP_CP_INSTANCE_OFFLINE_AFTER_S`. |
| `ONESTEP_CP_API_RESPONSE_TIMEZONE` | Backend API | unset | Explicit timezone for datetime fields returned by the API. If unset, the backend falls back to container `TZ`, then `UTC`. |
| `ONESTEP_CP_DEBUG` | Backend API | `false` | Enables FastAPI debug mode. Advanced troubleshooting option. |
| `ONESTEP_CP_TIMEZONE` | Compose / deploy helper | `Asia/Shanghai` | Compose-level helper used to set container `TZ`. This indirectly affects API timestamp rendering when `ONESTEP_CP_API_RESPONSE_TIMEZONE` is unset. |
| `ONESTEP_CP_API_PORT` | Compose / deploy | `8000` | Host port bound to the raw API container. |
| `ONESTEP_CP_FRONTEND_PORT` | Compose / deploy | `4173` | Host port bound to the packaged frontend container. |
| `ONESTEP_CP_UI_API_BASE_URL` | Frontend runtime | `/` | Runtime API base path written into `/app-config.js` for the packaged frontend image. Not used by `pnpm dev`. |
| `ONESTEP_CP_POSTGRES_DB` | Bundled PostgreSQL | `onestep_control_plane` | Database name for the bundled PostgreSQL container used by local/deploy Compose files. |
| `ONESTEP_CP_POSTGRES_USER` | Bundled PostgreSQL | `postgres` | Database user for the bundled PostgreSQL container. |
| `ONESTEP_CP_POSTGRES_PASSWORD` | Bundled PostgreSQL | `postgres` locally | Database password for the bundled PostgreSQL container. Change this in real deployments. |
| `ONESTEP_CP_POSTGRES_PORT` | Bundled PostgreSQL | `5432` | Host port exposed for the bundled PostgreSQL container. If you change it locally, update `ONESTEP_CP_DATABASE_URL` to match. |
| `ONESTEP_CP_API_IMAGE` | Deploy only | `registry.example.com/onestep-control-plane-api:latest` | API image reference used by `docker-compose.deploy.yml`. |
| `ONESTEP_CP_FRONTEND_IMAGE` | Deploy only | `registry.example.com/onestep-control-plane-frontend:latest` | Frontend image reference used by `docker-compose.deploy.yml`. |
| `ONESTEP_CP_HOST` | `scripts/start-local.sh` | `0.0.0.0` | Bind host used by the local helper script. |
| `ONESTEP_CP_PORT` | `scripts/start-local.sh` | `8080` | Bind port used by the local helper script. |
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
docker compose run --rm migrate
```

If port `5432` is already occupied locally, change `ONESTEP_CP_POSTGRES_PORT` and
`ONESTEP_CP_DATABASE_URL` in `.env` together.

## Docker Compose

The repository now includes a multi-stage `Dockerfile` and a full `docker-compose.yml`
for `postgres`, `api`, and `frontend`.

Bring the full stack up:

```bash
cp .env.example .env
bash scripts/release-preflight.sh --compose-file docker-compose.yml --env-file .env
docker compose up --build -d postgres
docker compose run --rm migrate
docker compose up --build -d api frontend
bash scripts/run-smoke.sh --compose-file docker-compose.yml --env-file .env
```

Endpoints after startup:

- frontend: `http://127.0.0.1:4173`
- API: `http://127.0.0.1:8000`
- PostgreSQL: `127.0.0.1:5432`

The API container now starts Uvicorn only. Schema migrations run through the one-shot
`migrate` compose service so a bad migration can stop the release before the API
restarts. The frontend is built as static assets and served by Nginx, which proxies `/api/*`,
`/docs`, `/redoc`, `/healthz`, and `/readyz` back to the API container. At container
startup, Nginx writes `/app-config.js` from `ONESTEP_CP_UI_API_BASE_URL`, so you can
reuse the same frontend image across environments without rebuilding it. The default
runtime API base is `/`, which works with the bundled reverse proxy. If
`ONESTEP_CP_CONSOLE_AUTH_USERNAME` and
`ONESTEP_CP_CONSOLE_AUTH_PASSWORD` are set in `.env`, this local full-stack compose flow
also serves the login page and enforces console auth.

To create database-backed local console users, use the helper scripts from the repo root:

```bash
uv run python scripts/create_local_admin.py --username admin
uv run python scripts/create_local_user.py --username viewer1 --role viewer
uv run python scripts/create_local_user.py --username operator1 --role operator
```

When using the bundled Docker Compose stack, the same scripts are available inside the
API container after rebuild:

```bash
docker exec -it onestep-control-plane-api-1 /app/.venv/bin/python /app/scripts/create_local_user.py --username viewer1 --role viewer
```

For the first production-style deployment, bootstrap a local admin before opening the
console to operators:

```bash
docker compose --env-file .env.deploy -f docker-compose.deploy.yml run --rm \
  api /app/.venv/bin/python /app/scripts/create_local_admin.py --username admin
```

## Registry Deployment

If you have already pushed the API and frontend images to a registry, use
`docker-compose.deploy.yml` instead of rebuilding from source on the server.

Prepare the deployment env file:

```bash
cp .env.deploy.example .env.deploy
```

Set at least these values in `.env.deploy`:

- `ONESTEP_CP_API_IMAGE`
- `ONESTEP_CP_FRONTEND_IMAGE`
- `ONESTEP_CP_INGEST_TOKENS`
- `ONESTEP_CP_CONSOLE_AUTH_USERNAME`
- `ONESTEP_CP_CONSOLE_AUTH_PASSWORD`
- `ONESTEP_CP_UI_API_BASE_URL`
- `ONESTEP_CP_POSTGRES_PASSWORD`

`ONESTEP_CP_DATABASE_URL` is optional. Leave it empty to use the bundled `postgres`
service. If you set it to an external PostgreSQL DSN, the API will use that database,
but this deployment file still starts the bundled `postgres` container unless you trim
the compose file for your environment.

Deploy on the target machine:

```bash
docker login <registry>
bash scripts/release-preflight.sh --compose-file docker-compose.deploy.yml --env-file .env.deploy
docker compose --env-file .env.deploy -f docker-compose.deploy.yml pull api frontend
docker compose --env-file .env.deploy -f docker-compose.deploy.yml up -d postgres
docker compose --env-file .env.deploy -f docker-compose.deploy.yml run --rm migrate
docker compose --env-file .env.deploy -f docker-compose.deploy.yml up -d api frontend
```

Verify the rollout:

```bash
docker compose --env-file .env.deploy -f docker-compose.deploy.yml ps
bash scripts/run-smoke.sh --compose-file docker-compose.deploy.yml --env-file .env.deploy
```

When console auth is configured, the browser authenticates through the frontend login
page and receives an `HttpOnly` session cookie from the API. Agent ingestion remains
separate and still authenticates with `ONESTEP_CP_INGEST_TOKENS` only. The deploy file
also binds PostgreSQL and the raw API port to `127.0.0.1`, so external traffic should go
through the frontend port or an upstream reverse proxy.

Release and rollback procedures are documented in:

- `docs/runbooks/release.md`
- `docs/runbooks/rollback.md`

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

Run tests:

```bash
uv run pytest
```
