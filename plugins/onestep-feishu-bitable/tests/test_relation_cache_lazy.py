from __future__ import annotations

import asyncio
from typing import Any

import pytest

from onestep import Envelope
from onestep.resilience import ConnectorOperationError
from onestep_feishu_bitable import FeishuBitableConnector


class _CountingConnector:
    """Mock connector that counts search/create calls per business key.

    ``created_values`` tracks *relation-table* creates only (the ones the cache
    backfills from); ``sink_writes`` tracks writes to the project table.
    """

    def __init__(
        self,
        *,
        search_results: dict[str, list[str]] | None = None,
        relation_table: str = "companies",
        target_search_results: list[list[dict[str, Any]]] | None = None,
    ) -> None:
        self.searched_values: list[str] = []
        self.created_values: list[str] = []
        self.batch_created: list[dict[str, Any]] = []
        self.sink_writes: list[dict[str, Any]] = []
        self.search_results = search_results if search_results is not None else {}
        self.relation_table = relation_table
        # Queued results for target-table (match) searches, consumed in order.
        self.target_search_results = target_search_results if target_search_results is not None else []

    async def search_records(self, **kwargs: Any) -> dict[str, Any]:
        if kwargs["table_id"] != self.relation_table:
            # Target-table match lookup (upsert/update/insert).
            items = self.target_search_results.pop(0) if self.target_search_results else []
            return {"items": items, "has_more": False}
        value = kwargs["body"]["filter"]["conditions"][0]["value"][0]
        self.searched_values.append(value)
        record_ids = self.search_results.get(value)
        if record_ids is None:
            record_ids = [f"rec-{value.lower()}"]
        return {"items": [{"record_id": rid} for rid in record_ids]}

    async def create_record(self, **kwargs: Any) -> dict[str, Any]:
        fields = kwargs["fields"]
        if "name" in fields:
            # Create on the relation (companies) table.
            value = fields["name"]
            self.created_values.append(value)
            return {"record": {"record_id": f"created-{str(value).lower()}"}}
        # Create on the target (projects) table.
        self.sink_writes.append(kwargs)
        return {"record": {"record_id": "project-record"}}

    async def update_record(self, **kwargs: Any) -> dict[str, Any]:
        self.sink_writes.append(kwargs)
        return {"record": {"record_id": kwargs["record_id"]}}

    async def batch_create_records(self, **kwargs: Any) -> dict[str, Any]:
        self.batch_created.append(kwargs)
        if kwargs["table_id"] != self.relation_table:
            # Target-table batch create: echo ids, no relation cache involvement.
            return {
                "records": [
                    {"record_id": f"project-{i}"} for i, _ in enumerate(kwargs["records"])
                ]
            }
        records = [
            {
                "record_id": f"created-{record.get('name', 'x').lower()}",
                "fields": dict(record),
            }
            for record in kwargs["records"]
        ]
        return {"records": records}


def _build_sink(connector: _CountingConnector, *, cache: str | None, on_missing: str = "error",
                batch_size: int = 1, mode: str = "create", key: str = "name"):
    bitable = FeishuBitableConnector(app_id="app-id", app_secret="secret")
    bitable.search_records = connector.search_records  # type: ignore[method-assign]
    bitable.create_record = connector.create_record  # type: ignore[method-assign]
    bitable.batch_create_records = connector.batch_create_records  # type: ignore[method-assign]
    bitable.update_record = connector.update_record  # type: ignore[method-assign]
    relation: dict[str, Any] = {
        "from": "company_names",
        "table_id": "companies",
        "key": key,
        "on_missing": on_missing,
    }
    if cache is not None:
        relation["cache"] = cache
    options: dict[str, Any] = {
        "app_token": "project-app",
        "table_id": "projects",
        "mode": mode,
        "batch_size": batch_size,
        "relations": {"companies": relation},
    }
    # upsert/update/insert require exactly one match field; create forbids it.
    if mode != "create":
        options["match_fields"] = ["project_id"]
    return bitable.table_sink(**options)


def test_lazy_single_path_caches_hits_across_sends() -> None:
    async def scenario() -> None:
        connector = _CountingConnector()
        sink = _build_sink(connector, cache="lazy")
        await sink.send(Envelope(body={"company_names": "A"}))
        await sink.send(Envelope(body={"company_names": "A"}))
        # Second send is a cache hit: no second search.
        assert connector.searched_values == ["A"]
        assert sink._relation_caches["companies"] == {"A": "rec-a"}
        assert connector.sink_writes[1]["fields"]["companies"] == ["rec-a"]

    asyncio.run(scenario())


def test_lazy_batch_path_reuses_cache_across_flushes() -> None:
    async def scenario() -> None:
        connector = _CountingConnector()
        sink = _build_sink(connector, cache="lazy", batch_size=2, mode="upsert")
        await sink.send(Envelope(body={"project_id": "P-1", "company_names": "A"}))
        await sink.close()
        await sink.send(Envelope(body={"project_id": "P-2", "company_names": "A"}))
        await sink.close()
        assert connector.searched_values == ["A"]

    asyncio.run(scenario())


