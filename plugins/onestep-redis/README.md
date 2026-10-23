# onestep-redis

Redis connector plugin for onestep.

```bash
pip install onestep-redis
```

The plugin registers YAML resource types through the `onestep.resources` entry point:

- `redis`
- `redis_stream`

## Delivery, retries and crash recovery

Redis server **6.2 or newer** is required. Stream sources reserve fetched entries
locally, so subsequent fetches never execute the same active delivery again.
Each broker read waits at most one second, including when `block_ms: 0` is
configured, so drain/pause/shutdown can finish an empty fetch safely.
Retries keep the PEL entry reserved through their delay. Unstarted release
requeues locally without adding an attempt; successful ACK or terminal failure
removes the PEL entry. Closing the source leaves unfinished entries recoverable.

Every fetch performs one bounded `XAUTOCLAIM` scan, continuing its cursor across
calls; its count is bounded by `batch_size` and the fetch limit. This recovers
expired entries from any consumer, including a previous process's consumer.
`reclaim_idle_ms` controls the expiry threshold (default 60000, minimum 30), in
both `connector.stream(..., reclaim_idle_ms=60000)` and YAML `redis_stream`
resources. Active and queued deliveries renew every one-third of that interval.
Use a threshold comfortably above worst-case event loop stalls and Redis
round-trip time. Separate workers must use distinct consumer names; the default
is a UUID-based name.

ACK, retry and renewal atomically verify both the PEL consumer and its delivery
count. A stale handler cannot acknowledge a message reclaimed by another
worker. Renewal uses `XCLAIM JUSTID`, which preserves the count. Retry increases
the durable Redis delivery count; recovery claims also increase it. Envelopes
expose `encoded_attempts + times_delivered - 1`, so a fresh message starts at 0
and `MaxAttempts(3)` terminates on the third delivery failure. Count survives
worker restart in the PEL; a recovered delivery consumes an attempt even if the
previous owner crashed before invoking its handler.

This remains at-least-once delivery: a process or network stall longer than the
lease can overlap handlers across workers. Ownership fencing protects ACKs,
not external side effects; handlers/sinks still need idempotency. Do not trim
unacknowledged stream entries when recovery of their payloads is required.
Redis ACLs must allow `XAUTOCLAIM`, `XPENDING`, `XCLAIM`, `XACK` and `EVAL`, in
addition to the normal stream read/write commands.
