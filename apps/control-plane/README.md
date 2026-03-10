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

Set `VITE_API_BASE_URL` in `frontend/.env` if the API is not running on `http://127.0.0.1:8000`.
If you open the UI from another machine, point `VITE_API_BASE_URL` at the externally reachable
API address and make sure `ONESTEP_CP_CORS_ALLOW_ORIGINS` allows that frontend origin.

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
