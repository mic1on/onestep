from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from onestep import InMemoryCursorStore
from onestep.resilience import ConnectorErrorKind, ConnectorOperationError
from onestep_feishu_bitable import FeishuBitableConnector


CURSOR_FIELD = "updated_at"


def _items(count: int, *, cursor: int = 10) -> list[dict[str, Any]]:
    return [
        {
            "record_id": f"rec{index}",
            "fields": {CURSOR_FIELD: cursor, "order_no": f"K-{index:03d}"},
        }
        for index in range(1, count + 1)
    ]


def _make_source(records: list[dict[str, Any]], *, state: InMemoryCursorStore | None = None):
    """Build a real FeishuBitableIncrementalSource backed by a stubbed transport."""
    connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")

    async def search_records(**kwargs: Any) -> dict[str, Any]:
        return {"items": [dict(item) for item in records], "has_more": False}

    connector.search_records = search_records  # type: ignore[method-assign]
    store = state if state is not None else InMemoryCursorStore()
    return connector, store, _source(connector, store)


def _source(connector: FeishuBitableConnector, state: InMemoryCursorStore):
    return connector.incremental(
        app_token="app-token",
        table_id="tbl",
        cursor_field=CURSOR_FIELD,
        state=state,
        state_key="sync",
    )


def test_release_unstarted_removes_token_without_advancing_durable_cursor() -> None:
    async def scenario() -> None:
        connector, state, source = _make_source(_items(3))
        batch = await source.fetch(10)
        assert [delivery.payload["record_id"] for delivery in batch] == [
            "rec1",
            "rec2",
            "rec3",
        ]

        # The runtime drops the batch while pausing/stopping/draining.
        for delivery in batch:
            await delivery.release_unstarted()

        # Released tokens are gone from the deque and nothing was committed.
        assert list(source._pending) == []
        assert source._acked == set()
        assert await state.load("sync") is None
        await connector.close()

    asyncio.run(scenario())


def test_released_batch_head_is_not_committed_by_later_ack() -> None:
    async def scenario() -> None:
        connector, state, source = _make_source(_items(2))
        batch = await source.fetch(10)

        # Ack the successor while the head is still pending: the prefix commit only
        # ever advances from the deque head, so nothing is committed yet.
        await batch[1].ack()
        assert await state.load("sync") is None

        # Releasing the head drops it without committing it, exposing the acked
        # successor, which the prefix commit then writes on its own.
        await batch[0].release_unstarted()
        assert list(source._pending) == []
        assert await state.load("sync") == [10, "rec2"]

        # The released record was never committed, so it is still above the durable
        # cursor boundary rather than being skipped as delivered.
        assert source._committed_cursor == (10, "rec2")
        await connector.close()

    asyncio.run(scenario())


def test_whole_batch_release_rereads_records_after_restart() -> None:
    async def scenario() -> None:
        connector, state, source = _make_source(_items(3))
        batch = await source.fetch(10)
        for delivery in batch:
            await delivery.release_unstarted()
        assert await state.load("sync") is None

        # Restart against the same durable store: the range is re-read, so a
        # pause/restart cannot silently skip the released records.
        restarted = _source(connector, state)
        resumed = await restarted.fetch(10)
        assert [delivery.payload["record_id"] for delivery in resumed] == [
            "rec1",
            "rec2",
            "rec3",
        ]
        assert await state.load("sync") is None
        await connector.close()

    asyncio.run(scenario())


def test_fail_unblocks_prefix_commit_without_writing_cursor_directly() -> None:
    async def scenario() -> None:
        connector, state, source = _make_source(_items(3))
        batch = await source.fetch(10)

        # The head record is permanently abandoned: the cursor must not freeze.
        await batch[0].fail(RuntimeError("boom"))
        assert await state.load("sync") is None
        assert list(source._pending) == [(10, "rec2"), (10, "rec3")]

        # The abandoned record no longer blocks the prefix, so acking rec2 advances
        # the cursor to rec2 rather than stalling forever.
        await batch[1].ack()
        assert await state.load("sync") == [10, "rec2"]

        # Acking the tail advances it again, past the failed record.
        await batch[2].ack()
        assert await state.load("sync") == [10, "rec3"]
        await connector.close()

    asyncio.run(scenario())


