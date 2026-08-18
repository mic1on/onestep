# OneStep Control Plane

The Control Plane is the centralized ops control surface for the OneStep runtime, providing **remote monitoring**, **telemetry collection**, and **command dispatch**.

## Architecture Overview

```
┌──────────────────┐      WebSocket       ┌──────────────────┐      ┌────────────┐
│  OneStep Agent   │ ──────────────────▶   │  Control Plane   │ ──▶  │ PostgreSQL │
│  (ControlPlane   │    onestep-agent.v1   │  API (FastAPI)   │      │            │
│   Reporter)      │ ◀──────────────────   │                  │      │  services  │
└──────────────────┘      commands         │  /api/v1/agents  │      │  instances │
                                           │       /ws        │      │  sessions  │
                                           │                  │      │   tasks    │
                                           │  /api/v1/query   │      │  commands  │
                                           │                  │      │   events   │
                                           │  Console Auth    │      │  metrics   │
                                           └────────┬─────────┘      └────────────┘
                                                    │
                                                    │ SSE / REST
                                                    ▼
                                           ┌──────────────────┐
                                           │   Web Console    │
                                           │  (React + Vite)  │
                                           │                  │
                                           │  Service List    │
                                           │  Task Dashboard  │
                                           │  Instance View   │
                                           │  Command Panel   │
                                           │  Notifications   │
                                           └──────────────────┘
```

Core concept: OneStep runtime Agents **actively connect outbound via WebSocket** to the Control Plane (no inbound ports required on the Agent), reporting topology, heartbeats, metrics, and task events, while receiving operational commands.

## Core Concepts

### Service / Instance / Task
- **Service**: Logical service identifier, uniquely determined by `(name, environment)`
- **Instance**: Runtime instance (UUID identifier), carrying node name, hostname, PID, and version info
- **Task**: Task graph definition (Source, Sink, concurrency, timeout, retry policy), tracked for consistency via `topology_hash`

### Topology Description
The Reporter's `sync` telemetry carries task Source/Sink types, names, and config summaries for the control plane to display the task graph and compute `topology_hash`.

Connectors report by stable type names, e.g., `cron`, `interval`, `rabbitmq_queue`, `mysql_table_sink`, `redis_stream`, `feishu_bitable_incremental`, `feishu_bitable_table_sink`, and `http_sink`. Redis Streams reports stream, group, consumer, batch, block, trim, and other topology config; `HttpSink` reports URL, method, timeout, query parameter names, and success status codes.

Sensitive information is not sent into topology as-is: `HttpSink` hides URL credentials, removes URL query/fragment, and marks header and `params` values as `<redacted>`.

### WebSocket Session Lifecycle
1. Agent establishes WebSocket connection, sends `hello` (protocol version, capability declaration)
2. Server responds with `hello_ack` (containing accepted `accepted_capabilities`)
3. Agent begins sending `telemetry` messages (sync / heartbeat / metrics / events)
4. Server can dispatch `command` (ping / shutdown / drain / pause_task, etc.)
5. Agent replies with `command_ack` (accepted/rejected) and `command_result` (execution result)

### Capability Negotiation
The Agent declares its capabilities in `hello` (e.g., `command.ping`, `command.shutdown`, `telemetry.sync`), and the server only dispatches commands the Agent has accepted.

### Command Lifecycle
```
pending → dispatched → accepted/rejected → succeeded/failed/timeout/cancelled
```
All commands are persisted to the database; unacknowledged commands can be re-dispatched after reconnection.

### Online / Offline Detection
- Instance `last_seen_at` within `INSTANCE_OFFLINE_AFTER_S` (default 90s) → online
- Within health participation window `INSTANCE_HEALTH_PARTICIPATION_WINDOW_S` (default 1h) → counted in service health denominator

## Integrating with OneStep Agent

Enable Control Plane reporting in your OneStep application:

