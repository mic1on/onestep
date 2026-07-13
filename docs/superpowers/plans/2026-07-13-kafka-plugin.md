# Kafka Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an independent `onestep-kafka` plugin that consumes and produces Kafka topic messages through `aiokafka` while preserving onestep's at-least-once delivery contract.

**Architecture:** Keep the plugin repo-local but outside the root uv workspace because it requires Python `>=3.10` while core still supports Python `>=3.9`. Implement a `kafka` connector resource and a dual source/sink `kafka_topic` resource. Use an explicit per-partition contiguous offset tracker so concurrent out-of-order delivery completion never commits past an unfinished lower offset.

**Tech Stack:** Python `>=3.10`, `aiokafka>=0.14.0`, onestep `Source`/`Sink`/`Delivery`, onestep resource registry, pytest, uv, Redpanda for live Kafka integration.

---

## File Structure

- Create `plugins/onestep-kafka/pyproject.toml`: plugin package metadata, entry point, plugin-local uv source pointing `onestep` to the repo root.
- Create `plugins/onestep-kafka/README.md`: concise install, YAML, Python, and delivery caveat reference.
- Create `plugins/onestep-kafka/src/onestep_kafka/__init__.py`: public exports and entry point target.
- Create `plugins/onestep-kafka/src/onestep_kafka/connector.py`: connector, topic source/sink, delivery wrapper, offset tracker, key/header normalization, metadata injection.
- Create `plugins/onestep-kafka/src/onestep_kafka/resources.py`: YAML resource handlers and strict validation.
- Create `plugins/onestep-kafka/src/onestep_kafka/resilience.py`: aiokafka error classification into `ConnectorOperationError`.
- Create `plugins/onestep-kafka/tests/test_kafka_ack_tracker.py`: focused offset tracker tests.
- Create `plugins/onestep-kafka/tests/test_kafka_connector.py`: fake consumer/producer tests for codec, metadata, send/fetch/ack/retry/fail/release behavior.
- Create `plugins/onestep-kafka/tests/test_kafka_plugin.py`: entry point, YAML resource construction, strict validation, resilience tests.
- Create `plugins/onestep-kafka/tests/test_kafka_runtime_contract.py`: runtime shutdown behavior for `fetch_is_cancel_safe=False`.
- Create `plugins/onestep-kafka/tests/integration/test_kafka_live.py`: live Kafka publish/fetch/ack/redelivery tests.
- Create `.github/workflows/plugin-kafka.yml`: Kafka plugin test/build workflow on Python 3.10, 3.11, and 3.12 only.
- Modify `pyproject.toml`: add Python-version-marked `kafka` extra and local source path without adding a workspace member.
- Modify `uv.lock`: resolve new optional dependency metadata.
- Modify `docker-compose.integration.yml`: add Redpanda Kafka-compatible service.
- Modify `scripts/setup-integration-env.sh`: wait for Kafka and export Kafka integration env vars.
- Modify `scripts/run-integration-tests.sh`: include Kafka integration tests.
- Modify `scripts/run-reliability-checks.sh`: run Kafka plugin tests through plugin-local uv only on Python >=3.10.
- Modify `skills/onestep/references/connectors.md`: add short Kafka YAML guidance for future agent-generated workers.

## Important Compatibility Decision

Do not add `plugins/onestep-kafka` to `[tool.uv.workspace].members`. A workspace member with `requires-python = ">=3.10"` makes Python 3.9 `uv sync --all-packages` fail before tests start. The plugin still lives in the repo and is tested by its own workflow.

Use this root source mapping instead:

```toml
[tool.uv.sources]
onestep-kafka = { path = "plugins/onestep-kafka" }
```

Use Python-version markers in root extras:

```toml
"onestep-kafka>=0.1.0; python_version >= '3.10'"
```

### Task 1: Package Skeleton And Root Metadata

**Files:**
- Create: `plugins/onestep-kafka/pyproject.toml`
- Create: `plugins/onestep-kafka/README.md`
- Create: `plugins/onestep-kafka/src/onestep_kafka/__init__.py`
- Create: `plugins/onestep-kafka/src/onestep_kafka/connector.py`
- Create: `plugins/onestep-kafka/src/onestep_kafka/resources.py`
- Create: `plugins/onestep-kafka/src/onestep_kafka/resilience.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 1: Create the plugin package metadata**

Create `plugins/onestep-kafka/pyproject.toml`:

```toml
[project]
name = "onestep-kafka"
version = "0.1.0"
description = "Kafka connector plugin for onestep."
readme = "README.md"
requires-python = ">=3.10"
license = { text = "MIT" }
dependencies = [
    "onestep>=1.4.4",
    "aiokafka>=0.14.0",
]

[project.optional-dependencies]
test = [
    "pytest>=8.0.0",
]
dev = [
    "pytest>=8.0.0",
]

[project.entry-points."onestep.resources"]
kafka = "onestep_kafka:register"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/onestep_kafka"]

[tool.uv.sources]
onestep = { path = "../..", editable = true }
```

- [ ] **Step 2: Create a concise README**

Create `plugins/onestep-kafka/README.md`:

````markdown
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
````

- [ ] **Step 3: Create initial module files**

Create `plugins/onestep-kafka/src/onestep_kafka/__init__.py`:

```python
from __future__ import annotations

from .connector import KafkaConnector, KafkaDelivery, KafkaOffsetTracker, KafkaTopic
from .resources import register_resources as register

__all__ = [
    "KafkaConnector",
    "KafkaDelivery",
    "KafkaOffsetTracker",
    "KafkaTopic",
    "register",
]
```

Create `plugins/onestep-kafka/src/onestep_kafka/connector.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class KafkaConnector:
    bootstrap_servers: str | list[str]
    options: dict[str, Any] | None = None
```

Create `plugins/onestep-kafka/src/onestep_kafka/resources.py`:

```python
from __future__ import annotations

from onestep.resource_registry import ResourceRegistry


def register_resources(registry: ResourceRegistry) -> None:
    return None
```

Create `plugins/onestep-kafka/src/onestep_kafka/resilience.py`:

```python
from __future__ import annotations

from onestep.resilience import ConnectorErrorKind


def classify_kafka_error(exc: BaseException) -> ConnectorErrorKind | None:
    if isinstance(exc, (ConnectionError, OSError, TimeoutError)):
        return ConnectorErrorKind.DISCONNECTED
    return None
```

- [ ] **Step 4: Modify root package metadata without adding a workspace member**

In root `pyproject.toml`, add this optional extra:

```toml
kafka = [
    "onestep-kafka>=0.1.0; python_version >= '3.10'",
]
```

Add the same marked dependency to `integration`, `all`, and `dev`.

Add this source mapping under `[tool.uv.sources]`:

```toml
onestep-kafka = { path = "plugins/onestep-kafka" }
```

Do not add `plugins/onestep-kafka` to `[tool.uv.workspace].members`.

- [ ] **Step 5: Resolve lockfile**

Run:

```bash
uv lock
```

Expected: command exits 0 and `uv.lock` includes `onestep-kafka` and `aiokafka`.

- [ ] **Step 6: Verify plugin project installs on Python >=3.10**

Run:

```bash
uv run --project plugins/onestep-kafka python - <<'PY'
import onestep
import onestep_kafka
print(onestep_kafka.__all__)
PY
```

Expected: prints the exported names and exits 0.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock plugins/onestep-kafka
git commit -m "feat(kafka): add plugin package skeleton"
```

### Task 2: Offset Tracker

**Files:**
- Create: `plugins/onestep-kafka/tests/test_kafka_ack_tracker.py`
- Modify: `plugins/onestep-kafka/src/onestep_kafka/connector.py`

- [ ] **Step 1: Write failing offset tracker tests**

