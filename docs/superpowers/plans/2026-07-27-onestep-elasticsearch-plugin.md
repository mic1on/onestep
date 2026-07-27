# Elasticsearch/OpenSearch Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an independently testable `onestep-elasticsearch` plugin that sends acknowledged bulk writes to Elasticsearch and OpenSearch through one strict YAML and Python surface.

**Architecture:** Keep the package entirely under `plugins/onestep-elasticsearch` and use one lazily owned `httpx.AsyncClient` for the common `GET /` and `POST /_bulk` REST boundary. Normalize each envelope body into an explicit mapping-or-sequence logical batch, chunk by action count and NDJSON bytes, inspect every bulk item, and surface partial commits as `UNCERTAIN` unless deterministic-ID `index` replay is safe.

**Tech Stack:** Python `>=3.9`, `onestep>=1.7.1`, `httpx>=0.27`, Hatch, pytest, pytest-asyncio, Elasticsearch 8/9, OpenSearch 2/3.

---

## File Responsibility Map

- Create `plugins/onestep-elasticsearch/pyproject.toml`: independent package metadata, dependencies, Hatch build configuration, plugin-local editable onestep source, and `onestep.resources` entry point.
- Create `plugins/onestep-elasticsearch/README.md`: installation, Python/YAML usage, auth/TLS boundary, payload and acknowledgement semantics, compatibility matrix, stable-ID guidance, and deferred sources.
- Create `plugins/onestep-elasticsearch/src/onestep_elasticsearch/__init__.py`: public exports, package version, and `register` alias.
- Create `plugins/onestep-elasticsearch/src/onestep_elasticsearch/connector.py`: connector/client lifecycle, host selection, NDJSON normalization/chunking, bulk sink, response inspection, bounded subset retry, and typed bulk causes.
- Create `plugins/onestep-elasticsearch/src/onestep_elasticsearch/resources.py`: resource catalogs, builders, and strict cross-field validation for `elasticsearch` and `elasticsearch_bulk_sink`.
- Create `plugins/onestep-elasticsearch/src/onestep_elasticsearch/resilience.py`: HTTP/backend exception and status classification into `ConnectorErrorKind`.
- Create `plugins/onestep-elasticsearch/tests/test_elasticsearch_connector.py`: fake-transport tests for auth, TLS client construction, exact NDJSON, chunking, item inspection, retry subsets, uncertainty, lifecycle, and acknowledgement ordering.
- Create `plugins/onestep-elasticsearch/tests/test_elasticsearch_plugin.py`: installed entry point, catalog, strict YAML, and public export tests.
- Create `plugins/onestep-elasticsearch/tests/test_elasticsearch_resilience.py`: status/exception classification tests.
- Create `plugins/onestep-elasticsearch/tests/integration/test_elasticsearch_live.py`: identical live behavior against Elasticsearch or OpenSearch selected by environment.

No task in this plan edits root `pyproject.toml`, `uv.lock`, root scripts, workflows,
Compose files, root docs, changelog, or the worker image. Those files belong only
to `2026-07-27-onestep-database-plugin-integration.md`.

### Task 1: Establish The Independent Package And Public Types

**Files:**
- Create: `plugins/onestep-elasticsearch/pyproject.toml`
- Create: `plugins/onestep-elasticsearch/README.md`
- Create: `plugins/onestep-elasticsearch/src/onestep_elasticsearch/__init__.py`
- Create: `plugins/onestep-elasticsearch/src/onestep_elasticsearch/connector.py`
- Create: `plugins/onestep-elasticsearch/src/onestep_elasticsearch/resources.py`
- Create: `plugins/onestep-elasticsearch/src/onestep_elasticsearch/resilience.py`
- Create: `plugins/onestep-elasticsearch/tests/test_elasticsearch_plugin.py`

- [ ] **Step 1: Write the package-discovery test**

Create `plugins/onestep-elasticsearch/tests/test_elasticsearch_plugin.py`:

```python
from __future__ import annotations

from importlib import metadata as importlib_metadata

from onestep_elasticsearch import (
    ElasticsearchBulkError,
    ElasticsearchBulkItemError,
    ElasticsearchBulkSink,
    ElasticsearchConnector,
    register,
    register_resources,
)


def _entry_points_for_group(group: str):
    entry_points = importlib_metadata.entry_points()
    if hasattr(entry_points, "select"):
        return list(entry_points.select(group=group))
    return list(entry_points.get(group, ()))


def test_package_exports_the_approved_python_surface() -> None:
    assert register is register_resources
    assert ElasticsearchConnector.__name__ == "ElasticsearchConnector"
    assert ElasticsearchBulkSink.__name__ == "ElasticsearchBulkSink"
    assert ElasticsearchBulkError.__name__ == "ElasticsearchBulkError"
    assert ElasticsearchBulkItemError.__name__ == "ElasticsearchBulkItemError"


def test_package_exposes_resource_entry_point() -> None:
    assert any(
        item.name == "elasticsearch"
        and item.value == "onestep_elasticsearch:register"
        for item in _entry_points_for_group("onestep.resources")
    )
```

- [ ] **Step 2: Run the test and verify package discovery fails**

Run:

```bash
uv run --project plugins/onestep-elasticsearch --extra test python -m pytest -q plugins/onestep-elasticsearch/tests/test_elasticsearch_plugin.py
```

Expected: FAIL before collection because `plugins/onestep-elasticsearch/pyproject.toml`
or `onestep_elasticsearch` does not exist.

- [ ] **Step 3: Add exact package metadata**

Create `plugins/onestep-elasticsearch/pyproject.toml`:

```toml
[project]
name = "onestep-elasticsearch"
version = "0.1.0"
description = "Elasticsearch and OpenSearch connector plugin for onestep."
readme = "README.md"
requires-python = ">=3.9"
license = { text = "MIT" }
dependencies = [
    "onestep>=1.7.1",
    "httpx>=0.27",
]

[project.optional-dependencies]
test = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
]

[project.entry-points."onestep.resources"]
elasticsearch = "onestep_elasticsearch:register"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/onestep_elasticsearch"]

[tool.uv.sources]
onestep = { path = "../..", editable = true }

[tool.pytest.ini_options]
asyncio_mode = "auto"
markers = ["integration: live external service tests"]
```

