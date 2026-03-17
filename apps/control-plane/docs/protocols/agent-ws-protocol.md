# OneStep Agent WS Protocol

## 1. Status

This document is the canonical WebSocket protocol for `onestep` agents talking to
`onestep-control-plane`.

Current intent:

- agent plane is `WS-only`
- query/auth/health APIs remain HTTP
- telemetry and control share one long-lived WS session

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
  "message_id": "msg_123",
  "sent_at": "2026-03-17T12:00:00Z",
  "payload": {}
}
```

Fields:

- `type`: protocol message type
- `message_id`: sender-generated unique frame ID for traceability
- `sent_at`: UTC timestamp
- `payload`: message-specific body

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

Supported channels:

- `sync`
- `heartbeat`
- `metrics`
- `events`

Rules:

- `payload.body` reuses the historical HTTP request body shape
- existing `sequence`, `event_id`, and `window_id` semantics remain unchanged
- server routes `channel + body` into the corresponding ingestion logic

## 13. Telemetry Channel Semantics

### 13.1 `sync`

- same structure as historical `POST /api/v1/agents/sync`
- send immediately after `hello_ack`
- resend whenever topology hash changes
- resend after reconnect even if topology is unchanged

Idempotency:

- `(instance_id, sequence, sent_at)` for transport ordering
- `topology_hash` for snapshot convergence

### 13.2 `heartbeat`

- same structure as historical `POST /api/v1/agents/heartbeat`
- periodic even if WS ping/pong is healthy

Idempotency:

- `(instance_id, sequence, sent_at)`

### 13.3 `metrics`

- same structure as historical `POST /api/v1/agents/metrics`

Idempotency:

- `(instance_id, task_name, window_id)`

### 13.4 `events`

- same structure as historical `POST /api/v1/agents/events`

Idempotency:

- `event_id`

## 14. `command`

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

Rules:

- commands target exactly one `instance_id`
- the server may only send a command to an active session for that instance
- on reconnect, unsatisfied commands may be redelivered if not expired

## 15. `command_ack`

Allowed `status` values:

- `accepted`
- `rejected`

If `status` is `rejected`, include:

- `error_code`
- `error_message`

## 16. `command_result`

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
- duplicate commands must not be executed twice

## 19. Delivery Expectations

- `hello`: exactly once per session, retry by reconnect if needed
- `sync`: at least once, idempotent by sequence and topology convergence
- `heartbeat`: periodic best effort, latest value wins
- `metrics`: at least once preferred, deduped by `window_id`
- `events`: at least once preferred, deduped by `event_id`
- `command_ack`: at least once, deduped by `command_id`
- `command_result`: at least once, deduped by `command_id`

## 20. Priority and Backpressure

- high: `hello`, `heartbeat`, `command_ack`, `command_result`
- medium: `sync`
- low: `metrics`, `events`

Rules:

- low-priority telemetry must never block control acknowledgements
- if local buffers overflow, drop low-priority telemetry first
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