Create `plugins/onestep-kafka/tests/test_kafka_ack_tracker.py`:

```python
from __future__ import annotations

from onestep_kafka import KafkaOffsetTracker


def test_tracker_commits_contiguous_offsets_in_order() -> None:
    tracker = KafkaOffsetTracker()
    tracker.mark_fetched("orders", 0, 10)
    tracker.mark_fetched("orders", 0, 11)

    assert tracker.mark_completed("orders", 0, 10) == 11
    assert tracker.mark_completed("orders", 0, 11) == 12


def test_tracker_does_not_commit_past_gap() -> None:
    tracker = KafkaOffsetTracker()
    tracker.mark_fetched("orders", 0, 10)
    tracker.mark_fetched("orders", 0, 11)

    assert tracker.mark_completed("orders", 0, 11) is None
    assert tracker.mark_completed("orders", 0, 10) == 12


def test_tracker_keeps_partitions_independent() -> None:
    tracker = KafkaOffsetTracker()
    tracker.mark_fetched("orders", 0, 10)
    tracker.mark_fetched("orders", 1, 5)

    assert tracker.mark_completed("orders", 1, 5) == 6
    assert tracker.mark_completed("orders", 0, 10) == 11


def test_tracker_release_unstarted_does_not_commit() -> None:
    tracker = KafkaOffsetTracker()
    tracker.mark_fetched("orders", 0, 10)

    assert tracker.mark_released_unstarted("orders", 0, 10) == 10
    assert tracker.next_committed_offset("orders", 0) == 10
    assert tracker.mark_completed("orders", 0, 10) == 11


def test_tracker_does_not_release_started_delivery() -> None:
    tracker = KafkaOffsetTracker()
    tracker.mark_fetched("orders", 0, 10)
    tracker.mark_started("orders", 0, 10)

    assert tracker.mark_released_unstarted("orders", 0, 10) is None
    assert tracker.mark_completed("orders", 0, 10) == 11
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run --project plugins/onestep-kafka python -m pytest -q plugins/onestep-kafka/tests/test_kafka_ack_tracker.py
```

Expected: FAIL with `ImportError` or `AttributeError` because `KafkaOffsetTracker` is not implemented.

- [ ] **Step 3: Implement the offset tracker**

Replace `plugins/onestep-kafka/src/onestep_kafka/connector.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class _PartitionOffsetState:
    next_commit_offset: int | None = None
    fetched_offsets: set[int] = field(default_factory=set)
    started_offsets: set[int] = field(default_factory=set)
    completed_offsets: set[int] = field(default_factory=set)


class KafkaOffsetTracker:
    def __init__(self) -> None:
        self._states: dict[tuple[str, int], _PartitionOffsetState] = {}

    def mark_fetched(self, topic: str, partition: int, offset: int) -> None:
        state = self._state(topic, partition, offset)
        state.fetched_offsets.add(offset)

    def mark_started(self, topic: str, partition: int, offset: int) -> None:
        state = self._state(topic, partition, offset)
        state.started_offsets.add(offset)

    def mark_completed(self, topic: str, partition: int, offset: int) -> int | None:
        state = self._state(topic, partition, offset)
        state.completed_offsets.add(offset)
        next_offset = state.next_commit_offset
        if next_offset is None:
            next_offset = offset
        changed = False
        while next_offset in state.completed_offsets:
            state.completed_offsets.remove(next_offset)
            state.fetched_offsets.discard(next_offset)
            state.started_offsets.discard(next_offset)
            next_offset += 1
            changed = True
        if changed:
            state.next_commit_offset = next_offset
            return next_offset
        return None

    def mark_released_unstarted(self, topic: str, partition: int, offset: int) -> int | None:
        state = self._state(topic, partition, offset)
        if offset in state.started_offsets or offset in state.completed_offsets:
            return None
        state.fetched_offsets.discard(offset)
        return offset

    def next_committed_offset(self, topic: str, partition: int) -> int | None:
        state = self._states.get((topic, partition))
        return None if state is None else state.next_commit_offset

    def _state(self, topic: str, partition: int, offset: int) -> _PartitionOffsetState:
        key = (topic, partition)
        state = self._states.get(key)
        if state is None:
            state = _PartitionOffsetState(next_commit_offset=offset)
            self._states[key] = state
        elif state.next_commit_offset is None or offset < state.next_commit_offset:
            state.next_commit_offset = offset
        return state


@dataclass
class KafkaConnector:
    bootstrap_servers: str | list[str]
    options: dict[str, Any] | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
uv run --project plugins/onestep-kafka python -m pytest -q plugins/onestep-kafka/tests/test_kafka_ack_tracker.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add plugins/onestep-kafka/src/onestep_kafka/connector.py plugins/onestep-kafka/tests/test_kafka_ack_tracker.py
git commit -m "feat(kafka): add contiguous offset tracker"
```

### Task 3: Resource Registration And Validation

**Files:**
- Create: `plugins/onestep-kafka/tests/test_kafka_plugin.py`
- Modify: `plugins/onestep-kafka/src/onestep_kafka/connector.py`
- Modify: `plugins/onestep-kafka/src/onestep_kafka/resources.py`

- [ ] **Step 1: Write failing plugin/resource tests**

Create `plugins/onestep-kafka/tests/test_kafka_plugin.py`:

```python
from __future__ import annotations

from importlib import metadata as importlib_metadata
from typing import Any

import pytest

from onestep.config import load_app_config
from onestep_kafka import KafkaConnector, KafkaTopic


def test_package_exposes_onestep_resource_entry_point() -> None:
    entry_points = _entry_points_for_group("onestep.resources")

    assert any(
        entry_point.name == "kafka"
        and entry_point.value == "onestep_kafka:register"
        for entry_point in entry_points
    )


def test_yaml_builds_kafka_resources_via_plugin_entry_point() -> None:
    app = load_app_config(
        {
            "apiVersion": "onestep/v1alpha1",
            "kind": "App",
            "app": {"name": "kafka-plugin"},
            "resources": {
                "kafka_main": {
                    "type": "kafka",
                    "bootstrap_servers": ["localhost:9092"],
                    "options": {"security_protocol": "PLAINTEXT"},
                },
                "orders": {
                    "type": "kafka_topic",
                    "connector": "kafka_main",
                    "topic": "orders.events",
                    "group_id": "orders-workers",
                    "client_id": "onestep-test",
                    "batch_size": 25,
                    "poll_timeout_ms": 250,
                    "key": "static-key",
                    "headers": {"source": "onestep"},
                    "consumer_options": {"auto_offset_reset": "earliest"},
                    "producer_options": {"compression_type": "gzip"},
                },
            },
            "tasks": [],
        },
        strict=True,
    )

    connector = app.resources["kafka_main"]
    topic = app.resources["orders"]
    assert isinstance(connector, KafkaConnector)
    assert connector.bootstrap_servers == ["localhost:9092"]
    assert connector.options == {"security_protocol": "PLAINTEXT"}
    assert isinstance(topic, KafkaTopic)
    assert topic.connector is connector
    assert topic.topic == "orders.events"
    assert topic.group_id == "orders-workers"
    assert topic.client_id == "onestep-test"
    assert topic.batch_size == 25
    assert topic.poll_timeout_ms == 250
    assert topic.key == "static-key"
    assert topic.headers == {"source": "onestep"}
    assert topic.consumer_options == {"auto_offset_reset": "earliest"}
    assert topic.producer_options == {"compression_type": "gzip"}


def test_kafka_topic_group_id_is_optional_for_sink_only_resource() -> None:
    app = load_app_config(
        {
            "apiVersion": "onestep/v1alpha1",
            "kind": "App",
            "app": {"name": "kafka-sink-only"},
            "resources": {
                "kafka_main": {
                    "type": "kafka",
                    "bootstrap_servers": "localhost:9092",
                },
                "outbox": {
                    "type": "kafka_topic",
                    "connector": "kafka_main",
                    "topic": "orders.outbox",
                },
            },
            "tasks": [],
        },
        strict=True,
    )

    assert isinstance(app.resources["outbox"], KafkaTopic)
    assert app.resources["outbox"].group_id is None


def test_kafka_topic_rejects_invalid_batch_size_in_strict_mode() -> None:
    with pytest.raises(ValueError, match="'resources.orders.batch_size' must be a positive integer"):
        load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "bad-kafka"},
                "resources": {
                    "kafka_main": {
                        "type": "kafka",
                        "bootstrap_servers": "localhost:9092",
                    },
                    "orders": {
                        "type": "kafka_topic",
                        "connector": "kafka_main",
                        "topic": "orders.events",
                        "batch_size": 0,
                    },
                },
                "tasks": [],
            },
            strict=True,
        )


def _entry_points_for_group(group: str) -> tuple[Any, ...]:
    entry_points = importlib_metadata.entry_points()
    if hasattr(entry_points, "select"):
        return tuple(entry_points.select(group=group))
    return tuple(entry_points.get(group, ()))
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run --project plugins/onestep-kafka python -m pytest -q plugins/onestep-kafka/tests/test_kafka_plugin.py
```