def test_fail_of_middle_record_does_not_freeze_cursor() -> None:
    async def scenario() -> None:
        connector, state, source = _make_source(_items(2))
        batch = await source.fetch(10)

        # Acking the tail while the head is still pending commits nothing: the prefix
        # commit only ever advances from the deque head.
        await batch[1].ack()
        assert await state.load("sync") is None

        # Failing the head drops its token, which lets the already-acked successor be
        # committed instead of stalling on the abandoned record.
        await batch[0].fail(ValueError("permanent"))
        assert await state.load("sync") == [10, "rec2"]
        assert list(source._pending) == []
        await connector.close()

    asyncio.run(scenario())


def test_retry_does_not_commit_and_unblocks_the_prefix() -> None:
    async def scenario() -> None:
        connector, state, source = _make_source(_items(2))
        batch = await source.fetch(10)

        await batch[0].retry()
        assert await state.load("sync") is None
        assert list(source._pending) == [(10, "rec2")]

        # The retried token no longer blocks the prefix commit for rec2.
        await batch[1].ack()
        assert await state.load("sync") == [10, "rec2"]
        await connector.close()

    asyncio.run(scenario())


def test_retry_with_delay_sleeps_and_does_not_commit() -> None:
    async def scenario() -> None:
        connector, state, source = _make_source(_items(1))
        batch = await source.fetch(10)

        started = time.monotonic()
        await batch[0].retry(delay_s=0.01)
        elapsed = time.monotonic() - started

        assert elapsed >= 0.01
        assert await state.load("sync") is None
        assert list(source._pending) == []
        await connector.close()

    asyncio.run(scenario())


def test_release_retry_and_fail_are_idempotent() -> None:
    async def scenario() -> None:
        connector, state, source = _make_source(_items(3))
        batch = await source.fetch(10)

        # Releasing twice is harmless and keeps bookkeeping clean.
        for delivery in batch:
            await delivery.release_unstarted()
            await delivery.release_unstarted()
        assert list(source._pending) == []
        assert source._acked == set()
        assert await state.load("sync") is None

        # Repeating every action on an already-removed token is also harmless and
        # must not resurrect it into _acked or commit anything.
        for delivery in batch:
            await delivery.fail(RuntimeError("boom"))
            await delivery.retry(delay_s=0.0)
            await delivery.retry()
        assert list(source._pending) == []
        assert source._acked == set()
        assert await state.load("sync") is None
        await connector.close()

    asyncio.run(scenario())


def test_idempotent_actions_do_not_corrupt_a_live_prefix() -> None:
    async def scenario() -> None:
        connector, state, source = _make_source(_items(2))
        batch = await source.fetch(10)

        await batch[0].ack()
        # rec1 is the head, so acking it commits straight away.
        assert await state.load("sync") == [10, "rec1"]

        # Re-releasing/re-failing an already-committed token must not undo the commit
        # nor resurrect the token in _acked.
        await batch[0].release_unstarted()
        await batch[0].fail(RuntimeError("boom"))
        assert (10, "rec1") not in source._acked
        assert await state.load("sync") == [10, "rec1"]

        await batch[1].ack()
        assert await state.load("sync") == [10, "rec2"]
        await connector.close()

    asyncio.run(scenario())


def test_concurrent_release_retry_and_fail_keep_state_consistent() -> None:
    async def scenario() -> None:
        connector, state, source = _make_source(_items(9))
        batch = await source.fetch(10)

        async def hammer(delivery, index: int) -> None:
            if index % 3 == 0:
                await delivery.release_unstarted()
                await delivery.fail(RuntimeError("boom"))
            elif index % 3 == 1:
                await delivery.retry(delay_s=0.001)
                await delivery.release_unstarted()
            else:
                await delivery.fail(RuntimeError("boom"))
                await delivery.retry()

        await asyncio.gather(
            *(hammer(delivery, index) for index, delivery in enumerate(batch))
        )

        assert list(source._pending) == []
        assert source._acked == set()
        assert await state.load("sync") is None
        await connector.close()

    asyncio.run(scenario())


def test_retry_rewinds_read_cursor_so_record_is_redelivered_in_process() -> None:
    """A retried record must be visible to the very next poll, not only after restart.

    ``fetch()`` advances the read cursor to the tail of the batch it returned, so
    dropping the token alone would hide that row until the process restarted. The
    transport returns every record and the source filters client-side against the read
    cursor, which is exactly the path under test here.
    """

    async def scenario() -> None:
        records = _items(3, cursor=10)
        for record, cursor in zip(records, (10, 20, 30)):
            record["fields"][CURSOR_FIELD] = cursor
        connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")

        async def search_records(**kwargs: Any) -> dict[str, Any]:
            return {"items": [dict(item) for item in records], "has_more": False}

        connector.search_records = search_records  # type: ignore[method-assign]
        state = InMemoryCursorStore()
        source = _source(connector, state)

        batch = await source.fetch(10)
        assert [delivery.payload["record_id"] for delivery in batch] == [
            "rec1",
            "rec2",
            "rec3",
        ]

        # The middle record fails retryably and the runtime retries it.
        await batch[1].retry(delay_s=0.01)

        # It no longer wedges the prefix commit and nothing was committed.
        assert (20, "rec2") not in source._pending
        assert await state.load("sync") is None

        # The read cursor rewound to the record before it, so the next poll re-reads it.
        assert source._fetched_cursor == (10, "rec1")
        redelivered = await source.fetch(10)
        assert "rec2" in [delivery.payload["record_id"] for delivery in redelivered]
        await connector.close()

    asyncio.run(scenario())


