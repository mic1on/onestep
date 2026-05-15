# Rollback Runbook

Use this runbook when a release passed image pull and rollout started, but migrations, startup, or smoke checks failed.

## Inputs Required

- The last known good API image tag.
- The last known good frontend image tag.
- The current `.env.deploy`.
- Access to the target host and registry.

## 1. Freeze Changes

- Stop any additional deploy attempts.
- Capture the failing logs before changing container state:

```bash
docker compose --env-file .env.deploy -f docker-compose.deploy.yml logs --tail=200 api frontend
```

## 2. Decide Whether Database Rollback Is Needed

- If the release failed before `docker compose ... run --rm migrate`, do not roll back the database. Revert only images or env vars.
- If the migration ran and was backward-compatible, prefer rolling back application images first.
- If the migration was destructive or the previous application version cannot run on the new schema, stop and use the matching Alembic downgrade or restore from backup before reopening traffic.

## 3. Revert Images

Edit `.env.deploy` and set:

- `ONESTEP_CP_API_IMAGE` to the last known good API image
- `ONESTEP_CP_FRONTEND_IMAGE` to the last known good frontend image

Then pull and restart only the application services:

```bash
docker compose --env-file .env.deploy -f docker-compose.deploy.yml pull api frontend
docker compose --env-file .env.deploy -f docker-compose.deploy.yml up -d api frontend
```

## 4. Revert Environment Variables If Needed

If the release changed configuration values, restore the previous `.env.deploy` values before restarting the services. Common examples:

- `ONESTEP_CP_DATABASE_URL`
- `ONESTEP_CP_CORS_ALLOW_ORIGINS`
- `ONESTEP_CP_CONSOLE_AUTH_USERNAME`
- `ONESTEP_CP_CONSOLE_AUTH_PASSWORD`
- `ONESTEP_CP_UI_API_BASE_URL`

Restart after reverting:

```bash
docker compose --env-file .env.deploy -f docker-compose.deploy.yml up -d api frontend
```

## 5. Re-Run Smoke

```bash
bash scripts/run-smoke.sh --compose-file docker-compose.deploy.yml --env-file .env.deploy
```

If smoke still fails, keep the rollback in place and escalate to manual investigation.

## 6. Database Downgrade Or Restore

Only execute this step if the previous application version is incompatible with the migrated schema.

- Preferred: restore the database from the most recent verified backup or snapshot.
- If you are using repository-managed backup artifacts, restore with:

```bash
bash scripts/restore-postgres.sh --env-file .env.deploy --input backups/<file>.dump --yes
```

- If an Alembic downgrade exists and has already been validated for this release, run it explicitly:

```bash
docker compose --env-file .env.deploy -f docker-compose.deploy.yml run --rm api alembic downgrade -1
```

Do not invent a downgrade command during the incident if the migration was never tested in reverse.

## 7. Closeout

- Save the relevant `docker compose logs`, failing smoke output, and image tags used.
- Record whether the failure happened in preflight, migration, rollout, or smoke.
- Open follow-up work for any missing migration rollback automation or monitoring gaps.