Expected: FAIL because resource handlers and `KafkaTopic` are not implemented.

- [ ] **Step 3: Add resource construction methods**

In `plugins/onestep-kafka/src/onestep_kafka/connector.py`, extend `KafkaConnector` and add `KafkaTopic`:

```python
class KafkaConnector:
    def __init__(self, bootstrap_servers: str | list[str], options: dict[str, Any] | None = None) -> None:
        self.bootstrap_servers = bootstrap_servers
        self.options = options or {}

    def topic(
        self,
        topic: str,
        *,
        group_id: str | None = None,
        client_id: str | None = None,
        batch_size: int = 100,
        poll_timeout_ms: int = 1000,
        key: str | bytes | None = None,
        headers: dict[str, Any] | list[tuple[str, Any]] | None = None,
        consumer_options: dict[str, Any] | None = None,
        producer_options: dict[str, Any] | None = None,
    ) -> "KafkaTopic":
        return KafkaTopic(
            connector=self,
            topic=topic,
            group_id=group_id,
            client_id=client_id,
            batch_size=batch_size,
            poll_timeout_ms=poll_timeout_ms,
            key=key,
            headers=headers,
            consumer_options=consumer_options or {},
            producer_options=producer_options or {},
        )
```

Add this class below `KafkaConnector`:

```python
class KafkaTopic:
    fetch_is_cancel_safe = False

    def __init__(
        self,
        *,
        connector: KafkaConnector,
        topic: str,
        group_id: str | None,
        client_id: str | None,
        batch_size: int,
        poll_timeout_ms: int,
        key: str | bytes | None,
        headers: dict[str, Any] | list[tuple[str, Any]] | None,
        consumer_options: dict[str, Any],
        producer_options: dict[str, Any],
    ) -> None:
        self.connector = connector
        self.topic = topic
        self.name = topic
        self.group_id = group_id
        self.client_id = client_id
        self.batch_size = batch_size
        self.poll_timeout_ms = poll_timeout_ms
        self.key = key
        self.headers = headers
        self.consumer_options = consumer_options
        self.producer_options = producer_options
```

- [ ] **Step 4: Implement YAML resource handlers and validation**

Replace `plugins/onestep-kafka/src/onestep_kafka/resources.py` with:

```python
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from onestep.resource_registry import ResourceBuildContext, ResourceRegistry, ResourceSpecHandler, ResourceValidationContext

from .connector import KafkaConnector

_KAFKA_FIELDS = frozenset({"type", "bootstrap_servers", "options"})
_KAFKA_TOPIC_FIELDS = frozenset(
    {
        "type",
        "name",
        "connector",
        "topic",
        "group_id",
        "client_id",
        "batch_size",
        "poll_timeout_ms",
        "key",
        "headers",
        "consumer_options",
        "producer_options",
    }
)


def register_resources(registry: ResourceRegistry) -> None:
    registry.register_resource_type(
        ResourceSpecHandler(
            type="kafka",
            allowed_fields=_KAFKA_FIELDS,
            build=_build_kafka,
            validate=_validate_kafka,
        )
    )
    registry.register_resource_type(
        ResourceSpecHandler(
            type="kafka_topic",
            allowed_fields=_KAFKA_TOPIC_FIELDS,
            build=_build_kafka_topic,
            validate=_validate_kafka_topic,
        )
    )


def _build_kafka(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> KafkaConnector:
    return KafkaConnector(
        _string_or_string_list(spec.get("bootstrap_servers"), field=f"{ctx.field}.bootstrap_servers"),
        options=ctx.mapping_value(spec.get("options"), field=f"{ctx.field}.options"),
    )


def _build_kafka_topic(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> Any:
    connector = ctx.resolve_dependency(spec, "connector")
    if not hasattr(connector, "topic"):
        raise TypeError(f"resource {spec['connector']!r} cannot build kafka_topic")
    return connector.topic(
        ctx.resource_name(spec, key="topic"),
        group_id=spec.get("group_id"),
        client_id=spec.get("client_id"),
        batch_size=spec.get("batch_size", 100),
        poll_timeout_ms=spec.get("poll_timeout_ms", 1000),
        key=spec.get("key"),
        headers=spec.get("headers"),
        consumer_options=ctx.mapping_value(spec.get("consumer_options"), field=f"{ctx.field}.consumer_options"),
        producer_options=ctx.mapping_value(spec.get("producer_options"), field=f"{ctx.field}.producer_options"),
    )


def _validate_kafka(ctx: ResourceValidationContext, spec: Mapping[str, Any]) -> None:
    _string_or_string_list(spec.get("bootstrap_servers"), field=f"{ctx.field}.bootstrap_servers")
    if "options" in spec and not isinstance(spec.get("options"), Mapping):
        raise TypeError(f"'{ctx.field}.options' must be a mapping")


def _validate_kafka_topic(ctx: ResourceValidationContext, spec: Mapping[str, Any]) -> None:
    ctx.require_string(spec, "connector")
    ctx.require_string(spec, "topic")
    if "group_id" in spec and spec.get("group_id") is not None:
        ctx.string_value(spec.get("group_id"), field=f"{ctx.field}.group_id")
    if "client_id" in spec and spec.get("client_id") is not None:
        ctx.string_value(spec.get("client_id"), field=f"{ctx.field}.client_id")
    if "batch_size" in spec:
        _positive_integer(spec.get("batch_size"), field=f"{ctx.field}.batch_size")
    if "poll_timeout_ms" in spec:
        _non_negative_integer(spec.get("poll_timeout_ms"), field=f"{ctx.field}.poll_timeout_ms")
    if "headers" in spec:
        _validate_headers(spec.get("headers"), field=f"{ctx.field}.headers")
    for key in ("consumer_options", "producer_options"):
        if key in spec and not isinstance(spec.get(key), Mapping):
            raise TypeError(f"'{ctx.field}.{key}' must be a mapping")


def _string_or_string_list(value: Any, *, field: str) -> str | list[str]:
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        items = [item for item in value if isinstance(item, str) and item.strip()]
        if len(items) == len(value) and items:
            return list(items)
    raise ValueError(f"'{field}' must be a non-empty string or list of strings")


def _positive_integer(value: Any, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"'{field}' must be a positive integer")
    return value


def _non_negative_integer(value: Any, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"'{field}' must be a non-negative integer")
    return value


def _validate_headers(value: Any, *, field: str) -> None:
    if value is None:
        return
    if isinstance(value, Mapping):
        for key in value:
            if not isinstance(key, str) or not key:
                raise ValueError(f"'{field}' header names must be non-empty strings")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, item in enumerate(value):
            if not isinstance(item, Sequence) or isinstance(item, (str, bytes)) or len(item) != 2:
                raise TypeError(f"'{field}[{index}]' must be a two-item header pair")
            if not isinstance(item[0], str) or not item[0]:
                raise ValueError(f"'{field}[{index}][0]' must be a non-empty string")
        return
    raise TypeError(f"'{field}' must be a mapping or list of header pairs")
```

