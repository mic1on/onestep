# onestep-cf-queues

Cloudflare Queues connector plugin for `onestep`. It consumes and publishes over
the [HTTP pull-consumer REST API](https://developers.cloudflare.com/queues/configuration/pull-consumers/),
so it runs from any environment outside Cloudflare Workers.

```bash
pip install onestep-cf-queues
```

The package registers these YAML resource types through the `onestep.resources`
entry point:

- `cf_queues` (connector)
- `cf_queue` (source + sink)

Python usage:

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

## Prerequisites

1. Enable HTTP pull on the queue: `npx wrangler queues consumer http add <QUEUE-NAME>`.
   A queue cannot have both a Worker (push) consumer and an HTTP (pull) consumer.
2. Create an API token with the **Queues** `Edit` permission (read **and** write).
   A pull consumer must be able to write to acknowledge messages.

## How it maps to onestep

| onestep | Cloudflare Queues API |
|---|---|
| `Source.fetch` | `POST /accounts/{id}/queues/{qid}/messages/pull` |
| `Delivery.ack` | `POST .../messages/ack` with `acks: [{lease_id}]` |
| `Delivery.retry(delay_s)` | `POST .../messages/ack` with `retries: [{lease_id, delay_seconds}]` |
| `Sink.send` | `POST .../messages` (single message) |

Acks and retries are buffered and flushed in batches (`ack_batch_size`, up to
100 per request) or on a timer (`ack_flush_interval_s`), then combined into a
single `/ack` call.

## Delivery metadata

Fetched messages decode the standard onestep envelope and expose Cloudflare
message metadata under `delivery.envelope.meta["cf_queues"]`:

```python
{
    "id": "1ad27d24c83de78953da635dc2ea208f",
    "timestamp_ms": 1689615013586,
    "attempts": 2,
    "metadata": {"CF-Content-Type": "json"},
}
```

`lease_id` is kept internal to ack/retry/release handling and is not exposed on
the envelope.

## Content types

Attach a pull consumer only to queues whose messages use the `text`, `bytes`,
or `json` content type (the default is `json`). The `v8` content type is
Workers-only and cannot be decoded. For `json` and `bytes` content types the
body arrives base64-encoded; the connector decodes base64 automatically before
running it through the envelope codec.

## Failure handling (`on_fail`)

- `leave` (default): do nothing on failure. The message is re-delivered once its
  `visibility_timeout` expires.
- `retry`: mark the message for immediate retry (put back in the queue now).
- `ack`: acknowledge (drop) the message on failure, e.g. after a dead-letter
  sink has already handled it.

## Short polling and no lease renewal

Unlike SQS long polling, Cloudflare pull uses **short polling**: `fetch` returns
immediately (empty when there are no messages), so `fetch_is_cancel_safe` is
`True`. Configure `poll_interval_s` to control how often the source polls.

Cloudflare Queues has **no lease-renewal (heartbeat) endpoint**. A message's
lease lasts exactly `visibility_timeout` (default 30s, max 12 hours). For
long-running handlers, set `visibility_timeout_ms` large enough to cover the
worst-case processing time, since the lease cannot be extended mid-processing.

## Limits

Cloudflare Queues enforces these limits (see the
[limits docs](https://developers.cloudflare.com/queues/platform/limits/)):

- Message size: 128 KB.
- Consumer batch size: up to 100 messages (`batch_size`, `ack_batch_size`).
- `visibility_timeout`: up to 12 hours.
- Retry `delay_seconds`: up to 24 hours.
- Per-queue throughput: 5,000 messages/second.

Delivery is at-least-once, so make handlers idempotent when duplicates matter.
