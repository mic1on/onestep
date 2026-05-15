# Release Runbook

This runbook assumes the API image no longer runs Alembic automatically at container startup. Schema changes are a separate release step and must complete before the API rollout.

## Preconditions

- A release candidate commit passed `.github/workflows/ci.yml`.
- Target host has Docker Engine with the Compose plugin.
- Registry credentials are available on the target host.
- Release operator has prepared `.env.deploy` from `.env.deploy.example`.

## 1. Preflight

Run the static checks before touching the running stack:

```bash
bash scripts/release-preflight.sh --compose-file docker-compose.deploy.yml --env-file .env.deploy
```

This validates:

- Docker and `docker compose` are installed.
- `docker-compose.deploy.yml` renders successfully.
- The deploy env file contains required image and ingestion auth settings.
- Bundled PostgreSQL has a password if `ONESTEP_CP_DATABASE_URL` is unset.

## 2. Pull Images

```bash
docker login <registry>
docker compose --env-file .env.deploy -f docker-compose.deploy.yml pull api frontend
```

If the deployment uses the bundled PostgreSQL container for the first time, also pull `postgres`:

```bash
docker compose --env-file .env.deploy -f docker-compose.deploy.yml pull postgres
```

## 3. Start Or Refresh PostgreSQL

Bring the database dependency up first so the migration step has a stable target:

```bash
docker compose --env-file .env.deploy -f docker-compose.deploy.yml up -d postgres
```

## 4. Apply Migrations Explicitly

Run Alembic as a one-shot task. If this step fails, stop the release and do not restart the API service.

```bash
docker compose --env-file .env.deploy -f docker-compose.deploy.yml run --rm migrate
```

Expected result: the command exits `0` after `alembic upgrade head`.

## 5. Roll Out API And Frontend

```bash
docker compose --env-file .env.deploy -f docker-compose.deploy.yml up -d api frontend
```

If this is the first rollout into a fresh production-style environment, bootstrap the
first local admin before handing the console to operators:

```bash
docker compose --env-file .env.deploy -f docker-compose.deploy.yml run --rm \
  api /app/.venv/bin/python /app/scripts/create_local_admin.py --username admin
```

## 6. Smoke Test

Run smoke checks against the deployed stack:

```bash
bash scripts/run-smoke.sh --compose-file docker-compose.deploy.yml --env-file .env.deploy
```

The smoke script verifies:

- `GET /healthz` returns HTTP `200`
- `GET /readyz` returns HTTP `200` with `"status":"ready"`
- the packaged frontend serves `/`
- the frontend runtime config is present at `/app-config.js`

## 7. Post-Release Checks

- Confirm `docker compose --env-file .env.deploy -f docker-compose.deploy.yml ps` shows `api` and `frontend` healthy.
- Confirm the login page and a read-only page load manually through the frontend port or upstream reverse proxy.
- Record the deployed image tags and release timestamp in the release log used by your team.

## Failure Policy

- `release-preflight.sh` failure: fix config or host tooling before proceeding.
- `migrate` failure: stop immediately. Do not restart `api`. Investigate the migration and use the rollback runbook if a partial change reached production.
- `run-smoke.sh` failure after rollout: treat the release as failed and begin rollback unless the issue is clearly isolated to a non-production path.
