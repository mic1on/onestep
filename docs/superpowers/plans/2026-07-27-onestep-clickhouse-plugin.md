# ClickHouse Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an independently testable `onestep-clickhouse` plugin that inserts explicit mapping-or-sequence batches into existing ClickHouse tables and returns only after every chunk is acknowledged.

**Architecture:** Keep all work under `plugins/onestep-clickhouse` and wrap the vendor-supported `clickhouse-connect` async client behind a lazily owned `ClickHouseConnector`. Normalize configured or inferred columns before the first network call, submit chunks sequentially through `AsyncClient.insert`, reject fire-and-forget async insert settings, and surface later-chunk failures as `UNCERTAIN`.

**Tech Stack:** Python `>=3.9`, `onestep>=1.7.1`, `clickhouse-connect>=0.8`, Hatch, pytest, pytest-asyncio, ClickHouse LTS/current.

---

## File Responsibility Map

- Create `plugins/onestep-clickhouse/pyproject.toml`: independent metadata, dependency floor, Hatch build, plugin-local onestep source, and entry point.
- Create `plugins/onestep-clickhouse/README.md`: install, Python/YAML surfaces, column rules, direct backpressure, async-insert acknowledgement, concurrency tuning, and duplicate guidance.
- Create `plugins/onestep-clickhouse/src/onestep_clickhouse/__init__.py`: public API, version, and registration alias.
- Create `plugins/onestep-clickhouse/src/onestep_clickhouse/connector.py`: owned async-client lifecycle, payload normalization, chunked table sink, and payload cause.
- Create `plugins/onestep-clickhouse/src/onestep_clickhouse/resources.py`: `clickhouse` and `clickhouse_table_sink` catalogs, builders, and strict validation.
- Create `plugins/onestep-clickhouse/src/onestep_clickhouse/resilience.py`: ClickHouse exception/code classification and redacted `ConnectorOperationError` conversion.
- Create `plugins/onestep-clickhouse/tests/test_clickhouse_connector.py`: client ownership, columns, payload rejection, chunks, acknowledgement, partial commits, and close.
- Create `plugins/onestep-clickhouse/tests/test_clickhouse_plugin.py`: installed entry point, public exports, catalog, and strict YAML.
- Create `plugins/onestep-clickhouse/tests/test_clickhouse_resilience.py`: exception/code classification.
- Create `plugins/onestep-clickhouse/tests/integration/test_clickhouse_live.py`: existing-table live inserts and immediate visibility.

This plan never edits root metadata, lockfiles, scripts, workflows, Compose,
documentation, changelog, or worker-image files. Shared ownership begins only in
`2026-07-27-onestep-database-plugin-integration.md`.

### Task 1: Create The Independent Package And Public API

**Files:**
- Create: `plugins/onestep-clickhouse/pyproject.toml`
- Create: `plugins/onestep-clickhouse/README.md`
- Create: `plugins/onestep-clickhouse/src/onestep_clickhouse/__init__.py`
- Create: `plugins/onestep-clickhouse/src/onestep_clickhouse/connector.py`
- Create: `plugins/onestep-clickhouse/src/onestep_clickhouse/resources.py`
- Create: `plugins/onestep-clickhouse/src/onestep_clickhouse/resilience.py`
- Create: `plugins/onestep-clickhouse/tests/test_clickhouse_plugin.py`

- [ ] **Step 1: Write a failing package-surface test**

Create `test_clickhouse_plugin.py`:

```python
from __future__ import annotations

from importlib import metadata as importlib_metadata

from onestep_clickhouse import (
    ClickHouseConnector,
    ClickHousePayloadError,
    ClickHouseTableSink,
    register,
    register_resources,
)


def test_public_surface_and_entry_point() -> None:
    assert register is register_resources
    assert ClickHouseConnector.__name__ == "ClickHouseConnector"
    assert ClickHouseTableSink.__name__ == "ClickHouseTableSink"
    assert ClickHousePayloadError.__name__ == "ClickHousePayloadError"
    entry_points = importlib_metadata.entry_points()
    selected = entry_points.select(group="onestep.resources") if hasattr(entry_points, "select") else entry_points.get("onestep.resources", ())
    assert any(item.name == "clickhouse" and item.value == "onestep_clickhouse:register" for item in selected)
```

- [ ] **Step 2: Run the test and verify the package is missing**

Run:

```bash
uv run --project plugins/onestep-clickhouse --extra test python -m pytest -q plugins/onestep-clickhouse/tests/test_clickhouse_plugin.py
```

Expected: FAIL because the project/package does not exist.

- [ ] **Step 3: Create exact package metadata**

