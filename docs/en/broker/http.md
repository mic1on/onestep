---
title: HTTP Sink / Fetcher | Broker
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

## HTTP Fetcher

`HttpFetcher` (since 1.13.0) is a stateless HTTP client resource that task
handlers use to **pull** from external HTTP endpoints on demand. It is a
connector, not a source — the runtime never polls it. The trigger is always a
delivery from a cron (or any other) source, and the handler calls
`fetch()` / `fetch_rows()` through `ctx.resources` while executing.

It reuses the `http_sink` transport stack (URL/header/param/timeout/status
normalization and error classification), supports a static or per-call JSON
request body, validates `success_statuses`, and parses the JSON response.
Failures raise `ConnectorOperationError`, so the task's retry policy owns
failure handling; the fetcher itself keeps no state between calls.

```python
import os

from onestep import CronSource, HttpFetcher, MemoryQueue, OneStepApp

app = OneStepApp("fetch-demo")

api = HttpFetcher(
    "orders-api",
    url="https://api.example.com/orders",
    headers={"Authorization": f"Bearer {os.environ['API_TOKEN']}"},
    params={"status": "paid"},
    timeout_s=10.0,
    success_statuses=[200],
)
app.register_resource("api", api)

@app.task(source=CronSource("*/5 * * * *"), emit=MemoryQueue("results"))
async def sync_paid_orders(ctx, _):
    payload = await ctx.resources["api"].fetch(params_override={"status": "paid"})
    return {"count": len(payload["orders"])}
```

`fetch(params_override=None, body_override=None)` returns the parsed JSON;
`params_override` merges over the static `params`, and `body_override`
replaces the static `body` (only POST/PUT/PATCH carry a body; GET/DELETE never
send one). An empty response body (e.g. HTTP 204) returns `None`.

`fetch_rows()` builds on `fetch()` and projects the response into a list of
row mappings, so emit-to-sink flows can fan out fetched rows without a
hand-written HTTP client. `rows` is an optional extractor callable (sync or
async) that receives the parsed payload and returns a list of mappings;
contract violations (a non-list result or non-mapping items) raise a
`PERMANENT` error, while exceptions raised inside the extractor propagate
unchanged:

```python
rows_fn = lambda payload: payload["orders"]  # or async def rows_fn(payload): ...

api = HttpFetcher(
    "orders-api",
    url="https://api.example.com/orders",
    rows=rows_fn,
)
app.register_resource("api", api)

@app.task(source=CronSource("*/5 * * * *"), emit=out)
async def fan_out_rows(ctx, _):
    return await ctx.resources["api"].fetch_rows()  # list[dict], emitted row by row
```

Like `HttpSink`: `429` is classified as rate limiting, `408`/`425`/`5xx` as
transient errors, and other non-success statuses as permanent errors;
transport-layer failures (timeouts, `URLError`, `OSError`) are classified as
retryable or permanent the same way.

## HTTP Fetcher YAML Configuration

```yaml
resources:
  tick:
    type: cron
    expr: "*/5 * * * *"

  orders_api:
    type: http_fetcher
    url: "https://api.example.com/orders"
    headers:
      Authorization: "Bearer ${API_TOKEN}"
    params:
      status: paid
    timeout_s: 10
    success_statuses: [200]

  out:
    type: memory

tasks:
  - name: sync_paid_orders
    source: tick
    emit: out
    handler:
      ref: myapp.handlers:sync_orders
```

`http_fetcher` supports `url`, `method` (default `GET`), `headers`, `params`,
`body`, `timeout_s`, `success_statuses`, and `rows` (a callable ref string
pointing at the extractor function). Strict validation rejects unknown fields.
Handlers access the fetcher instance through its resource name
(`orders_api` in the example).

## Control Plane

When the Control Plane reporter is enabled, `HttpSink` and `HttpFetcher`
appear as `http_sink` / `http_fetcher` in the task topology. Account
information in URLs is redacted, URL query and fragment are removed, and
header/param values are marked as `<redacted>` to prevent tokens from
appearing in topology payloads.

## Next Steps

- [Webhook](/en/broker/webhook) - Receive external HTTP requests
- [YAML Task Definition](/en/yaml-task-definition) - Using `http_sink`, `http_fetcher`, and direct forwarding tasks
- [Custom Source/Sink](/en/broker/custom) - Implement your own input/output targets
