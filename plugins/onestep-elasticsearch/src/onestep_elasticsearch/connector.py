from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from onestep import ConnectorErrorKind, ConnectorOperation, ConnectorOperationError, Envelope, Sink


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

    async def send(self, envelope: Envelope) -> None:
        raise NotImplementedError("bulk send is introduced by Task 4")