def test_lazy_on_missing_empty_does_not_cache_absence() -> None:
    async def scenario() -> None:
        connector = _CountingConnector(search_results={"A": []})
        sink = _build_sink(connector, cache="lazy", on_missing="empty")
        await sink.send(Envelope(body={"company_names": "A"}))
        await sink.send(Envelope(body={"company_names": "A"}))
        # A miss is never cached: both sends must re-search.
        assert connector.searched_values == ["A", "A"]

    asyncio.run(scenario())


def test_lazy_on_missing_error_does_not_cache_absence() -> None:
    async def scenario() -> None:
        connector = _CountingConnector(search_results={"A": []})
        sink = _build_sink(connector, cache="lazy", on_missing="error")
        with pytest.raises(ConnectorOperationError):
            await sink.send(Envelope(body={"company_names": "A"}))
        assert connector.searched_values == ["A"]
        assert sink._relation_caches["companies"] == {}

        connector.search_results["A"] = ["rec-a"]
        await sink.send(Envelope(body={"company_names": "A"}))
        assert connector.searched_values == ["A", "A"]
        assert sink._relation_caches["companies"] == {"A": "rec-a"}

    asyncio.run(scenario())


def test_lazy_multiple_matches_is_error_and_does_not_pollute_cache() -> None:
    async def scenario() -> None:
        connector = _CountingConnector(search_results={"A": ["rec-1", "rec-2"]})
        sink = _build_sink(connector, cache="lazy")
        with pytest.raises(ConnectorOperationError):
            await sink.send(Envelope(body={"company_names": "A"}))
        assert sink._relation_caches["companies"] == {}
        with pytest.raises(ConnectorOperationError):
            await sink.send(Envelope(body={"company_names": "A"}))
        assert connector.searched_values == ["A", "A"]

    asyncio.run(scenario())


def test_lazy_create_backfills_cache_and_skips_later_search_and_create() -> None:
    async def scenario() -> None:
        connector = _CountingConnector(search_results={"A": []})
        sink = _build_sink(connector, cache="lazy", on_missing="create")
        await sink.send(Envelope(body={"company_names": "A"}))
        assert connector.created_values == ["A"]
        assert sink._relation_caches["companies"] == {"A": "created-a"}
        searches_after_first = list(connector.searched_values)

        await sink.send(Envelope(body={"company_names": "A"}))
        # Second send is a cache hit: no additional search, no additional create.
        assert connector.searched_values == searches_after_first
        assert connector.created_values == ["A"]

    asyncio.run(scenario())


def test_cache_none_regression_searches_every_time() -> None:
    async def scenario() -> None:
        connector = _CountingConnector()
        sink = _build_sink(connector, cache=None)
        await sink.send(Envelope(body={"company_names": "A"}))
        await sink.send(Envelope(body={"company_names": "A"}))
        # Unchanged legacy behaviour: no cache, one search per send.
        assert connector.searched_values == ["A", "A"]
        assert sink._relation_caches == {}

    asyncio.run(scenario())


def test_cache_none_batch_path_searches_every_flush() -> None:
    async def scenario() -> None:
        connector = _CountingConnector()
        sink = _build_sink(connector, cache=None, batch_size=2, mode="upsert")
        await sink.send(Envelope(body={"project_id": "P-1", "company_names": "A"}))
        await sink.close()
        await sink.send(Envelope(body={"project_id": "P-2", "company_names": "A"}))
        await sink.close()
        assert connector.searched_values == ["A", "A"]

    asyncio.run(scenario())


def test_lazy_batch_create_backfills_instance_cache() -> None:
    async def scenario() -> None:
        connector = _CountingConnector(search_results={"A": []})
        sink = _build_sink(connector, cache="lazy", on_missing="create", batch_size=2, mode="upsert")
        await sink.send(Envelope(body={"project_id": "P-1", "company_names": "A"}))
        await sink.close()
        assert sink._relation_caches["companies"] == {"A": "created-a"}
        await sink.send(Envelope(body={"project_id": "P-2", "company_names": "A"}))
        await sink.close()
        # Second batch is a cache hit: no relation search, no relation create.
        assert connector.searched_values == ["A"]
        relation_creates = [c for c in connector.batch_created if c["table_id"] == "companies"]
        assert len(relation_creates) == 1

    asyncio.run(scenario())


def test_caches_are_per_sink_instance() -> None:
    async def scenario() -> None:
        connector = _CountingConnector()
        first = _build_sink(connector, cache="lazy")
        second = _build_sink(connector, cache="lazy")
        await first.send(Envelope(body={"company_names": "A"}))
        await second.send(Envelope(body={"company_names": "A"}))
        # No cross-sink sharing by design.
        assert connector.searched_values == ["A", "A"]

    asyncio.run(scenario())
