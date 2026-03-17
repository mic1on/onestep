# OneStep Agent WS Protocol

## 1. Status

This document defines the WebSocket protocol between `onestep` agents and
`onestep-control-plane`.

Current intent:

- agent plane is `WS-only`
- query/auth/health APIs remain HTTP
- telemetry and control share one long-lived WS session

This copy is a working draft in the `onestep` repo. The long-term canonical
source should live in `onestep-control-plane`.

## 2. Goals

- Let agents actively connect out to the control plane without inbound access
- Carry `report + control` over one connection
- Preserve enough business IDs for idempotent processing
- Keep payload migration low by reusing existing telemetry bodies where possible

## 3. Non-Goals

- Replacing query APIs with WS
- Supporting hot task graph mutation in v1
- Supporting pause/resume/drain semantics before runtime primitives exist

## 4. Endpoint

- Route: `GET /api/v1/agents/ws`
- Scheme: `ws` for local dev, `wss` for real deployments

## 5. Authentication

The WS handshake uses the same ingest token model as the historical HTTP agent
ingestion endpoints.

Allowed forms:

- `Authorization: Bearer <token>`
- `Sec-WebSocket-Protocol: onestep-agent.v1, bearer.<token>`

Server behavior:

- invalid or missing token: reject upgrade
- valid token: accept upgrade and wait for `hello`

## 6. Transport Rules

- JSON text frames only
- UTF-8 encoding
- one protocol message per WS frame
- WS `ping/pong` is transport-level only and not a substitute for business `heartbeat`

## 7. Message Envelope

Every JSON frame must follow this outer envelope:

```json
{
  "type": "hello",
  "message_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
  "sent_at": "2026-03-17T12:00:00Z",
  "payload": {}
}
```

Fields:

- `type`: protocol message type
- `message_id`: sender-generated unique frame ID for traceability
- `sent_at`: UTC timestamp
- `payload`: message-specific body

Notes:

- `message_id` is not the business idempotency key for telemetry or commands
- business idempotency is defined per message kind below

## 8. Message Types

Supported protocol messages:

- `hello`
- `hello_ack`
- `telemetry`
- `command`
- `command_ack`
- `command_result`
- `error`

Direction:

- agent -> server: `hello`, `telemetry`, `command_ack`, `command_result`
- server -> agent: `hello_ack`, `command`, `error`

## 9. Session Lifecycle

Expected sequence:

1. agent opens WS
2. agent sends `hello`
3. server validates identity and returns `hello_ack`
4. agent sends full `sync`
5. agent sends periodic `heartbeat`
6. agent sends `metrics` and `events` as available
7. server may send `command` at any time after `hello_ack`
8. agent returns `command_ack`
9. agent returns `command_result`

Reconnect behavior:

- the agent always initiates a new session
- after reconnect, the agent must send a new `hello`
- after `hello_ack`, the agent must send a fresh full `sync`
- server should mark older active sessions for the same `instance_id` as superseded

## 10. `hello`

Purpose:

- identify the agent instance
- negotiate protocol version and capabilities
- establish a server-side agent session

Example:

```json
{
  "type": "hello",
  "message_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
  "sent_at": "2026-03-17T12:00:00Z",
  "payload": {
    "protocol_version": "1",
    "capabilities": [
      "telemetry.sync",
      "telemetry.heartbeat",
      "telemetry.metrics",
      "telemetry.events",
      "command.shutdown",
      "command.sync_now",
      "command.flush_metrics",
      "command.flush_events"
    ],
    "service": {
      "name": "billing-sync",
      "environment": "prod",
      "node_name": "vm-prod-3",
      "instance_id": "8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df",
      "deployment_version": "1.0.0+c435c99"
    },
    "runtime": {
      "onestep_version": "1.0.0",
      "python_version": "3.12.3",
      "hostname": "host-a",
      "pid": 12345,
      "started_at": "2026-03-17T11:58:00Z"
    }
  }
}
```

Required payload fields:

- `protocol_version`
- `capabilities`
- `service`
- `runtime`

Identity rule:

- `service.instance_id` is the stable runtime instance identity for session ownership

## 11. `hello_ack`

Purpose:

- confirm session establishment
- return server-selected runtime settings
- tell the agent it may start normal traffic

Example:

