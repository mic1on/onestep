"""Regression tests for the indexed-insert close() race and the index-hit skip.

Two independent P0 reliability bugs are covered here:

* ``close()`` used to drain only ``_pending_order``.  An entry whose write is
  already in flight has been sealed *out* of that queue while still recorded in
  ``_pending_by_key``, so close could exit with waiters unresolved -- raising a
  bare ``AssertionError`` and hanging every caller blocked in ``send()``.
* An ``insert_key_index`` hit returned immediately, trusting an in-memory
  snapshot taken at ``open()``.  A row deleted upstream after that snapshot was
  silently skipped while the runtime still acked it and advanced the cursor,
  which is permanent silent data loss.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from onestep import Envelope
from onestep.config import load_app_config
from onestep.resilience import (
    ConnectorErrorKind,
    ConnectorOperation,
    ConnectorOperationError,
)
from onestep_feishu_bitable import FeishuBitableConnector


def _make_indexed_sink(
    *,
    connector: FeishuBitableConnector | None = None,
    batch_size: int = 100,
    insert_keys: set[str] | None = None,
    **kwargs: Any,
) -> tuple[Any, FeishuBitableConnector]:
    """Build an indexed-insert sink with a preloaded startup index."""
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


# ---------------------------------------------------------------------------
# BUG A - close() must drain sealed/in-flight entries and never hang a caller
# ---------------------------------------------------------------------------


def test_close_with_sealed_inflight_entry_does_not_raise_assertion_error() -> None:
    """An entry sealed out of _pending_order is still drained by close().

    This reproduces the audited race for real: the timer flush seals the entry
    (removing it from ``_pending_order``) and then blocks on the network write,
    so the entry is sealed-but-in-flight while ``close()`` runs.  Checking only
    ``_pending_order`` made close exit here and assert.
    """

    async def scenario() -> None:
        write_started = asyncio.Event()
        release_write = asyncio.Event()
        created: list[dict[str, Any]] = []

        async def fake_batch_create(**kwargs: Any) -> dict[str, Any]:
            records = kwargs.get("records", [])
            created.extend(dict(r) for r in records)
            write_started.set()
            await release_write.wait()
            return {"records": [{"fields": dict(r)} for r in records]}

        sink, connector = _make_indexed_sink(
            insert_keys=set(), batch_size=100, flush_interval_s=0.01
        )
        connector.batch_create_records = fake_batch_create  # type: ignore[assignment]

        send_task = asyncio.create_task(sink.send(Envelope(body={"编号": "K-1"})))

        # The timer flush has sealed the entry and is now awaiting the network.
        await asyncio.wait_for(write_started.wait(), timeout=5.0)
        assert not sink._pending_order
        assert sink._pending_by_key

        close_task = asyncio.create_task(sink.close())
        await asyncio.sleep(0.05)
        # close() is waiting on the in-flight write, so it must not have
        # returned (and certainly not raised) yet.
        assert not close_task.done()

        release_write.set()
        # No AssertionError, and both the caller and close() complete.
        await asyncio.wait_for(close_task, timeout=5.0)
        await asyncio.wait_for(send_task, timeout=5.0)
        assert len(created) == 1

    asyncio.run(scenario())


def test_close_settles_sealed_inflight_entry_when_its_write_never_returns() -> None:
    """A sealed-in-flight write that never settles still releases its caller.

    The write is abandoned (simulating a stalled request whose task is gone),
    which leaves a pending key that close() cannot flush.  close() must fail
    that key's waiter instead of hanging the application.
    """

    async def scenario() -> None:
        sink, _ = _make_indexed_sink(
            insert_keys=set(), batch_size=100, close_drain_max_rounds=10
        )
        send_task = asyncio.create_task(sink.send(Envelope(body={"编号": "K-1"})))
        await asyncio.sleep(0.01)

        # Sealed with no in-flight writer and nothing left to seal: the drain
        # cannot advance, so close reports instead of spinning to the bound.
        pending = sink._pending_by_key["K-1"]
        pending.state = type(pending.state).WRITING
        sink._pending_order.clear()
        sink._flush_task = None

        with pytest.raises(ConnectorOperationError) as excinfo:
            await asyncio.wait_for(sink.close(), timeout=5.0)
        assert "close" in str(excinfo.value)

        # The caller is released with an error rather than deadlocking.
        with pytest.raises(ConnectorOperationError):
            await asyncio.wait_for(send_task, timeout=5.0)
        assert sink._pending_by_key == {}
        assert sink.inflight_waiter_count == 0

    asyncio.run(scenario())


def test_close_leaves_no_unresolved_waiters() -> None:
    """close() resolves every outstanding waiter, with an error when it fails."""

    async def scenario() -> None:
        sink, connector = _make_indexed_sink(insert_keys=set(), batch_size=100)

        async def fake_batch_create(**kwargs: Any) -> dict[str, Any]:
            records = kwargs.get("records", [])
            return {"records": [{"fields": dict(r)} for r in records]}

        connector.batch_create_records = fake_batch_create  # type: ignore[assignment]

        send_tasks = [
            asyncio.create_task(sink.send(Envelope(body={"编号": f"K-{i}"})))
            for i in range(3)
        ]
        await asyncio.sleep(0.01)

        collected_waiters = [
            waiter
            for pending in sink._pending_by_key.values()
            for waiter in pending.waiters
        ]
        assert collected_waiters

        await asyncio.wait_for(sink.close(), timeout=5.0)
        await asyncio.wait_for(asyncio.gather(*send_tasks), timeout=5.0)

        # No waiter may be left pending, and the sink holds no pending keys.
        assert all(waiter.done() for waiter in collected_waiters)
        assert sink._pending_by_key == {}
        assert not sink._pending_order
        assert sink.inflight_waiter_count == 0

    asyncio.run(scenario())


def test_send_after_close_raises_instead_of_silently_buffering() -> None:
    """A post-close send fails loudly instead of buffering an unflushable row."""

    async def scenario() -> None:
        sink, _ = _make_indexed_sink(insert_keys=set(), batch_size=100)
        await sink.close()

        with pytest.raises(ConnectorOperationError) as excinfo:
            await sink.send(Envelope(body={"编号": "K-1"}))
        assert excinfo.value.kind is ConnectorErrorKind.PERMANENT
        assert excinfo.value.operation is ConnectorOperation.SEND

        # Nothing was buffered behind the caller's back.
        assert sink._pending_by_key == {}
        assert not sink._pending_order

    asyncio.run(scenario())


def test_send_after_close_raises_on_the_buffered_path_too() -> None:
    """The non-indexed buffered path is guarded as well."""

    async def scenario() -> None:
        connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")
        sink = connector.table_sink(
            app_token="app-token",
            table_id="tbl",
            mode="create",
            match_fields=None,
            batch_size=5,
        )
        await sink.close()
        with pytest.raises(ConnectorOperationError) as excinfo:
            await sink.send(Envelope(body={"a": 1}))
        assert excinfo.value.kind is ConnectorErrorKind.PERMANENT
        assert sink._buffer == []

    asyncio.run(scenario())


def test_close_is_idempotent_and_legacy_close_still_flushes() -> None:
    """Double close is a no-op, and the legacy path still flushes its buffer."""

    async def scenario() -> None:
        sink, connector = _make_indexed_sink(insert_keys=set(), batch_size=100)
        created: list[dict[str, Any]] = []

        async def fake_batch_create(**kwargs: Any) -> dict[str, Any]:
            records = kwargs.get("records", [])
            created.extend(dict(r) for r in records)
            return {"records": [{"fields": dict(r)} for r in records]}

        connector.batch_create_records = fake_batch_create  # type: ignore[assignment]
        await sink.send(Envelope(body={"编号": "K-1"}))
        await asyncio.wait_for(sink.close(), timeout=5.0)
        await asyncio.wait_for(sink.close(), timeout=5.0)
        assert len(created) == 1

        # Legacy (non-indexed) close must still flush a partial buffer.
        legacy_connector = FeishuBitableConnector(
            app_id="app-id", app_secret="secret"
        )
        legacy = legacy_connector.table_sink(
            app_token="app-token",
            table_id="tbl",
            mode="create",
            match_fields=None,
            batch_size=5,
        )
        legacy_created: list[dict[str, Any]] = []

        async def fake_legacy_create(**kwargs: Any) -> dict[str, Any]:
            records = kwargs.get("records", [])
            legacy_created.extend(dict(r) for r in records)
            return {"records": [{"fields": dict(r)} for r in records]}

        legacy_connector.batch_create_records = fake_legacy_create  # type: ignore[assignment]
        await legacy.send(Envelope(body={"a": 1}))
        await asyncio.wait_for(legacy.close(), timeout=5.0)
        assert len(legacy_created) == 1

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# BUG B - an index hit must not silently skip a since-deleted destination row
# ---------------------------------------------------------------------------


def test_index_hit_recreates_row_deleted_upstream() -> None:
    """The safe default confirms the hit and re-creates a deleted row."""

    async def scenario() -> None:
        # Startup snapshot claims K-1 exists, but upstream deleted it since.
        sink, connector = _make_indexed_sink(insert_keys={"K-1"}, batch_size=10)

        searches: list[dict[str, Any]] = []
        created: list[dict[str, Any]] = []

        async def fake_search(**kwargs: Any) -> dict[str, Any]:
            searches.append(kwargs)
            return {"items": [], "has_more": False}

        async def fake_batch_create(**kwargs: Any) -> dict[str, Any]:
            records = kwargs.get("records", [])
            created.extend(dict(r) for r in records)
            return {"records": [{"fields": dict(r)} for r in records]}

        connector.search_records = fake_search  # type: ignore[assignment]
        connector.batch_create_records = fake_batch_create  # type: ignore[assignment]

        await sink.send(Envelope(body={"编号": "K-1", "内容": "payload"}))
        await asyncio.wait_for(sink.close(), timeout=5.0)

        # The skip was verified, then the missing row was written.
        assert len(searches) == 1
        assert len(created) == 1
        assert created[0]["编号"] == "K-1"

    asyncio.run(scenario())


def test_index_hit_skips_only_after_confirmation() -> None:
    """A confirmed hit is still skipped, without any write."""

    async def scenario() -> None:
        sink, connector = _make_indexed_sink(insert_keys={"K-1"}, batch_size=10)
        created: list[dict[str, Any]] = []

        async def fake_search(**kwargs: Any) -> dict[str, Any]:
            return {"items": [{"record_id": "rec-1"}], "has_more": False}

        async def fake_batch_create(**kwargs: Any) -> dict[str, Any]:
            created.extend(dict(r) for r in kwargs.get("records", []))
            return {"records": [{"fields": dict(r)} for r in kwargs["records"]]}

        connector.search_records = fake_search  # type: ignore[assignment]
        connector.batch_create_records = fake_batch_create  # type: ignore[assignment]

        await sink.send(Envelope(body={"编号": "K-1"}))
        await asyncio.wait_for(sink.close(), timeout=5.0)
        assert created == []

    asyncio.run(scenario())


def test_index_hit_verification_failure_never_drops_the_row() -> None:
    """A failed verification lookup fails the send; absence is never assumed."""

    async def scenario() -> None:
        sink, connector = _make_indexed_sink(insert_keys={"K-1"}, batch_size=10)
        created: list[dict[str, Any]] = []

        async def fake_search(**kwargs: Any) -> dict[str, Any]:
            raise ConnectorOperationError(
                backend="feishu_bitable",
                operation=ConnectorOperation.SEND,
                kind=ConnectorErrorKind.TRANSIENT,
                source_name="test",
                retry_delay_s=1.0,
                message="search unavailable",
            )

        async def fake_batch_create(**kwargs: Any) -> dict[str, Any]:
            created.extend(dict(r) for r in kwargs.get("records", []))
            return {"records": [{"fields": dict(r)} for r in kwargs["records"]]}

        connector.search_records = fake_search  # type: ignore[assignment]
        connector.batch_create_records = fake_batch_create  # type: ignore[assignment]

        with pytest.raises(ConnectorOperationError):
            await sink.send(Envelope(body={"编号": "K-1"}))
        # Neither silently skipped nor blindly re-created.
        assert created == []

    asyncio.run(scenario())


def test_index_hit_first_write_of_a_new_key_needs_no_verification() -> None:
    """A key absent from the startup snapshot is written without any lookup.

    Verification only ever applies to keys the sink believes already exist; the
    first occurrence of a genuinely new record goes straight to the write path,
    so the common append case keeps its zero-search cost.
    """

    async def scenario() -> None:
        sink, connector = _make_indexed_sink(insert_keys=set(), batch_size=1)
        searches: list[dict[str, Any]] = []
        created: list[dict[str, Any]] = []

        async def fake_search(**kwargs: Any) -> dict[str, Any]:
            searches.append(kwargs)
            return {"items": [], "has_more": False}

        async def fake_batch_create(**kwargs: Any) -> dict[str, Any]:
            records = kwargs.get("records", [])
            created.extend(dict(r) for r in records)
            return {"records": [{"fields": dict(r)} for r in records]}

        connector.search_records = fake_search  # type: ignore[assignment]
        connector.batch_create_records = fake_batch_create  # type: ignore[assignment]

        await sink.send(Envelope(body={"编号": "K-NEW", "内容": "first"}))
        await asyncio.wait_for(sink.close(), timeout=5.0)

        # Written with no lookup, and the write was confirmed into the index.
        assert searches == []
        assert len(created) == 1
        assert sink._insert_keys == {"K-NEW"}

    asyncio.run(scenario())


def test_index_hit_verifies_the_startup_snapshot_key() -> None:
    """A startup-snapshot key is confirmed before it is skipped."""

    async def scenario() -> None:
        sink, connector = _make_indexed_sink(insert_keys={"K-1"}, batch_size=10)
        searches: list[dict[str, Any]] = []

        async def fake_search(**kwargs: Any) -> dict[str, Any]:
            searches.append(kwargs)
            return {"items": [{"record_id": "rec-1"}], "has_more": False}

        connector.search_records = fake_search  # type: ignore[assignment]

        await sink.send(Envelope(body={"编号": "K-1"}))
        await asyncio.wait_for(sink.close(), timeout=5.0)

        assert len(searches) == 1
        # The lookup is the exact single-key confirmation, not a blind trust.
        conditions = searches[0]["body"]["filter"]["conditions"]
        assert conditions[0]["field_name"] == "编号"
        assert conditions[0]["value"] == ["K-1"]

    asyncio.run(scenario())


def test_index_skip_verification_opt_in_restores_zero_search_fast_path() -> None:
    """The explicit opt-in keeps the zero-search behaviour, by design."""

    async def scenario() -> None:
        sink, connector = _make_indexed_sink(
            insert_keys={"K-1"},
            batch_size=10,
            insert_index_skip_verification=True,
        )
        searches: list[dict[str, Any]] = []

        async def fake_search(**kwargs: Any) -> dict[str, Any]:
            searches.append(kwargs)
            return {"items": [], "has_more": False}

        connector.search_records = fake_search  # type: ignore[assignment]

        await sink.send(Envelope(body={"编号": "K-1"}))
        await asyncio.wait_for(sink.close(), timeout=5.0)
        assert searches == []

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Configuration, validation and backward compatibility
# ---------------------------------------------------------------------------


def test_skip_verification_defaults_to_safe_and_is_overridable() -> None:
    connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")
    safe = connector.table_sink(
        app_token="app-token",
        table_id="tbl",
        mode="insert",
        match_fields=["编号"],
        insert_key_index=True,
    )
    assert safe.insert_index_skip_verification is False
    assert safe.close_drain_max_rounds >= 1

    opted_in = connector.table_sink(
        app_token="app-token",
        table_id="tbl",
        mode="insert",
        match_fields=["编号"],
        insert_key_index=True,
        insert_index_skip_verification=True,
        close_drain_max_rounds=7,
    )
    assert opted_in.insert_index_skip_verification is True
    assert opted_in.close_drain_max_rounds == 7


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"insert_index_skip_verification": "yes"}, "insert_index_skip_verification.*boolean"),
        ({"close_drain_max_rounds": 0}, "close_drain_max_rounds.*[>=1]"),
    ],
)
def test_new_options_reject_unsupported_values(
    overrides: dict[str, object], message: str
) -> None:
    connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")
    config: dict[str, object] = {
        "app_token": "app-token",
        "table_id": "tbl",
        "mode": "insert",
        "match_fields": ["编号"],
        "insert_key_index": True,
    }
    config.update(overrides)
    with pytest.raises((TypeError, ValueError), match=message):
        connector.table_sink(**config)


def test_yaml_accepts_new_options_and_keeps_old_configs_working() -> None:
    """New fields validate in strict mode; a pre-existing config is unchanged."""
    base_resource: dict[str, Any] = {
        "type": "feishu_bitable_table_sink",
        "connector": "feishu",
        "app_token": "token",
        "table_id": "table",
        "mode": "insert",
        "match_fields": ["编号"],
        "batch_size": 100,
        "insert_key_index": True,
    }
    connector_resource = {
        "type": "feishu_bitable",
        "app_id": "id",
        "app_secret": "secret",
    }

    # Backward compatibility: the configuration written before these options
    # existed must still load, with the safe defaults applied.
    legacy = load_app_config(
        {
            "apiVersion": "onestep/v1alpha1",
            "kind": "App",
            "app": {"name": "legacy"},
            "resources": {"feishu": connector_resource, "sink": dict(base_resource)},
            "tasks": [],
        },
        strict=True,
    )
    assert legacy.resources["sink"].insert_index_skip_verification is False
    assert legacy.resources["sink"].close_drain_max_rounds >= 1

    # The new options are accepted and threaded through.
    configured = load_app_config(
        {
            "apiVersion": "onestep/v1alpha1",
            "kind": "App",
            "app": {"name": "configured"},
            "resources": {
                "feishu": connector_resource,
                "sink": dict(
                    base_resource,
                    insert_index_skip_verification=True,
                    close_drain_max_rounds=25,
                ),
            },
            "tasks": [],
        },
        strict=True,
    )
    assert configured.resources["sink"].insert_index_skip_verification is True
    assert configured.resources["sink"].close_drain_max_rounds == 25
