# Backup And Restore Runbook

This runbook covers repeatable PostgreSQL backup and restore operations for the OneStep
control plane. The scripts assume either:

- `ONESTEP_CP_DATABASE_URL` points at the target PostgreSQL instance, or
- the bundled Compose PostgreSQL settings are present in `.env.deploy` or `.env`.

The scripts normalize SQLAlchemy-style URLs such as `postgresql+psycopg://...` into
plain PostgreSQL URLs before calling `pg_dump` or `pg_restore`.

## Prerequisites

- `pg_dump` and `pg_restore` are installed on the operator host.
- The target database is PostgreSQL.
- The operator has a validated env file such as `.env.deploy`.
- The control plane release candidate has already passed smoke checks before the backup is taken for
  a restore drill.

## 1. Create A Backup

From the control-plane directory (`apps/control-plane`):

```bash
bash scripts/backup-postgres.sh --env-file .env.deploy
```

Optional overrides:

```bash
bash scripts/backup-postgres.sh --env-file .env.deploy --output backups/staging-pre-r2.dump
bash scripts/backup-postgres.sh --database-url 'postgresql://user:pass@host:5432/dbname'
```

Expected result:

- the script exits `0`
- a `.dump` file exists under `backups/` or the custom `--output` path
- the file timestamp is recorded in the release log

## 2. Restore Into Staging Or A Drill Environment

Restore is destructive for the target database contents, so the script requires `--yes`.

```bash
bash scripts/restore-postgres.sh --env-file .env.deploy --input backups/staging-pre-r2.dump --yes
```

Optional explicit DSN:

```bash
bash scripts/restore-postgres.sh \
  --database-url 'postgresql://user:pass@host:5432/dbname' \
  --input backups/staging-pre-r2.dump \
  --yes
```

Expected result:

- the script exits `0`
- `pg_restore` completes without SQL errors
- no manual DDL or DML repair is needed after the scripted restore

## 3. Validate The Restored Environment

After restore, validate both database shape and application behavior:

```bash
docker compose --env-file .env.deploy -f docker-compose.deploy.yml run --rm migrate
bash scripts/run-smoke.sh --compose-file docker-compose.deploy.yml --env-file .env.deploy
```

Manual checks:

- `GET /readyz` returns `200` with `"status":"ready"`
- the login page loads through the control plane port
- a known local admin can authenticate
- at least one service list page and one task detail page render correctly
- recent rows are present in `task_events`, `task_metric_windows`, and `agent_commands`

## 4. Success Criteria

Treat the restore drill as successful only if all of the following are true:

- the backup script completed without manual intervention
- the restore script completed without manual SQL fixes
- the restored stack passed smoke checks
- application pages loaded and authenticated as expected
- the elapsed restore time was captured for future planning

## 5. Failure Handling

- Backup failure: do not proceed with the release or drill until a fresh backup succeeds.
- Restore failure: preserve the failing output, stop automated retries, and investigate the
  target database state before re-running `pg_restore`.
- Smoke or readyz failure after restore: keep the restored environment isolated and debug
  before reopening it to operators.

## 6. Drill Record Template

Record each drill with these fields:

- backup file path
- backup start and finish time
- restore start and finish time
- target environment
- smoke result
- manual validation result
- issues found
- follow-up actions
