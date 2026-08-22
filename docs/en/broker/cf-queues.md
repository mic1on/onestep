---
title: Cloudflare Queues | Broker
outline: deep
---

# Cloudflare Queues

[Cloudflare Queues](https://developers.cloudflare.com/queues/) is Cloudflare's
managed message queue. onestep integrates with it through the official
[`cloudflare` Python SDK](https://github.com/cloudflare/cloudflare-python),
which calls the
[HTTP pull-consumer REST API](https://developers.cloudflare.com/queues/configuration/pull-consumers/),
so it can consume and publish messages from any environment outside Cloudflare
Workers.

## Installation

```bash
pip install onestep-cf-queues
# or as an optional dependency of onestep
pip install 'onestep[cloudflare]'
```

## Prerequisites

1. Enable HTTP pull on the queue:

   ```bash
   npx wrangler queues consumer http add <QUEUE-NAME>
   ```

   A queue cannot have both a Worker (push) consumer and an HTTP (pull) consumer.

2. Create an API token with the **Queues** `Edit` (read + write) permission. A
   pull consumer needs write permission to acknowledge (ack) messages.

## Configuration

Python:

```python
from onestep import OneStepApp
from onestep_cf_queues import CFQueuesConnector

app = OneStepApp("cf-queues-demo")
cf = CFQueuesConnector(account_id="<account-id>", api_token="<api-token>")
jobs = cf.queue("<queue-id>", batch_size=10, visibility_timeout_ms=30000)


@app.task(source=jobs)
async def consume(ctx, item):
    print("processing", item)
```

YAML:

```yaml
resources:
  cf:
    type: cf_queues
    account_id: "${CF_ACCOUNT_ID}"
    api_token: "${CF_QUEUES_TOKEN}"

  jobs:
    type: cf_queue
    connector: cf
    queue_id: "${CF_QUEUE_ID}"
    batch_size: 10
    visibility_timeout_ms: 30000
    on_fail: leave

tasks:
  - name: consume
    source: jobs
    handler:
      ref: your_package.tasks:consume
```

## Resource types

- `cf_queues`: the connector, holding `account_id`, `api_token`, plus optional
  `base_url` and `timeout_s`.
- `cf_queue`: both a source and a sink, referencing the connector via
  `connector`.

`cf_queue` fields:

| Field | Default | Description |
| --- | --- | --- |
| `queue_id` | (required) | Cloudflare queue ID |
| `batch_size` | 5 | Messages returned per pull (1–100) |
| `visibility_timeout_ms` | server default 30s | Lease duration, up to 12 hours |
| `poll_interval_s` | 1.0 | Short-polling interval |
| `on_fail` | `leave` | Failure handling: `leave` / `retry` / `ack` |
| `ack_batch_size` | 100 | Leases combined into one `/ack` request (1–100) |
| `ack_flush_interval_s` | 0.5 | Timer flush interval for ack/retry |

## Semantic mapping

The connector wraps the official `cloudflare` SDK's async client
(`AsyncCloudflare().queues.messages`):

| onestep | cloudflare SDK call |
| --- | --- |
| `Source.fetch` | `queues.messages.pull(queue_id, account_id=...)` |
| `Delivery.ack` | `queues.messages.ack(..., acks=[{lease_id}])` |
| `Delivery.retry(delay_s)` | `queues.messages.ack(..., retries=[{lease_id, delay_seconds}])` |
| `Sink.send` | `queues.messages.push(...)` |

Acks and retries are buffered and combined into a single `/ack` request once
`ack_batch_size` is reached or `ack_flush_interval_s` elapses.

## Message metadata

Pulled messages decode the standard onestep envelope and expose Cloudflare
metadata under `delivery.envelope.meta["cf_queues"]`:

```python
{
    "id": "1ad27d24c83de78953da635dc2ea208f",
    "timestamp_ms": 1689615013586,
    "attempts": 2,
    "metadata": {"CF-Content-Type": "json"},
}
```

`lease_id` is used only for internal ack/retry/release and is never exposed on
the envelope.

## Content types and encoding

A pull consumer can only handle the `text`, `bytes`, and `json` content types
(default `json`); it cannot decode the Workers-only `v8` type. For `json` and
`bytes`, the body is transmitted base64-encoded, and the connector decodes
base64 automatically before handing it to the envelope codec.

## Failure handling (`on_fail`)

- `leave` (default): do nothing on failure; the message is re-delivered by the
  server after `visibility_timeout` expires.
- `retry`: mark for immediate retry; the message goes back to the queue right
  away.
- `ack`: acknowledge (drop) the message on failure, e.g. after a dead-letter
  sink has already handled it.

## Short polling and no lease renewal

Unlike SQS long polling, Cloudflare pull is **short polling**: `fetch` returns
immediately (empty when there are no messages), so `fetch_is_cancel_safe` is
`True`. Use `poll_interval_s` to control the polling frequency.

Cloudflare Queues has **no lease-renewal (heartbeat) endpoint**; the lease
duration is fixed to `visibility_timeout` (default 30s, max 12 hours). For
long-running handlers, set `visibility_timeout_ms` large enough to cover the
worst-case processing time, since the lease cannot be extended mid-processing.

## Limits

- Message size: 128 KB.
- Consumer batch: up to 100 messages.
- `visibility_timeout`: up to 12 hours.
- Retry `delay_seconds`: up to 24 hours.
- Per-queue throughput: 5,000 messages/second.

Delivery is at-least-once, so make handlers idempotent when duplicates matter.