Create `pyproject.toml`:

```toml
[project]
name = "onestep-clickhouse"
version = "0.1.0"
description = "ClickHouse connector plugin for onestep."
readme = "README.md"
requires-python = ">=3.9"
license = { text = "MIT" }
dependencies = ["onestep>=1.7.1", "clickhouse-connect>=0.8"]

[project.optional-dependencies]
test = ["pytest>=8.0.0", "pytest-asyncio>=0.23.0"]
dev = ["pytest>=8.0.0", "pytest-asyncio>=0.23.0"]

[project.entry-points."onestep.resources"]
clickhouse = "onestep_clickhouse:register"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/onestep_clickhouse"]

[tool.uv.sources]
onestep = { path = "../..", editable = true }

[tool.pytest.ini_options]
asyncio_mode = "auto"
markers = ["integration: live external service tests"]
```

Create the package README required by that metadata at
`plugins/onestep-clickhouse/README.md`:

```markdown
# onestep-clickhouse

ClickHouse connector plugin for onestep.
```

- [ ] **Step 4: Add initial public modules**

Create `connector.py`:

```python
from __future__ import annotations

from typing import Any

from onestep import Sink


class ClickHousePayloadError(ValueError):
    pass


class ClickHouseConnector:
    def __init__(self, dsn: str, *, client_options: dict[str, Any] | None = None, client: Any | None = None) -> None:
        self.dsn = dsn
        self.client_options = dict(client_options or {})
        self._client = client

    def table_sink(self, *, table: str, **options: Any) -> "ClickHouseTableSink":
        return ClickHouseTableSink(connector=self, table=table, **options)


class ClickHouseTableSink(Sink):
    def __init__(self, *, connector: ClickHouseConnector, table: str, **options: Any) -> None:
        super().__init__(f"clickhouse.table:{table}")
        self.connector = connector
        self.table = table
        self.options = dict(options)

    async def send(self, envelope) -> None:
        raise NotImplementedError("table insert is introduced by Task 3")
```

Create `resources.py`:

```python
from __future__ import annotations

from onestep import ResourceRegistry


def register_resources(registry: ResourceRegistry) -> None:
    return None
```

Create `resilience.py`:

```python
from __future__ import annotations

from onestep import ConnectorErrorKind


def classify_clickhouse_error(exc: BaseException) -> ConnectorErrorKind | None:
    if isinstance(exc, (ConnectionError, OSError)):
        return ConnectorErrorKind.DISCONNECTED
    return None
```

Create `__init__.py`:

```python
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _package_version

from .connector import ClickHouseConnector, ClickHousePayloadError, ClickHouseTableSink
from .resources import register_resources

try:
    __version__ = _package_version("onestep-clickhouse")
except PackageNotFoundError:
    __version__ = "dev"

register = register_resources
__all__ = ["ClickHouseConnector", "ClickHousePayloadError", "ClickHouseTableSink", "__version__", "register", "register_resources"]
```

- [ ] **Step 5: Re-run the package test**

Run the Step 2 command.

Expected: `1 passed`.

- [ ] **Step 6: Commit package foundation**

```bash
git add plugins/onestep-clickhouse
git commit -m "feat(clickhouse): add plugin package foundation"
```

### Task 2: Normalize Rows And Enforce Column Contracts

**Files:**
- Modify: `plugins/onestep-clickhouse/src/onestep_clickhouse/connector.py`
- Create: `plugins/onestep-clickhouse/tests/test_clickhouse_connector.py`

- [ ] **Step 1: Write failing normalization tests**

Create `test_clickhouse_connector.py`:

```python
from __future__ import annotations

import pytest

from onestep_clickhouse import ClickHouseConnector, ClickHousePayloadError


def test_configured_columns_produce_ordered_rows() -> None:
    sink = ClickHouseConnector("http://clickhouse:8123/default").table_sink(table="events", columns=("id", "kind"))
    columns, rows = sink._normalize([{"kind": "a", "id": 1}, {"id": 2, "kind": "b"}])
    assert columns == ("id", "kind")
    assert rows == [[1, "a"], [2, "b"]]


def test_first_mapping_infers_deterministic_column_order() -> None:
    sink = ClickHouseConnector("http://clickhouse:8123/default").table_sink(table="events")
    columns, rows = sink._normalize([{"id": 1, "kind": "a"}, {"kind": "b", "id": 2}])
    assert columns == ("id", "kind")
    assert rows == [[1, "a"], [2, "b"]]


@pytest.mark.parametrize("body", [[], "text", [1], [{"id": 1}, {"id": 2, "extra": True}], [{"id": 1}, {"other": 2}]])
def test_invalid_payloads_fail_before_insert(body) -> None:
    sink = ClickHouseConnector("http://clickhouse:8123/default").table_sink(table="events", columns=("id",))
    with pytest.raises(ClickHousePayloadError):
        sink._normalize(body)
```

