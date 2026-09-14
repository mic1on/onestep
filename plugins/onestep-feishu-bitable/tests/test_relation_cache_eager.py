from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from onestep import Envelope
from onestep.resilience import ConnectorErrorKind, ConnectorOperation, ConnectorOperationError
from onestep_feishu_bitable import FeishuBitableConnector


class _EagerConnector:
    """Mock connector serving relation-table scan pages and target writes."""

    def __init__(
        self,
        *,
        pages: dict[str, list[dict[str, Any]]],
        relation_tables: tuple[str, ...] = ("companies",),
        target_table: str = "projects",
        total: int | None = None,
    ) -> None:
        self.pages = pages
        self.relation_tables = relation_tables
        self.target_table = target_table
        self.total = total
        self.scan_calls: list[dict[str, Any]] = []
        self.relation_search_values: list[str] = []
        self.created_values: list[str] = []
        self.sink_writes: list[dict[str, Any]] = []

    async def search_records(self, **kwargs: Any) -> dict[str, Any]:
        table_id = kwargs["table_id"]
        if table_id in self.relation_tables:
            body = kwargs.get("body", {})
            if "field_names" in body:
                # Startup scan page.
                self.scan_calls.append(
                    {
                        "table_id": table_id,
                        "field_names": body["field_names"],
                        "page_size": kwargs.get("page_size"),
                        "page_token": kwargs.get("page_token"),
                        "operation": kwargs.get("operation"),
                    }
                )
                token = kwargs.get("page_token") or "page-1"
                page = self.pages.get(table_id, [])
                index = int(str(token).split("-")[-1]) - 1
                result: dict[str, Any] = {}
                if self.total is not None:
                    result["total"] = self.total
                if index >= len(page):
                    result.update({"items": [], "has_more": False})
                    return result
                result.update(
                    {
                        "items": page[index],
                        "has_more": index + 1 < len(page),
                        "page_token": f"page-{index + 2}",
                    }
                )
                return result
            # Runtime relation lookup.
            value = body["filter"]["conditions"][0]["value"][0]
            self.relation_search_values.append(value)
            if value.startswith("known"):
                return {"items": [{"record_id": f"found-{value}"}]}
            return {"items": []}
        # Target-table match lookup.
        return {"items": [], "has_more": False}

    async def create_record(self, **kwargs: Any) -> dict[str, Any]:
        fields = kwargs["fields"]
        if "name" in fields:
            self.created_values.append(fields["name"])
            return {"record": {"record_id": f"created-{str(fields['name']).lower()}"}}
        self.sink_writes.append(kwargs)
        return {"record": {"record_id": "project-record"}}


def _build_sink(
    connector: _EagerConnector,
    *,
    caches: dict[str, dict[str, Any]],
    page_size: int = 500,
    max_pages: int = 200,
    mode: str = "create",
):
    bitable = FeishuBitableConnector(app_id="app-id", app_secret="secret")
    bitable.search_records = connector.search_records  # type: ignore[method-assign]
    bitable.create_record = connector.create_record  # type: ignore[method-assign]
    options: dict[str, Any] = {
        "app_token": "project-app",
        "table_id": "projects",
        "mode": mode,
        "insert_index_page_size": page_size,
        "insert_index_max_pages": max_pages,
        "relations": caches,
    }
    if mode != "create":
        options["match_fields"] = ["project_id"]
    return bitable.table_sink(**options)


def _relation(cache: str, *, on_missing: str = "error", key: str = "name", table_id: str = "companies"):
    return {
        "from": "company_names",
        "table_id": table_id,
        "key": key,
        "on_missing": on_missing,
        "cache": cache,
    }


def test_eager_open_pages_key_field_and_populates_cache() -> None:
    async def scenario() -> None:
        connector = _EagerConnector(
            pages={
                "companies": [
                    [{"record_id": "rec-1", "fields": {"name": "A"}}],
                    [{"record_id": "rec-2", "fields": {"name": "B"}}],
                ]
            }
        )
        sink = _build_sink(connector, caches={"companies": _relation("eager")}, page_size=2)
        await sink.open()

        assert len(connector.scan_calls) == 2
        assert connector.scan_calls[0]["field_names"] == ["name"]
        assert connector.scan_calls[0]["page_size"] == 2
        assert connector.scan_calls[0]["operation"] is ConnectorOperation.OPEN
        assert sink._relation_caches["companies"] == {"A": "rec-1", "B": "rec-2"}
        assert "companies" in sink._relation_eager_loaded

    asyncio.run(scenario())