- [ ] **Step 5: Run plugin tests**

Run:

```bash
uv run --project plugins/onestep-kafka python -m pytest -q plugins/onestep-kafka/tests/test_kafka_plugin.py plugins/onestep-kafka/tests/test_kafka_ack_tracker.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add plugins/onestep-kafka/src/onestep_kafka/connector.py plugins/onestep-kafka/src/onestep_kafka/resources.py plugins/onestep-kafka/tests/test_kafka_plugin.py
git commit -m "feat(kafka): register yaml resources"
```

### Task 4: Connector Runtime With Fake aiokafka Clients

**Files:**
- Create: `plugins/onestep-kafka/tests/test_kafka_connector.py`
- Modify: `plugins/onestep-kafka/src/onestep_kafka/connector.py`

- [ ] **Step 1: Write fake-client connector tests**

Create `plugins/onestep-kafka/tests/test_kafka_connector.py`:

```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from onestep.envelope import Envelope
from onestep_kafka import KafkaConnector


@dataclass(frozen=True)
class FakeTopicPartition:
    topic: str
    partition: int


@dataclass
class FakeRecord:
    topic: str
    partition: int
    offset: int
    value: bytes
    key: bytes | None = None
    headers: list[tuple[str, bytes | None]] | None = None
    timestamp: int | None = None


class FakeConsumer:
    def __init__(self, *topics: str, **kwargs: Any) -> None:
        self.topics = topics
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.records: dict[FakeTopicPartition, list[FakeRecord]] = {}
        self.commits: list[dict[FakeTopicPartition, int]] = []
        self.seeks: list[tuple[FakeTopicPartition, int]] = []

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def getmany(self, *, timeout_ms: int, max_records: int):
        selected: dict[FakeTopicPartition, list[FakeRecord]] = {}
        remaining = max_records
        for topic_partition, records in list(self.records.items()):
            if remaining <= 0:
                break
            batch = records[:remaining]
            self.records[topic_partition] = records[remaining:]
            if batch:
                selected[topic_partition] = batch
                remaining -= len(batch)
        return selected

    async def commit(self, offsets):
        self.commits.append(dict(offsets))

    def seek(self, topic_partition, offset: int) -> None:
        self.seeks.append((topic_partition, offset))


class FakeProducer:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.sent: list[dict[str, Any]] = []

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def send_and_wait(self, topic: str, **kwargs: Any) -> None:
        self.sent.append({"topic": topic, **kwargs})


class FakeDriver:
    TopicPartition = FakeTopicPartition

    def __init__(self) -> None:
        self.consumers: list[FakeConsumer] = []
        self.producers: list[FakeProducer] = []

    def AIOKafkaConsumer(self, *topics: str, **kwargs: Any) -> FakeConsumer:
        consumer = FakeConsumer(*topics, **kwargs)
        self.consumers.append(consumer)
        return consumer

    def AIOKafkaProducer(self, **kwargs: Any) -> FakeProducer:
        producer = FakeProducer(**kwargs)
        self.producers.append(producer)
        return producer


def test_kafka_topic_sends_encoded_envelope_with_key_and_headers() -> None:
    async def scenario() -> None:
        driver = FakeDriver()
        connector = KafkaConnector("localhost:9092", options={"security_protocol": "PLAINTEXT"}, driver=driver)
        topic = connector.topic(
            "orders.out",
            key="tenant-a",
            headers={"source": "onestep"},
            producer_options={"compression_type": "gzip"},
        )

        await topic.send(Envelope(body={"id": 1}, meta={"trace": "abc"}, attempts=2))

        producer = driver.producers[0]
        assert producer.kwargs["bootstrap_servers"] == "localhost:9092"
        assert producer.kwargs["security_protocol"] == "PLAINTEXT"
        assert producer.kwargs["compression_type"] == "gzip"
        assert producer.sent[0]["topic"] == "orders.out"
        assert producer.sent[0]["key"] == b"tenant-a"
        assert producer.sent[0]["headers"] == [("source", b"onestep")]
        assert producer.sent[0]["value"] == b'{"body": {"id": 1}, "meta": {"trace": "abc"}, "attempts": 2}'

    asyncio.run(scenario())


def test_kafka_topic_fetch_decodes_envelopes_and_injects_metadata() -> None:
    async def scenario() -> None:
        driver = FakeDriver()
        connector = KafkaConnector("localhost:9092", driver=driver)
        topic = connector.topic(
            "orders.in",
            group_id="workers",
            client_id="worker-1",
            batch_size=10,
            poll_timeout_ms=5,
            consumer_options={"auto_offset_reset": "earliest"},
        )
        consumer = await topic._open_consumer()
        tp = FakeTopicPartition("orders.in", 0)
        consumer.records[tp] = [
            FakeRecord(
                topic="orders.in",
                partition=0,
                offset=10,
                value=b'{"body": {"id": 1}, "meta": {"trace": "abc", "kafka": {"old": "kept", "offset": 1}}, "attempts": 3}',
                key=b"tenant-a",
                headers=[("source", b"upstream")],
                timestamp=1720000000000,
            )
        ]

        deliveries = await topic.fetch(10)

        assert len(deliveries) == 1
        delivery = deliveries[0]
        assert delivery.payload == {"id": 1}
        assert delivery.envelope.attempts == 3
        assert delivery.envelope.meta["trace"] == "abc"
        assert delivery.envelope.meta["kafka"] == {
            "old": "kept",
            "topic": "orders.in",
            "partition": 0,
            "offset": 10,
            "timestamp": 1720000000000,
            "key": "tenant-a",
            "headers": {"source": "upstream"},
        }
        assert driver.consumers[0].kwargs["enable_auto_commit"] is False
        assert driver.consumers[0].kwargs["group_id"] == "workers"
        assert driver.consumers[0].kwargs["client_id"] == "worker-1"
        assert driver.consumers[0].kwargs["auto_offset_reset"] == "earliest"

    asyncio.run(scenario())


def test_ack_commits_only_after_contiguous_offsets_complete() -> None:
    async def scenario() -> None:
        driver = FakeDriver()
        connector = KafkaConnector("localhost:9092", driver=driver)
        topic = connector.topic("orders.in", group_id="workers")
        consumer = await topic._open_consumer()
        tp = FakeTopicPartition("orders.in", 0)
        consumer.records[tp] = [
            FakeRecord("orders.in", 0, 10, b'{"id": 10}'),
            FakeRecord("orders.in", 0, 11, b'{"id": 11}'),
        ]

        first, second = await topic.fetch(2)
        await first.start_processing()
        await second.start_processing()
        await second.ack()
        assert consumer.commits == []

        await first.ack()
        assert consumer.commits == [{tp: 12}]

    asyncio.run(scenario())


def test_retry_does_not_commit_and_seeks_to_offset() -> None:
    async def scenario() -> None:
        driver = FakeDriver()
        connector = KafkaConnector("localhost:9092", driver=driver)
        topic = connector.topic("orders.in", group_id="workers")
        consumer = await topic._open_consumer()
        tp = FakeTopicPartition("orders.in", 0)
        consumer.records[tp] = [FakeRecord("orders.in", 0, 10, b'{"id": 10}')]

        delivery = (await topic.fetch(1))[0]
        await delivery.start_processing()
        await delivery.retry()

        assert consumer.commits == []
        assert consumer.seeks == [(tp, 10)]

    asyncio.run(scenario())


def test_fail_advances_commit_tracker() -> None:
    async def scenario() -> None:
        driver = FakeDriver()
        connector = KafkaConnector("localhost:9092", driver=driver)
        topic = connector.topic("orders.in", group_id="workers")
        consumer = await topic._open_consumer()
        tp = FakeTopicPartition("orders.in", 0)
        consumer.records[tp] = [FakeRecord("orders.in", 0, 10, b'{"id": 10}')]

        delivery = (await topic.fetch(1))[0]
        await delivery.start_processing()
        await delivery.fail(RuntimeError("poison"))

        assert consumer.commits == [{tp: 11}]

    asyncio.run(scenario())


def test_release_unstarted_does_not_commit_and_seeks_to_offset() -> None:
    async def scenario() -> None:
        driver = FakeDriver()
        connector = KafkaConnector("localhost:9092", driver=driver)
        topic = connector.topic("orders.in", group_id="workers")
        consumer = await topic._open_consumer()
        tp = FakeTopicPartition("orders.in", 0)
        consumer.records[tp] = [FakeRecord("orders.in", 0, 10, b'{"id": 10}')]

        delivery = (await topic.fetch(1))[0]
        await delivery.release_unstarted()

        assert consumer.commits == []
        assert consumer.seeks == [(tp, 10)]

    asyncio.run(scenario())
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run --project plugins/onestep-kafka python -m pytest -q plugins/onestep-kafka/tests/test_kafka_connector.py
```