Create the package README required by that metadata at
`plugins/onestep-elasticsearch/README.md`:

```markdown
# onestep-elasticsearch

Elasticsearch and OpenSearch connector plugin for onestep.
```

- [ ] **Step 4: Add the approved public types and registration target**

Create `connector.py` with these initial complete public types:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from onestep import Sink


@dataclass(frozen=True)
class ElasticsearchBulkItemError:
    action_index: int
    operation: str
    document_id: str | None
    status: int
    error_type: str | None
    reason: str


class ElasticsearchBulkError(Exception):
    def __init__(self, items: list[ElasticsearchBulkItemError], *, partial_success: bool = False) -> None:
        self.items = tuple(items)
        self.partial_success = partial_success
        summary = ", ".join(
            f"item={item.action_index} status={item.status} reason={item.reason[:160]}"
            for item in self.items[:10]
        )
        super().__init__(f"Elasticsearch bulk request failed: {summary}")


class ElasticsearchConnector:
    def __init__(self, hosts: str | list[str], **options: Any) -> None:
        self.hosts = [hosts] if isinstance(hosts, str) else list(hosts)
        self.options = dict(options)

    def bulk_sink(self, *, index: str, **options: Any) -> "ElasticsearchBulkSink":
        return ElasticsearchBulkSink(connector=self, index=index, **options)


class ElasticsearchBulkSink(Sink):
    def __init__(self, *, connector: ElasticsearchConnector, index: str, **options: Any) -> None:
        super().__init__(f"elasticsearch.bulk:{index}")
        self.connector = connector
        self.index = index
        self.options = dict(options)

    async def send(self, envelope) -> None:
        raise NotImplementedError("bulk send is introduced by Task 4")
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


def classify_elasticsearch_error(exc: BaseException) -> ConnectorErrorKind | None:
    if isinstance(exc, (ConnectionError, OSError)):
        return ConnectorErrorKind.DISCONNECTED
    return None
```

Create `__init__.py`:

```python
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _package_version

from .connector import (
    ElasticsearchBulkError,
    ElasticsearchBulkItemError,
    ElasticsearchBulkSink,
    ElasticsearchConnector,
)
from .resources import register_resources

try:
    __version__ = _package_version("onestep-elasticsearch")
except PackageNotFoundError:
    __version__ = "dev"

register = register_resources

__all__ = [
    "ElasticsearchBulkError",
    "ElasticsearchBulkItemError",
    "ElasticsearchBulkSink",
    "ElasticsearchConnector",
    "__version__",
    "register",
    "register_resources",
]
```

- [ ] **Step 5: Run the package test and verify it passes**

Run the Step 2 command again.

Expected: `2 passed`.

- [ ] **Step 6: Commit the independent package foundation**

```bash
git add plugins/onestep-elasticsearch
git commit -m "feat(elasticsearch): add plugin package foundation"
```

### Task 2: Normalize Payloads And Produce Exact NDJSON Chunks

**Files:**
- Modify: `plugins/onestep-elasticsearch/src/onestep_elasticsearch/connector.py`
- Create: `plugins/onestep-elasticsearch/tests/test_elasticsearch_connector.py`

- [ ] **Step 1: Write failing payload and chunk tests**

Create `test_elasticsearch_connector.py` with:

```python
from __future__ import annotations

import pytest

from onestep import Envelope
from onestep_elasticsearch.connector import ElasticsearchConnector


def test_mapping_becomes_one_newline_terminated_bulk_action() -> None:
    sink = ElasticsearchConnector("http://search:9200").bulk_sink(
        index="events", id_field="event_id"
    )
    chunks = sink._encode_chunks({"event_id": "evt-1", "value": 3})

    assert chunks == [
        b'{"index":{"_index":"events","_id":"evt-1"}}\n'
        b'{"event_id":"evt-1","value":3}\n'
    ]


def test_sequence_chunks_by_action_count() -> None:
    sink = ElasticsearchConnector("http://search:9200").bulk_sink(
        index="events", chunk_size=2
    )
    chunks = sink._encode_chunks([{"n": 1}, {"n": 2}, {"n": 3}])

    assert len(chunks) == 2
    assert chunks[0].count(b'\n') == 4
    assert chunks[1].count(b'\n') == 2


@pytest.mark.parametrize("body", [[], "text", ["text"], [{"ok": 1}, 2]])
def test_invalid_logical_batch_is_rejected(body) -> None:
    sink = ElasticsearchConnector("http://search:9200").bulk_sink(index="events")

    with pytest.raises((TypeError, ValueError)):
        sink._encode_chunks(body)


def test_one_action_larger_than_byte_limit_is_rejected() -> None:
    sink = ElasticsearchConnector("http://search:9200").bulk_sink(
        index="events", max_chunk_bytes=40
    )

    with pytest.raises(ValueError, match="max_chunk_bytes"):
        sink._encode_chunks({"payload": "x" * 100})
```

- [ ] **Step 2: Run the tests and verify the missing helper fails**

Run:

```bash
uv run --project plugins/onestep-elasticsearch --extra test python -m pytest -q plugins/onestep-elasticsearch/tests/test_elasticsearch_connector.py
```

Expected: FAIL with `AttributeError: 'ElasticsearchBulkSink' object has no attribute '_encode_chunks'`.

- [ ] **Step 3: Implement complete payload normalization and chunking helpers**

Add these imports and definitions to `connector.py`, replace the sink constructor,
and add the methods exactly as shown:

```python
import json
from collections.abc import Mapping, Sequence

from onestep import ConnectorErrorKind, ConnectorOperation, ConnectorOperationError, Envelope


def _logical_documents(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, Mapping):
        return [dict(body)]
    if not isinstance(body, Sequence) or isinstance(body, (str, bytes, bytearray)):
        raise TypeError("bulk payload must be a mapping or non-empty sequence of mappings")
    if not body:
        raise ValueError("bulk payload sequence must not be empty")
    documents: list[dict[str, Any]] = []
    for index, item in enumerate(body):
        if not isinstance(item, Mapping):
            raise TypeError(f"bulk payload item {index} must be a mapping")
        documents.append(dict(item))
    return documents


