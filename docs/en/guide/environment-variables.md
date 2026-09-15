---
title: Environment Variables | Guide
outline: deep
---

# Environment Variables

OneStep uses environment variables to inject deployment-specific configuration into the process: run targets, instance identity, Control Plane connectivity, server-side parameters, and third-party SDK credentials. This page collects every variable, its default, and its purpose as a single reference for deployment and troubleshooting.

Scope of this page:

- [How Variables Are Resolved](#how-variables-are-resolved) — where variables come from and which source wins
- [Core Runtime](#core-runtime) — variables read by the runtime and YAML loading
- [systemd Deployment Template](#systemd-deployment-template) — the `deploy/` templates and preflight script
- [Worker Runtime Image](#worker-runtime-image) — the official container image entrypoint
- [Control Plane Reporter](#control-plane-reporter) — worker-side telemetry reporting
- [Instance Identity](#instance-identity) — the `instance_id` resolution order
- [Control Plane Server](#control-plane-server) — the full `ONESTEP_CP_*` parameter set
- [Worker Agent](#worker-agent) — the `onestep-agent` execution host
- [Connectors and Third-Party SDKs](#connectors-and-third-party-sdks) — AWS credentials, timezone
- [Local Development and Integration Tests](#local-development-and-integration-tests)
- [Injected Variables](#injected-variables) — written by the framework, do not set manually

## How Variables Are Resolved

A single `onestep run` or `onestep check` may see variables from three sources, in descending priority:

1. **Process environment**: systemd `EnvironmentFile`, `docker run -e`, `docker compose` `environment`, shell `export`.
2. **`.env` files** (YAML targets only): resolved as `--env-file` CLI flag → `app.env_file` in the YAML → a `.env` file next to the YAML (auto-detected, skipped when absent).
3. **`${VAR}` expansion inside YAML**: variable references in config values are substituted at load time.

`.env` loading uses `setdefault` semantics: **an existing process environment variable always wins**, and a same-named key in the file is ignored. The number of loaded values is written to the `onestep` logger's DEBUG output.

### Expansion Syntax

Every string value in the YAML goes through expansion. Three forms are supported:

| Form | Meaning |
| --- | --- |
| `${VAR}` | Read `VAR`; substitutes an empty string when unset |
| `${VAR:-default}` | Read `VAR`; falls back to `default` when unset |
| `${VAR:default}` | Same as above, single-colon form |

Two behaviours worth knowing:

- **Pure references keep their type**: when the whole string is a single `${VAR}`, the expanded result is decoded as a JSON literal — `"30"` becomes the integer `30`, `"true"` becomes a boolean. Mixed strings such as `prefix-${VAR}` always stay strings.
- **No implicit scalar guessing**: values like `yes`, `0123`, or ISO dates are not coerced just because they look like YAML scalars; JSON rules apply.

### Strict Mode

By default a missing variable expands to an empty string and the problem surfaces late. To fail early, enable strict checking:

```bash
onestep check --strict-env worker.yaml
onestep run worker.yaml --strict-env
```

You can also pin it in the YAML so `check`, `run`, `render`, and `build` all agree:

```yaml
app:
  name: billing-sync
  env_file: .env          # optional, point at a variables file
  strict_env: true        # optional, fail on missing variables
```

`strict_env` only inspects `${VAR}` references **without a default** and reports the variable name plus the reference location; `${VAR:-default}` references never fail. See [YAML Task Definition](/en/yaml-task-definition) for the command contract.

## Core Runtime

| Variable | Default | Description |
| --- | --- | --- |
| `TZ` | System local timezone | Fallback timezone for `CronSource` / `IntervalSource` when none is configured explicitly. Set it explicitly in containers and systemd units to avoid schedule drift when the host timezone changes |
| `PYTHONPATH` | Empty | Python module search path. The systemd template and worker image append the application directory automatically |

There is **no** `ONESTEP_LOG_LEVEL`-style variable: the log level comes from the `--log-level` flag, YAML `app.logging.level`, or `logging` configuration in code — see [Logging & Task Events](/en/guide/logging). The metrics endpoint works the same way and is enabled by `--metrics-addr`; see [Metrics & Health Checks](/en/guide/metrics).

## systemd Deployment Template

`deploy/systemd/onestep-app.service` and `deploy/bin/onestep-preflight.sh` read the following variables, configured in `/etc/onestep/onestep-app.env` (copied from `deploy/env/onestep-app.env.example`):

| Variable | Default | Description |
| --- | --- | --- |
| `APP_CWD` | None (required) | Application working directory. Preflight prepends it to `PYTHONPATH`; `ExecStart` runs from here |
| `APP_TARGET` | None (required) | The `onestep` target, e.g. `your_package.tasks:app` or `worker.yaml` |
| `ONESTEP_BIN` | None (required) | Path to the `onestep` executable, usually `/srv/onestep-app/.venv/bin/onestep`; falls back to a `PATH` lookup when missing |

The unit also pins `PYTHONUNBUFFERED=1` so stdout logs reach journald immediately, and runs `onestep check "$APP_TARGET"` via `ExecStartPre` so a failing check keeps the service from starting. Full installation steps are in [Production Deploy](/en/guide/deploy).

## Worker Runtime Image

The official `ghcr.io/mic1on/onestep-worker` image entrypoint reads:

| Variable | Default | Description |
| --- | --- | --- |
| `ONESTEP_TARGET` | None (required) | YAML file path or Python import target; when missing the container exits with `ONESTEP_TARGET is required` |
| `WORKSPACE_DIR` | `/workspace` | Workspace path. The entrypoint adds it and its `src/` to `PYTHONPATH`, and installs dependencies from `requirements.txt` / `pyproject.toml` found inside |

Startup order: resolve `ONESTEP_TARGET` → install workspace dependencies → `onestep check` → `onestep run`. Both run modes are covered in [Worker Runtime Image](/en/guide/worker-runtime-image).

## Control Plane Reporter

The `onestep-control-plane` reporter reads the following variables through `ControlPlaneReporterConfig.from_env()`. Startup fails when `base_url` or `token` is missing; everything else has a default.

### Connectivity and Service Identity

| Variable | Default | Description |
| --- | --- | --- |
| `ONESTEP_CONTROL_PLANE_URL` | None (required) | Control Plane base URL. Accepts `http://`, `https://`, `ws://`, `wss://`; the default sender derives the WebSocket endpoint from it (`http` → `ws`, `https` → `wss`, path suffix `/api/v1/agents/ws`). Alias: `ONESTEP_CONTROL_URL` |
| `ONESTEP_CONTROL_PLANE_TOKEN` | None (required) | Reporting auth token, matching the server's `ONESTEP_CP_INGEST_TOKENS`. Alias: `ONESTEP_CONTROL_TOKEN` |
| `ONESTEP_CONTROL_PLANE_ENVIRONMENT` | `dev` | Deployment environment label; only `dev`, `staging`, `prod` are accepted. Alias: `ONESTEP_ENV` |
| `ONESTEP_SERVICE_NAME` | App `app.name` | Service name; `(service_name, environment)` uniquely identifies a service |
| `ONESTEP_SERVICE_DESCRIPTION` | Empty | Service-level description shown in the service catalog; independent from `tasks[].description` |
| `ONESTEP_NODE_NAME` | Empty | Node name, useful to tell instances apart across machines |
| `ONESTEP_DEPLOYMENT_VERSION` | Empty | Deployment version shown in instance details. Alias: `ONESTEP_VERSION` |

### Reporting Cadence and Buffering

| Variable | Default | Description |
| --- | --- | --- |
| `ONESTEP_CONTROL_PLANE_HEARTBEAT_INTERVAL_S` | `30.0` | Heartbeat interval (seconds) |
| `ONESTEP_CONTROL_PLANE_METRICS_INTERVAL_S` | `30.0` | Metric batch interval (seconds) |
| `ONESTEP_CONTROL_PLANE_EVENT_FLUSH_INTERVAL_S` | `5.0` | Task event flush interval (seconds) |
| `ONESTEP_CONTROL_PLANE_EVENT_BATCH_SIZE` | `100` | Maximum events per batch |
| `ONESTEP_CONTROL_PLANE_MAX_PENDING_EVENTS` | `1000` | Buffered events while offline; oldest data is dropped past the cap |
| `ONESTEP_CONTROL_PLANE_MAX_PENDING_METRIC_BATCHES` | `120` | Buffered metric batches while offline |
| `ONESTEP_CONTROL_PLANE_TIMEOUT_S` | `5.0` | Per-request HTTP timeout (seconds) |
| `ONESTEP_CONTROL_PLANE_RECONNECT_BASE_DELAY_S` | `0.5` | Initial reconnect backoff (seconds) |
| `ONESTEP_CONTROL_PLANE_RECONNECT_MAX_DELAY_S` | `30.0` | Reconnect backoff ceiling (seconds); must be ≥ the base delay |
| `ONESTEP_CONTROL_PLANE_SHUTDOWN_FLUSH_TIMEOUT_S` | `0.5` | Time to flush unsent data on shutdown (seconds); `0` is allowed |

### Instance Identity Variables

`instance_id` is decided by three variables; see [Instance Identity](#instance-identity) for the resolution order:

| Variable | Default | Description |
| --- | --- | --- |
| `ONESTEP_INSTANCE_ID` | Empty | Explicit UUID, highest priority |
| `ONESTEP_REPLICA_KEY` | Empty | Replica slot name such as `worker-0`; derives a deterministic UUIDv5 from `service_name + environment + replica_key` |
| `ONESTEP_STATE_DIR` | `~/.onestep/control-plane-state/<environment>/<service_name>` | Local identity state directory holding `identity.json` and sequence numbers |

Example:

```bash
export ONESTEP_CONTROL_PLANE_URL=https://control-plane.example.com
export ONESTEP_CONTROL_PLANE_TOKEN=replace-me
export ONESTEP_CONTROL_PLANE_ENVIRONMENT=prod
export ONESTEP_SERVICE_NAME=billing-sync
export ONESTEP_REPLICA_KEY=worker-0
```

The YAML can also pin some fields directly (`reporter: true` still resolves connectivity from the environment):

```yaml
reporter:
  base_url: https://control-plane.example.com
  token: ${ONESTEP_CONTROL_PLANE_TOKEN}
  service_name: billing-sync-worker
  service_description: Synchronizes billing data into the warehouse
```

More wiring options are in [Control Plane](/en/control-plane/) and [Stable Instance Identity](/en/stable-instance-identity).

## Instance Identity

`ControlPlaneReporterConfig.from_env()` resolves instance identity in a fixed order; hitting any one source is enough:

1. `ONESTEP_INSTANCE_ID` — an explicit UUID, always first.
2. `ONESTEP_REPLICA_KEY` — derives a UUIDv5 from `service_name + environment + replica_key`, so the same key always maps to the same instance.
3. `identity.json` inside `ONESTEP_STATE_DIR` — generated on first start and reused on restart; `heartbeat_sequence` / `sync_sequence` keep counting from this file.

How to choose:

| Scenario | Recommended |
| --- | --- |
| Single process per host (systemd, long-lived VM) | Persist `ONESTEP_STATE_DIR`, e.g. `/var/lib/onestep/billing-sync` |
| Multiple replicas, Kubernetes StatefulSet | Set `ONESTEP_REPLICA_KEY` (a stable slot name such as `$(HOSTNAME)`) |
| Manual debugging | Set `ONESTEP_INSTANCE_ID` temporarily; never hand the same UUID to two live processes |

Note: two live processes must never share one state directory — startup fails on the identity lock. Full details are in [Stable Instance Identity](/en/stable-instance-identity).

## Control Plane Server

The Control Plane server (`apps/control-plane`) reads variables with the `ONESTEP_CP_` prefix through pydantic-settings; the uppercased field name is the variable name, and a `.env` file in the current directory is also read. Fields and defaults:

### Basics and Database

| Variable | Default | Description |
| --- | --- | --- |
| `ONESTEP_CP_APP_ENV` | `dev` (Compose passes `docker` by default) | Environment label. Affects the login cookie's `Secure` flag (`prod` only) and development fallbacks |
| `ONESTEP_CP_DEBUG` | `false` | Debug switch passed through to the FastAPI app |
| `ONESTEP_CP_DATABASE_URL` | `postgresql+psycopg://postgres:postgres@localhost:5432/onestep_control_plane` | SQLAlchemy DSN; an empty value falls back to this default. The desktop build uses SQLite |
| `ONESTEP_CP_HOST` | `127.0.0.1` (desktop entry) / `0.0.0.0` (`start-local.sh`) | Bind address; read by the desktop entry and local script, not part of the backend Settings |
| `ONESTEP_CP_PORT` | `4173` | Bind port; same as above |
| `ONESTEP_CP_LOG_LEVEL` | `info` | uvicorn log level; same as above |
| `ONESTEP_CP_WORKER_PACKAGE_STORAGE_DIR` | `.onestep-control-plane/packages` | Directory for received worker packages |
| `ONESTEP_CP_UI_DIST_DIR` | `frontend/dist` locally / `/app/frontend/dist` in Docker | Frontend static asset directory |
| `ONESTEP_CP_UI_API_BASE_URL` | `/` | Frontend API base path served from `/app-config.js` by the packaged image; not used by `pnpm dev` |

### Auth and Tokens

| Variable | Default | Description |
| --- | --- | --- |
| `ONESTEP_CP_INGEST_TOKENS` | Empty | Bearer tokens for telemetry ingest and Agent WS access; accepts a single token, comma-separated list, or JSON array. Ingest endpoints return 503 when empty, so reporting requires a configured token |
| `ONESTEP_CP_WORKER_AGENT_REGISTRATION_TOKENS` | Empty | Worker Agent registration tokens, also comma-separated or a JSON array; the registration endpoint returns 503 when empty |
| `ONESTEP_CP_CONNECTOR_SECRET` | Empty | Plain string used to derive the connector secret encryption key. Required to use Connectors, and must stay identical across restarts and replicas |

```bash
export ONESTEP_CP_INGEST_TOKENS='dev-token'
export ONESTEP_CP_INGEST_TOKENS='token-a,token-b'
export ONESTEP_CP_INGEST_TOKENS='["token-a","token-b"]'
```

### Console Auth and Security

| Variable | Default | Description |
| --- | --- | --- |
| `ONESTEP_CP_CONSOLE_AUTH_USERNAME` | Empty | Shared console username; must be set together with the password or startup fails |
| `ONESTEP_CP_CONSOLE_AUTH_PASSWORD` | Empty | Shared console password |
| `ONESTEP_CP_CONSOLE_AUTH_SESSION_TTL_S` | `604800` | Login session lifetime (seconds); 7 days by default |
| `ONESTEP_CP_CONSOLE_SENSITIVE_AUTH_WINDOW_S` | `900` | Window (seconds) for the "recent re-authentication" requirement on destructive commands |
| `ONESTEP_CP_CONSOLE_LOGIN_MAX_FAILURES` | `5` | Failed logins before lockout |
| `ONESTEP_CP_CONSOLE_LOGIN_FAILURE_WINDOW_S` | `900` | Failure-counting window (seconds) |
| `ONESTEP_CP_CONSOLE_LOGIN_LOCKOUT_S` | `900` | Lockout duration (seconds) |
| `ONESTEP_CP_CORS_ALLOW_ORIGINS` | Empty | Allowed browser origins, comma-separated or a JSON array; no cross-origin access by default |
| `ONESTEP_CP_CONSOLE_BASE_URL` | Empty | Public console URL; when set, webhook notifications render absolute links |

### Notifications and Retention

| Variable | Default | Description |
| --- | --- | --- |
| `ONESTEP_CP_NOTIFICATION_OUTBOX_MAX_ATTEMPTS` | `5` | Maximum webhook delivery attempts before marking a request permanently failed |
| `ONESTEP_CP_NOTIFICATION_OUTBOX_BATCH_SIZE` | `50` | Maximum requests handled per outbox drain |
| `ONESTEP_CP_NOTIFICATION_OUTBOX_DRAIN_INTERVAL_S` | `2.0` | Outbox drain interval (seconds) |
| `ONESTEP_CP_NOTIFICATION_OUTBOX_BACKOFF_BASE_S` | `2.0` | Initial retry backoff after a failure (seconds) |
| `ONESTEP_CP_NOTIFICATION_OUTBOX_BACKOFF_MAX_S` | `300.0` | Exponential backoff ceiling (seconds) |
| `ONESTEP_CP_NOTIFICATION_DELIVERY_TIMEOUT_S` | `5.0` | Single webhook delivery timeout (seconds) |
| `ONESTEP_CP_NOTIFICATION_MISSED_START_SCAN_INTERVAL_S` | `60` | Missed schedule scan interval (seconds) |
| `ONESTEP_CP_RETENTION_TASK_EVENTS_DAYS` | `30` | `task_events` retention (days) |
| `ONESTEP_CP_RETENTION_TASK_METRIC_WINDOWS_DAYS` | `90` | `task_metric_windows` retention (days) |
| `ONESTEP_CP_RETENTION_AGENT_COMMANDS_DAYS` | `30` | Retention for terminal `agent_commands` (days); unfinished commands are never deleted |
| `ONESTEP_CP_RETENTION_DELETE_BATCH_SIZE` | `1000` | Maximum rows deleted per batch |
| `ONESTEP_CP_RETENTION_RUN_INTERVAL_S` | `86400` | Automatic cleanup interval (seconds); once a day by default |

### Liveness, Metrics, and Response Format

| Variable | Default | Description |
| --- | --- | --- |
| `ONESTEP_CP_INSTANCE_OFFLINE_AFTER_S` | `90` | An instance is considered offline when `last_seen_at` is older than this (seconds) |
| `ONESTEP_CP_INSTANCE_HEALTH_PARTICIPATION_WINDOW_S` | `3600` | Window (seconds) in which instances count toward the service health denominator; must be ≥ the offline threshold |
| `ONESTEP_CP_PROMETHEUS_CACHE_TTL_S` | `15.0` | In-process cache lifetime for `/metrics` (seconds); `0` disables caching |
| `ONESTEP_CP_API_RESPONSE_TIMEZONE` | Empty (falls back to `TZ`, then `UTC`) | Output timezone for query API timestamps |
| `ONESTEP_CP_BACKGROUND_WORKER_LEADER_POLL_INTERVAL_S` | `5` | Leader election poll interval for background workers (seconds) |
| `ONESTEP_CP_READINESS_TASK_STALE_AFTER_S` | `120` | Staleness threshold for background tasks in readiness checks (seconds) |

### Deployment and Local Scripts

These are used mainly by Compose files, the desktop entry, and local scripts rather than the backend Settings:

| Variable | Default | Description |
| --- | --- | --- |
| `ONESTEP_CP_IMAGE` | None | Server image used by Compose |
| `ONESTEP_CP_TIMEZONE` | `Asia/Shanghai` | Injected into containers as `TZ`; also drives backend response timezone |
| `ONESTEP_CP_POSTGRES_DB` | `onestep_control_plane` | Bundled PostgreSQL database name |
| `ONESTEP_CP_POSTGRES_USER` | `postgres` | Bundled PostgreSQL user |
| `ONESTEP_CP_POSTGRES_PASSWORD` | `postgres` (example) / required (deploy compose) | Bundled PostgreSQL password; change it in production |
| `ONESTEP_CP_POSTGRES_PORT` | `5432` | Bundled PostgreSQL published port |
| `ONESTEP_CP_SQLITE_PATH` | `.data/control-plane-dev.db` | SQLite file used by `scripts/start-local.sh` |
| `ONESTEP_CP_ALEMBIC_INI` | Repo-root `alembic.ini` | Alembic config path used by the desktop entry |
| `ONESTEP_CP_ALEMBIC_SCRIPT_LOCATION` | `backend/alembic` | Migration script directory |
| `ONESTEP_CP_REPO_ROOT` | Auto-detected | Repository root for the desktop entry |
| `ONESTEP_CP_SMOKE_READY_TIMEOUT_S` | `120` | Readiness timeout for smoke scripts (seconds) |
| `ONESTEP_CP_SMOKE_BASE_URL` | `http://127.0.0.1:4173` | Base URL used by smoke scripts |
| `ONESTEP_CP_SMOKE_API_URL` | Same as `BASE_URL` | API URL used by smoke scripts |
| `ONESTEP_CP_SMOKE_FRONTEND_URL` | Same as `BASE_URL` | Frontend URL used by smoke scripts |

Frontend development variables (`apps/control-plane/frontend/.env`, unrelated to runtime variables):

| Variable | Default | Description |
| --- | --- | --- |
| `VITE_API_BASE_URL` | Empty (same origin) | API base URL for Vite development |

## Worker Agent

`onestep-agent` (`apps/work-agent`) writes configuration to `~/.onestep/worker-agent/config.json` via `setup`; environment variables **override** the config file at runtime:

| Variable | Default | Description |
| --- | --- | --- |
| `ONESTEP_PLANE_URL` | Config file `plane_url` | Control Plane URL |
| `ONESTEP_AGENT_REGISTRATION_TOKEN` | Config file `registration_token` | One-time registration token |
| `ONESTEP_WORKER_AGENT_DIR` | `~/.onestep/worker-agent` | Working directory: identity, deployment state, venvs, logs |
| `ONESTEP_WORKER_AGENT_NAME` | `worker-agent` | Agent name shown in the console |
| `ONESTEP_WORKER_AGENT_MAX_CONCURRENCY` | `1` | Maximum concurrently running deployments |
| `ONESTEP_WORKER_AGENT_CONFIG_DIR` | `ONESTEP_WORKER_AGENT_DIR` | Config file directory, equivalent to `--config-dir` |

## Connectors and Third-Party SDKs

| Variable | Default | Description |
| --- | --- | --- |
| `AWS_ACCESS_KEY_ID` | Empty | Read by the SQS / SNS plugins through the standard boto3 credential chain; on EC2/Lambda an IAM role removes the need |
| `AWS_SECRET_ACCESS_KEY` | Empty | Same as above |
| `AWS_SESSION_TOKEN` | Empty | Session token for temporary (STS) credentials |
| `AWS_DEFAULT_REGION` | Empty | boto3 default region; an explicit `region_name` on the resource wins |
| `AWS_REGION` | Empty | Equivalent to `AWS_DEFAULT_REGION`; the standard chain reads both |
| `AWS_ENDPOINT_URL` | Empty | Overrides the AWS endpoint, for LocalStack or self-hosted compatible services |
| `TZ` | System local timezone | Scheduling fallback; see [Core Runtime](#core-runtime) |

Other connectors (RabbitMQ, Redis, Kafka, MySQL/PostgreSQL, MongoDB, Elasticsearch, ClickHouse, Feishu) take addresses and credentials as constructor arguments or YAML resource fields and do not read `ONESTEP_*` variables. When you do want them from the environment, use `${VAR}` expansion or read `os.environ` in code.

## Local Development and Integration Tests

These variables only apply to repository development scripts and integration tests; they never affect a production runtime. Defaults come from `scripts/setup-integration-env.sh`:

| Variable | Default | Description |
| --- | --- | --- |
| `ONESTEP_PYTHON_BIN` | `.venv/bin/python`, else `python3` | Interpreter for reliability checks and integration tests |
| `LOCALSTACK_ENDPOINT` | `http://127.0.0.1:4566` | LocalStack endpoint, also exported as `AWS_ENDPOINT_URL` |
| `KEEP_INTEGRATION_SERVICES` | `0` | Set to `1` to keep Compose dependencies running after tests |
| `ONESTEP_RABBITMQ_URL` | `amqp://guest:guest@127.0.0.1:5672/` | RabbitMQ integration test URL |
| `ONESTEP_RABBITMQ_QUEUE` | `onestep.integration` | RabbitMQ test queue |
| `REDIS_URL` | `redis://127.0.0.1:6379` | Redis Streams integration test URL |
| `ONESTEP_KAFKA_BOOTSTRAP_SERVERS` | `127.0.0.1:9092` | Kafka integration test URL |
| `ONESTEP_KAFKA_TOPIC_PREFIX` | `onestep.integration` | Kafka test topic prefix |
| `ONESTEP_SQS_QUEUE_NAME` | `onestep-integration.fifo` | Queue created in LocalStack |
| `ONESTEP_SQS_QUEUE_URL` | Exported after creation | SQS integration test queue URL |
| `ONESTEP_SQS_GROUP_ID` | `workers` | SQS FIFO consumer group |
| `ONESTEP_MYSQL_HOST` / `ONESTEP_MYSQL_PORT` / `ONESTEP_MYSQL_DATABASE` / `ONESTEP_MYSQL_USER` / `ONESTEP_MYSQL_PASSWORD` | `127.0.0.1` / `3306` / `onestep` / `root` / `root` | MySQL integration test connection parameters |
| `ONESTEP_MYSQL_DSN` | Assembled and exported by the script | MySQL integration test DSN |
| `ONESTEP_POSTGRES_HOST` / `ONESTEP_POSTGRES_PORT` / `ONESTEP_POSTGRES_DATABASE` / `ONESTEP_POSTGRES_USER` / `ONESTEP_POSTGRES_PASSWORD` | `127.0.0.1` / `5432` / `onestep` / `onestep` / `onestep` | PostgreSQL integration test connection parameters |
| `ONESTEP_POSTGRES_DSN` | Assembled and exported by the script | PostgreSQL integration test DSN |
| `ONESTEP_CLICKHOUSE_DSN` | `http://default:clickhouse@127.0.0.1:8123/onestep` | ClickHouse integration test DSN |
| `ONESTEP_MONGODB_URI` | `mongodb://127.0.0.1:27017/onestep?replicaSet=rs0` | MongoDB integration test URI |
| `ONESTEP_ELASTICSEARCH_URL` / `ONESTEP_OPENSEARCH_URL` | None | Elasticsearch / OpenSearch integration test URLs |

Control Plane smoke and demo scripts (`scripts/run-control-plane-smoke.sh`, `run-control-plane-demo.sh`):

| Variable | Default | Description |
| --- | --- | --- |
| `ONESTEP_CONTROL_PLANE_DIR` | `../onestep-control-plane` | Control Plane repository path |
| `ONESTEP_CONTROL_PLANE_SMOKE_TIMEOUT_S` | `45` | Timeout for waiting on Control Plane startup (seconds) |
| `ONESTEP_CONTROL_PLANE_SMOKE_POLL_S` | `1` | Readiness poll interval (seconds) |
| `ONESTEP_CONTROL_PLANE_WAIT_TIMEOUT_S` | `15` | Demo script timeout for waiting on reported data (seconds) |

Example Compose files (such as `examples/prometheus/docker-compose.yml`) use `ONESTEP_WORKER_IMAGE` for the onestep service image; the example defaults to a published `ghcr.io/mic1on/onestep-worker` tag.

## Injected Variables

The following variables are written into child processes by the framework or the control plane at runtime — **do not set them manually**:

| Variable | Injected by | Description |
| --- | --- | --- |
| `ONESTEP_DEPLOYMENT_ID` | Worker Agent supervisor | Current deployment ID |
| `ONESTEP_WORKER_AGENT_ID` | Worker Agent supervisor | Execution host agent ID |
| `ONESTEP_RUNTIME_INSTANCE_ID` | Worker Agent supervisor | Runtime instance ID |
| `ONESTEP_INSTANCE_ID` | Worker Agent supervisor | Same value as the previous entry, consumed directly by the reporter |
| `ONESTEP_WORKER_REPORTING_TOKEN` | Control Plane (injected when a deployment starts) | In custom reporting mode, the compiled worker.yaml references it as `${ONESTEP_WORKER_REPORTING_TOKEN}` |

## Upgrade Notes

- After upgrading, compare your existing config files against `deploy/env/onestep-app.env.example`, `apps/control-plane/.env.example`, and `.env.deploy.example` for added or renamed variables.
- Control Plane server variable names map one-to-one onto pydantic fields; renaming a field renames its variable. Cross-check this page against the example files for the version you deploy.
- Production tokens, passwords, and `ONESTEP_CP_CONNECTOR_SECRET` belong in a systemd `EnvironmentFile`, container secrets, or a platform secret manager — never in the repository.

## Next Steps

- [Production Deploy](/en/guide/deploy) - systemd, Docker, EC2, and Lambda deployment shapes
- [Control Plane](/en/control-plane/) - reporter telemetry and remote task control
- [Stable Instance Identity](/en/stable-instance-identity) - the full `instance_id` resolution rules
- [YAML Task Definition](/en/yaml-task-definition) - `${VAR}` expansion and the `strict_env` contract
- [Worker Runtime Image](/en/guide/worker-runtime-image) - containerized YAML workers