Expected: FAIL because runtime methods are not implemented.

- [ ] **Step 3: Replace connector implementation with runtime code**

Replace `plugins/onestep-kafka/src/onestep_kafka/connector.py` with a complete implementation containing these public classes and helpers:

```python
from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from onestep.envelope import Envelope
from onestep.resilience import ConnectorOperation, ConnectorOperationError

from onestep.connectors.base import Delivery, Sink, Source
from onestep.connectors.codec import decode_envelope, encode_envelope

from .resilience import as_kafka_connector_operation_error

try:  # pragma: no cover - optional dependency
    from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
    from aiokafka.structs import TopicPartition
except ImportError:  # pragma: no cover - optional dependency
    AIOKafkaConsumer = None
    AIOKafkaProducer = None
    TopicPartition = None


@dataclass
class _PartitionOffsetState:
    next_commit_offset: int | None = None
    fetched_offsets: set[int] = field(default_factory=set)
    started_offsets: set[int] = field(default_factory=set)
    completed_offsets: set[int] = field(default_factory=set)


class KafkaOffsetTracker:
    def __init__(self) -> None:
        self._states: dict[tuple[str, int], _PartitionOffsetState] = {}

    def mark_fetched(self, topic: str, partition: int, offset: int) -> None:
        state = self._state(topic, partition, offset)
        state.fetched_offsets.add(offset)

    def mark_started(self, topic: str, partition: int, offset: int) -> None:
        state = self._state(topic, partition, offset)
        state.started_offsets.add(offset)

    def mark_completed(self, topic: str, partition: int, offset: int) -> int | None:
        state = self._state(topic, partition, offset)
        state.completed_offsets.add(offset)
        next_offset = state.next_commit_offset if state.next_commit_offset is not None else offset
        changed = False
        while next_offset in state.completed_offsets:
            state.completed_offsets.remove(next_offset)
            state.fetched_offsets.discard(next_offset)
            state.started_offsets.discard(next_offset)
            next_offset += 1
            changed = True
        if changed:
            state.next_commit_offset = next_offset
            return next_offset
        return None

    def mark_released_unstarted(self, topic: str, partition: int, offset: int) -> int | None:
        state = self._state(topic, partition, offset)
        if offset in state.started_offsets or offset in state.completed_offsets:
            return None
        state.fetched_offsets.discard(offset)
        return offset

    def next_committed_offset(self, topic: str, partition: int) -> int | None:
        state = self._states.get((topic, partition))
        return None if state is None else state.next_commit_offset

    def _state(self, topic: str, partition: int, offset: int) -> _PartitionOffsetState:
        key = (topic, partition)
        state = self._states.get(key)
        if state is None:
            state = _PartitionOffsetState(next_commit_offset=offset)
            self._states[key] = state
        elif state.next_commit_offset is None or offset < state.next_commit_offset:
            state.next_commit_offset = offset
        return state


class KafkaDelivery(Delivery):
    def __init__(self, topic: "KafkaTopic", record: Any) -> None:
        self.topic = topic
        self.record = record
        super().__init__(_decode_record_envelope(record))

    async def start_processing(self) -> None:
        self.topic._offset_tracker.mark_started(self.record.topic, self.record.partition, self.record.offset)

    async def release_unstarted(self) -> None:
        seek_offset = self.topic._offset_tracker.mark_released_unstarted(
            self.record.topic,
            self.record.partition,
            self.record.offset,
        )
        if seek_offset is not None:
            await self.topic.seek_to_offset(self.record.topic, self.record.partition, seek_offset)

    async def ack(self) -> None:
        next_offset = self.topic._offset_tracker.mark_completed(
            self.record.topic,
            self.record.partition,
            self.record.offset,
        )
        if next_offset is not None:
            await self.topic.commit_offset(self.record.topic, self.record.partition, next_offset)

    async def retry(self, *, delay_s: float | None = None) -> None:
        if delay_s:
            await asyncio.sleep(delay_s)
        await self.topic.seek_to_offset(self.record.topic, self.record.partition, self.record.offset)

    async def fail(self, exc: Exception | None = None) -> None:
        await self.ack()


class KafkaConnector:
    def __init__(
        self,
        bootstrap_servers: str | list[str],
        options: dict[str, Any] | None = None,
        *,
        driver: Any | None = None,
    ) -> None:
        self.bootstrap_servers = bootstrap_servers
        self.options = options or {}
        self._driver_override = driver

    def topic(
        self,
        topic: str,
        *,
        group_id: str | None = None,
        client_id: str | None = None,
        batch_size: int = 100,
        poll_timeout_ms: int = 1000,
        key: str | bytes | None = None,
        headers: dict[str, Any] | list[tuple[str, Any]] | None = None,
        consumer_options: dict[str, Any] | None = None,
        producer_options: dict[str, Any] | None = None,
    ) -> "KafkaTopic":
        return KafkaTopic(
            connector=self,
            topic=topic,
            group_id=group_id,
            client_id=client_id,
            batch_size=batch_size,
            poll_timeout_ms=poll_timeout_ms,
            key=key,
            headers=headers,
            consumer_options=consumer_options or {},
            producer_options=producer_options or {},
        )

    def driver(self) -> Any:
        if self._driver_override is not None:
            return self._driver_override
        if AIOKafkaConsumer is None or AIOKafkaProducer is None or TopicPartition is None:
            raise RuntimeError("KafkaConnector requires aiokafka. Install onestep-kafka.")
        return _AiokafkaDriver()


class _AiokafkaDriver:
    AIOKafkaConsumer = AIOKafkaConsumer
    AIOKafkaProducer = AIOKafkaProducer
    TopicPartition = TopicPartition


class KafkaTopic(Source, Sink):
    fetch_is_cancel_safe = False

    def __init__(
        self,
        *,
        connector: KafkaConnector,
        topic: str,
        group_id: str | None,
        client_id: str | None,
        batch_size: int,
        poll_timeout_ms: int,
        key: str | bytes | None,
        headers: dict[str, Any] | list[tuple[str, Any]] | None,
        consumer_options: dict[str, Any],
        producer_options: dict[str, Any],
    ) -> None:
        Source.__init__(self, topic)
        Sink.__init__(self, topic)
        self.connector = connector
        self.topic = topic
        self.group_id = group_id
        self.client_id = client_id
        self.batch_size = batch_size
        self.poll_timeout_ms = poll_timeout_ms
        self.key = key
        self.headers = headers
        self.consumer_options = consumer_options
        self.producer_options = producer_options
        self._consumer: Any | None = None
        self._producer: Any | None = None
        self._consumer_lock: asyncio.Lock | None = None
        self._producer_lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._offset_tracker = KafkaOffsetTracker()

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        errors: list[BaseException] = []
        if self._consumer is not None:
            try:
                await self._consumer.stop()
            except Exception as exc:
                errors.append(exc)
            finally:
                self._consumer = None
        if self._producer is not None:
            try:
                await self._producer.stop()
            except Exception as exc:
                errors.append(exc)
            finally:
                self._producer = None
        if errors:
            raise errors[0]

    async def fetch(self, limit: int) -> list[Delivery]:
        try:
            consumer = await self._open_consumer()
            max_records = max(1, min(limit, self.batch_size))
            batches = await consumer.getmany(timeout_ms=self.poll_timeout_ms, max_records=max_records)
            deliveries: list[Delivery] = []
            for records in batches.values():
                for record in records:
                    if len(deliveries) >= max_records:
                        break
                    self._offset_tracker.mark_fetched(record.topic, record.partition, record.offset)
                    deliveries.append(KafkaDelivery(self, record))
            return deliveries
        except ConnectorOperationError:
            raise
        except Exception as exc:
            connector_error = as_kafka_connector_operation_error(
                operation=ConnectorOperation.FETCH,
                exc=exc,
                source_name=self.name,
                retry_delay_s=self.poll_timeout_ms / 1000,
            )
            if connector_error is None:
                raise
            raise connector_error from exc

    async def send(self, envelope: Envelope) -> None:
        try:
            producer = await self._open_producer()
            await producer.send_and_wait(
                self.topic,
                value=encode_envelope(envelope),
                key=_normalize_key(self.key),
                headers=_normalize_headers(self.headers),
            )
        except ConnectorOperationError:
            raise
        except Exception as exc:
            connector_error = as_kafka_connector_operation_error(
                operation=ConnectorOperation.SEND,
                exc=exc,
                source_name=self.name,
                retry_delay_s=self.poll_timeout_ms / 1000,
            )
            if connector_error is None:
                raise
            raise connector_error from exc

    async def commit_offset(self, topic: str, partition: int, next_offset: int) -> None:
        consumer = await self._open_consumer()
        topic_partition = self.connector.driver().TopicPartition(topic, partition)
        await consumer.commit({topic_partition: next_offset})

    async def seek_to_offset(self, topic: str, partition: int, offset: int) -> None:
        consumer = await self._open_consumer()
        topic_partition = self.connector.driver().TopicPartition(topic, partition)
        consumer.seek(topic_partition, offset)

    async def _open_consumer(self) -> Any:
        self._ensure_runtime_locks()
        assert self._consumer_lock is not None
        async with self._consumer_lock:
            if self._consumer is not None:
                return self._consumer
            if not self.group_id:
                raise ValueError("kafka_topic requires group_id when used as a source")
            driver = self.connector.driver()
            options = {
                **self.connector.options,
                **self.consumer_options,
                "bootstrap_servers": self.connector.bootstrap_servers,
                "group_id": self.group_id,
                "enable_auto_commit": False,
            }
            if self.client_id is not None:
                options["client_id"] = self.client_id
            self._consumer = driver.AIOKafkaConsumer(self.topic, **options)
            await self._consumer.start()
            return self._consumer

    async def _open_producer(self) -> Any:
        self._ensure_runtime_locks()
        assert self._producer_lock is not None
        async with self._producer_lock:
            if self._producer is not None:
                return self._producer
            driver = self.connector.driver()
            options = {
                **self.connector.options,
                **self.producer_options,
                "bootstrap_servers": self.connector.bootstrap_servers,
            }
            if self.client_id is not None:
                options["client_id"] = self.client_id
            self._producer = driver.AIOKafkaProducer(**options)
            await self._producer.start()
            return self._producer

    def _ensure_runtime_locks(self) -> None:
        current_loop = asyncio.get_running_loop()
        if self._loop is not current_loop:
            self._loop = current_loop
            self._consumer_lock = asyncio.Lock()
            self._producer_lock = asyncio.Lock()
            self._consumer = None
            self._producer = None
            self._offset_tracker = KafkaOffsetTracker()


def _decode_record_envelope(record: Any) -> Envelope:
    envelope = decode_envelope(record.value)
    kafka_meta = dict(envelope.meta.get("kafka", {})) if isinstance(envelope.meta.get("kafka"), Mapping) else {}
    kafka_meta.update(
        {
            "topic": record.topic,
            "partition": record.partition,
            "offset": record.offset,
            "timestamp": getattr(record, "timestamp", None),
            "key": _decode_bytes(getattr(record, "key", None)),
            "headers": _decode_headers(getattr(record, "headers", None)),
        }
    )
    envelope.meta["kafka"] = kafka_meta
    return envelope


def _normalize_key(value: str | bytes | None) -> bytes | None:
    if value is None or isinstance(value, bytes):
        return value
    return value.encode("utf-8")


def _normalize_headers(headers: dict[str, Any] | list[tuple[str, Any]] | None) -> list[tuple[str, bytes | None]] | None:
    if headers is None:
        return None
    items = headers.items() if isinstance(headers, Mapping) else headers
    return [(str(key), _normalize_header_value(value)) for key, value in items]


def _normalize_header_value(value: Any) -> bytes | None:
    if value is None or isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return str(value).encode("utf-8")


def _decode_headers(headers: Sequence[tuple[str, bytes | None]] | None) -> dict[str, str | None]:
    if not headers:
        return {}
    return {key: _decode_bytes(value) for key, value in headers}


def _decode_bytes(value: bytes | None) -> str | None:
    if value is None:
        return None
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return repr(value)
```

