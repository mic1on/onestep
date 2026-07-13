# Kafka 插件设计

## 背景

onestep 主包已经形成了清晰的插件边界：核心包保持小而稳定，具体上下游能力由独立插件通过 `onestep.resources` entry point 注册。现有 RabbitMQ、Redis Streams、SQS、MySQL、PostgreSQL 等插件都围绕 `Source`、`Sink`、`Delivery` 三个 runtime 契约实现。

Kafka 是 onestep 作为数据流转核心需要覆盖的关键基础设施。第一版 Kafka 插件应优先成为可靠的普通 topic source/sink，而不是一次性覆盖 Kafka Streams、事务生产者、Schema Registry、retry topic 编排等完整生态能力。

本设计采用用户确认的方向：第一版做消费加生产普通 topic，使用 `aiokafka`，并接受 Kafka 插件单独要求 Python `>=3.10`。

## 目标

- 新增 repo-local 插件包 `plugins/onestep-kafka`。
- 插件包名为 `onestep-kafka`，Python import 包名为 `onestep_kafka`。
- 插件要求 Python `>=3.10`，不改变 onestep core 的 Python `>=3.9` 要求。
- 使用 `aiokafka>=0.14.0` 作为 Kafka client。
- 注册 YAML resource type：
  - `kafka`
  - `kafka_topic`
- `kafka_topic` 同时实现 `Source` 和 `Sink`，支持普通 Kafka topic 的消费和生产。
- 消费端使用 consumer group 和 manual commit，符合 onestep at-least-once 契约。
- 生产端使用 onestep envelope codec 写入 Kafka value。
- 提供 focused unit tests、runtime contract tests 和可选 live Kafka integration tests。

## 非目标

- 不提供 exactly-once 语义。
- 不启用 Kafka auto commit。
- 不实现 Kafka transactions 或事务生产者。
- 不实现 Kafka Streams、join、window、stateful stream processing。
- 不内置 Schema Registry、Avro、Protobuf 或 JSON Schema 编解码。
- 不在第一版设计 retry topic、delay topic 或 DLQ topic 编排；用户可用 onestep 现有 retry/dead-letter 机制，dead-letter sink 可以是另一个 `kafka_topic`。
- 不拆分 `kafka_consumer` 和 `kafka_producer` resource type。
- 不改变 core `Source`、`Sink`、`Delivery`、`OneStepApp` API。
- 不改变 reporter payload、control-plane WebSocket protocol、runtime identity 或 remote-control 行为。

## 包结构

```text
plugins/onestep-kafka/
  pyproject.toml
  README.md
  src/onestep_kafka/
    __init__.py
    connector.py
    resources.py
    resilience.py
  tests/
    test_kafka_ack_tracker.py
    test_kafka_connector.py
    test_kafka_plugin.py
    test_kafka_runtime_contract.py
    integration/
      test_kafka_live.py
```

`connector.py` contains the runtime implementation: connector, topic source/sink, delivery wrapper, offset tracking, producer and consumer lifecycle.

`resources.py` contains YAML construction and strict field validation.

`resilience.py` maps common `aiokafka` errors into `ConnectorOperationError` where that improves retry behavior and observability.

`__init__.py` exports the public Python API and the entry point target:

```python
from .resources import register_resources as register
```

## Dependencies

The plugin depends on:

- `onestep>=1.4.4`
- `aiokafka>=0.14.0`

The plugin package sets:

```toml
requires-python = ">=3.10"
```

The plugin should live in the repo, but it should not be added to the root uv
workspace while onestep core still supports Python 3.9. A workspace member with
`requires-python = ">=3.10"` makes Python 3.9 `uv sync --all-packages` fail
before tests start. Keep `plugins/onestep-kafka` as a repo-local package with a
plugin-local `tool.uv.sources` entry pointing `onestep` at the repo root.

The root project may add source metadata for local optional-extra resolution:

```toml
[tool.uv.sources]
onestep-kafka = { path = "plugins/onestep-kafka" }
```

The core optional extras should add:

```toml
kafka = [
    "onestep-kafka>=0.1.0; python_version >= '3.10'",
]
```

The `all`, `dev`, and `integration` extras may include the same Python-version-marked Kafka dependency. Core 3.9 CI must not install or test the Kafka plugin. Kafka gets its own 3.10+ plugin workflow and can be included in integration/reliability checks only when the active Python is 3.10 or newer.

## YAML Resource Types

Example worker wiring:

```yaml
resources:
  kafka_main:
    type: kafka
    bootstrap_servers: "${KAFKA_BOOTSTRAP_SERVERS}"
    options:
      security_protocol: SASL_SSL

  orders:
    type: kafka_topic
    connector: kafka_main
    topic: orders.events
    group_id: onestep-orders
    client_id: "${HOSTNAME:-onestep-local}"
    batch_size: 100
    poll_timeout_ms: 1000
```