```json
{
  "type": "hello_ack",
  "message_id": "01ARZ3NE5YQJ3K9M2N0V5N7V7T",
  "sent_at": "2026-03-17T12:00:00Z",
  "payload": {
    "session_id": "sess_01ARZ3NDY0B7Q2M7D3Y9R1W3QT",
    "protocol_version": "1",
    "heartbeat_interval_s": 30,
    "accepted_capabilities": [
      "telemetry.sync",
      "telemetry.heartbeat",
      "telemetry.metrics",
      "telemetry.events",
      "command.shutdown",
      "command.sync_now",
      "command.flush_metrics",
      "command.flush_events"
    ],
    "server_time": "2026-03-17T12:00:00Z"
  }
}
```

Required payload fields:

- `session_id`
- `protocol_version`
- `heartbeat_interval_s`
- `accepted_capabilities`
- `server_time`

Server rules:

- reject unsupported protocol versions with `error`
- on success, mark the session active for the instance

## 12. `telemetry`

Purpose:

- carry all agent-to-server telemetry over one common wrapper

Example:

```json
{
  "type": "telemetry",
  "message_id": "01ARZ3NG8T2X0GJSS0P7GJYK7N",
  "sent_at": "2026-03-17T12:00:05Z",
  "payload": {
    "channel": "heartbeat",
    "body": {
      "service": {
        "name": "billing-sync",
        "environment": "prod",
        "node_name": "vm-prod-3",
        "instance_id": "8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df",
        "deployment_version": "1.0.0+c435c99"
      },
      "runtime": {
        "onestep_version": "1.0.0",
        "python_version": "3.12.3",
        "hostname": "host-a",
        "pid": 12345,
        "started_at": "2026-03-17T11:58:00Z"
      },
      "health": {
        "status": "ok",
        "uptime_s": 125,
        "inflight_tasks": 2
      },
      "sent_at": "2026-03-17T12:00:05Z",
      "sequence": 3
    }
  }
}
```

Supported channels:

- `sync`
- `heartbeat`
- `metrics`
- `events`

Rules:

- `payload.body` should reuse the existing historical HTTP request body shape
- existing `sequence`, `event_id`, and `window_id` semantics remain unchanged
- server routes `channel + body` into the corresponding ingestion logic

## 13. Telemetry Channel Semantics

### 13.1 `sync`

Purpose:

- publish full current topology and runtime snapshot

Expected body:

- same structure as historical `POST /api/v1/agents/sync`

Behavior:

- send immediately after `hello_ack`
- resend whenever topology hash changes
- resend after reconnect even if topology is unchanged

Idempotency:

- `(instance_id, sequence, sent_at)` for transport ordering
- `topology_hash` for snapshot convergence

### 13.2 `heartbeat`

Purpose:

- confirm business liveness and current health

Expected body:

- same structure as historical `POST /api/v1/agents/heartbeat`

Behavior:

- periodic
- required even if WS ping/pong is healthy

Idempotency:

- `(instance_id, sequence, sent_at)`

### 13.3 `metrics`

Purpose:

- publish aggregated task metric windows

Expected body:

- same structure as historical `POST /api/v1/agents/metrics`

Idempotency:

- `(instance_id, task_name, window_id)`

### 13.4 `events`

Purpose:

- publish discrete runtime events

Expected body:

- same structure as historical `POST /api/v1/agents/events`

Idempotency:

- `event_id`

## 14. `command`

Purpose:

- let the server instruct an online agent instance to perform a control action

Example:

```json
{
  "type": "command",
  "message_id": "01ARZ3NK0A9M2P6RYXW1F6W8TV",
  "sent_at": "2026-03-17T12:00:10Z",
  "payload": {
    "command_id": "cmd_01ARZ3NJY6F7WE6ZK5B7YTW9JJ",
    "kind": "shutdown",
    "args": {},
    "timeout_s": 10,
    "created_at": "2026-03-17T12:00:10Z"
  }
}
```

Required payload fields:

- `command_id`
- `kind`
- `args`
- `timeout_s`
- `created_at`

Initial supported `kind` values:

- `ping`
- `shutdown`
- `sync_now`
- `flush_metrics`
- `flush_events`

Command routing rules:

- commands target exactly one `instance_id`
- the server may only send a command to an active session for that instance
- on reconnect, unsatisfied commands may be redelivered if not expired

## 15. `command_ack`

Purpose:

- confirm that the agent received the command and decided whether it can process it

Example:

```json
{
  "type": "command_ack",
  "message_id": "01ARZ3NM1Q41R1H3B7PQB49A9V",
  "sent_at": "2026-03-17T12:00:10Z",
  "payload": {
    "command_id": "cmd_01ARZ3NJY6F7WE6ZK5B7YTW9JJ",
    "status": "accepted",
    "received_at": "2026-03-17T12:00:10Z"
  }
}
```

Allowed `status` values:

- `accepted`
- `rejected`

Rules:

- `accepted` means the command was received and queued or started
- `accepted` does not mean success
- `rejected` means the command will not be executed

If `status` is `rejected`, include:

- `error_code`
- `error_message`

Example rejection payload:

```json
{
  "command_id": "cmd_01ARZ3NJY6F7WE6ZK5B7YTW9JJ",
  "status": "rejected",
  "received_at": "2026-03-17T12:00:10Z",
  "error_code": "unsupported_command",
  "error_message": "command kind is not supported by this agent build"
}
```

## 16. `command_result`

Purpose:

- return the terminal execution result of a previously accepted command

Example:

```json
{
  "type": "command_result",
  "message_id": "01ARZ3NP5CA0GQKV7RXR0P7VQK",
  "sent_at": "2026-03-17T12:00:11Z",
  "payload": {
    "command_id": "cmd_01ARZ3NJY6F7WE6ZK5B7YTW9JJ",
    "status": "succeeded",
    "finished_at": "2026-03-17T12:00:11Z",
    "result": {
      "ok": true
    }
  }
}
```

Allowed `status` values:

- `succeeded`
- `failed`
- `timeout`
- `cancelled`

Required payload fields:

- `command_id`
- `status`
- `finished_at`

If terminal state is not `succeeded`, include:

- `error_code`
- `error_message`

Optional fields:

- `result`
- `duration_ms`

## 17. `error`

Purpose:

- report protocol-level or validation-level failures

Example:

```json
{
  "type": "error",
  "message_id": "01ARZ3NQ9C5P2YJ4SH2RXY7N1F",
  "sent_at": "2026-03-17T12:00:00Z",
  "payload": {
    "code": "unsupported_protocol_version",
    "message": "protocol_version=2 is not supported",
    "close_connection": true
  }
}
```

Suggested error codes:

- `invalid_message`
- `unauthorized`
- `unsupported_protocol_version`
- `session_not_initialized`
- `unknown_command`
- `duplicate_command_result`
- `invalid_telemetry_channel`

Rules:

- `error` is for protocol handling, not business telemetry failure reporting
- server may close the connection after sending `error`

## 18. Ordering and Idempotency

General rules:

- WS frame order is preserved per connection, but reconnect breaks transport continuity
- business idempotency must not rely on connection continuity
- agent and server must tolerate duplicate delivery

Specific rules:

- telemetry bodies keep their historical business ids
- commands are keyed by `command_id`
- if an agent receives a duplicate `command_id`, it must not execute the command again
- for duplicate commands, the agent should resend the latest known `command_ack` and
  `command_result` when available

## 19. Delivery Expectations

Required reliability level by message class:

- `hello`: exactly once per session, retry by reconnect if needed
- `sync`: at least once, idempotent by sequence and topology convergence
- `heartbeat`: periodic best effort, latest value wins
- `metrics`: at least once preferred, deduped by `window_id`
- `events`: at least once preferred, deduped by `event_id`
- `command_ack`: at least once, deduped by `command_id`
- `command_result`: at least once, deduped by `command_id`

## 20. Priority and Backpressure

One WS connection carries mixed traffic, so the agent must apply logical priority:

- high: `hello`, `heartbeat`, `command_ack`, `command_result`
- medium: `sync`
- low: `metrics`, `events`

Rules:

- low-priority telemetry must never block control acknowledgements
- if local buffers are bounded and overflow occurs, drop low-priority telemetry first
- dropped telemetry should be logged locally

## 21. Reconnection

Agent rules:

- use exponential backoff with jitter
- open a fresh WS session after disconnect
- send `hello` first on every reconnect
- send fresh `sync` after `hello_ack`

Server rules:

- a new valid session for the same `instance_id` supersedes older active sessions
- pending commands that are not acked and not expired may be redelivered on the new session

## 22. Minimal End-to-End Flow

```text
agent                                server
-----                                ------
connect ---------------------------> accept WS
hello -----------------------------> validate identity
<------------------------------ hello_ack
telemetry(sync) -------------------> apply sync snapshot
telemetry(heartbeat) --------------> update liveness
<------------------------------ command(shutdown)
command_ack -----------------------> mark acked
command_result --------------------> mark succeeded
```

## 23. Compatibility Notes

- HTTP query APIs remain unchanged
- HTTP auth APIs remain unchanged
- HTTP health APIs remain unchanged
- historical HTTP agent ingestion endpoints are replaced by WS in the target design

## 24. Open Implementation Notes

- service-side ingestion logic should be shared between WS and any transitional compatibility layer
- `onestep` should expose WS support behind a transport abstraction
- if WS client dependencies are optional, they should live behind an extra such as `onestep[ws]`