- [ ] **Step 2: Run tests and verify `_normalize` is missing**

Run:

```bash
uv run --project plugins/onestep-clickhouse --extra test python -m pytest -q plugins/onestep-clickhouse/tests/test_clickhouse_connector.py
```

Expected: FAIL with missing `_normalize`.

- [ ] **Step 3: Replace the table sink constructor and add complete normalization**

Add `Mapping` and `Sequence` imports and replace the sink constructor with:

```python
from collections.abc import Mapping, Sequence


class ClickHouseTableSink(Sink):
    def __init__(
        self,
        *,
        connector: ClickHouseConnector,
        table: str,
        columns: Sequence[str] | None = None,
        batch_size: int = 1000,
        settings: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(f"clickhouse.table:{table}")
        if not table:
            raise ValueError("table must not be empty")
        if columns is not None and (not columns or len(set(columns)) != len(columns)):
            raise ValueError("columns must be a non-empty unique sequence")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.connector = connector
        self.table = table
        self.columns = tuple(columns) if columns is not None else None
        self.batch_size = batch_size
        self.settings = dict(settings or {})
        if self.settings.get("async_insert") in {1, True, "1"} and self.settings.get("wait_for_async_insert") not in {1, True, "1"}:
            raise ValueError("async_insert requires wait_for_async_insert=1")

    def _documents(self, body: Any) -> list[dict[str, Any]]:
        if isinstance(body, Mapping):
            return [dict(body)]
        if not isinstance(body, Sequence) or isinstance(body, (str, bytes, bytearray)):
            raise ClickHousePayloadError("payload must be a mapping or non-empty sequence of mappings")
        if not body:
            raise ClickHousePayloadError("payload sequence must not be empty")
        if any(not isinstance(item, Mapping) for item in body):
            raise ClickHousePayloadError("every payload item must be a mapping")
        return [dict(item) for item in body]

    def _normalize(self, body: Any) -> tuple[tuple[str, ...], list[list[Any]]]:
        documents = self._documents(body)
        columns = self.columns or tuple(documents[0].keys())
        expected = set(columns)
        rows: list[list[Any]] = []
        for index, document in enumerate(documents):
            actual = set(document)
            if actual != expected:
                missing = sorted(expected - actual)
                extra = sorted(actual - expected)
                raise ClickHousePayloadError(f"row {index} column mismatch: missing={missing}, extra={extra}")
            rows.append([document[column] for column in columns])
        return columns, rows
```

- [ ] **Step 4: Run normalization tests**

Run the Step 2 command.

Expected: `7 passed`.

- [ ] **Step 5: Commit row normalization**

```bash
git add plugins/onestep-clickhouse/src/onestep_clickhouse/connector.py plugins/onestep-clickhouse/tests/test_clickhouse_connector.py
git commit -m "feat(clickhouse): normalize explicit row batches"
```

### Task 3: Implement Owned Async Client And Acknowledged Chunk Inserts

**Files:**
- Modify: `plugins/onestep-clickhouse/src/onestep_clickhouse/connector.py`
- Modify: `plugins/onestep-clickhouse/tests/test_clickhouse_connector.py`

- [ ] **Step 1: Add a complete fake client and failing send tests**

Append:

