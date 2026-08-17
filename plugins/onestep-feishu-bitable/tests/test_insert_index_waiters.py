from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import pytest

from onestep import Envelope
from onestep.resilience import (
    ConnectorErrorKind,
    ConnectorOperation,
    ConnectorOperationError,
)
from onestep_feishu_bitable import FeishuBitableConnector


async def _make_indexed_sink(
    *,
    connector: FeishuBitableConnector | None = None,
    batch_size: int = 100,
    insert_keys: set[str] | None = None,
    **kwargs: Any,
) -> Any:
    """Create an indexed sink with preloaded keys for testing."""
    if connector is None:
        connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")
    sink = connector.table_sink(
        app_token="app-token",
        table_id="tbl",
        mode="insert",
        match_fields=["编号"],
        batch_size=batch_size,
        insert_key_index=True,
        **kwargs,
    )
    if insert_keys is not None:
        sink._insert_keys = insert_keys
        sink._index_loaded = True
    return sink, connector


def test_feishu_insert_index_hit_completes_without_search_or_write() -> None:
    """Send with a key already in the index returns immediately."""
    async def scenario() -> None:
        sink, _ = await _make_indexed_sink(insert_keys={"K-1"})
        await sink.send(Envelope(body={"编号": "K-1"}))
        # No error, no search, no write

    asyncio.run(scenario())


def test_feishu_insert_send_waits_for_its_batch_write() -> None:
    """A send does not return until the batch write completes."""
    async def scenario() -> None:
        write_started = asyncio.Event()
        release_write = asyncio.Event()
        created_batches: list[list[dict[str, Any]]] = []

        async def fake_batch_create(**kwargs: Any) -> dict[str, Any]:
            records = kwargs.get("records", [])
            created_batches.append(list(records))
            write_started.set()
            await release_write.wait()
            return {"records": [{"fields": dict(r)} for r in records]}

        sink, connector = await _make_indexed_sink(insert_keys=set())
        connector.batch_create_records = fake_batch_create  # type: ignore[assignment]

        send_tasks = [
            asyncio.create_task(sink.send(Envelope(body={"编号": f"K-{i:03d}"})))
            for i in range(100)
        ]
        await asyncio.wait_for(write_started.wait(), timeout=1.0)
        assert not any(task.done() for task in send_tasks)
        release_write.set()
        await asyncio.gather(*send_tasks)
        assert len(created_batches) == 1
        assert len(created_batches[0]) == 100

    asyncio.run(scenario())


def test_feishu_insert_concurrent_duplicate_key_creates_once() -> None:
    """Two sends for the same absent key produce one create."""
    async def scenario() -> None:
        created_batches: list[list[dict[str, Any]]] = []

        async def fake_batch_create(**kwargs: Any) -> dict[str, Any]:
            records = kwargs.get("records", [])
            created_batches.append(list(records))
            return {"records": [{"fields": dict(r)} for r in records]}

        sink, connector = await _make_indexed_sink(insert_keys=set())
        connector.batch_create_records = fake_batch_create  # type: ignore[assignment]

        t1 = asyncio.create_task(sink.send(Envelope(body={"编号": "K-1"})))
        t2 = asyncio.create_task(sink.send(Envelope(body={"编号": "K-1"})))
        await asyncio.gather(t1, t2)
        total_creates = sum(len(b) for b in created_batches)
        assert total_creates == 1

    asyncio.run(scenario())


