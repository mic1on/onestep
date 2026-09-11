from __future__ import annotations

import asyncio
from typing import Any

from onestep import Envelope
from onestep_feishu_bitable import FeishuBitableConnector


def _make_sink(connector: FeishuBitableConnector) -> Any:
    return connector.table_sink(
        app_token="app-token",
        table_id="projects",
        mode="insert",
        match_fields=["编号"],
        batch_size=10,
        insert_key_index=True,
        relations={
            "关联企业": {
                "from": "企业名称",
                "table_id": "enterprise",
                "key": "企业名称",
                "on_missing": "empty",
                "cache": "eager",
            }
        },
    )


def test_indexed_insert_resolves_relations_before_write() -> None:
    """Indexed insert resolves relation business values to record ids before the
    batch create, reusing the eager relation cache with zero search."""

    async def scenario() -> None:
        connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")
        sink = _make_sink(connector)

        # Simulate open(): insert index empty + eager relation cache preloaded.
        sink._insert_keys = set()
        sink._index_loaded = True
        sink._relation_caches["关联企业"] = {"企业A": "rec_enterprise_A"}

        created_records: list[dict[str, Any]] = []

        async def fake_batch_create(**kwargs: Any) -> dict[str, Any]:
            records = kwargs.get("records", [])
            created_records.extend(dict(r) for r in records)
            return {"records": [{"record_id": f"new-{i}", "fields": dict(r)} for i, r in enumerate(records)]}

        connector.batch_create_records = fake_batch_create  # type: ignore[assignment]

        await sink.send(Envelope(body={"编号": "A-1", "企业名称": "企业A"}))
        await sink.close()

        assert len(created_records) == 1
        # batch_create_records receives the raw field dicts (not {"fields": ...}).
        fields = created_records[0]
        # Relation field holds the resolved record id, not the business value.
        assert fields["关联企业"] == ["rec_enterprise_A"]
        # The consumed source field is removed.
        assert "企业名称" not in fields
        # The match field is preserved.
        assert fields["编号"] == "A-1"

    asyncio.run(scenario())


def test_indexed_insert_skips_relation_resolution_for_existing_key() -> None:
    """A key already in the insert index returns without resolving relations."""

    async def scenario() -> None:
        connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")
        sink = _make_sink(connector)

        sink._insert_keys = {"A-1"}
        sink._index_loaded = True
        sink._relation_caches["关联企业"] = {}

        search_calls: list[dict[str, Any]] = []

        async def fake_search(**kwargs: Any) -> dict[str, Any]:
            search_calls.append(kwargs)
            return {"items": [], "has_more": False}

        connector.search_records = fake_search  # type: ignore[assignment]

        # Existing key: returns before any relation resolution.
        await sink.send(Envelope(body={"编号": "A-1", "企业名称": "企业A"}))
        await sink.close()

        assert search_calls == []

    asyncio.run(scenario())
