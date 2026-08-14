from __future__ import annotations

import asyncio
from typing import Any

import pytest

from onestep.resilience import ConnectorOperationError
from onestep_feishu_bitable.connector import _canonical_insert_key, FeishuBitablePayloadError
from onestep_feishu_bitable import FeishuBitableConnector


def test_feishu_insert_index_canonicalizes_source_and_destination_keys() -> None:
    """Verify _canonical_insert_key handles all type cases."""
    assert _canonical_insert_key("hello") == "hello"
    assert _canonical_insert_key("  spaced  ") == "spaced"
    assert _canonical_insert_key(42) == "42"
    assert _canonical_insert_key(3.14) == "3.14"
    assert _canonical_insert_key(True) == "True"
    assert _canonical_insert_key(False) == "False"
    # Non-finite float -> permanent error
    with pytest.raises(FeishuBitablePayloadError):
        _canonical_insert_key(float("nan"))
    with pytest.raises(FeishuBitablePayloadError):
        _canonical_insert_key(float("inf"))
    with pytest.raises(FeishuBitablePayloadError):
        _canonical_insert_key(float("-inf"))
    # Empty/missing -> permanent error
    with pytest.raises(FeishuBitablePayloadError):
        _canonical_insert_key("")
    with pytest.raises(FeishuBitablePayloadError):
        _canonical_insert_key("  ")
    with pytest.raises(FeishuBitablePayloadError):
        _canonical_insert_key(None)
    # Unsupported type -> permanent error
    with pytest.raises(FeishuBitablePayloadError):
        _canonical_insert_key({"key": "val"})
    with pytest.raises(FeishuBitablePayloadError):
        _canonical_insert_key([1, 2])


def test_feishu_insert_index_open_pages_match_field_only() -> None:
    """Startup scan pages only the match field into a bounded set."""
    async def scenario() -> None:
        sink_calls: list[dict[str, Any]] = []

        async def fake_search_records(**kwargs: Any) -> dict[str, Any]:
            sink_calls.append(
                {
                    "field_names": kwargs.get("body", {}).get("field_names"),
                    "page_size": kwargs.get("page_size"),
                    "page_token": kwargs.get("page_token"),
                }
            )
            token = kwargs.get("page_token")
            if token is None:
                return {
                    "items": [
                        {"fields": {"编号": "A-1"}},
                        {"fields": {"编号": "A-2"}},
                    ],
                    "has_more": True,
                    "page_token": "p2",
                }
            return {
                "items": [
                    {"fields": {"编号": "A-3"}},
                ],
                "has_more": False,
            }

        connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")
        connector.search_records = fake_search_records  # type: ignore[assignment]
        sink = connector.table_sink(
            app_token="app-token",
            table_id="tbl",
            mode="insert",
            match_fields=["编号"],
            batch_size=10,
            insert_key_index=True,
            insert_index_page_size=2,
            insert_index_max_pages=5,
        )
        assert sink._insert_keys is None
        await sink.open()
        assert sink._insert_keys == {"A-1", "A-2", "A-3"}
        assert sink._index_loaded is True
        assert len(sink_calls) == 2
        for call in sink_calls:
            assert call["field_names"] == ["编号"]

    asyncio.run(scenario())


def test_feishu_insert_index_open_rejects_truncated_scan() -> None:
    """Startup fails if Feishu reports more pages than max_pages."""
    async def scenario() -> None:
        async def fake_search_records(**kwargs: Any) -> dict[str, Any]:
            return {
                "items": [{"fields": {"编号": "K-1"}}],
                "has_more": True,
                "page_token": "next",
            }

        connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")
        connector.search_records = fake_search_records  # type: ignore[assignment]
        sink = connector.table_sink(
            app_token="app-token",
            table_id="tbl",
            mode="insert",
            match_fields=["编号"],
            batch_size=10,
            insert_key_index=True,
            insert_index_page_size=1,
            insert_index_max_pages=1,
        )
        with pytest.raises(ConnectorOperationError) as excinfo:
            await sink.open()
        msg = str(excinfo.value)
        assert "insert_index_max_pages=1" in msg
        # Privacy: no app token in error
        assert "app-token" not in msg
        assert sink._insert_keys is None

    asyncio.run(scenario())


def test_feishu_insert_index_open_rejects_duplicate_page_token() -> None:
    """Startup fails if page_token does not advance."""
    async def scenario() -> None:
        async def fake_search_records(**kwargs: Any) -> dict[str, Any]:
            return {
                "items": [{"fields": {"编号": "K-1"}}],
                "has_more": True,
                "page_token": "stuck",
            }

        connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")
        connector.search_records = fake_search_records  # type: ignore[assignment]
        sink = connector.table_sink(
            app_token="app-token",
            table_id="tbl",
            mode="insert",
            match_fields=["编号"],
            batch_size=10,
            insert_key_index=True,
            insert_index_page_size=1,
            insert_index_max_pages=5,
        )
        with pytest.raises(ConnectorOperationError):
            await sink.open()

    asyncio.run(scenario())


def test_feishu_insert_index_counts_missing_and_duplicate_keys() -> None:
    """Missing key records and duplicates are counted without logging values."""
    async def scenario() -> None:
        async def fake_search_records(**kwargs: Any) -> dict[str, Any]:
            return {
                "items": [
                    {"fields": {"编号": "K-1"}},
                    {"fields": {"编号": "K-1"}},  # duplicate
                    {"fields": {}},  # missing key
                    {"fields": None},  # missing fields
                    {"not_fields": True},  # no fields key
                ],
                "has_more": False,
            }

        connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")
        connector.search_records = fake_search_records  # type: ignore[assignment]
        sink = connector.table_sink(
            app_token="app-token",
            table_id="tbl",
            mode="insert",
            match_fields=["编号"],
            batch_size=10,
            insert_key_index=True,
            insert_index_page_size=10,
            insert_index_max_pages=5,
        )
        await sink.open()
        assert sink._insert_keys == {"K-1"}
        assert sink._scan_duplicate_keys == 1
        assert sink._scan_missing_key_records >= 1

    asyncio.run(scenario())