```python
from onestep import ConnectorErrorKind, ConnectorOperationError, Envelope


class FakeAsyncClient:
    def __init__(self, *, fail_call: int | None = None) -> None:
        self.calls: list[dict] = []
        self.fail_call = fail_call
        self.closed = False

    async def insert(self, table, rows, *, column_names, settings):
        self.calls.append({"table": table, "rows": rows, "column_names": column_names, "settings": settings})
        if self.fail_call == len(self.calls):
            raise TimeoutError("response timed out after submission")
        return object()

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_send_awaits_each_chunk_in_order() -> None:
    client = FakeAsyncClient()
    connector = ClickHouseConnector("http://clickhouse:8123/default", client=client)
    sink = connector.table_sink(table="events", columns=("id",), batch_size=2)
    await sink.send(Envelope(body=[{"id": 1}, {"id": 2}, {"id": 3}]))
    assert [call["rows"] for call in client.calls] == [[[1], [2]], [[3]]]


@pytest.mark.asyncio
async def test_later_chunk_timeout_is_uncertain_and_stops() -> None:
    client = FakeAsyncClient(fail_call=2)
    sink = ClickHouseConnector("http://clickhouse:8123/default", client=client).table_sink(table="events", columns=("id",), batch_size=1)
    with pytest.raises(ConnectorOperationError) as captured:
        await sink.send(Envelope(body=[{"id": 1}, {"id": 2}, {"id": 3}]))
    assert captured.value.kind is ConnectorErrorKind.UNCERTAIN
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_injected_client_is_not_closed() -> None:
    client = FakeAsyncClient()
    connector = ClickHouseConnector("http://clickhouse:8123/default", client=client)
    await connector.close()
    await connector.close()
    assert client.closed is False


@pytest.mark.asyncio
async def test_owned_client_is_lazy_and_closes_once(monkeypatch) -> None:
    import clickhouse_connect

    client = FakeAsyncClient()
    close_calls = 0

    async def close_once():
        nonlocal close_calls
        close_calls += 1
        client.closed = True

    client.close = close_once
    factory_calls = []

    async def build_client(**options):
        factory_calls.append(options)
        return client

    monkeypatch.setattr(clickhouse_connect, "get_async_client", build_client)
    connector = ClickHouseConnector("http://clickhouse:8123/default")
    assert factory_calls == []
    await connector.table_sink(table="events", columns=("id",)).send(Envelope(body={"id": 1}))
    assert factory_calls == [{"dsn": "http://clickhouse:8123/default"}]
    await connector.close()
    await connector.close()
    assert client.closed is True
    assert close_calls == 1
```

- [ ] **Step 2: Run send tests and verify failure**

Run:

```bash
uv run --project plugins/onestep-clickhouse --extra test python -m pytest -q plugins/onestep-clickhouse/tests/test_clickhouse_connector.py -k "send or timeout or injected or owned"
```

Expected: FAIL because `send` is not implemented and `close` is missing.

- [ ] **Step 3: Replace the connector with the complete client-ownership contract**

```python
class ClickHouseConnector:
    def __init__(self, dsn: str, *, client_options: Mapping[str, Any] | None = None, client: Any | None = None) -> None:
        if not dsn:
            raise ValueError("dsn must not be empty")
        self.dsn = dsn
        self.client_options = dict(client_options or {})
        self._client = client
        self._owns_client = client is None
        self._closed = False

    async def _get_client(self):
        if self._client is None:
            import clickhouse_connect

            self._client = await clickhouse_connect.get_async_client(dsn=self.dsn, **self.client_options)
        return self._client

    def table_sink(self, *, table: str, **options: Any) -> "ClickHouseTableSink":
        return ClickHouseTableSink(connector=self, table=table, **options)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_client and self._client is not None:
            result = self._client.close()
            if hasattr(result, "__await__"):
                await result
```

- [ ] **Step 4: Implement sequential acknowledged sends**

Add imports for `ConnectorErrorKind`, `ConnectorOperation`,
`ConnectorOperationError`, and `Envelope`, then add:

```python
    async def send(self, envelope: Envelope) -> None:
        try:
            columns, rows = self._normalize(envelope.body)
        except ClickHousePayloadError as exc:
            raise ConnectorOperationError(backend="clickhouse", operation=ConnectorOperation.SEND, kind=ConnectorErrorKind.PERMANENT, source_name=self.name, cause=exc) from exc
        client = await self.connector._get_client()
        committed = 0
        try:
            for start in range(0, len(rows), self.batch_size):
                chunk = rows[start : start + self.batch_size]
                await client.insert(self.table, chunk, column_names=columns, settings=self.settings)
                committed += 1
        except Exception as exc:
            from .resilience import classify_clickhouse_error

            kind = classify_clickhouse_error(exc)
            if kind is None:
                raise
            if committed:
                kind = ConnectorErrorKind.UNCERTAIN
            raise ConnectorOperationError(backend="clickhouse", operation=ConnectorOperation.SEND, kind=kind, source_name=self.name, cause=exc) from exc
```

- [ ] **Step 5: Run connector tests and verify pass**

Run the Step 2 command without `-k`.

Expected: all connector tests pass.

- [ ] **Step 6: Commit client and acknowledged sends**

```bash
git add plugins/onestep-clickhouse/src/onestep_clickhouse/connector.py plugins/onestep-clickhouse/tests/test_clickhouse_connector.py
git commit -m "feat(clickhouse): add acknowledged async table inserts"
```

### Task 4: Classify ClickHouse Failures Without Leaking Rows

**Files:**
- Modify: `plugins/onestep-clickhouse/src/onestep_clickhouse/resilience.py`
- Create: `plugins/onestep-clickhouse/tests/test_clickhouse_resilience.py`

