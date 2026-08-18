---
title: HTTP Sink | Broker
outline: deep
---

# HTTP Sink

`HttpSink` sends task return values as JSON requests to external HTTP endpoints. It only implements `Sink`, suitable for notifying webhooks, calling internal services, or forwarding processing results to systems that only offer HTTP interfaces.

## Basic Usage

```python
import os

from onestep import HttpSink, MemoryQueue, OneStepApp

app = OneStepApp("notify-demo")
source = MemoryQueue("events")
notify = HttpSink(
    "notify",
    url="https://example.com/hooks/events",
    headers={"Authorization": f"Bearer {os.environ['NOTIFY_TOKEN']}"},
    timeout_s=2.5,
)


@app.task(source=source, emit=notify)
async def forward_event(ctx, event):
    return {
        "id": event["id"],
        "kind": event["kind"],
    }
```

The task return value is sent as the JSON body. `HttpSink` uses `POST` by default and automatically adds `Content-Type: application/json` if not explicitly set.

## Configuration Options

```python
sink = HttpSink(
    "notify",
    url="https://example.com/hooks/events",
    method="POST",
    headers={"X-Api-Key": "secret-token"},
    params={"source": "onestep"},
    timeout_s=5.0,
    success_statuses=[200, 201, 202, 204],
)
```

| Parameter | Description | Default |
|-----------|-------------|---------|
| `url` | HTTP or HTTPS target address | Required |
| `method` | Request method, uppercased | `POST` |
| `headers` | Request headers map, values converted to string | `{}` |
| `params` | Static query parameter map, values converted to string or list of strings | `{}` |
| `timeout_s` | Single request timeout, must be > 0 | `5.0` |
| `success_statuses` | HTTP status codes considered successful | `[200, 201, 202, 204]` |

If the response status code is not in `success_statuses`, the send fails with a connector error. `429` is classified as rate limit, `408`, `425`, and `5xx` as transient errors, and other non-success codes as permanent errors.

`GET` and `DELETE` do not send a JSON body. Static `params` and the task's returned mapping payload are encoded into the query string:

```python
lookup = HttpSink(
    "lookup",
    url="https://example.com/users",
    method="GET",
    params={"api_key": "secret"},
    success_statuses=[200],
)
```

## YAML Configuration

```yaml
resources:
  incoming:
    type: memory

  notify:
    type: http_sink
    url: "https://example.com/hooks/events"
    method: POST
    headers:
      Authorization: "Bearer ${NOTIFY_TOKEN}"
    params:
      source: onestep
    timeout_s: 5
    success_statuses: [200, 202]

tasks:
  - name: forward_event
    source: incoming
    emit: notify
    handler:
      ref: myapp.handlers:normalize_event
```

`http_sink` supports `url`, `method`, `headers`, `params`, `timeout_s`, and `success_statuses`. Strict validation rejects unknown fields.

## YAML Direct Forwarding

YAML tasks can omit `handler` and only configure `emit`. The runtime forwards the payload received from the source directly to the sink.

```yaml
resources:
  incoming:
    type: memory

  notify:
    type: http_sink
    url: "https://example.com/hooks/events"
    headers:
      X-Api-Key: "${NOTIFY_TOKEN}"

tasks:
  - name: forward_raw_event
    source: incoming
    emit: notify
```

Direct forwarding is only suitable when the payload already matches the target API's expected structure. For field transformations, signing, idempotency checks, or error normalization, use a Python handler to process and return the result.

## Control Plane

When the Control Plane reporter is enabled, `HttpSink` appears as `http_sink` in the task topology. Account information in URLs is redacted, URL query and fragment are removed, and header/param values are marked as `<redacted>` to prevent tokens from appearing in topology payloads.

## Next Steps

- [Webhook](/broker/webhook) - Receive external HTTP requests
- [YAML Task Definition](/yaml-task-definition) - Using `http_sink` and direct forwarding tasks
- [Custom Source/Sink](/broker/custom) - Implement your own output target