`kafka` accepts:

- `bootstrap_servers`: string or string list accepted by `aiokafka`
- `options`: optional mapping passed to `AIOKafkaConsumer` and `AIOKafkaProducer` when relevant

`kafka_topic` accepts:

- `connector`: reference to a `kafka` resource
- `topic`: Kafka topic name
- `group_id`: consumer group id required when the resource is used as a source
- `client_id`: optional client id used by both consumer and producer
- `batch_size`: maximum messages returned by one `fetch()`
- `poll_timeout_ms`: consumer poll timeout
- `key`: optional static producer key for sink usage
- `headers`: optional static producer headers for sink usage
- `consumer_options`: optional mapping passed only to `AIOKafkaConsumer`
- `producer_options`: optional mapping passed only to `AIOKafkaProducer`

Connection-level options remain available through `kafka.options`. Topic-level overrides belong on `kafka_topic`. SASL and TLS settings are not modeled as custom schema fields in the first version; they pass through to `aiokafka` using `options`, `consumer_options`, or `producer_options`.

`group_id` is not required for sink-only usage. If a `kafka_topic` without `group_id` is used as a source, consumer open or fetch should fail with a clear configuration error.

## Runtime Behavior

`KafkaConnector` stores shared configuration and builds `KafkaTopic` resources.

`KafkaTopic` implements both `Source` and `Sink`:

- source path uses `AIOKafkaConsumer`
- sink path uses `AIOKafkaProducer`
- producer and consumer lifecycle is opened lazily and closed explicitly by `close()`
- separate consumer and producer instances are allowed because `kafka_topic` can be used as source, sink, or both

`fetch()` uses `AIOKafkaConsumer.getmany()` and converts returned records into `KafkaDelivery` objects. It should return at most `batch_size` deliveries, even if the client returns records from multiple topic partitions.

`send(envelope)` uses `AIOKafkaProducer.send_and_wait()` and writes `encode_envelope(envelope)` as the Kafka value.

The plugin sets `fetch_is_cancel_safe = False`. A fetched Kafka record already advances the consumer position in memory even though the offset is not committed. Runtime shutdown, drain, and pause paths must therefore call `release_unstarted()` for deliveries fetched but not started.

## Delivery And Offset Semantics

The plugin follows onestep's at-least-once contract.

Consumer configuration must set:

```python
enable_auto_commit = False
```

Successful message flow:

1. `fetch()` receives Kafka records.
2. The runtime calls `delivery.start_processing()`.
3. The handler runs.
4. Selected sinks receive the result envelope.
5. The runtime calls `delivery.ack()`.
6. The plugin commits eligible Kafka offsets.

`ack()` must not blindly commit the individual message offset. onestep can process messages concurrently, and Kafka commits are per topic-partition. If offset 11 finishes before offset 10 in the same partition, committing offset 12 would lose offset 10 after a crash.

The plugin therefore maintains a per topic-partition contiguous ack tracker:

- track fetched offsets
- track offsets that have started processing
- track offsets that have completed through `ack()` or terminal `fail()`
- commit only the next offset after the highest contiguous completed range
- never commit past a gap

`ack()` marks the message complete and attempts to commit the newly contiguous offset for its topic-partition.

`retry(delay_s=...)` does not commit the message. If a delay is provided, it sleeps before making the message eligible for redelivery. The implementation may seek the consumer position back to the failed offset when doing so is safe for that partition; it must not skip uncommitted lower offsets.

`fail()` treats the message as terminal and advances the same contiguous tracker used by `ack()`. This matches onestep dead-letter behavior: after a message is successfully dead-lettered or otherwise terminally failed, the source should not poison-loop forever.

`release_unstarted()` must not commit. It releases records fetched during a fetch cycle but not yet handed to processing. The implementation should keep these offsets uncommitted and seek the consumer back to the earliest released offset for the partition when needed.

Failure consequences remain onestep-standard:

- If a sink send succeeds and Kafka `ack()` fails, the input may be retried and downstream output may be duplicated.
- Multi-sink fan-out is not transactional.
- If dead-letter publish fails, the original Kafka delivery is retried and the offset is not committed.
- Production handlers and sinks must be idempotent where duplicates matter.

## Message Codec And Metadata

Kafka value uses the existing onestep envelope codec:

- producer: `encode_envelope(envelope)`
- consumer: `decode_envelope(record.value)`

The consumer adds Kafka metadata to `envelope.meta["kafka"]`:

