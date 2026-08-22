---
title: 'Case: Multi-Connector Event Fan-out Pipeline | Guide'
outline: deep
---

# Case: Multi-Connector Event Fan-out Pipeline

This case shows how a single task coordinates multiple connectors: it reads events
from Redis Streams, normalizes them in a handler, and uses **conditional Sink
routing** to fan different events out to MySQL persistence, an HTTP callback, and an
audit stream, with terminal failures going to a dead-letter queue. It suits the
"one source, multiple destinations, branch by business condition" pattern.

```text
redis_stream: events            ┌─ mysql_table_sink: orders (active)
  └─ handler:normalize_event ───┤
        (conditional routing +   ├─ http_sink: notify (active)
         per-sink transform)     └─ audit_stream (all events)
                                    ⇢ dead_letter: events_dead (terminal failure)
```

## Goals & Boundaries

- A single task can `emit` to multiple Sinks. The Delivery is only `ack()`ed after
  all selected Sinks write successfully.
- **Conditional routing** uses a Python predicate to decide which destination an
  event goes to; YAML only declares topology, judgment logic lives in Python.
- **Per-sink transform** projects the same handler result into the shape each
  destination needs, without building multiple payloads in the handler.
- Writes are at-least-once and **not cross-Sink transactional**: once fan-out
  begins, an earlier successful Sink write is not rolled back even if a later Sink
  fails. Therefore every destination must be idempotent.
- If the handler or any transform raises, no Sink output is emitted this round and
  the task retries; after retries are exhausted it goes to `dead_letter`.

## Prerequisites

Install plugins for the source and destinations:

```bash
pip install onestep-redis 'onestep-sql[mysql]>=0.1.0'
# http_sink is provided by onestep core, no extra plugin needed
```

Before starting, verify:

1. The Redis Stream and consumer group exist, or runtime creation is allowed.
2. The MySQL target table has a unique key covering `order_id` for idempotent upsert.
3. The HTTP callback endpoint is idempotent and tolerates redelivery of the same
   event (via an idempotency key or dedup).
4. Predicates and transforms are callables importable in the current Python runtime.

## Full YAML

Save as `worker.yaml`:

```yaml
apiVersion: onestep/v1alpha1
kind: App

app:
  name: event-fanout
  shutdown_timeout_s: 60
  strict_env: true
  logging:
    level: "${FANOUT_LOG_LEVEL:-INFO}"

resources:
  redis_main:
    type: redis
    url: "${FANOUT_REDIS_URL:-redis://localhost:6379}"

  events:
    type: redis_stream
    connector: redis_main
    stream: "events:incoming"
    group: "fanout-workers"
    batch_size: 100

  events_dead:
    type: redis_stream
    connector: redis_main
    stream: "dead_letter:events"
    group: "fanout-workers"

  mysql_main:
    type: mysql
    dsn: "${FANOUT_MYSQL_DSN}"

  orders_sink:
    type: mysql_table_sink
    connector: mysql_main
    table: orders
    mode: upsert
    keys: [order_id]
    update_columns: [status, amount]
    update_expr:
      updated_at: "NOW(6)"
    serialize_json: auto

  notify:
    type: http_sink
    url: "${FANOUT_NOTIFY_URL}"
    method: POST
    timeout_s: 5

  audit_stream:
    type: redis_stream
    connector: redis_main
    stream: "events:audit"

tasks:
  - name: fanout_events
    description: Normalize events and fan out to MySQL, HTTP, and audit
    source: events
    emit:
      # All events go to the audit stream (unconditional).
      - audit_stream
      # Only active order events persist and call back; per-sink transforms project each shape.
      - when:
          ref: worker.routing:is_active_order
        then:
          - sink: orders_sink
            transform: worker.transforms:to_order_row
          - sink: notify
            transform: worker.transforms:to_notify_body
    dead_letter: [events_dead]
    concurrency: 8
    timeout_s: 30
    retry:
      type: exponential_backoff
      max_attempts: 5
      min_delay_s: 1
      max_delay_s: 20
      jitter: full
    handler:
      ref: worker.tasks:normalize_event
```

