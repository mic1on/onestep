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
    def __init__(self, hosts: str | list[str], **options: Any) -> None:
        self.hosts = [hosts] if isinstance(hosts, str) else list(hosts)
        self.options = dict(options)

    def bulk_sink(self, *, index: str, **options: Any) -> "ElasticsearchBulkSink":
        return ElasticsearchBulkSink(connector=self, index=index, **options)


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