class ElasticsearchBulkSink(Sink):
    def __init__(
        self,
        *,
        connector: ElasticsearchConnector,
        index: str,
        operation: str = "index",
        id_field: str | None = None,
        chunk_size: int = 500,
        max_chunk_bytes: int = 5_000_000,
        refresh: bool | str = False,
        pipeline: str | None = None,
        max_retries: int = 2,
    ) -> None:
        super().__init__(f"elasticsearch.bulk:{index}")
        if operation not in {"index", "create"}:
            raise ValueError("operation must be 'index' or 'create'")
        if chunk_size <= 0 or max_chunk_bytes <= 0:
            raise ValueError("chunk_size and max_chunk_bytes must be positive")
        self.connector = connector
        self.index = index
        self.operation = operation
        self.id_field = id_field
        self.chunk_size = chunk_size
        self.max_chunk_bytes = max_chunk_bytes
        self.refresh = refresh
        self.pipeline = pipeline
        self.max_retries = max_retries

    def _encode_action(self, document: Mapping[str, Any]) -> bytes:
        metadata: dict[str, Any] = {"_index": self.index}
        if self.id_field is not None and self.id_field in document:
            metadata["_id"] = str(document[self.id_field])
        action = json.dumps(
            {self.operation: metadata}, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        source = json.dumps(
            dict(document), separators=(",", ":"), ensure_ascii=False, default=str
        ).encode("utf-8")
        return action + b"\n" + source + b"\n"

    def _encode_chunks(self, body: Any) -> list[bytes]:
        chunks: list[bytes] = []
        current: list[bytes] = []
        current_bytes = 0
        for document in _logical_documents(body):
            action = self._encode_action(document)
            if len(action) > self.max_chunk_bytes:
                raise ValueError("one bulk action exceeds max_chunk_bytes")
            if current and (
                len(current) >= self.chunk_size
                or current_bytes + len(action) > self.max_chunk_bytes
            ):
                chunks.append(b"".join(current))
                current = []
                current_bytes = 0
            current.append(action)
            current_bytes += len(action)
        if current:
            chunks.append(b"".join(current))
        return chunks
```

- [ ] **Step 4: Run the focused tests and verify they pass**

Run the Step 2 command again.

Expected: `7 passed`.

- [ ] **Step 5: Commit payload normalization**

```bash
git add plugins/onestep-elasticsearch/src/onestep_elasticsearch/connector.py plugins/onestep-elasticsearch/tests/test_elasticsearch_connector.py
git commit -m "feat(elasticsearch): add deterministic bulk chunking"
```

### Task 3: Implement Connector HTTP, Authentication, Distribution, And Lifecycle

**Files:**
- Modify: `plugins/onestep-elasticsearch/src/onestep_elasticsearch/connector.py`
- Modify: `plugins/onestep-elasticsearch/tests/test_elasticsearch_connector.py`
- Modify: `plugins/onestep-elasticsearch/src/onestep_elasticsearch/resilience.py`
- Create: `plugins/onestep-elasticsearch/tests/test_elasticsearch_resilience.py`

- [ ] **Step 1: Add failing lifecycle, host, auth, and classification tests**

Append to `test_elasticsearch_connector.py`:

```python
import httpx


@pytest.mark.asyncio
async def test_connector_round_robins_hosts_and_does_not_close_injected_client() -> None:
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"version": {"distribution": "opensearch"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connector = ElasticsearchConnector(
        ["http://one:9200", "http://two:9200"], client=client
    )

    await connector.request_json("GET", "/")
    await connector.request_json("GET", "/")
    await connector.close()

    assert seen == ["http://one:9200/", "http://two:9200/"]
    assert client.is_closed is False
    await client.aclose()


@pytest.mark.asyncio
async def test_basic_auth_and_custom_headers_are_applied() -> None:
    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connector = ElasticsearchConnector(
        "https://search:9200",
        username="writer",
        password="secret",
        headers={"X-Tenant": "blue"},
        client=client,
    )
    await connector.request_json("GET", "/")

    assert captured[0].headers["authorization"].startswith("Basic ")
    assert captured[0].headers["x-tenant"] == "blue"
    await client.aclose()


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"api_key": "encoded-key"}, "ApiKey encoded-key"),
        ({"bearer_token": "token"}, "Bearer token"),
    ],
)
@pytest.mark.asyncio
async def test_api_key_and_bearer_auth_headers(options, expected) -> None:
    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connector = ElasticsearchConnector("https://search:9200", client=client, **options)
    await connector.request_json("GET", "/")
    assert captured[0].headers["authorization"] == expected
    await client.aclose()


@pytest.mark.asyncio
async def test_owned_client_receives_ca_and_client_certificate(monkeypatch) -> None:
    captured: dict[str, object] = {}
    close_calls = 0

    class BuiltClient:
        async def aclose(self) -> None:
            nonlocal close_calls
            close_calls += 1

    def build_client(**options):
        captured.update(options)
        return BuiltClient()

    monkeypatch.setattr(httpx, "AsyncClient", build_client)
    connector = ElasticsearchConnector(
        "https://search:9200",
        ca_certs="/etc/ssl/search-ca.pem",
        client_cert="/etc/ssl/client.pem",
        client_key="/etc/ssl/client-key.pem",
    )
    await connector._get_client()
    assert captured == {
        "verify": "/etc/ssl/search-ca.pem",
        "cert": ("/etc/ssl/client.pem", "/etc/ssl/client-key.pem"),
    }
    await connector.close()
    await connector.close()
    assert close_calls == 1
```

Create `test_elasticsearch_resilience.py`:

```python
from __future__ import annotations

import httpx

from onestep import ConnectorErrorKind
from onestep_elasticsearch.resilience import (
    classify_elasticsearch_exception,
    classify_elasticsearch_status,
)