- [ ] **Step 4: Run connector and tracker tests**

Run:

```bash
uv run --project plugins/onestep-kafka python -m pytest -q \
  plugins/onestep-kafka/tests/test_kafka_ack_tracker.py \
  plugins/onestep-kafka/tests/test_kafka_connector.py \
  plugins/onestep-kafka/tests/test_kafka_plugin.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add plugins/onestep-kafka/src/onestep_kafka/connector.py plugins/onestep-kafka/tests/test_kafka_connector.py
git commit -m "feat(kafka): implement topic source and sink"
```

### Task 5: Resilience Error Mapping

**Files:**
- Modify: `plugins/onestep-kafka/src/onestep_kafka/resilience.py`
- Modify: `plugins/onestep-kafka/tests/test_kafka_plugin.py`

- [ ] **Step 1: Add failing resilience tests**

Append to `plugins/onestep-kafka/tests/test_kafka_plugin.py`:

```python
from onestep.resilience import ConnectorErrorKind, ConnectorOperation, ConnectorOperationError
from onestep_kafka.resilience import as_kafka_connector_operation_error, classify_kafka_error


def test_kafka_plugin_normalizes_common_connection_errors() -> None:
    connection_error = ConnectionError("broker unavailable")

    assert classify_kafka_error(connection_error) is ConnectorErrorKind.DISCONNECTED
    normalized = as_kafka_connector_operation_error(
        operation=ConnectorOperation.OPEN,
        exc=connection_error,
        source_name="orders",
        retry_delay_s=1.0,
    )

    assert isinstance(normalized, ConnectorOperationError)
    assert normalized.backend == "kafka"
    assert normalized.operation is ConnectorOperation.OPEN
    assert normalized.kind is ConnectorErrorKind.DISCONNECTED
    assert normalized.source_name == "orders"
    assert normalized.retry_delay_s == 1.0
    assert normalized.cause is connection_error
```