- `topic`
- `partition`
- `offset`
- `timestamp`
- `key`
- `headers`

The plugin should preserve existing `envelope.meta["kafka"]` keys that are unrelated to the current delivery only if doing so cannot create ambiguity. Current delivery fields should overwrite stale delivery identity fields.

Producer key and headers are supported in the first version:

- static `key` and `headers` through YAML resource fields
- Python API can pass bytes-compatible values directly when constructing the resource

The first version does not provide key factories, payload-field mapping, or schema-aware encoding. Those can be added later without changing the base resource model.

## Strict Validation

YAML handlers should reject unknown fields in strict mode and report errors at the resource path.

Validation requirements:

- `kafka.bootstrap_servers` is required.
- `kafka.options`, when provided, must be a mapping.
- `kafka_topic.connector` must resolve to a `KafkaConnector`.
- `kafka_topic.topic` is required and must be a non-empty string.
- `kafka_topic.group_id` is optional at resource construction time, but required before opening a consumer.
- `batch_size` must be positive.
- `poll_timeout_ms` must be non-negative.
- `headers`, when provided, must be a mapping or list form accepted by the plugin.
- `consumer_options` and `producer_options`, when provided, must be mappings.

The first implementation should keep validation focused on field shape and onestep semantics. It should not try to validate every Kafka client option ahead of `aiokafka`.

## Control Plane Impact

No control-plane coordination is required for the first version.

The plugin only adds runtime resource types. It does not remove or rename reporter payload fields, change task lifecycle event names, change WebSocket protocol behavior, change runtime identity, or change remote task-control behavior.

Topology display can rely on existing generic source/sink descriptors. Friendly labels for `kafka` and `kafka_topic` can be added later in control-plane UI if needed.

## Testing

Focused unit tests cover:

- entry point registration through `onestep.resources`
- strict resource field validation
- missing optional dependency error behavior
- envelope encode/decode through Kafka value bytes
- Kafka metadata injection
- producer key and headers normalization
- per-partition contiguous ack tracker
- out-of-order ack does not commit past a gap
- ack after gap closure commits the highest contiguous offset
- `retry()` does not commit
- `fail()` participates in contiguous commit progression
- `release_unstarted()` does not commit
- connector open/close idempotence

Runtime contract tests cover:

- `KafkaTopic.fetch_is_cancel_safe is False`
- fetched but unstarted deliveries are released during stop controls
- cancellation does not commit offsets for unstarted deliveries

Integration tests are marked `integration` and use live Kafka infrastructure. They cover:

- producing a message and fetching it through a real consumer group
- acknowledging a message and verifying it is not redelivered
- retrying or crashing before ack and verifying redelivery
- producing task output to a Kafka topic
- reading and writing headers/key where supported

Default validation for development:

```bash
uv run --all-packages python -m pytest -q plugins/onestep-kafka/tests -m "not integration"
```

Live validation when Kafka infrastructure is available:

```bash
uv run --all-packages python -m pytest -q plugins/onestep-kafka/tests/integration -m integration
```

Before release or handoff, run the repo reliability checks:

```bash
uv run pytest -q -m "not integration"
./scripts/run-reliability-checks.sh
```

## Documentation And Agent Guidance

The plugin README should include concise install, YAML, and Python examples. It should state the important delivery caveats rather than burying them in long prose:

- at-least-once, not exactly-once
- auto commit disabled
- sink output may duplicate if the worker crashes after output send and before Kafka commit
- handlers and downstream sinks should be idempotent where duplicates matter

The onestep skill connector reference should add a short Kafka section after implementation so future agent-generated workers use the correct resource names and manual-commit mental model.

## Release Impact

The new plugin starts at version `0.1.0`.

Adding optional extras and dependency metadata requires updating `uv.lock`. The Kafka plugin should not be added as a root workspace member while core Python 3.9 support remains active.

Publishing `onestep-kafka` is independent from core unless the release also changes core package behavior. If root package metadata changes to add the `kafka` extra, follow the onestep release rules for core version, changelog, lockfile, and tag.

No `onestep-control-plane` release is required for the first Kafka plugin release.

## Success Criteria

This work is done when:

- `onestep-kafka` can be installed as an independent plugin package
- YAML workers can use `type: kafka` and `type: kafka_topic`
- a task can consume from Kafka and emit to Kafka
- offsets are committed only after onestep processing reaches `ack()` or terminal `fail()`
- out-of-order concurrent completion cannot commit past an unfinished lower offset in the same partition
- drain, pause, and shutdown do not commit fetched but unstarted messages
- unit and runtime contract tests pass without live Kafka
- live integration tests pass when Kafka infrastructure is available
- the README and onestep skill reference document the resource names and at-least-once caveats