def test_status_classification() -> None:
    assert classify_elasticsearch_status(429) is ConnectorErrorKind.THROTTLED
    assert classify_elasticsearch_status(503) is ConnectorErrorKind.TRANSIENT
    assert classify_elasticsearch_status(401) is ConnectorErrorKind.MISCONFIGURED
    assert classify_elasticsearch_status(400) is ConnectorErrorKind.PERMANENT


def test_http_exception_classification() -> None:
    request = httpx.Request("POST", "https://search/_bulk")
    assert classify_elasticsearch_exception(httpx.ConnectError("down", request=request)) is ConnectorErrorKind.DISCONNECTED
    assert classify_elasticsearch_exception(httpx.ReadTimeout("late", request=request)) is ConnectorErrorKind.UNCERTAIN
```

- [ ] **Step 2: Run the focused tests and verify missing HTTP methods fail**

Run:

```bash
uv run --project plugins/onestep-elasticsearch --extra test python -m pytest -q plugins/onestep-elasticsearch/tests/test_elasticsearch_connector.py plugins/onestep-elasticsearch/tests/test_elasticsearch_resilience.py
```

Expected: FAIL because `request_json`, `classify_elasticsearch_exception`, and
`classify_elasticsearch_status` are not defined.

- [ ] **Step 3: Implement exact error classifiers**

Replace `resilience.py` with:

```python
from __future__ import annotations

import httpx

from onestep import ConnectorErrorKind


def classify_elasticsearch_status(status: int) -> ConnectorErrorKind:
    if status == 429:
        return ConnectorErrorKind.THROTTLED
    if status in {502, 503, 504}:
        return ConnectorErrorKind.TRANSIENT
    if status in {401, 403, 404}:
        return ConnectorErrorKind.MISCONFIGURED
    return ConnectorErrorKind.PERMANENT


def classify_elasticsearch_exception(exc: BaseException) -> ConnectorErrorKind | None:
    if isinstance(exc, httpx.ConnectError):
        return ConnectorErrorKind.DISCONNECTED
    if isinstance(exc, (httpx.ReadTimeout, httpx.WriteError, httpx.ReadError)):
        return ConnectorErrorKind.UNCERTAIN
    if isinstance(exc, httpx.TimeoutException):
        return ConnectorErrorKind.UNCERTAIN
    if isinstance(exc, (OSError, ConnectionError)):
        return ConnectorErrorKind.DISCONNECTED
    return None