def test_retry_never_rewinds_behind_durable_cursor() -> None:
    """Rewinding behind the committed cursor would replay the whole range."""

    async def scenario() -> None:
        connector, state, source = _make_source(_items(3))
        batch = await source.fetch(10)
        await batch[0].ack()
        committed = source._committed_cursor
        assert committed is not None
        fetched_before = source._fetched_cursor

        # Retrying an already-committed token must not move the read cursor back.
        await batch[0].retry()
        assert source._committed_cursor == committed
        assert source._fetched_cursor == fetched_before
        await connector.close()

    asyncio.run(scenario())


def test_main_path_paging_is_bounded_when_every_row_is_behind_the_cursor() -> None:
    """The sorted main path must not page forever when no row fills the batch.

    The loop only stops early once it has collected ``limit`` records; with a lagging
    cursor every returned row is filtered out client-side, so without a shared page
    bound the source would keep calling the API until ``has_more`` went false.
    """

    async def scenario() -> None:
        connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")
        calls = 0

        async def search_records(**kwargs: Any) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            # Always reports more pages and never yields a row past the cursor.
            return {
                "items": [
                    {
                        "record_id": f"old{index}",
                        "fields": {CURSOR_FIELD: 1, "order_no": f"old-{index}"},
                    }
                    for index in range(2)
                ],
                "has_more": True,
                "page_token": f"page-{calls}",
            }

        connector.search_records = search_records  # type: ignore[method-assign]
        state = InMemoryCursorStore()
        state_value = [100, "rec-committed"]
        await state.save("sync", state_value)
        source = _source(connector, state)
        source.fallback_scan_page_limit = 4

        with pytest.raises(ConnectorOperationError, match="fallback_scan_page_limit=4") as exc_info:
            await source.fetch(10)

        assert exc_info.value.kind is ConnectorErrorKind.PERMANENT
        # Bounded: it stops at the configured page bound instead of looping forever.
        assert calls == 4
        await connector.close()

    asyncio.run(scenario())


def test_missing_page_token_with_has_more_fails_instead_of_silently_truncating() -> None:
    """Truncating would let the caller advance the cursor past unread pages."""

    async def scenario() -> None:
        connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")

        async def search_records(**kwargs: Any) -> dict[str, Any]:
            return {
                "items": [
                    {
                        "record_id": "rec1",
                        "fields": {CURSOR_FIELD: 10, "order_no": "K-1"},
                    }
                ],
                "has_more": True,
                # No page_token even though more pages are claimed.
                "page_token": "",
            }

        connector.search_records = search_records  # type: ignore[method-assign]
        state = InMemoryCursorStore()
        source = _source(connector, state)

        with pytest.raises(ConnectorOperationError, match="has_more=true without a page_token") as exc_info:
            await source.fetch(10)

        assert exc_info.value.kind is ConnectorErrorKind.TRANSIENT
        # Nothing was committed, so no unread page is skipped.
        assert await state.load("sync") is None
        await connector.close()

    asyncio.run(scenario())


def test_repeated_page_token_aborts_instead_of_rereading_the_same_page() -> None:
    async def scenario() -> None:
        connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")
        calls = 0

        async def search_records(**kwargs: Any) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            return {
                "items": [
                    {
                        "record_id": "rec1",
                        "fields": {CURSOR_FIELD: 10, "order_no": "K-1"},
                    }
                ],
                "has_more": True,
                "page_token": "same-token",
            }

        connector.search_records = search_records  # type: ignore[method-assign]
        state = InMemoryCursorStore()
        source = _source(connector, state)

        with pytest.raises(ConnectorOperationError, match="repeated page_token"):
            await source.fetch(10)

        # First call hands out the token; the second sees it repeat and aborts.
        assert calls == 2
        await connector.close()

    asyncio.run(scenario())
