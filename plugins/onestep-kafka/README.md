# onestep-kafka

Kafka connector plugin for `onestep`.

```bash
pip install onestep-kafka
```

The package registers these YAML resource types through the `onestep.resources`
entry point:

- `kafka`
- `kafka_topic`

Python usage:

```python
from onestep_kafka import KafkaConnector
```

YAML usage:

```yaml
resources:
  kafka_main:
    type: kafka
    bootstrap_servers: "${KAFKA_BOOTSTRAP_SERVERS}"

  orders:
    type: kafka_topic
    connector: kafka_main
    topic: orders.events
    group_id: onestep-orders
    batch_size: 100
    poll_timeout_ms: 1000
```

## Delivery semantics

The plugin disables Kafka auto commit and follows onestep's at-least-once
contract. Offsets are committed only after processing reaches `ack()` or a
terminal `fail()`.

If a worker sends output to a sink and exits before the Kafka offset commit
succeeds, downstream output can be duplicated. Handlers and downstream sinks
should be idempotent when duplicates matter.

Commit tracking follows the completed prefix of the records actually fetched,
so compacted offsets and transaction-marker gaps do not stall progress. A
fetched but unfinished record remains a barrier even if later records succeed.
Partition revocation/assignment clears that partition's tracker and fences old
deliveries: an old handler cannot commit or queue a retry under a new assignment.

Retries retain the original record locally, incrementing the zero-based
`Envelope.attempts` for each new delivery. They do not seek backward through
other active records or publish replacement messages. `MaxAttempts(3)` therefore
terminates on the third failure during a partition assignment. Unstarted release
retains the record without incrementing attempts. The original offset stays
uncommitted until success or terminal failure, including during retry delay.

Kafka does not store a delivery count. Local retry counts reset to the encoded
envelope's attempts on process restart or partition reassignment; a retry limit
is **per uninterrupted assignment**, not a durable lifetime limit. On those
boundaries Kafka replays uncommitted records. A handler already running during
revocation may still produce side effects, so sink idempotency remains required.