- [ ] **Step 1: Write failing classification tests**

Create `test_clickhouse_resilience.py`:

```python
from __future__ import annotations

from onestep import ConnectorErrorKind
from onestep_clickhouse.resilience import classify_clickhouse_error, redacted_clickhouse_cause


class ServerError(Exception):
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        super().__init__(message)


def test_clickhouse_error_code_classes() -> None:
    assert classify_clickhouse_error(ServerError(516, "authentication failed")) is ConnectorErrorKind.MISCONFIGURED
    assert classify_clickhouse_error(ServerError(60, "unknown table")) is ConnectorErrorKind.MISCONFIGURED
    assert classify_clickhouse_error(ServerError(241, "memory limit")) is ConnectorErrorKind.THROTTLED
    assert classify_clickhouse_error(ServerError(53, "type mismatch")) is ConnectorErrorKind.PERMANENT
    assert classify_clickhouse_error(TimeoutError("receive timeout")) is ConnectorErrorKind.UNCERTAIN


def test_redacted_cause_preserves_code_but_caps_message() -> None:
    cause = redacted_clickhouse_cause(ServerError(53, "x" * 1000))
    assert cause.code == 53
    assert len(str(cause)) < 600
```

- [ ] **Step 2: Run and verify missing redaction/classification fails**

Run:

```bash
uv run --project plugins/onestep-clickhouse --extra test python -m pytest -q plugins/onestep-clickhouse/tests/test_clickhouse_resilience.py
```

Expected: FAIL because `redacted_clickhouse_cause` is missing and code classes are not mapped.

- [ ] **Step 3: Implement complete classification and redacted cause**

Replace `resilience.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass

from onestep import ConnectorErrorKind


@dataclass(frozen=True)
class ClickHouseErrorCause(Exception):
    code: int | None
    message: str

    def __str__(self) -> str:
        return f"ClickHouse error code={self.code}: {self.message}"


def redacted_clickhouse_cause(exc: BaseException) -> ClickHouseErrorCause:
    return ClickHouseErrorCause(getattr(exc, "code", None), str(exc)[:500])


def classify_clickhouse_error(exc: BaseException) -> ConnectorErrorKind | None:
    if isinstance(exc, TimeoutError):
        return ConnectorErrorKind.UNCERTAIN
    if isinstance(exc, (ConnectionError, OSError)):
        return ConnectorErrorKind.DISCONNECTED
    code = getattr(exc, "code", None)
    if code in {202, 203, 209, 210}:
        return ConnectorErrorKind.DISCONNECTED
    if code in {159, 164, 241, 252}:
        return ConnectorErrorKind.THROTTLED
    if code in {60, 62, 81, 516}:
        return ConnectorErrorKind.MISCONFIGURED
    if code in {6, 27, 53, 117, 386}:
        return ConnectorErrorKind.PERMANENT
    if code is not None:
        return ConnectorErrorKind.TRANSIENT
    return None
```

Replace `ClickHouseTableSink.send()` with the complete method below so
classification uses the original exception while the public error retains only
the redacted cause:

```python
    async def send(self, envelope: Envelope) -> None:
        try:
            columns, rows = self._normalize(envelope.body)
        except ClickHousePayloadError as exc:
            raise ConnectorOperationError(
                backend="clickhouse",
                operation=ConnectorOperation.SEND,
                kind=ConnectorErrorKind.PERMANENT,
                source_name=self.name,
                cause=exc,
            ) from exc
        client = await self.connector._get_client()
        committed = 0
        try:
            for start in range(0, len(rows), self.batch_size):
                chunk = rows[start : start + self.batch_size]
                await client.insert(
                    self.table,
                    chunk,
                    column_names=columns,
                    settings=self.settings,
                )
                committed += 1
        except Exception as exc:
            from .resilience import classify_clickhouse_error, redacted_clickhouse_cause

            kind = classify_clickhouse_error(exc)
            if kind is None:
                raise
            if committed:
                kind = ConnectorErrorKind.UNCERTAIN
            raise ConnectorOperationError(
                backend="clickhouse",
                operation=ConnectorOperation.SEND,
                kind=kind,
                source_name=self.name,
                cause=redacted_clickhouse_cause(exc),
            ) from exc
```

- [ ] **Step 4: Run resilience and connector tests**

Run:

```bash
uv run --project plugins/onestep-clickhouse --extra test python -m pytest -q plugins/onestep-clickhouse/tests/test_clickhouse_resilience.py plugins/onestep-clickhouse/tests/test_clickhouse_connector.py
```

Expected: all tests pass and no row content appears in raised messages.

- [ ] **Step 5: Commit resilience mapping**