def test_eager_rich_text_key_flattens_into_cache() -> None:
    """A text (type=1) relation key returned as rich-text segments must build the
    eager cache instead of being skipped as an unsupported shape (issue #175)."""

    async def scenario() -> None:
        connector = _EagerConnector(
            pages={
                "companies": [
                    [
                        {
                            "record_id": "rec-1",
                            "fields": {"name": [{"text": "某公司", "type": "text"}]},
                        },
                        {
                            "record_id": "rec-2",
                            "fields": {"name": [{"text": "乙公司", "type": "text"}]},
                        },
                    ]
                ]
            }
        )
        sink = _build_sink(connector, caches={"companies": _relation("eager")}, page_size=2)
        await sink.open()

        assert sink._relation_caches["companies"] == {
            "某公司": "rec-1",
            "乙公司": "rec-2",
        }
        assert "companies" in sink._relation_eager_loaded

        # A rich-text key now resolves from the cache with zero runtime search.
        await sink.send(Envelope(body={"company_names": [{"text": "某公司", "type": "text"}]}))
        assert connector.relation_search_values == []
        assert connector.sink_writes[0]["fields"]["companies"] == ["rec-1"]

    asyncio.run(scenario())


def test_eager_empty_cache_warns_when_records_were_skipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When the source table is non-empty but no key normalized, the empty
    eager snapshot must warn instead of silently dropping every relation."""

    async def scenario() -> None:
        connector = _EagerConnector(
            pages={
                "companies": [
                    [
                        {"record_id": "rec-1"},
                        {"record_id": "rec-2", "fields": {}},
                        {"record_id": "rec-3", "fields": {"name": "  "}},
                    ]
                ]
            }
        )
        sink = _build_sink(connector, caches={"companies": _relation("eager")})
        with caplog.at_level(logging.WARNING, logger="onestep_feishu_bitable.connector"):
            await sink.open()

        assert sink._relation_caches["companies"] == {}
        assert "companies" in sink._relation_eager_loaded

    asyncio.run(scenario())
    records = [r for r in caplog.records if getattr(r, "event", None) == "feishu_relation_cache_scan"]
    warns = [r for r in records if getattr(r, "phase", None) == "warn_empty"]
    assert len(warns) == 1
    assert warns[0].target_field == "companies"
    assert "loaded no keys" in warns[0].error


def test_eager_hits_need_no_runtime_search() -> None:
    async def scenario() -> None:
        connector = _EagerConnector(
            pages={"companies": [[{"record_id": "rec-1", "fields": {"name": "A"}}]]}
        )
        sink = _build_sink(connector, caches={"companies": _relation("eager")})
        await sink.open()
        await sink.send(Envelope(body={"company_names": "A"}))
        assert connector.relation_search_values == []
        assert connector.sink_writes[0]["fields"]["companies"] == ["rec-1"]

    asyncio.run(scenario())


def test_eager_multiple_fields_scan_in_configuration_order() -> None:
    async def scenario() -> None:
        connector = _EagerConnector(
            pages={
                "companies": [[{"record_id": "rec-1", "fields": {"name": "A"}}]],
                "depts": [[{"record_id": "rec-2", "fields": {"code": "D1"}}]],
            },
            relation_tables=("companies", "depts"),
        )
        sink = _build_sink(
            connector,
            caches={
                "companies": _relation("eager"),
                "depts": _relation("eager", key="code", table_id="depts"),
            },
        )
        await sink.open()
        assert [call["table_id"] for call in connector.scan_calls] == ["companies", "depts"]
        assert sink._relation_caches["depts"] == {"D1": "rec-2"}

    asyncio.run(scenario())


def test_eager_missing_or_empty_key_is_skipped_and_counted() -> None:
    async def scenario() -> None:
        connector = _EagerConnector(
            pages={
                "companies": [
                    [
                        {"record_id": "rec-1", "fields": {"name": "A"}},
                        {"record_id": "rec-2", "fields": {}},
                        {"record_id": "rec-3", "fields": {"name": "  "}},
                        {"record_id": "rec-4"},
                    ]
                ]
            }
        )
        sink = _build_sink(connector, caches={"companies": _relation("eager")})
        await sink.open()
        assert sink._relation_caches["companies"] == {"A": "rec-1"}

    asyncio.run(scenario())


def test_eager_duplicate_key_last_wins() -> None:
    async def scenario() -> None:
        connector = _EagerConnector(
            pages={
                "companies": [
                    [
                        {"record_id": "rec-1", "fields": {"name": "A"}},
                        {"record_id": "rec-2", "fields": {"name": "A"}},
                    ]
                ]
            }
        )
        sink = _build_sink(connector, caches={"companies": _relation("eager")})
        await sink.open()
        assert sink._relation_caches["companies"] == {"A": "rec-2"}

    asyncio.run(scenario())


def test_eager_multi_key_cell_maps_every_key_to_record() -> None:
    async def scenario() -> None:
        connector = _EagerConnector(
            pages={
                "companies": [
                    [{"record_id": "rec-1", "fields": {"name": ["A", "B"]}}]
                ]
            }
        )
        sink = _build_sink(connector, caches={"companies": _relation("eager")})
        await sink.open()
        assert sink._relation_caches["companies"] == {"A": "rec-1", "B": "rec-1"}

    asyncio.run(scenario())


def test_eager_max_pages_exhausted_fails_open() -> None:
    async def scenario() -> None:
        connector = _EagerConnector(
            pages={
                "companies": [
                    [{"record_id": "rec-1", "fields": {"name": "A"}}],
                    [{"record_id": "rec-2", "fields": {"name": "B"}}],
                ]
            }
        )
        sink = _build_sink(
            connector, caches={"companies": _relation("eager")}, max_pages=1, page_size=1
        )
        with pytest.raises(ConnectorOperationError) as raised:
            await sink.open()
        assert raised.value.kind is ConnectorErrorKind.PERMANENT
        assert raised.value.operation is ConnectorOperation.OPEN
        assert "companies" not in sink._relation_eager_loaded

    asyncio.run(scenario())


def test_eager_page_token_not_advancing_fails_open() -> None:
    async def scenario() -> None:
        connector = _EagerConnector(pages={"companies": []})

        async def stuck_search(**kwargs: Any) -> dict[str, Any]:
            return {
                "items": [{"record_id": "rec-1", "fields": {"name": "A"}}],
                "has_more": True,
                "page_token": "same-token",
            }

        connector.search_records = stuck_search  # type: ignore[method-assign]
        bitable = FeishuBitableConnector(app_id="app-id", app_secret="secret")
        bitable.search_records = stuck_search  # type: ignore[method-assign]
        sink = bitable.table_sink(
            app_token="project-app",
            table_id="projects",
            mode="create",
            relations={"companies": _relation("eager")},
        )
        with pytest.raises(ConnectorOperationError) as raised:
            await sink.open()
        assert raised.value.kind is ConnectorErrorKind.PERMANENT

    asyncio.run(scenario())


def test_eager_scan_failure_is_wrapped_as_permanent_open_error() -> None:
    async def scenario() -> None:
        bitable = FeishuBitableConnector(app_id="app-id", app_secret="secret")

        async def boom(**kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("network down")

        bitable.search_records = boom  # type: ignore[method-assign]
        sink = bitable.table_sink(
            app_token="project-app",
            table_id="projects",
            mode="create",
            relations={"companies": _relation("eager")},
        )
        with pytest.raises(ConnectorOperationError) as raised:
            await sink.open()
        assert raised.value.kind is ConnectorErrorKind.PERMANENT
        assert raised.value.operation is ConnectorOperation.OPEN
        assert isinstance(raised.value.__cause__, RuntimeError)

    asyncio.run(scenario())


def test_eager_runtime_miss_falls_back_to_search_and_backfills() -> None:
    async def scenario() -> None:
        connector = _EagerConnector(
            pages={"companies": [[{"record_id": "rec-1", "fields": {"name": "A"}}]]}
        )
        sink = _build_sink(connector, caches={"companies": _relation("eager")})
        await sink.open()

        # A key added to Feishu after the scan: must fall back to search.
        await sink.send(Envelope(body={"company_names": "known-new"}))
        assert connector.relation_search_values == ["known-new"]
        assert sink._relation_caches["companies"]["known-new"] == "found-known-new"

        await sink.send(Envelope(body={"company_names": "known-new"}))
        assert connector.relation_search_values == ["known-new"]

    asyncio.run(scenario())


def test_eager_empty_miss_skips_search_and_leaves_unset() -> None:
    """on_missing=empty treats an eager snapshot miss as absence: zero search."""

    async def scenario() -> None:
        connector = _EagerConnector(
            pages={"companies": [[{"record_id": "rec-1", "fields": {"name": "A"}}]]}
        )
        sink = _build_sink(
            connector, caches={"companies": _relation("eager", on_missing="empty")}
        )
        await sink.open()

        # A key absent from the snapshot: empty policy leaves it unset, no search.
        await sink.send(Envelope(body={"company_names": "brand-new"}))
        assert connector.relation_search_values == []
        assert connector.sink_writes[0]["fields"]["companies"] == []

    asyncio.run(scenario())


def test_eager_error_miss_still_searches() -> None:
    """on_missing=error must still search on a miss: a post-snapshot key may
    resolve, and only a confirmed absence raises."""

    async def scenario() -> None:
        connector = _EagerConnector(
            pages={"companies": [[{"record_id": "rec-1", "fields": {"name": "A"}}]]}
        )
        sink = _build_sink(
            connector, caches={"companies": _relation("eager", on_missing="error")}
        )
        await sink.open()

        # "known-new" exists in Feishu but not the snapshot: search links it.
        await sink.send(Envelope(body={"company_names": "known-new"}))
        assert connector.relation_search_values == ["known-new"]
        assert connector.sink_writes[0]["fields"]["companies"] == ["found-known-new"]

    asyncio.run(scenario())


def test_eager_miss_does_not_duplicate_create_for_existing_record() -> None:
    """on_missing=create must re-search on a miss before creating anything."""

    async def scenario() -> None:
        connector = _EagerConnector(
            pages={"companies": [[{"record_id": "rec-1", "fields": {"name": "A"}}]]}
        )
        sink = _build_sink(
            connector, caches={"companies": _relation("eager", on_missing="create")}
        )
        await sink.open()

        # "known-new" exists in Feishu but was not in the startup snapshot.
        await sink.send(Envelope(body={"company_names": "known-new"}))
        assert connector.relation_search_values == ["known-new"]
        assert connector.created_values == []
        assert connector.sink_writes[0]["fields"]["companies"] == ["found-known-new"]

    asyncio.run(scenario())


def test_eager_genuinely_missing_key_creates_after_search() -> None:
    async def scenario() -> None:
        connector = _EagerConnector(pages={"companies": [[]]})
        sink = _build_sink(
            connector, caches={"companies": _relation("eager", on_missing="create")}
        )
        await sink.open()
        await sink.send(Envelope(body={"company_names": "brand-new"}))
        # A confirmed miss is searched before creating (never "absent because uncached").
        assert connector.relation_search_values
        assert set(connector.relation_search_values) == {"brand-new"}
        assert connector.created_values == ["brand-new"]
        assert sink._relation_caches["companies"]["brand-new"] == "created-brand-new"

        # Once cached, later sends skip search and create entirely.
        await sink.send(Envelope(body={"company_names": "brand-new"}))
        assert set(connector.relation_search_values) == {"brand-new"}
        assert connector.created_values == ["brand-new"]

    asyncio.run(scenario())


def test_lazy_field_is_not_scanned_on_open() -> None:
    async def scenario() -> None:
        connector = _EagerConnector(pages={"companies": [[]]})
        sink = _build_sink(connector, caches={"companies": _relation("lazy")})
        await sink.open()
        assert connector.scan_calls == []
        assert sink._relation_caches["companies"] == {}

    asyncio.run(scenario())


def test_open_is_idempotent_for_eager_scans() -> None:
    async def scenario() -> None:
        connector = _EagerConnector(
            pages={"companies": [[{"record_id": "rec-1", "fields": {"name": "A"}}]]}
        )
        sink = _build_sink(connector, caches={"companies": _relation("eager")})
        await sink.open()
        first_scan_count = len(connector.scan_calls)
        await sink.open()
        assert len(connector.scan_calls) == first_scan_count

    asyncio.run(scenario())


def test_eager_scan_emits_structured_log_without_secrets(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        connector = _EagerConnector(
            pages={
                "companies": [
                    [
                        {"record_id": "rec-1", "fields": {"name": "A"}},
                        {"record_id": "rec-2", "fields": {"name": "A"}},
                        {"record_id": "rec-3", "fields": {}},
                    ]
                ]
            },
            total=3,
        )
        sink = _build_sink(connector, caches={"companies": _relation("eager")})
        with caplog.at_level(logging.INFO, logger="onestep_feishu_bitable.connector"):
            await sink.open()

    asyncio.run(scenario())
    records = [r for r in caplog.records if getattr(r, "event", None) == "feishu_relation_cache_scan"]
    assert records, "expected a feishu_relation_cache_scan log record"
    done = [r for r in records if getattr(r, "phase", None) == "done"]
    assert len(done) == 1, "expected exactly one phase=done record"
    record = done[0]
    assert record.name == "onestep_feishu_bitable.connector"
    assert record.target_field == "companies"
    assert record.table_id == "companies"
    assert record.scan_pages == 1
    assert record.scan_keys == 1
    assert record.missing_key_records == 1
    assert record.duplicate_keys == 1
    assert record.outcome == "success"
    assert record.page_size == 500
    assert record.max_pages == 200
    assert record.total == 3
    assert isinstance(record.duration_s, float)


def test_eager_scan_does_not_log_secret_tokens(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        bitable = FeishuBitableConnector(app_id="app-id", app_secret="secret")

        async def scan(**kwargs: Any) -> dict[str, Any]:
            return {"items": [], "has_more": False}

        bitable.search_records = scan  # type: ignore[method-assign]
        sink = bitable.table_sink(
            app_token="super-secret-app-token",
            table_id="projects",
            mode="create",
            relations={
                "companies": {
                    "from": "company_names",
                    "table_id": "companies",
                    "key": "name",
                    "app_token": "relation-secret-token",
                    "cache": "eager",
                }
            },
        )
        with caplog.at_level(logging.INFO, logger="onestep_feishu_bitable.connector"):
            await sink.open()

    asyncio.run(scenario())
    joined = " ".join(str(r.__dict__) for r in caplog.records)
    assert "super-secret-app-token" not in joined
    assert "relation-secret-token" not in joined


def test_eager_scan_log_does_not_leak_key_values_or_record_ids(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The scan log must report only counts/metadata, never business data."""

    async def scenario() -> None:
        connector = _EagerConnector(
            pages={
                "companies": [
                    [
                        {"record_id": "secret-record-1", "fields": {"name": "SecretCo"}},
                        {"record_id": "secret-record-2", "fields": {"name": "AnotherCo"}},
                    ]
                ]
            }
        )
        sink = _build_sink(connector, caches={"companies": _relation("eager")})
        with caplog.at_level(logging.INFO, logger="onestep_feishu_bitable.connector"):
            await sink.open()

    asyncio.run(scenario())
    joined = " ".join(str(r.__dict__) for r in caplog.records)
    # Business key values and record ids must never appear in logs.
    assert "SecretCo" not in joined
    assert "AnotherCo" not in joined
    assert "secret-record-1" not in joined
    assert "secret-record-2" not in joined


