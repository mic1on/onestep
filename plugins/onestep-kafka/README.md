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
