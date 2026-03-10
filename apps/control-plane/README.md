# OneStep Control Plane

Monorepo for the OneStep control plane. This repository will host:

- `backend`: telemetry ingestion API and query API for the monitoring console
- `frontend`: web console

## API skeleton

Current scope:

- FastAPI application skeleton
- settings/config bootstrap
- `/healthz` and `/readyz` endpoints
- task-oriented SQLAlchemy data model for services, instances, task windows, and task events
- bearer-protected ingestion endpoints under `/api/v1/agents/*`
- query endpoints under `/api/v1/services*` for the future UI
- `frontend` monitoring console scaffold for service, task, and instance views

## Data model

`backend` now defines five core tables:

- `services`: logical service identity keyed by `(name, environment)`
- `instances`: runtime instance snapshot keyed by `instance_id`
- `task_definitions`: current service-level task topology/config snapshot
- `task_metric_windows`: task-level aggregated metric windows
- `task_events`: discrete task lifecycle events with idempotent `event_id`

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

The monitoring console can require a single shared username/password pair via
`ONESTEP_CP_CONSOLE_AUTH_USERNAME` and `ONESTEP_CP_CONSOLE_AUTH_PASSWORD`. When both are
set, the frontend shows a login page, query endpoints require the authenticated session
cookie, and `/docs`, `/redoc`, and `/openapi.json` are protected by the same login.

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
uv run alembic upgrade head
```

If port `5432` is already occupied locally, change `ONESTEP_CP_POSTGRES_PORT` and
`ONESTEP_CP_DATABASE_URL` in `.env` together.

## Docker Compose

The repository now includes a multi-stage `Dockerfile` and a full `docker-compose.yml`
for `postgres`, `api`, and `frontend`.

Bring the full stack up:

```bash
cp .env.example .env
docker compose up --build -d
```

Endpoints after startup:

- frontend: `http://127.0.0.1:4173`
- API: `http://127.0.0.1:8000`
- PostgreSQL: `127.0.0.1:5432`

The API container runs `alembic upgrade head` automatically before starting Uvicorn.
The frontend is built as static assets and served by Nginx, which proxies `/api/*`,
`/docs`, `/redoc`, `/healthz`, and `/readyz` back to the API container. At container
startup, Nginx writes `/app-config.js` from `ONESTEP_CP_UI_API_BASE_URL`, so you can
reuse the same frontend image across environments without rebuilding it. The default
runtime API base is `/`, which works with the bundled reverse proxy. If
`ONESTEP_CP_CONSOLE_AUTH_USERNAME` and
`ONESTEP_CP_CONSOLE_AUTH_PASSWORD` are set in `.env`, this local full-stack compose flow
also serves the login page and enforces console auth.

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
docker compose --env-file .env.deploy -f docker-compose.deploy.yml pull
docker compose --env-file .env.deploy -f docker-compose.deploy.yml up -d
```

Verify the rollout:

```bash
docker compose --env-file .env.deploy -f docker-compose.deploy.yml ps
curl -fsS http://127.0.0.1:${ONESTEP_CP_API_PORT:-8000}/readyz
```

When console auth is configured, the browser authenticates through the frontend login
page and receives an `HttpOnly` session cookie from the API. Agent ingestion remains
separate and still authenticates with `ONESTEP_CP_INGEST_TOKENS` only. The deploy file
also binds PostgreSQL and the raw API port to `127.0.0.1`, so external traffic should go
through the frontend port or an upstream reverse proxy.

The API image still runs `alembic upgrade head` automatically on startup, so schema
migrations are applied as part of the container restart.

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

Example heartbeat call:

```bash
curl -X POST http://127.0.0.1:8080/api/v1/agents/heartbeat \
  -H 'Authorization: Bearer dev-token' \
  -H 'Content-Type: application/json' \
  -d @heartbeat.json
```

Example topology sync call:

```bash
curl -X POST http://127.0.0.1:8080/api/v1/agents/sync \
  -H 'Authorization: Bearer dev-token' \
  -H 'Content-Type: application/json' \
  -d @sync.json
```

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

`GET /api/v1/services/{service_name}/tasks` now returns both runtime aggregates and the
latest service-level topology snapshot from `task_definitions`. `GET /api/v1/services/{service_name}/dashboard`
also exposes `topology_hashes` and `topology_consistent` to surface instance drift.

Run tests:

```bash
uv run pytest
```