## Python Side

The handler normalizes events; the predicate decides routing; transforms project
the payload for each Sink. All three live in Python; YAML only declares static
topology.

```python
# worker/tasks.py
async def normalize_event(ctx, item):
    # Normalize the upstream event; return an intermediate result reused by Sinks.
    return {
        "order_id": item["id"],
        "kind": item.get("kind", "unknown"),
        "status": item.get("status", "pending"),
        "amount": item.get("amount", 0),
        "customer": item.get("customer"),
    }


# worker/routing.py
def is_active_order(ctx, payload, result) -> bool:
    # Predicate can receive ctx / payload (raw source message) / result (handler return).
    return result["kind"] == "order" and result["status"] == "active"


# worker/transforms.py
async def to_order_row(ctx, payload, result):
    # Project into the columns the orders table needs.
    return {
        "order_id": result["order_id"],
        "status": result["status"],
        "amount": result["amount"],
    }


def to_notify_body(ctx, payload, result):
    # Project into the body the HTTP callback needs.
    return {
        "order_id": result["order_id"],
        "event": "order.active",
    }
```

Rules recap (see [YAML Task Definition](/en/yaml-task-definition)):

- `emit` entries may mix unconditional Sinks and conditional routing maps, evaluated
  in order.
- `when` is a predicate ref; `then` / `otherwise` can be a Sink name, a list of
  names, or a list of `{sink, transform}` bindings. Omitting `otherwise` skips the
  route when the predicate is false.
- A single route takes only `then` or `otherwise`.
- A transform receives `(ctx, raw payload, handler result)` and returns the body sent
  to that Sink.

## Operation & Recovery

### Multi-Sink at-least-once semantics

When a task writes to multiple destinations, OneStep evaluates all selected
transforms first, and only starts sending after they all succeed. Key properties:

- Any transform fails ⇒ no Sink is sent this round, the task retries.
- A later Sink fails mid fan-out ⇒ earlier successful writes are **not rolled
  back**; the whole Delivery is treated as failed and retried, and on redelivery the
  earlier Sinks receive it again.

So every destination must be idempotent: MySQL uses `upsert` + unique key, the HTTP
callback uses an idempotency key or dedup, and the audit stream tolerates duplicates.

### Dead-letter

Terminal-failure messages after retries are written to `events_dead`. Consume the
dead-letter stream with a separate task for investigation or compensation—do not
drop them.

### Observability

| Event | Purpose |
|---|---|
| Task failure and retry events | Tell whether the handler, a transform, or a Sink failed. |
| Redis PEL (pending) depth | Whether consumption keeps up, or a Delivery is stuck. |
| Dead-letter stream length | Volume of terminal failures needing investigation. |
| HTTP callback non-2xx ratio | Downstream availability. |

Do not log DSNs, Redis URLs, or tokens in HTTP targets.

## Trade-offs

| Parameter | Value here | Trade-off |
|---|---:|---|
| Redis `batch_size` | 100 | High throughput; further capped by task concurrency. |
| `concurrency` | 8 | Caps in-flight deliveries; tune to the slowest Sink (usually HTTP). |
| HTTP `timeout_s` | 5 | Prevents slow downstream from stalling the whole pipeline. |
| `max_attempts` | 5 | Transient failures retry; exhaustion goes to dead-letter. |

## Related

- [Redis Streams Connector](/en/broker/redis)
- [MySQL: Table Sink and Conflict Policy](/en/broker/mysql#table-sink)
- [HTTP Sink](/en/broker/http)
- [Conditional Sink Routing and Per-Sink Transform](/en/yaml-task-definition)
- [Retry & Dead Letter](/en/core/retry)