def test_stale_cached_record_write_error_is_permanent_and_cache_not_evicted() -> None:
    """A cached record_id that Feishu rejects on write (deleted/renamed) surfaces
    as a PERMANENT error and does NOT auto-evict the cache entry.

    Per the design, cache invalidation is out of scope: the dangling record_id
    is surfaced by the write (RecordIdNotFound / LinkFieldConvFail) and converges
    on task restart, not by evicting the in-memory entry.
    """

    class _StaleWriteConnector(_EagerConnector):
        async def create_record(self, **kwargs: Any) -> dict[str, Any]:
            fields = kwargs["fields"]
            if "name" in fields:
                return {"record": {"record_id": f"created-{fields['name']}"}}
            # Target-table write carrying a stale relation record_id.
            raise ConnectorOperationError(
                backend="feishu_bitable",
                operation=ConnectorOperation.SEND,
                kind=ConnectorErrorKind.PERMANENT,
                source_name="test",
                retry_delay_s=1.0,
                message="LinkFieldConvFail: relation record not found",
            )

    async def scenario() -> None:
        connector = _StaleWriteConnector(
            pages={"companies": [[{"record_id": "rec-stale", "fields": {"name": "A"}}]]}
        )
        sink = _build_sink(connector, caches={"companies": _relation("eager")})
        await sink.open()
        assert sink._relation_caches["companies"] == {"A": "rec-stale"}

        with pytest.raises(ConnectorOperationError) as raised:
            await sink.send(Envelope(body={"company_names": "A"}))
        assert raised.value.kind is ConnectorErrorKind.PERMANENT
        # Cache entry is NOT auto-evicted; it stays for restart-time convergence.
        assert sink._relation_caches["companies"] == {"A": "rec-stale"}

    asyncio.run(scenario())


