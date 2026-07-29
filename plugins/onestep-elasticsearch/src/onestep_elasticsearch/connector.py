from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from onestep import (
    ConnectorErrorKind,
    ConnectorOperation,
    ConnectorOperationError,
    Envelope,
    Sink,
)

from .resilience import classify_elasticsearch_exception, classify_elasticsearch_status, redacted_es_cause


def _logical_document_views(body: Any) -> Sequence[Mapping[str, Any]]:
    if isinstance(body, Mapping):
        return (body,)
    if not isinstance(body, Sequence) or isinstance(body, (str, bytes, bytearray)):
        raise TypeError(
            "bulk payload must be a mapping or non-empty sequence of mappings"
        )
    if not body:
        raise ValueError("bulk payload sequence must not be empty")
    for index, item in enumerate(body):
        if not isinstance(item, Mapping):
            raise TypeError(f"bulk payload item {index} must be a mapping")
    return body


@dataclass(frozen=True)
class ElasticsearchBulkItemError:
    action_index: int
    operation: str
    document_id: str | None
    status: int
    error_type: str | None
    reason: str


class ElasticsearchBulkError(Exception):
    def __init__(
        self, items: list[ElasticsearchBulkItemError], *, partial_success: bool = False
    ) -> None:
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

    def redact(self, value: Any) -> str:
        result = str(value)
        secrets = [
            self.username,
            self.password,
            self.api_key,
            self.bearer_token,
            self.client_key,
            *self.headers.values(),
        ]
        for secret in sorted(
            {str(item) for item in secrets if item is not None and str(item)},
            key=len,
            reverse=True,
        ):
            result = result.replace(secret, "<redacted>")
        return result

    def _secret_tokens(self) -> list[str]:
        """Secret-bearing config tokens used to scrub error messages."""
        return collect_sensitive_tokens(
            self.username,
            self.password,
            self.api_key,
            self.bearer_token,
            self.client_key,
            self.headers,
        )

    async def _get_client(self):
        import httpx

        if self._client is None:
            verify: Any = (
                self.ca_certs if self.ca_certs is not None else self.verify_certs
            )
            cert: Any = None
            if self.client_cert is not None:
                cert = (
                    (self.client_cert, self.client_key)
                    if self.client_key
                    else self.client_cert
                )
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
            raw_payload = response.json()
        except ValueError:
            payload = {"error": {"reason": response.text[:500]}}
        else:
            payload = (
                dict(raw_payload)
                if isinstance(raw_payload, Mapping)
                else {
                    "error": {
                        "type": "invalid_response",
                        "reason": "response JSON must be an object",
                    }
                }
            )
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

    def bulk_sink(self, *, index: str, **options: Any) -> ElasticsearchBulkSink:
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

    def _iter_encoded_chunks(self, body: Any) -> Iterator[bytes]:
        documents = _logical_document_views(body)

        def encode() -> Iterator[bytes]:
            current: list[bytes] = []
            current_bytes = 0
            for document in documents:
                action = self._encode_action(document)
                if len(action) > self.max_chunk_bytes:
                    raise ValueError("one bulk action exceeds max_chunk_bytes")
                if current and (
                    len(current) >= self.chunk_size
                    or current_bytes + len(action) > self.max_chunk_bytes
                ):
                    yield b"".join(current)
                    current = []
                    current_bytes = 0
                current.append(action)
                current_bytes += len(action)
            if current:
                yield b"".join(current)

        return encode()

    def _encode_chunks(self, body: Any) -> list[bytes]:
        return list(self._iter_encoded_chunks(body))

    def _action_documents(self, body: bytes) -> list[bytes]:
        lines = body.splitlines(keepends=True)
        if len(lines) % 2:
            raise ValueError("bulk request must contain action/source line pairs")
        return [lines[index] + lines[index + 1] for index in range(0, len(lines), 2)]

    def _parse_items(
        self, payload: Mapping[str, Any]
    ) -> list[ElasticsearchBulkItemError]:
        failures: list[ElasticsearchBulkItemError] = []
        items = payload.get("items", [])
        if not isinstance(items, list):
            return [
                ElasticsearchBulkItemError(
                    0,
                    self.operation,
                    None,
                    0,
                    "invalid_response",
                    "bulk response items must be a list",
                )
            ]
        for index, wrapper in enumerate(items):
            if not isinstance(wrapper, Mapping) or len(wrapper) != 1:
                failures.append(
                    ElasticsearchBulkItemError(
                        index,
                        self.operation,
                        None,
                        0,
                        "invalid_response",
                        "bulk response item is invalid",
                    )
                )
                continue
            operation, item = next(iter(wrapper.items()))
            if not isinstance(item, Mapping):
                failures.append(
                    ElasticsearchBulkItemError(
                        index,
                        str(operation),
                        None,
                        0,
                        "invalid_response",
                        "bulk response item details are invalid",
                    )
                )
                continue
            status = int(item.get("status", 0))
            if 200 <= status < 300:
                continue
            error = item.get("error") or {}
            reason = error.get("reason") if isinstance(error, Mapping) else str(error)
            failures.append(
                ElasticsearchBulkItemError(
                    action_index=index,
                    operation=str(operation),
                    document_id=item.get("_id"),
                    status=status,
                    error_type=error.get("type")
                    if isinstance(error, Mapping)
                    else None,
                    reason=self.connector.redact(reason or "bulk item failed")[:500],
                )
            )
        return failures

    def _error_reason(self, error: Any) -> str:
        if isinstance(error, Mapping):
            reason = error.get("reason") or error.get("type") or "request failed"
        else:
            reason = error or "request failed"
        return self.connector.redact(reason)[:500]

    def _original_item_errors(
        self,
        failures: list[ElasticsearchBulkItemError],
        pending_indexes: list[int],
    ) -> list[ElasticsearchBulkItemError]:
        return [
            ElasticsearchBulkItemError(
                action_index=pending_indexes[item.action_index]
                if 0 <= item.action_index < len(pending_indexes)
                else item.action_index,
                operation=item.operation,
                document_id=item.document_id,
                status=item.status,
                error_type=item.error_type,
                reason=item.reason,
            )
            for item in failures
        ]

    def _params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "refresh": str(self.refresh).lower()
            if isinstance(self.refresh, bool)
            else self.refresh
        }
        if self.pipeline is not None:
            params["pipeline"] = self.pipeline
        return params

    async def _send_chunk(self, body: bytes) -> None:
        import asyncio
        import random

        pending = body
        pending_indexes = list(range(len(self._action_documents(body))))
        partial_success = False
        for attempt in range(self.max_retries + 1):
            status, payload = await self.connector.request_ndjson(
                "/_bulk", pending, params=self._params()
            )
            if status < 200 or status >= 300:
                failure = ElasticsearchBulkItemError(
                    pending_indexes[0],
                    self.operation,
                    None,
                    status,
                    None,
                    self._error_reason(payload.get("error", "request failed")),
                )
                if status in {429, 502, 503, 504} and attempt < self.max_retries:
                    await asyncio.sleep(
                        (0.05 * (2**attempt)) + random.uniform(0.0, 0.025)
                    )
                    continue
                raise ElasticsearchBulkError([failure], partial_success=partial_success)
            items = payload.get("items")
            expected_items = len(self._action_documents(pending))
            if not isinstance(items, list) or len(items) != expected_items:
                acknowledged = 0
                if isinstance(items, list):
                    for wrapper in items:
                        if isinstance(wrapper, Mapping) and len(wrapper) == 1:
                            item = next(iter(wrapper.values()))
                            if (
                                isinstance(item, Mapping)
                                and 200 <= int(item.get("status", 0)) < 300
                            ):
                                acknowledged += 1
                raise ElasticsearchBulkError(
                    [
                        ElasticsearchBulkItemError(
                            pending_indexes[0],
                            self.operation,
                            None,
                            0,
                            "invalid_response",
                            "bulk response item count did not match request",
                        )
                    ],
                    partial_success=partial_success or acknowledged > 0,
                )
            failures = self._parse_items(payload)
            if not failures and not payload.get("errors", False):
                return
            if not failures:
                failures = [
                    ElasticsearchBulkItemError(
                        pending_indexes[0],
                        self.operation,
                        None,
                        0,
                        "invalid_response",
                        "bulk response reported errors without a failed item",
                    )
                ]
            partial_success = partial_success or len(failures) < len(items)
            retryable_indexes = [
                item.action_index
                for item in failures
                if item.status in {429, 502, 503, 504}
            ]
            permanent = [
                item for item in failures if item.action_index not in retryable_indexes
            ]
            if permanent or not retryable_indexes or attempt == self.max_retries:
                raise ElasticsearchBulkError(
                    self._original_item_errors(failures, pending_indexes),
                    partial_success=partial_success,
                )
            actions = self._action_documents(pending)
            pending = b"".join(actions[index] for index in retryable_indexes)
            pending_indexes = [pending_indexes[index] for index in retryable_indexes]
            await asyncio.sleep((0.05 * (2**attempt)) + random.uniform(0.0, 0.025))
        raise AssertionError("bulk retry loop exhausted without returning or raising")

    async def send(self, envelope: Envelope) -> None:
        try:
            documents = _logical_document_views(envelope.body)
            replay_safe = (
                self.operation == "index"
                and self.id_field is not None
                and all(self.id_field in document for document in documents)
            )
            chunks = self._iter_encoded_chunks(envelope.body)
        except (TypeError, ValueError) as exc:
            raise ConnectorOperationError(
                backend="elasticsearch",
                operation=ConnectorOperation.SEND,
                kind=ConnectorErrorKind.PERMANENT,
                source_name=self.name,
                cause=exc,
            ) from None
        committed_chunks = 0
        try:
            for chunk in chunks:
                await self._send_chunk(chunk)
                committed_chunks += 1
        except ElasticsearchBulkError as exc:
            base_kind = (
                classify_elasticsearch_status(exc.items[0].status)
                if exc.items
                else ConnectorErrorKind.PERMANENT
            )
            kind = (
                ConnectorErrorKind.UNCERTAIN
                if (committed_chunks or exc.partial_success) and not replay_safe
                else base_kind
            )
            raise ConnectorOperationError(
                backend="elasticsearch",
                operation=ConnectorOperation.SEND,
                kind=kind,
                source_name=self.name,
                cause=exc,
            ) from None
        except (TypeError, ValueError) as exc:
            raise ConnectorOperationError(
                backend="elasticsearch",
                operation=ConnectorOperation.SEND,
                kind=ConnectorErrorKind.PERMANENT,
                source_name=self.name,
                cause=exc,
            ) from None
        except Exception as exc:
            kind = classify_elasticsearch_exception(exc)
            if kind is None:
                raise
            if committed_chunks and not replay_safe:
                kind = ConnectorErrorKind.UNCERTAIN
            raise ConnectorOperationError(
                backend="elasticsearch",
                operation=ConnectorOperation.SEND,
                kind=kind,
                source_name=self.name,
                cause=redacted_es_cause(exc, secrets=self.connector._secret_tokens()),
            ) from None