```

- [ ] **Step 4: Replace the connector shell with the complete owned-client contract**

In `connector.py`, replace `ElasticsearchConnector` with:

```python
class ElasticsearchConnector:
    def __init__(
        self,
        hosts: str | list[str],
        *,
        distribution: str = "auto",
        username: str | None = None,
        password: str | None = None,
        api_key: str | None = None,
        bearer_token: str | None = None,
        headers: Mapping[str, str] | None = None,
        verify_certs: bool = True,
        ca_certs: str | None = None,
        client_cert: str | None = None,
        client_key: str | None = None,
        request_timeout_s: float = 10.0,
        client: Any | None = None,
    ) -> None:
        normalized = [hosts] if isinstance(hosts, str) else list(hosts)
        if not normalized:
            raise ValueError("hosts must not be empty")
        if distribution not in {"auto", "elasticsearch", "opensearch"}:
            raise ValueError("distribution must be auto, elasticsearch, or opensearch")
        self.hosts = [item.rstrip("/") for item in normalized]
        self.distribution = distribution
        self.username = username
        self.password = password
        self.api_key = api_key
        self.bearer_token = bearer_token
        self.headers = dict(headers or {})
        self.verify_certs = verify_certs
        self.ca_certs = ca_certs
        self.client_cert = client_cert
        self.client_key = client_key
        self.request_timeout_s = request_timeout_s
        self._client = client
        self._owns_client = client is None
        self._host_index = 0
        self._closed = False

    def _auth_headers(self) -> dict[str, str]:
        import base64

        result = dict(self.headers)
        if self.username is not None and self.password is not None:
            raw = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            result["Authorization"] = f"Basic {raw}"
        elif self.api_key is not None:
            result["Authorization"] = f"ApiKey {self.api_key}"
        elif self.bearer_token is not None:
            result["Authorization"] = f"Bearer {self.bearer_token}"
        return result

    async def _get_client(self):
        import httpx

        if self._client is None:
            verify: Any = self.ca_certs if self.ca_certs is not None else self.verify_certs
            cert: Any = None
            if self.client_cert is not None:
                cert = (self.client_cert, self.client_key) if self.client_key else self.client_cert
            self._client = httpx.AsyncClient(verify=verify, cert=cert)
        return self._client

    def _next_url(self, path: str) -> str:
        host = self.hosts[self._host_index % len(self.hosts)]
        self._host_index += 1
        return f"{host}/{path.lstrip('/')}"

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        content: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        client = await self._get_client()
        request_headers = self._auth_headers()
        request_headers.update(headers or {})
        response = await client.request(
            method,
            self._next_url(path),
            params=dict(params or {}),
            content=content,
            headers=request_headers,
            timeout=self.request_timeout_s,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {"error": {"reason": response.text[:500]}}
        return response.status_code, payload

    async def request_ndjson(
        self, path: str, body: bytes, *, params: Mapping[str, Any] | None = None
    ) -> tuple[int, dict[str, Any]]:
        return await self.request_json(
            "POST",
            path,
            params=params,
            content=body,
            headers={"Content-Type": "application/x-ndjson"},
        )

    def bulk_sink(self, *, index: str, **options: Any) -> "ElasticsearchBulkSink":
        return ElasticsearchBulkSink(connector=self, index=index, **options)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_client and self._client is not None:
            await self._client.aclose()
```

- [ ] **Step 5: Run the focused tests and verify they pass**

Run the Step 2 command again.

Expected: all tests pass.

- [ ] **Step 6: Commit transport and lifecycle**

```bash
git add plugins/onestep-elasticsearch/src/onestep_elasticsearch plugins/onestep-elasticsearch/tests
git commit -m "feat(elasticsearch): add common async REST transport"
```

### Task 4: Send Bulk Chunks, Inspect Items, Retry Subsets, And Preserve Uncertainty

**Files:**
- Modify: `plugins/onestep-elasticsearch/src/onestep_elasticsearch/connector.py`
- Modify: `plugins/onestep-elasticsearch/tests/test_elasticsearch_connector.py`

- [ ] **Step 1: Add failing acknowledgement and partial-commit tests**

Append:

```python
from onestep import ConnectorErrorKind, ConnectorOperationError


@pytest.mark.asyncio
async def test_send_waits_for_every_success_item() -> None:
    calls: list[bytes] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.content)
        return httpx.Response(200, json={"errors": False, "items": [{"index": {"status": 201}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connector = ElasticsearchConnector("http://search:9200", client=client)
    sink = connector.bulk_sink(index="events", chunk_size=1)

    await sink.send(Envelope(body=[{"n": 1}, {"n": 2}]))

    assert len(calls) == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_partial_auto_id_failure_is_uncertain() -> None:
    responses = [
        httpx.Response(200, json={"errors": False, "items": [{"index": {"status": 201}}]}),
        httpx.Response(200, json={"errors": True, "items": [{"index": {"status": 400, "error": {"type": "mapper_parsing_exception", "reason": "bad field"}}}]}),
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = ElasticsearchConnector("http://search:9200", client=client).bulk_sink(index="events", chunk_size=1)

    with pytest.raises(ConnectorOperationError) as captured:
        await sink.send(Envelope(body=[{"n": 1}, {"n": "bad"}]))

    assert captured.value.kind is ConnectorErrorKind.UNCERTAIN
    assert captured.value.cause.items[0].status == 400
    await client.aclose()


@pytest.mark.asyncio
async def test_429_retries_only_failed_subset() -> None:
    bodies: list[bytes] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        if len(bodies) == 1:
            return httpx.Response(200, json={"errors": True, "items": [
                {"index": {"status": 201}},
                {"index": {"status": 429, "_id": "2", "error": {"type": "rejected", "reason": "busy"}}},
            ]})
        return httpx.Response(200, json={"errors": False, "items": [{"index": {"status": 201}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = ElasticsearchConnector("http://search:9200", client=client).bulk_sink(index="events", id_field="id")
    await sink.send(Envelope(body=[{"id": "1"}, {"id": "2"}]))

    assert bodies[1].count(b"\n") == 2
    assert b'"_id":"2"' in bodies[1]
    await client.aclose()


@pytest.mark.asyncio
async def test_missing_bulk_item_acknowledgement_is_permanent() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": False, "items": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = ElasticsearchConnector("http://search:9200", client=client).bulk_sink(index="events")
    with pytest.raises(ConnectorOperationError) as captured:
        await sink.send(Envelope(body={"id": "one"}))
    assert captured.value.kind is ConnectorErrorKind.PERMANENT
    assert captured.value.cause.items[0].status == 0
    await client.aclose()
```

- [ ] **Step 2: Run the three tests and verify `NotImplementedError`**

Run:

```bash
uv run --project plugins/onestep-elasticsearch --extra test python -m pytest -q plugins/onestep-elasticsearch/tests/test_elasticsearch_connector.py -k "send_waits or partial_auto or retries_only or missing_bulk_item"
```

Expected: all selected tests FAIL at `ElasticsearchBulkSink.send`.

- [ ] **Step 3: Implement item parsing and subset reconstruction**

Add these complete methods to `ElasticsearchBulkSink`:

```python
    def _action_documents(self, body: bytes) -> list[bytes]:
        lines = body.splitlines(keepends=True)
        return [lines[index] + lines[index + 1] for index in range(0, len(lines), 2)]

    def _parse_items(self, payload: Mapping[str, Any]) -> list[ElasticsearchBulkItemError]:
        failures: list[ElasticsearchBulkItemError] = []
        for index, wrapper in enumerate(payload.get("items", [])):
            operation, item = next(iter(wrapper.items()))
            status = int(item.get("status", 0))
            if 200 <= status < 300:
                continue
            error = item.get("error") or {}
            reason = error.get("reason") if isinstance(error, Mapping) else str(error)
            failures.append(
                ElasticsearchBulkItemError(
                    action_index=index,
                    operation=operation,
                    document_id=item.get("_id"),
                    status=status,
                    error_type=error.get("type") if isinstance(error, Mapping) else None,
                    reason=str(reason or "bulk item failed"),
                )
            )
        return failures

    def _params(self) -> dict[str, Any]:
        params: dict[str, Any] = {"refresh": str(self.refresh).lower() if isinstance(self.refresh, bool) else self.refresh}
        if self.pipeline is not None:
            params["pipeline"] = self.pipeline
        return params

    async def _send_chunk(self, body: bytes) -> None:
        import asyncio
        import random

        pending = body
        partial_success = False
        for attempt in range(self.max_retries + 1):
            status, payload = await self.connector.request_ndjson("/_bulk", pending, params=self._params())
            if status < 200 or status >= 300:
                failure = ElasticsearchBulkItemError(0, self.operation, None, status, None, str(payload.get("error", "request failed")))
                if status in {429, 502, 503, 504} and attempt < self.max_retries:
                    await asyncio.sleep((0.05 * (2**attempt)) + random.uniform(0.0, 0.025))
                    continue
                raise ElasticsearchBulkError([
                    failure
                ], partial_success=partial_success)
            items = payload.get("items")
            expected_items = len(self._action_documents(pending))
            if not isinstance(items, list) or len(items) != expected_items:
                acknowledged = 0
                if isinstance(items, list):
                    for wrapper in items:
                        if isinstance(wrapper, Mapping) and len(wrapper) == 1:
                            item = next(iter(wrapper.values()))
                            if isinstance(item, Mapping) and 200 <= int(item.get("status", 0)) < 300:
                                acknowledged += 1
                raise ElasticsearchBulkError(
                    [ElasticsearchBulkItemError(0, self.operation, None, 0, "invalid_response", "bulk response item count did not match request")],
                    partial_success=partial_success or acknowledged > 0,
                )
            failures = self._parse_items(payload)
            if not failures:
                return
            partial_success = partial_success or len(failures) < len(payload.get("items", []))
            retryable_indexes = [item.action_index for item in failures if item.status in {429, 502, 503, 504}]
            permanent = [item for item in failures if item.action_index not in retryable_indexes]
            if permanent or not retryable_indexes or attempt == self.max_retries:
                raise ElasticsearchBulkError(failures, partial_success=partial_success)
            actions = self._action_documents(pending)
            pending = b"".join(actions[index] for index in retryable_indexes)
            await asyncio.sleep((0.05 * (2**attempt)) + random.uniform(0.0, 0.025))
        raise AssertionError("bulk retry loop exhausted without returning or raising")
```

Also import `classify_elasticsearch_exception` and
`classify_elasticsearch_status` from `.resilience`.

- [ ] **Step 4: Implement logical-send error and idempotency classification**

Replace `send()` with:

```python
    async def send(self, envelope: Envelope) -> None:
        try:
            chunks = self._encode_chunks(envelope.body)
        except (TypeError, ValueError) as exc:
            raise ConnectorOperationError(
                backend="elasticsearch",
                operation=ConnectorOperation.SEND,
                kind=ConnectorErrorKind.PERMANENT,
                source_name=self.name,
                cause=exc,
            ) from exc
        committed_chunks = 0
        try:
            for chunk in chunks:
                await self._send_chunk(chunk)
                committed_chunks += 1
        except ElasticsearchBulkError as exc:
            base_kind = classify_elasticsearch_status(exc.items[0].status) if exc.items else ConnectorErrorKind.PERMANENT
            replay_safe = self.operation == "index" and self.id_field is not None
            kind = ConnectorErrorKind.UNCERTAIN if (committed_chunks or exc.partial_success) and not replay_safe else base_kind
            raise ConnectorOperationError(
                backend="elasticsearch",
                operation=ConnectorOperation.SEND,
                kind=kind,
                source_name=self.name,
                cause=exc,
            ) from exc
        except Exception as exc:
            kind = classify_elasticsearch_exception(exc)
            if kind is None:
                raise
            replay_safe = self.operation == "index" and self.id_field is not None
            if committed_chunks and not replay_safe:
                kind = ConnectorErrorKind.UNCERTAIN
            raise ConnectorOperationError(
                backend="elasticsearch",
                operation=ConnectorOperation.SEND,
                kind=kind,
                source_name=self.name,
                cause=exc,
            ) from exc
```

- [ ] **Step 5: Run the complete connector suite**

Run:

```bash
uv run --project plugins/onestep-elasticsearch --extra test python -m pytest -q plugins/onestep-elasticsearch/tests/test_elasticsearch_connector.py plugins/onestep-elasticsearch/tests/test_elasticsearch_resilience.py
```

Expected: all tests pass, including two HTTP calls for a two-chunk batch and a
single-action body on retry.

- [ ] **Step 6: Commit acknowledged bulk semantics**

```bash
git add plugins/onestep-elasticsearch/src/onestep_elasticsearch/connector.py plugins/onestep-elasticsearch/tests
git commit -m "feat(elasticsearch): implement acknowledged bulk sends"
```

### Task 5: Register Strict YAML Resources And Catalogs

**Files:**
- Modify: `plugins/onestep-elasticsearch/src/onestep_elasticsearch/resources.py`
- Modify: `plugins/onestep-elasticsearch/tests/test_elasticsearch_plugin.py`

- [ ] **Step 1: Add failing catalog and strict YAML tests**

Append to `test_elasticsearch_plugin.py`:

```python
import pytest

from onestep import ResourceRegistry, load_app_config


def _config(resources):
    return {"apiVersion": "onestep/v1alpha1", "kind": "App", "app": {"name": "search"}, "resources": resources, "tasks": []}


def test_catalog_matches_strict_surface() -> None:
    registry = ResourceRegistry()
    register(registry)
    catalog = {entry.type: entry for entry in registry.catalog_entries()}

    assert catalog["elasticsearch"].roles == ("connector",)
    assert catalog["elasticsearch_bulk_sink"].roles == ("sink",)
    assert catalog["elasticsearch_bulk_sink"].connector_types == ("elasticsearch",)
    assert catalog["elasticsearch_bulk_sink"].topology_fields == ("index", "operation", "chunk_size")


def test_strict_yaml_builds_connector_and_sink() -> None:
    app = load_app_config(_config({
        "search": {"type": "elasticsearch", "hosts": ["https://search:9200"], "distribution": "opensearch", "api_key": "secret"},
        "events": {"type": "elasticsearch_bulk_sink", "connector": "search", "index": "events", "operation": "create", "chunk_size": 25},
    }), strict=True)

    assert isinstance(app.resources["search"], ElasticsearchConnector)
    assert app.resources["events"].operation == "create"


@pytest.mark.parametrize("connector", [
    {"type": "elasticsearch", "hosts": []},
    {"type": "elasticsearch", "hosts": ["ftp://search"]},
    {"type": "elasticsearch", "hosts": ["https://search"], "username": "u"},
    {"type": "elasticsearch", "hosts": ["https://search"], "api_key": "a", "bearer_token": "b"},
])
def test_strict_yaml_rejects_invalid_connector(connector) -> None:
    with pytest.raises((TypeError, ValueError)):
        load_app_config(_config({"search": connector}), strict=True)
```

- [ ] **Step 2: Run strict tests and verify registration fails**

Run:

```bash
uv run --project plugins/onestep-elasticsearch --extra test python -m pytest -q plugins/onestep-elasticsearch/tests/test_elasticsearch_plugin.py -k "catalog or strict"
```

Expected: FAIL because the registry has no `elasticsearch` handler.

- [ ] **Step 3: Implement the complete catalogs and handlers**

Replace `resources.py` with a complete module containing the two field sets,
catalog entries, builders, and validators. Use these exact public definitions:

```python
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlparse

from onestep import ResourceBuildContext, ResourceCatalogEntry, ResourceCatalogField, ResourceRegistry, ResourceSpecHandler, ResourceValidationContext

from .connector import ElasticsearchConnector

CONNECTOR_FIELDS = frozenset({"type", "hosts", "distribution", "username", "password", "api_key", "bearer_token", "headers", "verify_certs", "ca_certs", "client_cert", "client_key", "request_timeout_s"})
SINK_FIELDS = frozenset({"type", "connector", "index", "operation", "id_field", "chunk_size", "max_chunk_bytes", "refresh", "pipeline"})

CONNECTOR_CATALOG = ResourceCatalogEntry(
    type="elasticsearch", roles=("connector",), label="Elasticsearch / OpenSearch",
    fields=(
        ResourceCatalogField("hosts", "string_list", required=True),
        ResourceCatalogField("distribution", "string", default="auto", options=("auto", "elasticsearch", "opensearch")),
        ResourceCatalogField("username", "string"), ResourceCatalogField("password", "string", secret=True),
        ResourceCatalogField("api_key", "string", secret=True), ResourceCatalogField("bearer_token", "string", secret=True),
        ResourceCatalogField("headers", "mapping", secret=True), ResourceCatalogField("verify_certs", "boolean", default=True),
        ResourceCatalogField("ca_certs", "string"), ResourceCatalogField("client_cert", "string"),
        ResourceCatalogField("client_key", "string", secret=True), ResourceCatalogField("request_timeout_s", "number", default=10.0),
    ),
)
SINK_CATALOG = ResourceCatalogEntry(
    type="elasticsearch_bulk_sink", roles=("sink",), label="Elasticsearch Bulk Sink", connector_types=("elasticsearch",),
    fields=(
        ResourceCatalogField("connector", "ref", required=True), ResourceCatalogField("index", "string", required=True),
        ResourceCatalogField("operation", "string", default="index", options=("index", "create")), ResourceCatalogField("id_field", "string"),
        ResourceCatalogField("chunk_size", "integer", default=500), ResourceCatalogField("max_chunk_bytes", "integer", default=5_000_000),
        ResourceCatalogField("refresh", "json", default=False), ResourceCatalogField("pipeline", "string"),
    ), topology_fields=("index", "operation", "chunk_size"),
)


def _hosts(value: Any, *, field: str) -> list[str]:
    values = [value] if isinstance(value, str) else list(value) if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else []
    if not values or any(not isinstance(item, str) or urlparse(item).scheme not in {"http", "https"} or not urlparse(item).netloc for item in values):
        raise ValueError(f"'{field}' must contain non-empty HTTP(S) URLs")
    return values


def _validate_connector(ctx: ResourceValidationContext, spec: Mapping[str, Any]) -> None:
    _hosts(spec.get("hosts"), field=f"{ctx.field}.hosts")
    distribution = spec.get("distribution", "auto")
    if distribution not in {"auto", "elasticsearch", "opensearch"}:
        raise ValueError(f"'{ctx.field}.distribution' is invalid")
    if (spec.get("username") is None) != (spec.get("password") is None):
        raise ValueError(f"'{ctx.field}' requires username and password together")
    modes = int(spec.get("username") is not None) + int(spec.get("api_key") is not None) + int(spec.get("bearer_token") is not None)
    if modes > 1:
        raise ValueError(f"'{ctx.field}' accepts only one authentication mode")
    if "headers" in spec and not isinstance(spec.get("headers"), Mapping):
        raise TypeError(f"'{ctx.field}.headers' must be a mapping")
    ctx.validate_positive_number(spec.get("request_timeout_s"), field=f"{ctx.field}.request_timeout_s")


def _validate_sink(ctx: ResourceValidationContext, spec: Mapping[str, Any]) -> None:
    ctx.require_string(spec, "connector"); ctx.require_string(spec, "index")
    if spec.get("operation", "index") not in {"index", "create"}:
        raise ValueError(f"'{ctx.field}.operation' is invalid")
    ctx.validate_positive_integer(spec.get("chunk_size"), field=f"{ctx.field}.chunk_size")
    ctx.validate_positive_integer(spec.get("max_chunk_bytes"), field=f"{ctx.field}.max_chunk_bytes")
    if spec.get("refresh", False) not in {False, True, "wait_for"}:
        raise ValueError(f"'{ctx.field}.refresh' must be false, true, or wait_for")


def _build_connector(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> ElasticsearchConnector:
    return ElasticsearchConnector(_hosts(spec.get("hosts"), field=f"{ctx.field}.hosts"), distribution=spec.get("distribution", "auto"), username=spec.get("username"), password=spec.get("password"), api_key=spec.get("api_key"), bearer_token=spec.get("bearer_token"), headers=ctx.mapping_value(spec.get("headers"), field=f"{ctx.field}.headers"), verify_certs=spec.get("verify_certs", True), ca_certs=spec.get("ca_certs"), client_cert=spec.get("client_cert"), client_key=spec.get("client_key"), request_timeout_s=spec.get("request_timeout_s", 10.0))


def _build_sink(ctx: ResourceBuildContext, spec: Mapping[str, Any]):
    connector = ctx.resolve_dependency(spec, "connector")
    if not isinstance(connector, ElasticsearchConnector):
        raise TypeError(f"resource {spec['connector']!r} is not an ElasticsearchConnector")
    return connector.bulk_sink(index=ctx.require_string(spec, "index"), operation=spec.get("operation", "index"), id_field=spec.get("id_field"), chunk_size=spec.get("chunk_size", 500), max_chunk_bytes=spec.get("max_chunk_bytes", 5_000_000), refresh=spec.get("refresh", False), pipeline=spec.get("pipeline"))


def register_resources(registry: ResourceRegistry) -> None:
    registry.register_resource_type(ResourceSpecHandler(type="elasticsearch", catalog=CONNECTOR_CATALOG, allowed_fields=CONNECTOR_FIELDS, build=_build_connector, validate=_validate_connector))
    registry.register_resource_type(ResourceSpecHandler(type="elasticsearch_bulk_sink", catalog=SINK_CATALOG, allowed_fields=SINK_FIELDS, build=_build_sink, validate=_validate_sink))
```

- [ ] **Step 4: Run the complete plugin unit suite**

Run:

```bash
uv run --project plugins/onestep-elasticsearch --extra test python -m pytest -q plugins/onestep-elasticsearch/tests -m "not integration"
```

Expected: all tests pass.

- [ ] **Step 5: Commit strict resources**

```bash
git add plugins/onestep-elasticsearch/src/onestep_elasticsearch/resources.py plugins/onestep-elasticsearch/tests/test_elasticsearch_plugin.py
git commit -m "feat(elasticsearch): register strict YAML resources"
```

### Task 6: Prove Runtime Ordering And Add Live Compatibility Coverage

**Files:**
- Modify: `plugins/onestep-elasticsearch/tests/test_elasticsearch_connector.py`
- Create: `plugins/onestep-elasticsearch/tests/integration/test_elasticsearch_live.py`
- Modify: `plugins/onestep-elasticsearch/README.md`

- [ ] **Step 1: Add a runtime source-ack ordering regression test**

Append this complete `OneStepApp` contract test. It proves the source delivery is
not acknowledged while the backend response is pending, then is acknowledged
after the bulk response arrives:

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
async def test_runtime_ack_follows_backend_bulk_acknowledgement() -> None:
    import asyncio

    release = asyncio.Event()
    entered = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"errors": False, "items": [{"index": {"status": 201}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = ElasticsearchConnector("http://search:9200", client=client).bulk_sink(index="events", id_field="id")
    delivery = _AckRecordingDelivery(Envelope(body={"id": "1"}))
    source = _OneShotSource(delivery)
    app = OneStepApp("elasticsearch-runtime-order", shutdown_timeout_s=1.0)

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
    await client.aclose()
```

- [ ] **Step 2: Run the ordering test**

Run:

```bash
uv run --project plugins/onestep-elasticsearch --extra test python -m pytest -q plugins/onestep-elasticsearch/tests/test_elasticsearch_connector.py::test_runtime_ack_follows_backend_bulk_acknowledgement
```

Expected: PASS.

- [ ] **Step 3: Add one environment-driven live test used by every matrix cell**

Create `tests/integration/test_elasticsearch_live.py`:

```python
from __future__ import annotations

import os
import uuid

import httpx
import pytest

from onestep import Envelope
from onestep_elasticsearch import ElasticsearchConnector

URL = os.getenv("ONESTEP_ELASTICSEARCH_URL") or os.getenv("ONESTEP_OPENSEARCH_URL")
pytestmark = [pytest.mark.integration, pytest.mark.skipif(not URL, reason="search URL is not configured")]


@pytest.mark.asyncio
async def test_live_bulk_write_and_deterministic_replay() -> None:
    index = f"onestep-{uuid.uuid4().hex}"
    connector = ElasticsearchConnector(URL, distribution="auto")
    sink = connector.bulk_sink(index=index, id_field="id", refresh=True, chunk_size=1)
    try:
        await sink.send(Envelope(body=[{"id": "one", "value": 1}, {"id": "two", "value": 2}]))
        await sink.send(Envelope(body={"id": "one", "value": 3}))
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{URL.rstrip('/')}/{index}/_doc/one")
        assert response.status_code == 200
        assert response.json()["_source"]["value"] == 3
    finally:
        async with httpx.AsyncClient() as client:
            await client.delete(f"{URL.rstrip('/')}/{index}")
        await connector.close()
```

- [ ] **Step 4: Write the plugin README with all operational contracts**

Replace the minimal `README.md` with these exact sections and examples: install
`pip install onestep-elasticsearch`; the Python example from the approved design;
the strict YAML example; payload contract; Basic/API-key/Bearer and CA/client-cert
configuration; supported Elasticsearch 8/9 and OpenSearch 2/3 matrix; stable-ID
`index` replay; `create` and auto-ID duplication; partial-commit `UNCERTAIN`;
at-least-once source acknowledgement; and a deferred-features list containing
Cloud ID, sniffing, SigV4, data streams, administration APIs, dynamic actions,
update/delete, PIT, SQL, `elasticsearch_search_after`, and
`elasticsearch_scroll`.

Use this acknowledgement warning verbatim:

```markdown
## Delivery semantics

`send()` returns only after every bulk item in every chunk is acknowledged. onestep
acknowledges the source after sink sends, so a crash between the bulk acknowledgement
and source acknowledgement can duplicate output. Multi-sink fan-out is not
transactional. Use `operation: index` with a stable `id_field` when replay must
converge; auto-generated IDs and `create` are not replay-safe.
```

- [ ] **Step 5: Run plugin-local validation and build**

Run:

```bash
uv run --project plugins/onestep-elasticsearch --extra test python -m pytest -q plugins/onestep-elasticsearch/tests -m "not integration"
uv build plugins/onestep-elasticsearch --out-dir /tmp/onestep-elasticsearch-dist --sdist --wheel --clear
uvx twine check /tmp/onestep-elasticsearch-dist/*
git diff --check
```

Expected: unit tests pass; wheel and sdist build; `twine check` reports both
artifacts `PASSED`; `git diff --check` emits no output.

- [ ] **Step 6: Run a live matrix cell when a server URL is available**

Run one of:

```bash
ONESTEP_ELASTICSEARCH_URL=http://127.0.0.1:9200 uv run --project plugins/onestep-elasticsearch --extra test python -m pytest -q plugins/onestep-elasticsearch/tests/integration -m integration
ONESTEP_OPENSEARCH_URL=http://127.0.0.1:9200 uv run --project plugins/onestep-elasticsearch --extra test python -m pytest -q plugins/onestep-elasticsearch/tests/integration -m integration
```

Expected: PASS against the configured server. Publishing remains blocked until the
shared integration plan runs the required Elasticsearch 8/9 and OpenSearch 2/3
matrix.

- [ ] **Step 7: Commit docs and compatibility tests**

```bash
git add plugins/onestep-elasticsearch
git commit -m "test(elasticsearch): add runtime and live compatibility coverage"
```

## Plan Completion Gate

Run:

```bash
git status --short
git log --oneline --max-count=6
```

Expected: only plugin-local Elasticsearch/OpenSearch files were changed by this
plan, all plugin-local commits are present, and there are no shared root-file edits.
Hand the stable `plugins/onestep-elasticsearch` package to the shared integration
plan; do not publish it or add root extras from this track.