def test_cache_none_relations_are_never_scanned() -> None:
    async def scenario() -> None:
        connector = _EagerConnector(pages={"companies": [[]]})
        sink = _build_sink(connector, caches={"companies": _relation("none")})
        await sink.open()
        assert connector.scan_calls == []
        assert sink._relation_caches == {}

    asyncio.run(scenario())


def _scan_records(caplog: pytest.LogCaptureFixture) -> list[Any]:
    return [r for r in caplog.records if getattr(r, "event", None) == "feishu_relation_cache_scan"]


def test_eager_scan_emits_start_phase(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        connector = _EagerConnector(pages={"companies": [[]]})
        sink = _build_sink(connector, caches={"companies": _relation("eager")}, page_size=250, max_pages=50)
        with caplog.at_level(logging.INFO, logger="onestep_feishu_bitable.connector"):
            await sink.open()

    asyncio.run(scenario())
    records = _scan_records(caplog)
    starts = [r for r in records if getattr(r, "phase", None) == "start"]
    assert len(starts) == 1
    start = starts[0]
    assert start.target_field == "companies"
    assert start.table_id == "companies"
    assert start.page_size == 250
    assert start.max_pages == 50


def test_eager_scan_multi_page_page_count_and_total(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        connector = _EagerConnector(
            pages={
                "companies": [
                    [{"record_id": "rec-1", "fields": {"name": "A"}}],
                    [{"record_id": "rec-2", "fields": {"name": "B"}}],
                    [{"record_id": "rec-3", "fields": {"name": "C"}}],
                ]
            },
            total=3,
        )
        sink = _build_sink(connector, caches={"companies": _relation("eager")})
        with caplog.at_level(logging.INFO, logger="onestep_feishu_bitable.connector"):
            await sink.open()

    asyncio.run(scenario())
    records = _scan_records(caplog)
    pages = [r for r in records if getattr(r, "phase", None) == "page"]
    done = [r for r in records if getattr(r, "phase", None) == "done"]
    # Three pages -> exactly three page logs.
    assert len(pages) == 3
    # page logs carry page_number, page_records, scan_keys, total, has_more.
    assert [p.page_number for p in pages] == [1, 2, 3]
    assert [p.page_records for p in pages] == [1, 1, 1]
    # scan_keys accumulates monotonically.
    assert [p.scan_keys for p in pages] == [1, 2, 3]
    assert [p.total for p in pages] == [3, 3, 3]
    assert [p.has_more for p in pages] == [True, True, False]
    # done carries the same total and the final cumulative scan_keys.
    assert len(done) == 1
    assert done[0].total == 3
    assert done[0].scan_keys == 3
    assert done[0].scan_pages == 3


def test_eager_scan_done_phase_has_total_and_cumulative(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        connector = _EagerConnector(pages={"companies": [[]]}, total=0)
        sink = _build_sink(connector, caches={"companies": _relation("eager")})
        with caplog.at_level(logging.INFO, logger="onestep_feishu_bitable.connector"):
            await sink.open()

    asyncio.run(scenario())
    records = _scan_records(caplog)
    done = [r for r in records if getattr(r, "phase", None) == "done"]
    assert len(done) == 1
    assert done[0].total == 0
    assert done[0].outcome == "success"
    assert done[0].scan_pages == 1


def test_eager_scan_error_max_pages_exhausted(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        connector = _EagerConnector(
            pages={
                "companies": [
                    [{"record_id": "rec-1", "fields": {"name": "A"}}],
                    [{"record_id": "rec-2", "fields": {"name": "B"}}],
                ]
            }
        )
        sink = _build_sink(connector, caches={"companies": _relation("eager")}, max_pages=1, page_size=1)
        with caplog.at_level(logging.INFO, logger="onestep_feishu_bitable.connector"):
            with pytest.raises(ConnectorOperationError):
                await sink.open()

    asyncio.run(scenario())
    records = _scan_records(caplog)
    errors = [r for r in records if getattr(r, "phase", None) == "error"]
    assert len(errors) == 1
    assert "exceeded" in errors[0].error


def test_eager_scan_error_page_token_not_advancing(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        async def stuck_search(**kwargs: Any) -> dict[str, Any]:
            return {
                "items": [{"record_id": "rec-1", "fields": {"name": "A"}}],
                "has_more": True,
                "page_token": "same-token",
            }

        bitable = FeishuBitableConnector(app_id="app-id", app_secret="secret")
        bitable.search_records = stuck_search  # type: ignore[method-assign]
        sink = bitable.table_sink(
            app_token="project-app",
            table_id="projects",
            mode="create",
            relations={"companies": _relation("eager")},
        )
        with caplog.at_level(logging.INFO, logger="onestep_feishu_bitable.connector"):
            with pytest.raises(ConnectorOperationError):
                await sink.open()

    asyncio.run(scenario())
    records = _scan_records(caplog)
    errors = [r for r in records if getattr(r, "phase", None) == "error"]
    assert len(errors) == 1
    assert "did not advance" in errors[0].error


def test_eager_scan_error_scan_exception(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        async def boom(**kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("network down record-id-abc")

        bitable = FeishuBitableConnector(app_id="app-id", app_secret="secret")
        bitable.search_records = boom  # type: ignore[method-assign]
        sink = bitable.table_sink(
            app_token="project-app",
            table_id="projects",
            mode="create",
            relations={"companies": _relation("eager")},
        )
        with caplog.at_level(logging.INFO, logger="onestep_feishu_bitable.connector"):
            with pytest.raises(ConnectorOperationError):
                await sink.open()

    asyncio.run(scenario())
    records = _scan_records(caplog)
    errors = [r for r in records if getattr(r, "phase", None) == "error"]
    assert len(errors) == 1
    # Error detail is redacted: type name only, never the exception text.
    assert "RuntimeError" in errors[0].error
    assert "network down" not in errors[0].error
    assert "record-id-abc" not in errors[0].error


def test_eager_scan_all_phases_redacted(caplog: pytest.LogCaptureFixture) -> None:
    """No phase (start/page/done/error) may leak business keys, record ids, or tokens."""

    async def scenario() -> None:
        connector = _EagerConnector(
            pages={
                "companies": [
                    [
                        {"record_id": "secret-record-1", "fields": {"name": "SecretCo"}},
                        {"record_id": "secret-record-2", "fields": {"name": "OtherCo"}},
                    ],
                    [{"record_id": "secret-record-3", "fields": {"name": "ThirdCo"}}],
                ]
            },
            total=3,
        )
        sink = _build_sink(connector, caches={"companies": _relation("eager")})
        with caplog.at_level(logging.INFO, logger="onestep_feishu_bitable.connector"):
            await sink.open()

    asyncio.run(scenario())
    joined = " ".join(str(r.__dict__) for r in caplog.records)
    for secret in ("SecretCo", "OtherCo", "ThirdCo", "secret-record-1", "secret-record-2", "secret-record-3"):
        assert secret not in joined