```python
from onestep import (
    ControlPlaneReporter,
    ControlPlaneReporterConfig,
    OneStepApp,
)

app = OneStepApp("my-service")
reporter = ControlPlaneReporter(
    ControlPlaneReporterConfig.from_env(app_name=app.name)
)
reporter.attach(app)
```

It can also be enabled directly in YAML:

```yaml
reporter: true
```

`reporter: true` is still the recommended minimal configuration, reading connection info from environment variables.
To pin the service description in the config file, use a mapping instead:

```yaml
reporter:
  service_description: Synchronizes billing data into the warehouse
```

Environment variable configuration:

| Variable | Description | Example |
|---|---|---|
| `ONESTEP_CONTROL_PLANE_URL` | Control Plane service URL | `http://192.168.1.100:8080` |
| `ONESTEP_CONTROL_PLANE_TOKEN` | Worker reporting auth token | `my-token` |
| `ONESTEP_CONTROL_PLANE_ENVIRONMENT` | Deployment environment label | `prod` / `staging` |
| `ONESTEP_SERVICE_NAME` | Service name; defaults to `app.name` if not set | `billing-sync` |
| `ONESTEP_SERVICE_DESCRIPTION` | Service-level description; shown in service catalog and detail views | `Syncs billing data to warehouse` |

`reporter.service_description` / `ONESTEP_SERVICE_DESCRIPTION` is service-level metadata,
independent of task-level descriptions like `tasks[].description`.

See `ControlPlaneReporterConfig.from_env` for more configuration options.

## Deploying the Control Plane

The Control Plane is a separate repository and deployment unit, providing one-click Docker Compose setup:

```bash
git clone https://github.com/mic1on/onestep-control-plane
cd onestep-control-plane
cp .env.example .env
# Edit .env to configure database, Token, etc.
docker compose up --build -d
```

After startup:
- **Web Console**: `http://127.0.0.1:4173`
- **API**: `http://127.0.0.1:8000`
- **Interactive API Docs**: `http://127.0.0.1:8000/docs`

SQLite local dev mode is also available (no Docker required):

```bash
./scripts/start-local.sh
```

## Query API

The control plane provides REST query endpoints for the Web Console or third-party integrations:

| Endpoint | Description |
|---|---|
| `GET /api/v1/services` | Service list |
| `GET /api/v1/services/{name}/dashboard?environment=` | Service dashboard |
| `GET /api/v1/services/{name}/instances?environment=` | Instance list |
| `GET /api/v1/services/{name}/tasks?environment=` | Task list |
| `GET /api/v1/services/{name}/tasks/{task}?environment=` | Task details |
| `GET /api/v1/services/{name}/events?environment=` | Event stream |
| `GET /api/v1/services/{name}/commands?environment=` | Command history |
| `GET /api/v1/services/{name}/sessions?environment=` | Session records |
| `GET /api/v1/events?environment=` | Cross-service recent event stream |

`GET /api/v1/services/{name}/events` supports the following filter parameters:

| Parameter | Description |
|---|---|
| `task_name` | Filter by task name |
| `kind` | Filter by event type (e.g., `succeeded` / `failed` / `retried` / `dead_lettered`) |
| `instance_id` | Filter by worker instance |
| `occurred_after` / `occurred_before` | Filter by time range (UTC ISO time) |
| `limit` / `offset` | Pagination |

All query endpoints require Console Auth (username/password) authentication.

## Notifications & Webhook

The Control Plane supports pushing task events to instant messaging tools:

- **Feishu** group bot
- **WeChat Work** group bot

Supported event types: task started, succeeded, failed, retried, dead-lettered, missed scheduled start.

## More Resources

- [Agent WS Protocol](/agent-ws-protocol) — WebSocket communication protocol specification
- [Cross-Repo Collaboration](/ws-cross-repo-collaboration) — Boundaries and collaboration flow between the two repositories
- [GitHub: onestep-control-plane](https://github.com/mic1on/onestep-control-plane) — Source repository
