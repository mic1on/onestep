# onestep-mq

RabbitMQ connector plugin for onestep.

```bash
pip install onestep-mq
```

The plugin registers YAML resource types through the `onestep.resources` entry point:

- `rabbitmq`
- `rabbitmq_queue`

Framework retries retain the original unacknowledged broker delivery and requeue it
inside the source with `Envelope.attempts + 1`. `MaxAttempts` therefore reaches
`reject(requeue=False)` without republishing or acknowledging the original first.
The broker's prefetch slot remains occupied during retry delay. Releasing an
unstarted delivery still nacks/requeues it so a paused worker cannot hold it from
other consumers; closing the source also returns buffered messages to RabbitMQ.
These ownership transfers, receive-channel loss, and process restart reset local
attempt history to the encoded envelope's initial value. Broker redelivery after such a loss
is a new local retry budget, so configure a broker dead-letter policy when a bound
across reconnects is required.
