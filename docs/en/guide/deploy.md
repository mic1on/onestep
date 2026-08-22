---
title: Production Deploy | Guide
---

# Production Deploy

## CLI Deployment Entry Point

The `onestep` CLI is the production deployment entry point.

### Recommended Module Structure

```python
# tasks.py
from onestep import IntervalSource, OneStepApp

app = OneStepApp("billing-sync")


@app.task(source=IntervalSource.every(hours=1, immediate=True, overlap="skip"))
async def sync_billing(ctx, _):
    print("syncing billing data")
```

### Running the Application

```bash
# Standard run
onestep run your_package.tasks:app

# Shorthand
onestep your_package.tasks:app

# Check configuration
onestep check your_package.tasks:app

# JSON output (suitable for CI/CD)
onestep check --json your_package.tasks:app

# Render the worker topology (Mermaid diagram)
onestep render your_package.tasks:app
```

`onestep run` writes INFO-level application logs and task lifecycle events to stdout by default, suitable for ingestion by systemd, Docker, or log collectors. Use `--log-level DEBUG` to see more detailed fetched, started, and sink-success events, or `--no-task-events` to disable automatic task events. Full rules are documented in [Logging & Task Events](/en/guide/logging).

### Render the Worker Topology

`onestep render` prints the topology of any Python or YAML target as a Mermaid flowchart, ready to paste into GitHub READMEs, Notion, or Obsidian:

```bash
onestep render worker.yaml                  # mermaid by default
onestep render pkg.tasks:app --format mermaid
```

```text
graph LR
  %% app: billing-sync
  n0["extract_entities<br/>concurrency=4 · retry=NoRetry · timeout=300s"]
  n1["sqs-orders<br/>MemoryQueue"]
  n2["audit-log<br/>MemoryQueue"]
  n3["mysql.meta_sink<br/>MemoryQueue"]
  n1 --> n0
  n0 -->|"emit"| n2
  n0 -->|"when app.predicates:is_valid · app.transforms:to_meta"| n3
```

Task nodes carry concurrency, retry policy, and timeout; edges are labeled `emit` (plus the transform ref when a binding sets one), `when`/`otherwise` for conditional routes, and dashed `dead_letter` edges. Resources shared across tasks are drawn once, so chained topologies render as connected graphs. YAML targets also support `--env-file` and `--strict-env`.

## systemd Deployment

Complete deployment templates are available at:

- `deploy/README.md`
- `deploy/systemd/onestep-app.service`
- `deploy/env/onestep-app.env.example`
- `deploy/bin/onestep-preflight.sh`

### Installation Steps

```bash
# Create configuration directory
sudo mkdir -p /etc/onestep

# Copy environment variable template
sudo cp deploy/env/onestep-app.env.example /etc/onestep/onestep-app.env

# Copy systemd service file
sudo cp deploy/systemd/onestep-app.service /etc/systemd/system/onestep-app.service

# Reload systemd
sudo systemctl daemon-reload

# Enable and start the service
sudo systemctl enable --now onestep-app
```

### Viewing Status and Logs

```bash
# Check service status
sudo systemctl status onestep-app

# View logs
sudo journalctl -u onestep-app -f
```

## Docker Deployment

The official worker image bundles `onestep[all]` and a startup script that runs
`onestep check` and then `onestep run`. See
[Worker Runtime Image](/en/guide/worker-runtime-image) for details.

### Mounted workspace

```bash
docker run --rm \
  -e ONESTEP_TARGET=/workspace/worker.yaml \
  -v "$PWD:/workspace" \
  ghcr.io/mic1on/onestep-worker:1.11.0
```

Startup behavior:

1. adds `/workspace` and `/workspace/src` to `PYTHONPATH`
2. installs `/workspace/requirements.txt` if present; otherwise installs the
   current project when `/workspace/pyproject.toml` exists
3. runs `onestep check`, then `onestep run` on success

### Derived image (recommended for production)

Bake the code and YAML into the image instead of mounting and installing at
runtime:

