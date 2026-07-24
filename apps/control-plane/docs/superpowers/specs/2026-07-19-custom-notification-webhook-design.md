# Custom Notification Webhook Design

Date: 2026-07-19
Status: Ready for user review
Owner: Codex

## Summary

Add a third notification provider, `custom`, to the existing control-plane
notification channel system. Custom channels let operators choose `GET` or
`POST` and visually configure query parameters plus POST JSON body parameters.

Existing `feishu` and `wechat_work` channels keep their current fixed behavior:
provider-specific payloads sent with `POST`. The feature only changes how
custom channels build and send their outbound webhook request.

## Goals

- Add `custom` as a supported notification provider.
- Let custom channels choose `GET` or `POST`.
- Let operators configure query parameters with a row-based editor.
- Let operators configure POST JSON body parameters with the same row-based
  editor.
- Support inserting a small whitelist of notification event fields into
  parameter values.
- Keep notification matching, dedupe, delivery audit, service scope, event
  filters, and missed-start scanning behavior intact.
- Keep Feishu and WeCom behavior unchanged.

## Non-Goals

- No custom headers.
- No secret fields, token masking, or header encryption.
- No nested JSON objects or arrays in custom body parameters.
- No arbitrary template language or expression evaluation.
- No HTTP methods beyond `GET` and `POST`.
- No change to the runtime reporter protocol.
- No actual webhook send from the existing test-notification endpoint.

## Current Context

The control plane already has a global notification settings page and backend
notification flow:

- channels are stored in `notification_channels`
- deliveries are stored in `notification_deliveries`
- channels filter events by `enabled`, `service_scopes_json`, and
  `event_types_json`
- delivery rows dedupe sends and record request payload / response status
- outbound sends currently use `httpx.Client().post(...)`
- provider-specific payloads are built in `notification_payloads.py`
- the frontend settings page is
  `frontend/src/components/NotificationSettingsPage.tsx`

This feature fits inside that existing flow. It is a control-plane backend and
frontend change, so local development requires rebuilding and restarting the
control-plane image after implementation before trusting the running service.

## Decision

Use a provider-specific request config on notification channels:

```json
{
  "method": "POST",
  "query_params": [
    { "key": "service", "value": "{{ service_name }}" },
    { "key": "event", "value": "{{ event_type }}" }
  ],
  "body_params": [
    { "key": "task", "value": "{{ task_name }}" },
    { "key": "detail_url", "value": "{{ console_url }}" }
  ]
}
```

Rules:

- `provider = "custom"` requires `custom_config`.
- `provider = "feishu"` and `provider = "wechat_work"` must not include
  `custom_config`.
- `method` allows only `GET` or `POST`.
- `query_params` and `body_params` are flat arrays of `{ key, value }`.
- parameter values are rendered and sent as strings.
- parameter keys must be non-empty.
- parameter keys must be unique within their own group.
- `GET` sends only query parameters and does not allow body parameters.
- `POST` may send both query parameters and a flat JSON object body.

## Data Model

Add one nullable JSON column to `notification_channels`:

```text
custom_config_json
```

Existing rows get `NULL`, which preserves current Feishu and WeCom behavior.

`NotificationChannelSummary` should include `custom_config` because this first
version does not contain secrets. The webhook URL remains masked in responses
and is not exposed.

## API Shape

Extend provider values:

```text
feishu | wechat_work | custom
```

Add schemas:

```json
{
  "method": "GET",
  "query_params": [
    { "key": "service", "value": "{{ service_name }}" }
  ],
  "body_params": []
}
```

Create and update requests accept `custom_config` only for `provider: custom`.

Example create request:

```json
{
  "name": "ops-custom",
  "provider": "custom",
  "webhook_url": "https://example.com/notify",
  "enabled": true,
  "service_scopes": [],
  "event_types": ["task_failed", "instance_offline"],
  "missed_start_grace_seconds": 300,
  "custom_config": {
    "method": "POST",
    "query_params": [
      { "key": "service", "value": "{{ service_name }}" }
    ],
    "body_params": [
      { "key": "event", "value": "{{ event_type }}" },
      { "key": "task", "value": "{{ task_name }}" }
    ]
  }
}
```

When patching a channel, validation uses the merged channel state. For example,
changing a custom channel back to `feishu` must also clear `custom_config`, and
changing a Feishu channel to `custom` must provide a valid config.

## Variable Rendering

Parameter values may include template tokens in this exact form:

```text
{{ service_name }}
```

Supported variables:

- `event_type`
- `service_name`
- `service_environment`
- `task_name`
- `occurred_at`
- `scheduled_at`
- `duration_ms`
- `attempts`
- `instance_id`
- `node_name`
- `console_url`
- `failure_message`
- `success_summary`

Validation rejects unknown variables when saving a channel. At send time,
missing values render as empty strings. This keeps one custom channel usable
across task events and instance events even though not every event has every
field.

