# OneStep Control Plane Notification Webhook Design

## Status
Proposed

## Scope

Design a global notification center for `onestep-control-plane` that sends task event notifications to:

- Feishu group robot webhook
- WeCom group robot webhook

This design intentionally keeps the first version small:

- global configuration only
- enable notifications per service
- support webhook URL only
- support these event types:
  - `task_started`
  - `task_succeeded`
  - `task_failed`
  - `task_missed_start`

This version does not include:

- per-service local notification settings
- webhook signing
- task-level subscription filters
- retry queue for failed deliveries
- rich card rendering

## Requirements

### Functional

- Operators can create multiple global webhook channels.
- Each channel can be enabled or disabled.
- Each channel can choose which services are in scope.
- Each channel can choose which event types are enabled.
- The system sends real-time notifications for:
  - task started
  - task succeeded
  - task failed
- The system detects and sends derived notifications for:
  - task missed start
- Operators can send a test notification to a configured channel.
- The UI exposes a single global notifications settings page.

### Non-Functional

- Telemetry ingestion must not fail if webhook delivery fails.
- Duplicate notifications should be prevented.
- The design should reuse existing task event and task topology data.
- The first version should minimize schema and API complexity.

## Existing Reusable Foundations

The current codebase already contains the data needed for this feature:

- Runtime emits `TaskEventKind.STARTED`, `SUCCEEDED`, and `FAILED`.
- Schedule-based sources include `scheduled_at` in delivery metadata.
- Control plane task definitions already persist:
  - `source_kind`
  - `source_config_json`

Relevant code:

- `onestep/src/onestep/runtime/runner.py`
- `onestep/src/onestep/connectors/schedule.py`
- `onestep-control-plane/backend/src/onestep_control_plane_api/db/models.py`
- `onestep-control-plane/backend/src/onestep_control_plane_api/api/ingestion_support.py`

## Event Model

Notifications are divided into two groups.

### Raw task events

- `task_started`
- `task_succeeded`
- `task_failed`

These come directly from runtime task events.

### Derived alert

- `task_missed_start`

This is computed by control plane and is not emitted directly by runtime.

## Meaning of `task_missed_start`

`task_missed_start` applies only to scheduled tasks:

- `source_kind = cron`
- `source_kind = interval`

Definition:

- a task has an expected trigger time `scheduled_at`
- once `scheduled_at + missed_start_grace_seconds` has passed
- if no matching `task_started` event exists
- emit one `task_missed_start` notification

This is intentionally different from failure:

- `task_failed` means execution started and ended in failure
- `task_missed_start` means execution did not start on time

## Simplified Schema

Use one configuration table plus one delivery audit table.

### Table: `notification_channels`

Purpose:

- stores global webhook channel configuration
- stores service scopes and event subscriptions as JSON arrays

Columns:

- `id`
- `name`
- `provider`
- `webhook_url`
- `enabled`
- `service_scopes_json`
- `event_types_json`
- `missed_start_grace_seconds`
- `created_at`
- `updated_at`

Column meaning:

- `provider`: `feishu` or `wechat_work`
- `service_scopes_json`:

```json
[
  { "name": "billing-worker", "environment": "prod" },
  { "name": "invoice-worker", "environment": "staging" }
]
```

- `event_types_json`:

```json
[
  "task_started",
  "task_succeeded",
  "task_failed",
  "task_missed_start"
]
```

- `missed_start_grace_seconds`:
  global grace period for `task_missed_start` on this channel

Why `name + environment` instead of `service_id`:

- the configuration stays stable even if the service row is recreated
- it matches how operators already think about service identity
- it keeps the JSON readable in admin/debug contexts

### Table: `notification_deliveries`

Purpose:

- prevents duplicate sends
- stores delivery audit trail
- stores provider response for debugging

Columns:

- `id`
- `channel_id`
- `dedupe_key`
- `event_type`
- `service_name`
- `service_environment`
- `task_name`
- `task_event_id`
- `scheduled_at`
- `status`
- `request_payload_json`
- `response_status_code`
- `response_body`
- `error_message`
- `created_at`
- `sent_at`

Status values:

- `pending`
- `succeeded`
- `failed`

## Dedupe Strategy

### Raw task events

Use:

- `dedupe_key = "{channel_id}:{task_event_id}"`

Because one channel should receive a given runtime event at most once.

### Missed start alert

Use:

- `dedupe_key = "{channel_id}:{service_environment}:{service_name}:{task_name}:{scheduled_at}:task_missed_start"`

Because one expected scheduled run should produce at most one missed-start alert per channel.

## Backend Changes

### 1. Accept `started` in control plane event schema

Current control plane event kind handling does not fully expose `started`.

Update:

- `api/schemas.py`
- `api/constants.py`
- related query handling

To support:

- `started`
- `succeeded`
- `failed`
- existing kinds can remain unchanged

This is required both for direct notifications and for accurate missed-start detection.

### 2. Add notification router

Add a new backend router, for example:

- `api/routers/notifications.py`

Register it in:

- `api/routers/__init__.py`

Suggested endpoints:

- `GET /settings/notifications/channels`
- `POST /settings/notifications/channels`
- `PATCH /settings/notifications/channels/{channel_id}`
- `DELETE /settings/notifications/channels/{channel_id}`
- `POST /settings/notifications/channels/{channel_id}/test`
- `GET /settings/notifications/services`

### 3. Add notification service layer

Add a module such as:

- `api/notification_service.py`

Responsibilities:

- validate and persist channel config
- match services against `service_scopes_json`
- match event types against `event_types_json`
- build provider-specific webhook payloads
- send webhook request
- write delivery audit rows
- run missed-start scanning logic

## Notification Matching Logic

For a raw task event:

1. load enabled channels
2. check whether event type is in `event_types_json`
3. check whether `(service_name, environment)` exists in `service_scopes_json`
4. create `dedupe_key`
5. if not already sent, write a delivery row
6. send webhook
7. update delivery status

Notes:

- delivery failure must not fail the telemetry ingest request
- webhook errors are recorded only in `notification_deliveries`

## Missed Start Scanner

Run a lightweight background scanner in the API process.

### Trigger cadence

- every 60 seconds

### Scan scope

Only channels where:

- `enabled = true`
- `task_missed_start` exists in `event_types_json`

Only tasks where:

- service is in `service_scopes_json`
- task definition `source_kind` is `cron` or `interval`

### Detection algorithm

For each scheduled task:

1. calculate expected `scheduled_at` timestamps within a recent lookback window
2. for each expected timestamp:
3. if `scheduled_at + missed_start_grace_seconds <= now`
4. query whether a matching `task_started` event exists
5. if no match exists, emit one derived `task_missed_start`

Matching should use:

- service identity
- task name
- event kind = `started`
- `meta_json.scheduled_at`

Using `meta_json.scheduled_at` is important because it distinguishes:

- the expected schedule slot
- the actual ingestion time

### Lookback rule

Use a bounded recent window only.

Examples:

- interval task: scan a few intervals back
- cron task: scan a limited recent duration such as the last 24 hours

This keeps the scanner cheap and avoids replaying old history forever.

## Provider Payloads

First version should use simple text or markdown-style payloads.

Do not build rich cards in V1.

### Common message fields

- environment
- service name
- task name
- event type
- scheduled time if available
- actual event time
- failure summary if relevant
- console detail URL if available

### Example: task failed

```text
[Task Failed]
Environment: prod
Service: billing-worker
Task: sync_invoice
Scheduled At: 2026-04-30T10:00:00+08:00
Occurred At: 2026-04-30T10:00:05+08:00
Exception: TimeoutError
Message: upstream mysql timeout
```

### Example: task missed start

```text
[Task Missed Start]
Environment: prod
Service: billing-worker
Task: sync_invoice
Scheduled At: 2026-04-30T10:00:00+08:00
Grace Seconds: 300
Detected At: 2026-04-30T10:05:00+08:00
```

## Frontend Design

Current frontend routing has no settings page. Add one new page:

- `/settings/notifications`

Suggested file:

- `frontend/src/pages/settings-notifications/SettingsNotificationsPage.tsx`

Add route in:

- `frontend/src/app/router.tsx`

### Page layout

#### Channel list

For each channel show:

- name
- provider
- enabled state
- selected service count
- selected event types

Actions:

- create
- edit
- test
- delete

#### Create/Edit form

Fields:

- `name`
- `provider`
- `webhook_url`
- `enabled`
- multi-select service scopes
- event checkboxes:
  - task started
  - task succeeded
  - task failed
  - task missed start
- `missed_start_grace_seconds`

The grace period input is shown only when `task_missed_start` is enabled.

## API Payload Shapes

### Create channel request

```json
{
  "name": "prod-feishu-main",
  "provider": "feishu",
  "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
  "enabled": true,
  "service_scopes": [
    { "name": "billing-worker", "environment": "prod" },
    { "name": "invoice-worker", "environment": "prod" }
  ],
  "event_types": [
    "task_started",
    "task_succeeded",
    "task_failed",
    "task_missed_start"
  ],
  "missed_start_grace_seconds": 300
}
```

### Channel response

```json
{
  "id": "uuid",
  "name": "prod-feishu-main",
  "provider": "feishu",
  "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
  "enabled": true,
  "service_scopes": [
    { "name": "billing-worker", "environment": "prod" }
  ],
  "event_types": [
    "task_started",
    "task_succeeded",
    "task_failed"
  ],
  "missed_start_grace_seconds": 300,
  "created_at": "2026-04-30T12:00:00+08:00",
  "updated_at": "2026-04-30T12:00:00+08:00"
}
```

## Implementation Order

1. Extend control plane event kind support to include `started`.
2. Add migration for:
   - `notification_channels`
   - `notification_deliveries`
3. Add notification schemas and router.
4. Add create/list/update/delete/test APIs.
5. Add real-time notification dispatch for:
   - `task_started`
   - `task_succeeded`
   - `task_failed`
6. Add background missed-start scanner.
7. Add frontend settings page.
8. Add tests for:
   - schema validation
   - event matching
   - dedupe behavior
   - missed-start detection
   - provider payload generation

## Risks And Tradeoffs

### JSON arrays in config

Positive:

- simpler schema
- faster first implementation
- easier backend and frontend changes

Negative:

- database-level querying is weaker than normalized tables
- future fine-grained filters may require refactor

This tradeoff is acceptable for V1 because:

- expected channel count is small
- matching can happen in application code
- the scope model is intentionally simple

### In-process scanner

Positive:

- no extra worker service needed
- easy to ship in current architecture

Negative:

- if multiple API replicas run, scanner coordination will need care later

This is acceptable for V1 if the deployment is single-writer or if the dedupe table is enforced correctly.

## Recommendation

Implement the feature with:

- one config table using JSON arrays
- one delivery audit table
- direct support for `task_started`
- derived `task_missed_start` based on scheduled task metadata

This is the smallest design that remains operationally useful and avoids building a second event system.