- [ ] **Step 2: Run resilience test to verify it fails**

Run:

```bash
uv run --project plugins/onestep-kafka python -m pytest -q plugins/onestep-kafka/tests/test_kafka_plugin.py::test_kafka_plugin_normalizes_common_connection_errors
```

Expected: FAIL because `as_kafka_connector_operation_error` is not implemented.

- [ ] **Step 3: Implement resilience helpers**

Replace `plugins/onestep-kafka/src/onestep_kafka/resilience.py` with:

```python
from __future__ import annotations

from onestep.resilience import ConnectorErrorKind, ConnectorOperation, ConnectorOperationError

try:  # pragma: no cover - optional dependency
    import aiokafka.errors as aiokafka_errors
except ImportError:  # pragma: no cover - optional dependency
    aiokafka_errors = None


def classify_kafka_error(exc: BaseException) -> ConnectorErrorKind | None:
    if isinstance(exc, (ConnectionError, OSError, TimeoutError)):
        return ConnectorErrorKind.DISCONNECTED
    message = str(exc).lower()
    if "connection" in message and "closed" in message:
        return ConnectorErrorKind.DISCONNECTED
    if "timed out" in message or "timeout" in message:
        return ConnectorErrorKind.TRANSIENT
    if aiokafka_errors is None:
        return None

    kafka_error = getattr(aiokafka_errors, "KafkaError", None)
    if isinstance(kafka_error, type) and isinstance(exc, kafka_error):
        retriable = getattr(exc, "retriable", None)
        if callable(retriable) and retriable():
            return ConnectorErrorKind.TRANSIENT
        invalid_config = getattr(aiokafka_errors, "InvalidConfigurationError", None)
        if isinstance(invalid_config, type) and isinstance(exc, invalid_config):
            return ConnectorErrorKind.MISCONFIGURED
        return ConnectorErrorKind.PERMANENT
    return None


def as_kafka_connector_operation_error(
    *,
    operation: ConnectorOperation,
    exc: BaseException,
    source_name: str | None = None,
    retry_delay_s: float | None = None,
) -> ConnectorOperationError | None:
    kind = classify_kafka_error(exc)
    if kind is None:
        return None
    return ConnectorOperationError(
        backend="kafka",
        operation=operation,
        kind=kind,
        source_name=source_name,
        retry_delay_s=retry_delay_s,
        cause=exc,
    )
```

- [ ] **Step 4: Run plugin tests**

Run:

```bash
uv run --project plugins/onestep-kafka python -m pytest -q plugins/onestep-kafka/tests/test_kafka_plugin.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add plugins/onestep-kafka/src/onestep_kafka/resilience.py plugins/onestep-kafka/tests/test_kafka_plugin.py
git commit -m "feat(kafka): normalize connector errors"
```

### Task 6: Runtime Stop Contract

**Files:**
- Create: `plugins/onestep-kafka/tests/test_kafka_runtime_contract.py`
- Modify: `plugins/onestep-kafka/src/onestep_kafka/connector.py` only if the test exposes a real defect.

- [ ] **Step 1: Write runtime shutdown contract test**

Create `plugins/onestep-kafka/tests/test_kafka_runtime_contract.py`:

```python
from __future__ import annotations

import asyncio

from onestep import OneStepApp
from onestep_kafka import KafkaConnector

from .test_kafka_connector import FakeDriver, FakeRecord, FakeTopicPartition


def test_kafka_topic_fetch_is_not_cancel_safe() -> None:
    topic = KafkaConnector("localhost:9092", driver=FakeDriver()).topic("orders", group_id="workers")

    assert topic.fetch_is_cancel_safe is False


def test_shutdown_releases_fetched_unstarted_kafka_delivery() -> None:
    async def scenario() -> None:
        driver = FakeDriver()
        topic = KafkaConnector("localhost:9092", driver=driver).topic(
            "orders",
            group_id="workers",
            poll_timeout_ms=0,
        )
        consumer = await topic._open_consumer()
        tp = FakeTopicPartition("orders", 0)
        consumer.records[tp] = [FakeRecord("orders", 0, 10, b'{"id": 10}')]

        app = OneStepApp("kafka-shutdown", shutdown_timeout_s=1.0)
        handled: list[dict[str, int]] = []

        @app.task(source=topic, concurrency=1)
        async def consume(ctx, item):
            handled.append(item)

        serve_task = asyncio.create_task(app.serve())
        for _ in range(100):
            if not consumer.records[tp]:
                break
            await asyncio.sleep(0.001)
        app.request_shutdown()
        await asyncio.wait_for(serve_task, timeout=1.0)

        assert handled == []
        assert consumer.commits == []
        assert consumer.seeks == [(tp, 10)]

    asyncio.run(scenario())
```

- [ ] **Step 2: Run runtime contract test**

Run:

```bash
uv run --project plugins/onestep-kafka python -m pytest -q plugins/onestep-kafka/tests/test_kafka_runtime_contract.py
```

Expected: PASS.

- [ ] **Step 3: Run all non-integration Kafka plugin tests**

Run:

```bash
uv run --project plugins/onestep-kafka python -m pytest -q plugins/onestep-kafka/tests -m "not integration"
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add plugins/onestep-kafka/tests/test_kafka_runtime_contract.py plugins/onestep-kafka/src/onestep_kafka/connector.py
git commit -m "test(kafka): cover runtime stop contract"
```

### Task 7: CI, Reliability, And Integration Infrastructure

**Files:**
- Create: `.github/workflows/plugin-kafka.yml`
- Modify: `scripts/run-reliability-checks.sh`
- Modify: `docker-compose.integration.yml`
- Modify: `scripts/setup-integration-env.sh`
- Modify: `scripts/run-integration-tests.sh`
- Create: `plugins/onestep-kafka/tests/integration/test_kafka_live.py`

- [ ] **Step 1: Add Kafka plugin workflow**

Create `.github/workflows/plugin-kafka.yml`:

```yaml
name: Kafka Plugin

on:
  push:
    branches:
      - main
    paths:
      - ".github/workflows/plugin-kafka.yml"
      - "plugins/onestep-kafka/**"
      - "src/**"
      - "pyproject.toml"
      - "uv.lock"
  pull_request:
    paths:
      - ".github/workflows/plugin-kafka.yml"
      - "plugins/onestep-kafka/**"
      - "src/**"
      - "pyproject.toml"
      - "uv.lock"
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: kafka-plugin-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

env:
  PLUGIN_PATH: plugins/onestep-kafka

jobs:
  test:
    name: Test plugin (py${{ matrix.python-version }})
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.10", "3.11", "3.12"]

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      - name: Set up uv
        uses: astral-sh/setup-uv@v5

      - name: Install plugin test dependencies
        run: uv sync --project "$PLUGIN_PATH" --extra test --frozen

      - name: Run plugin tests
        run: uv run --project "$PLUGIN_PATH" python -m pytest -q "$PLUGIN_PATH/tests" -m "not integration"

      - name: Build plugin distribution
        run: uv build "$PLUGIN_PATH" --out-dir dist/plugin --sdist --wheel --clear

      - name: Check plugin metadata
        run: uvx twine check dist/plugin/*
```

- [ ] **Step 2: Update reliability script for Python-aware Kafka checks**

In `scripts/run-reliability-checks.sh`, keep the existing `plugin_paths` list and add this block after the loop:

```bash
if "$PYTHON_BIN" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
then
  if command -v uv >/dev/null 2>&1; then
    echo "==> plugins/onestep-kafka/tests"
    uv run --project "$ROOT_DIR/plugins/onestep-kafka" python -m pytest -q -m "not integration" "$ROOT_DIR/plugins/onestep-kafka/tests" "$@"
  else
    echo "==> plugins/onestep-kafka/tests (uv not found, skipped)"
  fi
else
  echo "==> plugins/onestep-kafka/tests (requires Python >=3.10, skipped)"
fi
```

- [ ] **Step 3: Add Redpanda service to integration compose**

Append this service to `docker-compose.integration.yml`:

```yaml
  redpanda:
    image: docker.redpanda.com/redpandadata/redpanda:v24.3.7
    container_name: onestep-redpanda
    command:
      - redpanda
      - start
      - --overprovisioned
      - --smp
      - "1"
      - --memory
      - 512M
      - --reserve-memory
      - 0M
      - --node-id
      - "0"
      - --check=false
      - --kafka-addr
      - PLAINTEXT://0.0.0.0:9092
      - --advertise-kafka-addr
      - PLAINTEXT://127.0.0.1:9092
    ports:
      - "9092:9092"
    healthcheck:
      test: ["CMD", "rpk", "cluster", "health"]
      interval: 5s
      timeout: 5s
      retries: 30
```

- [ ] **Step 4: Add Kafka wait and env exports**

In `scripts/setup-integration-env.sh`, add:

```bash
KAFKA_BOOTSTRAP_SERVERS="${ONESTEP_KAFKA_BOOTSTRAP_SERVERS:-127.0.0.1:9092}"
KAFKA_TOPIC_PREFIX="${ONESTEP_KAFKA_TOPIC_PREFIX:-onestep.integration}"
```

Add this function:

```bash
wait_for_kafka() {
  KAFKA_BOOTSTRAP_SERVERS="$KAFKA_BOOTSTRAP_SERVERS" "$PYTHON_BIN" - <<'PY'
import asyncio
import os
import time

from aiokafka import AIOKafkaProducer

bootstrap_servers = os.environ["KAFKA_BOOTSTRAP_SERVERS"]
last_error = None

async def check():
    producer = AIOKafkaProducer(bootstrap_servers=bootstrap_servers)
    await producer.start()
    await producer.stop()

for _ in range(60):
    try:
        asyncio.run(check())
        raise SystemExit(0)
    except Exception as exc:
        last_error = exc
        time.sleep(2)
raise SystemExit(f"Timed out waiting for Kafka: {last_error}")
PY
}
```

Call `wait_for_kafka` after `wait_for_redis`.

Add these exports to the final heredoc:

```bash
export ONESTEP_KAFKA_BOOTSTRAP_SERVERS="$KAFKA_BOOTSTRAP_SERVERS"
export ONESTEP_KAFKA_TOPIC_PREFIX="$KAFKA_TOPIC_PREFIX"
```

- [ ] **Step 5: Include Kafka integration tests**

In `scripts/run-integration-tests.sh`, add this path to the `for path in ...` list:

```bash
  plugins/onestep-kafka/tests/integration \
```

- [ ] **Step 6: Write live Kafka integration test**

Create `plugins/onestep-kafka/tests/integration/test_kafka_live.py`:

```python
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from onestep.envelope import Envelope
from onestep_kafka import KafkaConnector

pytestmark = pytest.mark.integration


def test_kafka_live_send_fetch_ack_and_no_redelivery() -> None:
    async def scenario() -> None:
        bootstrap_servers = os.environ["ONESTEP_KAFKA_BOOTSTRAP_SERVERS"]
        topic_name = f"{os.environ.get('ONESTEP_KAFKA_TOPIC_PREFIX', 'onestep.integration')}.{uuid.uuid4().hex}"
        group_id = f"onestep-{uuid.uuid4().hex}"

        connector = KafkaConnector(bootstrap_servers)
        sink = connector.topic(topic_name)
        source = connector.topic(
            topic_name,
            group_id=group_id,
            consumer_options={"auto_offset_reset": "earliest"},
            poll_timeout_ms=500,
        )

        try:
            await sink.send(Envelope(body={"id": 1}, meta={"trace": "live"}))
            deliveries = []
            for _ in range(20):
                deliveries = await source.fetch(1)
                if deliveries:
                    break
                await asyncio.sleep(0.1)
            assert len(deliveries) == 1
            assert deliveries[0].payload == {"id": 1}
            assert deliveries[0].envelope.meta["kafka"]["topic"] == topic_name
            await deliveries[0].start_processing()
            await deliveries[0].ack()

            redelivered = await source.fetch(1)
            assert redelivered == []
        finally:
            await source.close()
            await sink.close()

    asyncio.run(scenario())
```

- [ ] **Step 7: Run focused checks**

Run:

```bash
uv run --project plugins/onestep-kafka python -m pytest -q plugins/onestep-kafka/tests -m "not integration"
uv build plugins/onestep-kafka --out-dir dist/plugin --sdist --wheel --clear
uvx twine check dist/plugin/*
```

Expected: all commands PASS.

- [ ] **Step 8: Commit**

```bash
git add .github/workflows/plugin-kafka.yml docker-compose.integration.yml scripts/setup-integration-env.sh scripts/run-integration-tests.sh scripts/run-reliability-checks.sh plugins/onestep-kafka/tests/integration/test_kafka_live.py
git commit -m "ci(kafka): add plugin validation"
```

### Task 8: Skill Guidance And Final Validation

**Files:**
- Modify: `skills/onestep/references/connectors.md`
- Modify: `plugins/onestep-kafka/README.md`

- [ ] **Step 1: Add Kafka connector guidance to the onestep skill**

In `skills/onestep/references/connectors.md`, add this section after SQS:

````markdown
## Kafka

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

Kafka uses consumer groups with auto commit disabled. Offsets are committed
only after processing reaches `ack()` or terminal `fail()`. Treat it as
at-least-once: output sinks may receive duplicates if the worker exits after
output send and before the Kafka commit succeeds.

`group_id` is required when a `kafka_topic` is used as a source. Sink-only
topics may omit it.
````

- [ ] **Step 2: Run non-integration plugin tests**

Run:

```bash
uv run --project plugins/onestep-kafka python -m pytest -q plugins/onestep-kafka/tests -m "not integration"
```

Expected: PASS.

- [ ] **Step 3: Run core non-integration tests**

Run:

```bash
uv run pytest -q -m "not integration"
```

Expected: PASS.

- [ ] **Step 4: Run reliability checks**

Run:

```bash
./scripts/run-reliability-checks.sh
```

Expected: PASS. On Python 3.9, Kafka plugin tests are skipped with a clear message. On Python >=3.10 with uv available, Kafka plugin tests are included.

- [ ] **Step 5: Run live integration checks when Docker is available**

Run:

```bash
./scripts/run-integration-tests.sh
```

Expected: PASS, including `plugins/onestep-kafka/tests/integration/test_kafka_live.py`.

- [ ] **Step 6: Inspect final diff**

Run:

```bash
git status --short
git diff --stat
git diff -- pyproject.toml plugins/onestep-kafka/src/onestep_kafka/connector.py
```

Expected: only Kafka plugin, metadata, CI, integration, and skill guidance files changed.

- [ ] **Step 7: Commit final docs and skill guidance**

```bash
git add skills/onestep/references/connectors.md plugins/onestep-kafka/README.md
git commit -m "docs(kafka): document connector usage"
```

## Release Notes For The Implementer

- Do not publish during implementation unless explicitly asked.
- Do not add an upper bound on `onestep` or `aiokafka`.
- If publishing later, release `onestep-kafka` as a plugin package. If root metadata changes are released as part of core, follow the onestep release rules for core version, changelog, lockfile, and tag.
- No control-plane coordination is needed because this plugin adds runtime resource types without changing reporter payloads or remote-control protocol.