```bash
git add plugins/onestep-clickhouse/src/onestep_clickhouse plugins/onestep-clickhouse/tests
git commit -m "feat(clickhouse): classify insert failures"
```

### Task 5: Register Strict YAML Resources

**Files:**
- Modify: `plugins/onestep-clickhouse/src/onestep_clickhouse/resources.py`
- Modify: `plugins/onestep-clickhouse/tests/test_clickhouse_plugin.py`

- [ ] **Step 1: Add failing catalog and strict YAML tests**

Append:

```python
import pytest

from onestep import ResourceRegistry, load_app_config


def _config(resources):
    return {"apiVersion": "onestep/v1alpha1", "kind": "App", "app": {"name": "clickhouse"}, "resources": resources, "tasks": []}


def test_catalog_and_strict_yaml_surface() -> None:
    registry = ResourceRegistry(); register(registry)
    catalog = {entry.type: entry for entry in registry.catalog_entries()}
    assert catalog["clickhouse"].roles == ("connector",)
    assert catalog["clickhouse_table_sink"].roles == ("sink",)
    assert catalog["clickhouse_table_sink"].topology_fields == ("table", "columns", "batch_size")

    app = load_app_config(_config({
        "db": {"type": "clickhouse", "dsn": "https://writer:secret@clickhouse:8443/analytics", "client_options": {"connect_timeout": 10}},
        "events": {"type": "clickhouse_table_sink", "connector": "db", "table": "events", "columns": ["id", "kind"], "batch_size": 100, "settings": {"async_insert": 1, "wait_for_async_insert": 1}},
    }), strict=True)
    assert isinstance(app.resources["db"], ClickHouseConnector)
    assert app.resources["events"].columns == ("id", "kind")


def test_strict_yaml_rejects_unacknowledged_async_insert() -> None:
    with pytest.raises(ValueError, match="wait_for_async_insert"):
        load_app_config(_config({"db": {"type": "clickhouse", "dsn": "http://clickhouse:8123/default"}, "sink": {"type": "clickhouse_table_sink", "connector": "db", "table": "events", "settings": {"async_insert": 1}}}), strict=True)
```

- [ ] **Step 2: Run tests and verify missing handlers fail**

Run:

```bash
uv run --project plugins/onestep-clickhouse --extra test python -m pytest -q plugins/onestep-clickhouse/tests/test_clickhouse_plugin.py -k "catalog or strict"
```

Expected: FAIL because no resource handlers are registered.

- [ ] **Step 3: Implement exact catalogs, builders, and validators**

Replace `resources.py` with:

```python
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from onestep import ResourceBuildContext, ResourceCatalogEntry, ResourceCatalogField, ResourceRegistry, ResourceSpecHandler, ResourceValidationContext

from .connector import ClickHouseConnector

CONNECTOR_FIELDS = frozenset({"type", "dsn", "client_options"})
SINK_FIELDS = frozenset({"type", "connector", "table", "columns", "batch_size", "settings"})
CONNECTOR_CATALOG = ResourceCatalogEntry(type="clickhouse", roles=("connector",), label="ClickHouse", fields=(ResourceCatalogField("dsn", "string", required=True, secret=True), ResourceCatalogField("client_options", "mapping", secret=True)))
SINK_CATALOG = ResourceCatalogEntry(type="clickhouse_table_sink", roles=("sink",), label="ClickHouse Table Sink", connector_types=("clickhouse",), fields=(ResourceCatalogField("connector", "ref", required=True), ResourceCatalogField("table", "string", required=True), ResourceCatalogField("columns", "string_list"), ResourceCatalogField("batch_size", "integer", default=1000), ResourceCatalogField("settings", "mapping")), topology_fields=("table", "columns", "batch_size"))


def _validate_connector(ctx: ResourceValidationContext, spec: Mapping[str, Any]) -> None:
    dsn = ctx.require_string(spec, "dsn")
    parsed = urlparse(dsn)
    if parsed.scheme not in {"clickhouse", "clickhouses", "http", "https"} or not parsed.netloc:
        raise ValueError(f"'{ctx.field}.dsn' must be a ClickHouse or HTTP(S) DSN")
    if "client_options" in spec and not isinstance(spec.get("client_options"), Mapping):
        raise TypeError(f"'{ctx.field}.client_options' must be a mapping")


def _validate_sink(ctx: ResourceValidationContext, spec: Mapping[str, Any]) -> None:
    ctx.require_string(spec, "connector"); ctx.require_string(spec, "table")
    if "columns" in spec:
        ctx.require_non_empty_string_list(spec, "columns", field=f"{ctx.field}.columns")
    ctx.validate_positive_integer(spec.get("batch_size"), field=f"{ctx.field}.batch_size")
    settings = spec.get("settings", {})
    if not isinstance(settings, Mapping):
        raise TypeError(f"'{ctx.field}.settings' must be a mapping")
    if settings.get("async_insert") in {1, True, "1"} and settings.get("wait_for_async_insert") not in {1, True, "1"}:
        raise ValueError(f"'{ctx.field}.settings.wait_for_async_insert' must be 1 when async_insert is enabled")


def _build_connector(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> ClickHouseConnector:
    return ClickHouseConnector(ctx.require_string(spec, "dsn"), client_options=ctx.mapping_value(spec.get("client_options"), field=f"{ctx.field}.client_options"))


def _build_sink(ctx: ResourceBuildContext, spec: Mapping[str, Any]):
    connector = ctx.resolve_dependency(spec, "connector")
    if not isinstance(connector, ClickHouseConnector):
        raise TypeError(f"resource {spec['connector']!r} is not a ClickHouseConnector")
    columns = tuple(ctx.string_list(spec.get("columns"), field=f"{ctx.field}.columns")) if spec.get("columns") is not None else None
    return connector.table_sink(table=ctx.require_string(spec, "table"), columns=columns, batch_size=spec.get("batch_size", 1000), settings=ctx.mapping_value(spec.get("settings"), field=f"{ctx.field}.settings"))


def register_resources(registry: ResourceRegistry) -> None:
    registry.register_resource_type(ResourceSpecHandler(type="clickhouse", catalog=CONNECTOR_CATALOG, allowed_fields=CONNECTOR_FIELDS, build=_build_connector, validate=_validate_connector))
    registry.register_resource_type(ResourceSpecHandler(type="clickhouse_table_sink", catalog=SINK_CATALOG, allowed_fields=SINK_FIELDS, build=_build_sink, validate=_validate_sink))
```

