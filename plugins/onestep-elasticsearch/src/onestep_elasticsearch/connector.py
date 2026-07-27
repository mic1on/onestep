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
