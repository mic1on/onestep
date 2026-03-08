# OneStep Control Plane

Monorepo for the OneStep control plane. This repository will host:

- `apps/api`: telemetry ingestion API and query API for the monitoring console
- `apps/ui`: future web console

## API skeleton

Current scope:

- FastAPI application skeleton
- settings/config bootstrap
- `/healthz` and `/readyz` endpoints
- task-oriented SQLAlchemy data model for services, instances, task windows, and task events
- bearer-protected ingestion endpoints under `/api/v1/agents/*`
- query endpoints under `/api/v1/services*` for the future UI

## Data model

`apps/api` now defines five core tables:

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
uv run uvicorn onestep_control_plane_api.main:app --app-dir apps/api/src --reload
```

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
curl -X POST http://127.0.0.1:8000/api/v1/agents/heartbeat \
  -H 'Authorization: Bearer dev-token' \
  -H 'Content-Type: application/json' \
  -d @heartbeat.json
```

Example topology sync call:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/agents/sync \
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
