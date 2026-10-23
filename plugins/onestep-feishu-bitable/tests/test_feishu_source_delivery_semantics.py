from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from onestep import InMemoryCursorStore, MaxAttempts, OneStepApp
from onestep.runtime.runner import TaskRunner
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


@pytest.mark.parametrize("count,index", [(1, 0), (3, 0), (3, 1), (3, 2)])
@pytest.mark.parametrize("action", ["retry", "release_unstarted"])
def test_unresolved_row_is_redelivered_without_replaying_successors(count, index, action) -> None:
    async def scenario() -> None:
        connector, state, source = _make_source(_items(count))
        batch = await source.fetch(10)
        for i, delivery in enumerate(batch):
            if i != index:
                await delivery.ack()
        expected_cursor = [10, f"rec{index}"] if index else None
        assert await state.load("sync") == expected_cursor
        await getattr(batch[index], action)()
        # A retry/release is still a barrier even when every successor has acked.
        assert await state.load("sync") == expected_cursor
        resumed = await source.fetch(10)
        assert [d.payload["record_id"] for d in resumed] == [f"rec{index + 1}"]
        assert resumed[0].envelope.attempts == int(action == "retry")
        assert await source.fetch(10) == []  # no duplicate while retry is in flight
        await resumed[0].ack()
        assert await state.load("sync") == [10, f"rec{count}"]
        assert not source._pending
        assert not source._tokens
        assert not source._acked
        await connector.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["retry", "release_unstarted"])
def test_restart_recovers_unresolved_row_before_acked_successors(action) -> None:
    async def scenario() -> None:
        connector, state, source = _make_source(_items(3))
        first, second, third = await source.fetch(10)
        await first.ack()
        await third.ack()
        await getattr(second, action)()
        assert await state.load("sync") == [10, "rec1"]
        restarted = _source(connector, state)
        resumed = await restarted.fetch(10)
        assert [d.payload["record_id"] for d in resumed] == ["rec2", "rec3"]
        assert [d.envelope.attempts for d in resumed] == [0, 0]
        await connector.close()

    asyncio.run(scenario())


def test_pause_resume_releases_whole_batch_without_consuming_attempts() -> None:
    async def scenario() -> None:
        connector, state, source = _make_source(_items(3))
        batch = await source.fetch(10)
        for delivery in batch:
            await delivery.release_unstarted()
        assert await state.load("sync") is None
        resumed = await source.fetch(10)
        assert [d.payload["record_id"] for d in resumed] == ["rec1", "rec2", "rec3"]
        assert [d.envelope.attempts for d in resumed] == [0, 0, 0]
        for delivery in resumed:
            await delivery.ack()
        assert await state.load("sync") == [10, "rec3"]
        await connector.close()

    asyncio.run(scenario())


def test_retry_delay_keeps_row_fenced_and_invisible_until_ready() -> None:
    async def scenario() -> None:
        connector, state, source = _make_source(_items(2))
        first, second = await source.fetch(10)
        started = time.monotonic()
        retrying = asyncio.create_task(first.retry(delay_s=0.02))
        await asyncio.sleep(0)
        await second.ack()
        assert await source.fetch(10) == []
        assert await state.load("sync") is None
        await retrying
        assert time.monotonic() - started >= 0.02
        redelivered = await source.fetch(10)
        assert [d.payload["record_id"] for d in redelivered] == ["rec1"]
        await connector.close()

    asyncio.run(scenario())


def test_old_delivery_actions_cannot_settle_or_duplicate_a_new_attempt() -> None:
    async def scenario() -> None:
        connector, state, source = _make_source(_items(1))
        original, = await source.fetch(10)
        await asyncio.gather(original.retry(), original.retry(), original.release_unstarted())
        replacement, = await source.fetch(10)
        for action in (original.ack, original.fail, original.retry, original.release_unstarted):
            await action()
        assert await state.load("sync") is None
        assert await source.fetch(10) == []
        assert replacement.envelope.attempts == 1
        await replacement.ack()
        await replacement.ack()
        await replacement.retry()
        assert await state.load("sync") == [10, "rec1"]
        assert not source._tokens and not source._acked
        await connector.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["ack", "fail"])
def test_state_save_failure_retains_prefix_for_retry_and_restart(action) -> None:
    class FailingStore(InMemoryCursorStore):
        fail_save = True

        async def save(self, key, value):
            if self.fail_save:
                raise OSError("state unavailable")
            await super().save(key, value)

    async def scenario() -> None:
        state = FailingStore()
        connector, _, source = _make_source(_items(2), state=state)
        first, second = await source.fetch(10)
        await second.ack()
        with pytest.raises(OSError, match="state unavailable"):
            await getattr(first, action)()
        assert await state.load("sync") is None
        assert source._committed_cursor is None
        assert list(source._pending) == [(10, "rec1"), (10, "rec2")]
        assert source._acked == {(10, "rec2")}
        restarted = _source(connector, state)
        assert len(await restarted.fetch(10)) == 2
        await first.retry()
        redelivered, = await source.fetch(10)
        state.fail_save = False
        await redelivered.ack()
        assert await state.load("sync") == [10, "rec2"]
        assert not source._tokens and not source._pending and not source._acked
        await connector.close()

    asyncio.run(scenario())


def test_cancelled_state_save_does_not_mutate_commit_position() -> None:
    class SlowStore(InMemoryCursorStore):
        async def save(self, key, value):
            await asyncio.Future()

    async def scenario() -> None:
        connector, state, source = _make_source(_items(1), state=SlowStore())
        first, = await source.fetch(1)
        acking = asyncio.create_task(first.ack())
        await asyncio.sleep(0)
        acking.cancel()
        with pytest.raises(asyncio.CancelledError):
            await acking
        assert source._committed_cursor is None
        assert await state.load("sync") is None
        await first.retry()
        redelivered, = await source.fetch(1)
        assert redelivered.payload["record_id"] == "rec1"
        await connector.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("index", [0, 1])
def test_terminal_failure_advances_only_a_resolved_prefix(index) -> None:
    async def scenario() -> None:
        connector, state, source = _make_source(_items(2))
        batch = await source.fetch(10)
        await batch[index].fail(RuntimeError("permanent"))
        assert await state.load("sync") == ([10, "rec1"] if index == 0 else None)
        await batch[1 - index].ack()
        assert await state.load("sync") == [10, "rec2"]
        assert await _source(connector, state).fetch(10) == []
        await connector.close()

    asyncio.run(scenario())


def test_max_attempts_stops_poison_row_and_commits_terminal_tail() -> None:
    async def scenario() -> None:
        connector, state, source = _make_source(_items(1))
        app = OneStepApp("feishu-retry-budget")
        seen = []

        @app.task(source=source, retry=MaxAttempts(max_attempts=3, delay_s=0))
        async def poison(ctx, payload):
            seen.append(payload["record_id"])
            raise ValueError("poison row")

        runner = TaskRunner(app, app.tasks[0])
        for attempt in range(3):
            delivery, = await source.fetch(1)
            assert delivery.envelope.attempts == attempt
            await runner._handle_delivery(delivery)
        assert seen == ["rec1", "rec1", "rec1"]
        assert await source.fetch(1) == []
        assert await state.load("sync") == [10, "rec1"]
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