```dockerfile
FROM ghcr.io/mic1on/onestep-worker:1.11.0

WORKDIR /workspace
COPY . /workspace
ENV ONESTEP_TARGET=/workspace/worker.yaml
```

```bash
docker build -t my-worker .
docker run --rm my-worker
```

A worker is a long-running process. `onestep run` writes INFO logs and task
events to stdout, so a container log driver can collect them directly. If the
YAML uses a plugin not bundled in the image (e.g. `onestep-feishu-bitable`),
declare it in the workspace `requirements.txt` or `pyproject.toml`.

## Docker Compose Deployment

```yaml
# docker-compose.yml
services:
  worker:
    image: ghcr.io/mic1on/onestep-worker:1.11.0
    environment:
      ONESTEP_TARGET: /workspace/worker.yaml
    volumes:
      - ./:/workspace
    restart: unless-stopped
```

```bash
docker compose up -d          # start in the background
docker compose logs -f worker # follow logs
docker compose stop worker    # send SIGTERM, triggering graceful shutdown
```

For production, prefer a derived image (`build:` pointing at a Dockerfile that
contains your code and YAML) over mounting the source tree. `restart:
unless-stopped` restarts the worker after an abnormal exit; `docker compose
stop` sends `SIGTERM`, which `OneStepApp` handles as a normal shutdown request
and waits for in-flight tasks to complete.

Multi-connector workers can be orchestrated alongside their dependencies (e.g.
RabbitMQ, Redis) in the same compose file, using `depends_on` for startup order
and environment variables to inject DSNs/tokens.

## AWS EC2 Deployment

Running as a long-lived systemd service on EC2 is the recommended shape (see
"systemd Deployment" above). Typical steps:

1. Prepare the instance: install an onestep-compatible Python (3.9+), clone the
   app repo to `/srv/onestep-app`, create a virtualenv at
   `/srv/onestep-app/.venv`, and `pip install` the app plus required plugins.
2. Configure the service:

   ```bash
   sudo mkdir -p /etc/onestep
   sudo cp deploy/env/onestep-app.env.example /etc/onestep/onestep-app.env
   # edit APP_CWD / APP_TARGET / ONESTEP_BIN
   sudo cp deploy/systemd/onestep-app.service /etc/systemd/system/onestep-app.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now onestep-app
   ```

3. `ExecStartPre` runs `onestep check` as a preflight and refuses to start on
   failure; `ExecStart` then runs `onestep run`. The unit's `Restart=on-failure`
   handles crash recovery, and `TimeoutStopSec=45` leaves room for graceful
   shutdown.

You can also run the Docker / Docker Compose approach above directly on EC2
(after installing Docker Engine), which suits already-containerized teams.
Either way, put credentials in `/etc/onestep/*.env` or the instance IAM role /
SSM parameters, not in code.

Scale by adding instances according to source semantics: queue-style sources
(SQS, RabbitMQ, Redis Stream, Cloudflare Queues, ...) support multiple instances
consuming in parallel; schedule/polling sources (interval, cron, DB incremental)
should usually run a single instance, or use `overlap: skip` with a persisted
cursor to avoid duplication.

## AWS Lambda Deployment

Lambda is a request/response, short-lived model that does not match the
long-running loop of `onestep run`. **Do not** call `app.run()` / `app.serve()`
inside a Lambda. The correct approach is `OneStepApp.run_task_once()`, which
processes a single payload synchronously per invocation while reusing the same
handler and retry logic:

```python
# handler.py
import asyncio
from onestep import MemoryQueue, OneStepApp

app = OneStepApp("lambda-worker")


@app.task(source=MemoryQueue("in"))
async def handle(ctx, item):
    # business logic; the return value is the processing result
    return {"ok": True, "echo": item}


def lambda_handler(event, context=None):
    # process one payload per invocation; run_task_once runs the handler and retries
    return asyncio.run(app.run_task_once("handle", payload=event))
```

Notes:

- `run_task_once(task_name, payload=...)` requires the task's source to support
  manual runs (sources with `supports_manual_run=True` such as `MemoryQueue`,
  `interval`, `cron`). It runs the handler, returns a result dict on success,
  retries per the task's retry policy on failure, and finally raises (which
  Lambda records as an invocation failure).
- Use it to turn the Lambda event source (API Gateway, an SQS trigger,
  EventBridge, ...) into a payload. Note that message ack/retry is then owned by
  the Lambda event source, not by an onestep source loop.
- Packaging: use a Lambda container image (based on the official worker image or
  self-built, with `onestep` and plugins installed) or a Layer/zip. Make sure
  boto3-style plugins and any binary dependencies match the platform
  (`manylinux`).
- If your workload is essentially "continuously consume a queue", a long-lived
  worker on EC2/containers is usually a better fit than Lambda; Lambda suits
  event-driven, bursty, pay-per-invocation scenarios.

## Environment Variables

Key configuration variables:

| Variable | Description |
|----------|-------------|
| `APP_CWD` | Application working directory (systemd template) |
| `APP_TARGET` | App target, e.g. `your_package.tasks:app` (systemd template) |
| `ONESTEP_BIN` | Path to the `onestep` executable (systemd template) |
| `ONESTEP_TARGET` | YAML path or Python target (worker image) |
| `WORKSPACE_DIR` | Workspace path, default `/workspace` (worker image) |
| `PYTHONPATH` | Python module search path |

The systemd template automatically adds `APP_CWD` to `PYTHONPATH`; the worker
image automatically adds `WORKSPACE_DIR` and its `src/` to `PYTHONPATH`, so
in-repo modules import reliably.

## YAML Configuration

Supports YAML application definitions with `handler.ref` pointing to Python callables:

```yaml
app:
  name: billing-sync

resources:
  tick:
    type: interval
    minutes: 5
    immediate: true
  processed:
    type: memory

tasks:
  - name: sync_billing
    source: tick
    handler:
      ref: your_package.handlers.billing:sync_billing
      params:
        region: cn
    emit: [processed]
    retry:
      type: max_attempts
      max_attempts: 3
      delay_s: 10
```

Run a YAML application:

```bash
onestep check worker.yaml
onestep run worker.yaml
```

To upload to a worker agent or control plane, build a deployable zip first:

```bash
onestep build worker.yaml --strict --out dist/worker.zip
```

Container deployment can use the official worker runtime image. The image adds the workspace to `PYTHONPATH`, installs project dependencies, runs `onestep check`, then starts `onestep run`:

```bash
docker run --rm \
  -e ONESTEP_TARGET=/workspace/worker.yaml \
  -v "$PWD:/workspace" \
  ghcr.io/mic1on/onestep-worker:1.11.0
```

See [Worker Runtime Image](/en/guide/worker-runtime-image) for details.

## Production Recommendations

### State Persistence

For production, use `db.cursor_store(...)` or `db.state_store(...)` to ensure cursor and task state persists across process restarts:

```python
from onestep_mysql import MySQLConnector

db = MySQLConnector("mysql+pymysql://...")
state = db.cursor_store(table="onestep_cursor")

source = db.incremental(
    table="users",
    key="id",
    cursor=("updated_at", "id"),
    state=state,  # persist cursor
)
```

### Graceful Shutdown

Configure a shutdown timeout to ensure in-flight tasks have enough time to complete:

```python
app = OneStepApp("my-app", shutdown_timeout_s=30.0)
```

## Next Steps

- [RabbitMQ](/en/broker/rabbitmq) - distributed message queue
- [Redis Streams](/en/broker/redis) - lightweight message queue
- [MySQL](/en/broker/mysql) - database integration
- [PostgreSQL](/en/broker/postgres) - PostgreSQL integration
- [Kafka](/en/broker/kafka) - Kafka topic source/sink
- [Cloudflare Queues](/en/broker/cf-queues) - HTTP pull consumer
- [Worker Runtime Image](/en/guide/worker-runtime-image) - containerized YAML workers