def test_feishu_insert_batch_failure_completes_every_member_with_error() -> None:
    """A batch write failure raises through every pending send."""
    async def scenario() -> None:
        async def fake_batch_create(**kwargs: Any) -> dict[str, Any]:
            raise ConnectorOperationError(
                backend="feishu_bitable",
                operation=ConnectorOperation.SEND,
                kind=ConnectorErrorKind.PERMANENT,
                source_name="test",
                retry_delay_s=1.0,
                message="batch write failed",
            )

        sink, connector = await _make_indexed_sink(insert_keys=set(), batch_size=5)
        connector.batch_create_records = fake_batch_create  # type: ignore[assignment]

        tasks = [
            asyncio.create_task(sink.send(Envelope(body={"编号": f"K-{i}"})))
            for i in range(5)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            assert isinstance(r, ConnectorOperationError)
            assert "batch write failed" in str(r)

    asyncio.run(scenario())


def test_feishu_insert_timer_flush_completes_partial_batch_waiters() -> None:
    """A timer flush completes fewer-than-batch_size sends (without NameError)."""
    async def scenario() -> None:
        created_batches: list[list[dict[str, Any]]] = []

        async def fake_batch_create(**kwargs: Any) -> dict[str, Any]:
            records = kwargs.get("records", [])
            created_batches.append(list(records))
            return {"records": [{"fields": dict(r)} for r in records]}

        sink, connector = await _make_indexed_sink(
            insert_keys=set(), batch_size=100, flush_interval_s=0.05
        )
        connector.batch_create_records = fake_batch_create  # type: ignore[assignment]

        await asyncio.gather(
            sink.send(Envelope(body={"编号": "K-1"})),
            sink.send(Envelope(body={"编号": "K-2"})),
            sink.send(Envelope(body={"编号": "K-3"})),
        )
        assert len(created_batches) == 1
        assert len(created_batches[0]) == 3

    asyncio.run(scenario())


def test_feishu_insert_close_drains_partial_batch_and_every_waiter() -> None:
    """Close seals and flushes all remaining pending keys."""
    async def scenario() -> None:
        created_batches: list[list[dict[str, Any]]] = []

        async def fake_batch_create(**kwargs: Any) -> dict[str, Any]:
            records = kwargs.get("records", [])
            created_batches.append(list(records))
            return {"records": [{"fields": dict(r)} for r in records]}

        sink, connector = await _make_indexed_sink(
            insert_keys=set(), batch_size=100
        )
        connector.batch_create_records = fake_batch_create  # type: ignore[assignment]

        t1 = asyncio.create_task(sink.send(Envelope(body={"编号": "K-1"})))
        await asyncio.sleep(0.01)
        await sink.close()
        await t1
        assert len(created_batches) == 1
        assert len(created_batches[0]) == 1

    asyncio.run(scenario())


def test_feishu_insert_close_failure_fails_every_waiter() -> None:
    """Close that fails during flush completes remaining waiters with error."""
    async def scenario() -> None:
        async def fake_batch_create(**kwargs: Any) -> dict[str, Any]:
            raise ConnectorOperationError(
                backend="feishu_bitable",
                operation=ConnectorOperation.SEND,
                kind=ConnectorErrorKind.PERMANENT,
                source_name="test",
                retry_delay_s=1.0,
                message="close flush failed",
            )

        sink, connector = await _make_indexed_sink(
            insert_keys=set(), batch_size=100
        )
        connector.batch_create_records = fake_batch_create  # type: ignore[assignment]

        t1 = asyncio.create_task(sink.send(Envelope(body={"编号": "K-1"})))
        await asyncio.sleep(0.01)
        await sink.close()
        with pytest.raises(ConnectorOperationError):
            await t1

    asyncio.run(scenario())


def test_feishu_insert_cancelled_sender_leaves_no_waiter_after_close() -> None:
    """Cancelled send coroutines do not leave leaked Futures."""
    async def scenario() -> None:
        sink, _ = await _make_indexed_sink(insert_keys=set(), batch_size=100)

        t1 = asyncio.create_task(sink.send(Envelope(body={"编号": "K-1"})))
        await asyncio.sleep(0.01)
        t1.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t1
        await sink.close()

    asyncio.run(scenario())


def test_feishu_insert_ambiguous_write_finds_committed_keys_without_recreate() -> None:
    """An uncertain create is searched before any second create."""

    async def scenario() -> None:
        create_calls = 0
        search_calls: list[str] = []

        async def fake_batch_create(**kwargs: Any) -> dict[str, Any]:
            nonlocal create_calls
            create_calls += 1
            raise ConnectorOperationError(
                backend="feishu_bitable",
                operation=ConnectorOperation.SEND,
                kind=ConnectorErrorKind.UNCERTAIN,
                source_name="test",
                message="connection closed after request",
            )

        async def fake_search(**kwargs: Any) -> dict[str, Any]:
            value = kwargs["body"]["filter"]["conditions"][0]["value"][0]
            search_calls.append(value)
            return {"items": [{"record_id": "rec"}], "has_more": False}

        sink, connector = await _make_indexed_sink(insert_keys=set(), batch_size=2)
        connector.batch_create_records = fake_batch_create  # type: ignore[assignment]
        connector.search_records = fake_search  # type: ignore[assignment]

        await asyncio.gather(
            sink.send(Envelope(body={"编号": "K-1"})),
            sink.send(Envelope(body={"编号": "K-2"})),
        )
        assert create_calls == 1
        assert sorted(search_calls) == ["K-1", "K-2"]
        assert sink._insert_keys == {"K-1", "K-2"}

    asyncio.run(scenario())


def test_feishu_insert_ambiguous_write_recreates_only_confirmed_misses() -> None:
    """Recovery excludes keys that the exact lookup already found."""

    async def scenario() -> None:
        creates: list[list[str]] = []

        async def fake_batch_create(**kwargs: Any) -> dict[str, Any]:
            keys = [record["编号"] for record in kwargs["records"]]
            creates.append(keys)
            if len(creates) == 1:
                raise ConnectorOperationError(
                    backend="feishu_bitable",
                    operation=ConnectorOperation.SEND,
                    kind=ConnectorErrorKind.UNCERTAIN,
                    source_name="test",
                )
            return {"records": [{"record_id": "rec-2"}]}

        async def fake_search(**kwargs: Any) -> dict[str, Any]:
            value = kwargs["body"]["filter"]["conditions"][0]["value"][0]
            return {
                "items": [{"record_id": "rec-1"}] if value == "K-1" else [],
                "has_more": False,
            }

        sink, connector = await _make_indexed_sink(insert_keys=set(), batch_size=2)
        connector.batch_create_records = fake_batch_create  # type: ignore[assignment]
        connector.search_records = fake_search  # type: ignore[assignment]
        await asyncio.gather(
            sink.send(Envelope(body={"编号": "K-1"})),
            sink.send(Envelope(body={"编号": "K-2"})),
        )
        assert creates == [["K-1", "K-2"], ["K-2"]]

    asyncio.run(scenario())


def test_feishu_insert_lookup_error_is_never_treated_as_missing() -> None:
    """Recovery search errors do not trigger a blind create."""

    async def scenario() -> None:
        create_calls = 0

        async def fake_batch_create(**kwargs: Any) -> dict[str, Any]:
            nonlocal create_calls
            create_calls += 1
            raise ConnectorOperationError(
                backend="feishu_bitable",
                operation=ConnectorOperation.SEND,
                kind=ConnectorErrorKind.UNCERTAIN,
                source_name="test",
            )

        async def fake_search(**kwargs: Any) -> dict[str, Any]:
            raise ConnectorOperationError(
                backend="feishu_bitable",
                operation=ConnectorOperation.SEND,
                kind=ConnectorErrorKind.TRANSIENT,
                source_name="test",
            )

        sink, connector = await _make_indexed_sink(
            insert_keys=set(), batch_size=1, ambiguous_write_max_rounds=2
        )
        connector.batch_create_records = fake_batch_create  # type: ignore[assignment]
        connector.search_records = fake_search  # type: ignore[assignment]
        with pytest.raises(ConnectorOperationError) as excinfo:
            await sink.send(Envelope(body={"编号": "K-1"}))
        assert excinfo.value.kind is ConnectorErrorKind.UNCERTAIN
        assert create_calls == 1

    asyncio.run(scenario())


def test_feishu_insert_short_success_response_enters_recovery() -> None:
    """A short success response cannot acknowledge the whole batch."""

    async def scenario() -> None:
        search_calls = 0

        async def fake_batch_create(**kwargs: Any) -> dict[str, Any]:
            return {"records": [{"record_id": "only-one"}]}

        async def fake_search(**kwargs: Any) -> dict[str, Any]:
            nonlocal search_calls
            search_calls += 1
            return {"items": [{"record_id": "rec"}], "has_more": False}

        sink, connector = await _make_indexed_sink(insert_keys=set(), batch_size=2)
        connector.batch_create_records = fake_batch_create  # type: ignore[assignment]
        connector.search_records = fake_search  # type: ignore[assignment]
        await asyncio.gather(
            sink.send(Envelope(body={"编号": "K-1"})),
            sink.send(Envelope(body={"编号": "K-2"})),
        )
        assert search_calls == 2

    asyncio.run(scenario())


def test_feishu_insert_logs_aggregates_without_sensitive_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        create_calls = 0

        async def fake_search(**kwargs: Any) -> dict[str, Any]:
            body = kwargs["body"]
            if "field_names" in body:
                return {
                    "items": [
                        {
                            "record_id": "record-id-secret",
                            "fields": {"编号": "preexisting-key-secret"},
                        }
                    ],
                    "has_more": False,
                }
            return {
                "items": [
                    {
                        "record_id": "record-id-secret",
                        "fields": {"编号": "union-key-secret"},
                    }
                ],
                "has_more": False,
            }

        async def fake_batch_create(**kwargs: Any) -> dict[str, Any]:
            nonlocal create_calls
            create_calls += 1
            raise ConnectorOperationError(
                backend="feishu_bitable",
                operation=ConnectorOperation.SEND,
                kind=ConnectorErrorKind.UNCERTAIN,
                source_name="test",
                message="connection closed after request",
            )

        connector = FeishuBitableConnector(
            app_id="app-id-secret", app_secret="app-secret-value"
        )
        connector.search_records = fake_search  # type: ignore[assignment]
        connector.batch_create_records = fake_batch_create  # type: ignore[assignment]
        sink = connector.table_sink(
            app_token="app-token-secret",
            table_id="table-id-secret",
            mode="insert",
            match_fields=["编号"],
            batch_size=2,
            insert_key_index=True,
        )
        await sink.open()
        await sink.send(Envelope(body={"编号": "preexisting-key-secret"}))
        await asyncio.gather(
            sink.send(
                Envelope(
                    body={"编号": "union-key-secret", "内容": "payload-value-secret"}
                )
            ),
            sink.send(
                Envelope(
                    body={"编号": "second-key-secret", "内容": "payload-value-secret"}
                )
            ),
        )
        await sink.close()
        assert create_calls == 1

    caplog.set_level(logging.INFO, logger="onestep_feishu_bitable.connector")
    asyncio.run(scenario())

    by_event: dict[str, list[logging.LogRecord]] = {}
    for record in caplog.records:
        event = getattr(record, "event", None)
        if event is not None:
            by_event.setdefault(event, []).append(record)
    assert {
        "scan_pages",
        "scan_keys",
        "duration_s",
        "page_size",
        "max_pages",
    } <= by_event["feishu_insert_index_scan"][0].__dict__.keys()
    assert {
        "buffered_batch_size",
        "oldest_batch_age_s",
        "inflight_waiter_count",
        "flush_reason",
    } <= by_event["feishu_insert_buffer"][0].__dict__.keys()
    assert {
        "batch_size",
        "duration_s",
        "outcome",
        "flush_reason",
        "recovery_round",
    } <= by_event["feishu_insert_batch_write"][0].__dict__.keys()
    assert {
        "normal_lookup_avoided_count",
        "recovery_lookup_count",
        "outcome",
    } <= by_event["feishu_insert_lookup"][0].__dict__.keys()
    assert {
        "retry_count",
        "recovery_round",
        "unresolved_count",
    } <= by_event["feishu_insert_retry"][0].__dict__.keys()

    serialized = json.dumps(
        [record.__dict__ for record in caplog.records],
        ensure_ascii=False,
        default=str,
    )
    for secret in (
        "app-id-secret",
        "app-secret-value",
        "app-token-secret",
        "table-id-secret",
        "union-key-secret",
        "second-key-secret",
        "preexisting-key-secret",
        "payload-value-secret",
        "record-id-secret",
        "编号",
    ):
        assert secret not in serialized