- [ ] **Step 4: Run complete unit suite**

Run:

```bash
uv run --project plugins/onestep-clickhouse --extra test python -m pytest -q plugins/onestep-clickhouse/tests -m "not integration"
```

Expected: all unit tests pass.

- [ ] **Step 5: Commit strict resources**

```bash
git add plugins/onestep-clickhouse/src/onestep_clickhouse/resources.py plugins/onestep-clickhouse/tests/test_clickhouse_plugin.py
git commit -m "feat(clickhouse): register strict YAML resources"
```

### Task 6: Add Runtime Ordering, Live Tests, Documentation, And Package Validation

**Files:**
- Modify: `plugins/onestep-clickhouse/tests/test_clickhouse_connector.py`
- Create: `plugins/onestep-clickhouse/tests/integration/test_clickhouse_live.py`
- Modify: `plugins/onestep-clickhouse/README.md`

- [ ] **Step 1: Add and run a runtime source-ack ordering test**

Use this complete `OneStepApp` test. The fake insert waits on an event, allowing the
test to prove the source delivery remains unacknowledged until ClickHouse confirms
the insert:

```python
from onestep import Delivery, OneStepApp, Source


class _AckRecordingDelivery(Delivery):
    def __init__(self, envelope: Envelope) -> None:
        super().__init__(envelope)
        self.acked = False

    async def ack(self) -> None:
        self.acked = True

    async def retry(self, *, delay_s: float | None = None) -> None:
        raise AssertionError("runtime ordering test must not retry")

    async def fail(self, exc: Exception | None = None) -> None:
        raise AssertionError(f"runtime ordering test failed: {exc}")


class _OneShotSource(Source):
    poll_interval_s = 0.01

    def __init__(self, delivery: _AckRecordingDelivery) -> None:
        super().__init__("one-shot")
        self.delivery = delivery
        self.sent = False

    async def fetch(self, limit: int) -> list[Delivery]:
        if self.sent:
            return []
        self.sent = True
        return [self.delivery]


@pytest.mark.asyncio
async def test_runtime_ack_follows_clickhouse_insert_acknowledgement() -> None:
    import asyncio

    entered = asyncio.Event(); release = asyncio.Event()

    class BlockingClient(FakeAsyncClient):
        async def insert(self, table, rows, *, column_names, settings):
            entered.set(); await release.wait()
            return await super().insert(table, rows, column_names=column_names, settings=settings)

    sink = ClickHouseConnector("http://clickhouse:8123/default", client=BlockingClient()).table_sink(table="events", columns=("id",))
    delivery = _AckRecordingDelivery(Envelope(body={"id": 1}))
    source = _OneShotSource(delivery)
    app = OneStepApp("clickhouse-runtime-order", shutdown_timeout_s=1.0)

    @app.task(source=source, emit=sink, concurrency=1)
    async def forward(ctx, item):
        ctx.app.request_shutdown()
        return item

    serving = asyncio.create_task(app.serve())
    await entered.wait()
    assert delivery.acked is False
    release.set()
    await asyncio.wait_for(serving, timeout=2.0)
    assert delivery.acked is True
```

