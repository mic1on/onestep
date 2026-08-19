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

## Environment Variables

Key configuration variables:

| Variable | Description |
|----------|-------------|
| `APP_CWD` | Application working directory |
| `PYTHONPATH` | Python module search path |

The deployment template automatically adds `APP_CWD` to `PYTHONPATH`, ensuring modules within the repository can be imported correctly.

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
- [Worker Runtime Image](/en/guide/worker-runtime-image) - containerized YAML workers