Time values should use the same formatted strings used by existing
notification messages. Numeric values are converted to strings.

## Backend Send Behavior

Keep the existing notification matching flow:

1. build a `NotificationEventRecord`
2. find matching enabled channels
3. persist a pending delivery with a dedupe key
4. dispatch the delivery
5. record response status, response body, errors, and `sent_at`

For Feishu and WeCom:

- build the existing provider-specific payload
- send with `POST`
- preserve current tests and behavior

For custom:

- build query parameters by rendering `custom_config.query_params`
- for `GET`, call `client.get(webhook_url, params=query)`
- for `POST`, render `body_params` into a flat object and call
  `client.post(webhook_url, params=query, json=body)`
- record a delivery request payload that captures rendered custom request data,
  such as:

```json
{
  "method": "POST",
  "query": { "service": "billing-worker" },
  "body": { "event": "task_failed", "task": "sync_users" }
}
```

Delivery failure still must not fail telemetry ingestion, missed-start scans,
or instance-connectivity scans.

## Test Notification Behavior

Keep the existing endpoint behavior: test notification returns a preview and
does not actually send the webhook.

For custom channels, the preview should summarize the custom request shape:

- method
- rendered or example query parameters
- rendered or example body parameters for `POST`

This keeps the first version small and consistent with current behavior.

## Frontend Design

Extend the existing notification settings page rather than creating a new page.

Provider selection becomes:

```text
Feishu | WeCom | Custom
```

When `Feishu` or `WeCom` is selected:

- keep the current form behavior
- do not show method or parameter editors
- do not submit `custom_config`

When `Custom` is selected:

- show `Method` near the webhook URL
- show a compact `Query Parameters` editor
- show a compact `JSON Body Parameters` editor only for `POST`
- each parameter row has `key`, `value`, and delete action
- each value input has an `Insert Field` menu that inserts an allowed
  `{{ field_name }}` token
- empty rows are not submitted
- validation messages appear near the related parameter group

The layout should follow the current dense admin style:

- no new landing-style page
- no nested cards
- compact sections in the right panel
- lucide icons for add/delete/menu actions
- visible labels and accessible names for icon-only buttons
- focus states preserved

## Error Handling

Backend validation returns 422 for:

- `custom` channel without `custom_config`
- Feishu or WeCom channel with `custom_config`
- method other than `GET` or `POST`
- empty parameter key
- duplicate keys within `query_params`
- duplicate keys within `body_params`
- body parameters on `GET`
- unknown template variables

Frontend validation should catch the same common issues before submit. Backend
validation remains authoritative.

## Testing

### Backend API

Add or update tests in `backend/tests/test_notifications_api.py`:

- custom channel create/list/update round trip includes `custom_config`
- Feishu/WeCom reject `custom_config`
- custom rejects missing config
- custom rejects duplicate query keys
- custom rejects duplicate body keys
- custom rejects unknown variables
- custom rejects body params when method is `GET`
- patch validation uses merged existing state

### Backend Send Behavior

Add or update tests in `backend/tests/test_notification_service.py`:

- custom `GET` sends rendered query parameters
- custom `POST` sends rendered query parameters and JSON body
- missing event fields render as empty strings
- delivery request payload records rendered custom request data
- Feishu and WeCom existing behavior remains unchanged

### Frontend

Add or update tests in `frontend/src/api.notifications.test.ts` and a focused
notification settings component test:

- API contract supports `provider: custom` and `custom_config`
- create/update sends custom config for custom provider
- form does not submit custom config for Feishu or WeCom
- POST shows body parameter editor
- GET hides and omits body parameters
- Insert Field menu inserts allowed variable tokens

### Verification Commands

Run focused backend and frontend checks:

```bash
uv run pytest backend/tests/test_notifications_api.py backend/tests/test_notification_service.py
pnpm --filter onestep-control-plane-ui test -- api.notifications
```

If a notification settings component test is added, include that focused test
command as well.

After implementation changes to control-plane code, rebuild and restart the
local control-plane container before manual verification:

```bash
docker compose build plane
docker compose up -d plane
docker compose ps
```

## Acceptance Criteria

- Operators can create a `custom` notification channel.
- Operators can choose `GET` or `POST`.
- Operators can add visual query parameters.
- Operators can add visual POST JSON body parameters.
- Parameter values can include approved event-field variables.
- Unknown variables are rejected.
- `GET` sends only query parameters.
- `POST` sends query parameters and a flat JSON body.
- Feishu and WeCom channels keep their existing behavior.
- Delivery audit rows capture rendered request data without exposing the
  webhook URL.
- Focused backend and frontend tests pass.

## Risks And Follow-Ups

- Some receivers may want headers or secrets. That is intentionally deferred
  because it needs masking and secret-handling design.
- Some receivers may want nested JSON or typed values. This first version sends
  strings only to keep the UI and schema simple.
- Test notification remains preview-only. A future "send real test" action
  could be added separately if operators need end-to-end webhook validation.