Run:

```bash
uv run --project plugins/onestep-clickhouse --extra test python -m pytest -q plugins/onestep-clickhouse/tests/test_clickhouse_connector.py::test_runtime_ack_follows_clickhouse_insert_acknowledgement
```

Expected: PASS.

- [ ] **Step 2: Add a complete environment-driven live test**

Create `tests/integration/test_clickhouse_live.py`:

```python
from __future__ import annotations

import os
import uuid

import clickhouse_connect
import pytest

from onestep import Envelope
from onestep_clickhouse import ClickHouseConnector

DSN = os.getenv("ONESTEP_CLICKHOUSE_DSN")
pytestmark = [pytest.mark.integration, pytest.mark.skipif(not DSN, reason="ONESTEP_CLICKHOUSE_DSN is not configured")]


@pytest.mark.asyncio
async def test_live_mapping_sequence_chunking_and_visibility() -> None:
    table = f"onestep_{uuid.uuid4().hex}"
    admin = await clickhouse_connect.get_async_client(dsn=DSN)
    await admin.command(f"CREATE TABLE {table} (id UInt64, note Nullable(String)) ENGINE = MergeTree ORDER BY id")
    connector = ClickHouseConnector(DSN)
    sink = connector.table_sink(table=table, columns=("id", "note"), batch_size=2)
    try:
        await sink.send(Envelope(body={"id": 1, "note": None}))
        await sink.send(Envelope(body=[{"id": 2, "note": "two"}, {"id": 3, "note": "three"}, {"id": 4, "note": None}]))
        rows = await admin.query(f"SELECT id, note FROM {table} ORDER BY id")
        assert rows.result_rows == [(1, None), (2, "two"), (3, "three"), (4, None)]
    finally:
        await admin.command(f"DROP TABLE IF EXISTS {table}")
        await connector.close(); await admin.close()
```

- [ ] **Step 3: Write the README contract**

Replace the minimal `README.md` with install, the approved Python and strict YAML examples,
mapping-or-sequence input, exact/inferred column rules, `batch_size`, task
concurrency plus client-pool tuning, acknowledged async inserts, partial-commit
uncertainty, and a `ReplacingMergeTree` guidance example. Include this warning:

```markdown
## Delivery and duplicate semantics

The sink awaits every ClickHouse insert chunk and has no hidden queue. A crash after
ClickHouse acknowledges a chunk but before onestep acknowledges the source can
duplicate rows. A later chunk failure is reported as uncertain because earlier
chunks remain committed. Idempotency depends on table design; use stable event keys
and a dedup-aware engine such as `ReplacingMergeTree` when duplicates matter.
```

List deferred DDL, migrations, sources, streaming formats, Arrow/DataFrame APIs,
schema coercion, distributed routing, generated dedup tokens, and upserts.

- [ ] **Step 4: Run plugin-local tests and build artifacts**

```bash
uv run --project plugins/onestep-clickhouse --extra test python -m pytest -q plugins/onestep-clickhouse/tests -m "not integration"
uv build plugins/onestep-clickhouse --out-dir /tmp/onestep-clickhouse-dist --sdist --wheel --clear
uvx twine check /tmp/onestep-clickhouse-dist/*
git diff --check
```

Expected: tests pass; wheel/sdist build; both artifacts pass `twine check`; no
whitespace errors.

- [ ] **Step 5: Run live tests when ClickHouse is available**

```bash
ONESTEP_CLICKHOUSE_DSN=http://default:@127.0.0.1:8123/default uv run --project plugins/onestep-clickhouse --extra test python -m pytest -q plugins/onestep-clickhouse/tests/integration -m integration
```

Expected: PASS on a supported LTS/current server. Both version families remain a
publishing gate in the shared integration plan.

- [ ] **Step 6: Commit runtime proof, docs, and live coverage**

```bash
git add plugins/onestep-clickhouse
git commit -m "test(clickhouse): add runtime and live insert coverage"
```

## Plan Completion Gate

Run `git status --short` and `git log --oneline --max-count=6`.

Expected: this plan changed only `plugins/onestep-clickhouse/**`; all package-local
tests and artifacts pass. Hand the stable package to the shared integration track
without modifying or publishing root package metadata.
