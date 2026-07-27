from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId

from onestep_mongodb.connector import _ContiguousGenerationTracker
from onestep_mongodb.state_codec import decode_state, encode_state


def test_extended_json_round_trips_bson_values() -> None:
    value = [ObjectId("64b64c1234567890abcdef12"), datetime(2026, 7, 27, tzinfo=timezone.utc)]
    encoded = encode_state(value)
    assert isinstance(encoded, dict)
    assert decode_state(encoded) == value


@pytest.mark.asyncio
async def test_out_of_order_ack_does_not_cross_gap() -> None:
    saved: list[object] = []

    async def save(token):
        saved.append(token)

    tracker = _ContiguousGenerationTracker(save)
    first = tracker.add("one")
    second = tracker.add("two")
    await tracker.complete(second, advance=True)
    assert saved == []
    await tracker.complete(first, advance=True)
    assert saved == ["two"]


@pytest.mark.asyncio
async def test_invalidated_generation_ignores_late_ack_and_blocks_reopen() -> None:
    saved: list[object] = []
    tracker = _ContiguousGenerationTracker(lambda token: _append(saved, token))
    first = tracker.add("one")
    second = tracker.add("two")
    await tracker.invalidate(first.generation)
    assert tracker.can_fetch is False
    await tracker.complete(first, advance=True)
    assert saved == [] and tracker.can_fetch is False
    await tracker.complete(second, advance=False)
    assert tracker.can_fetch is True


async def _append(values, value):
    values.append(value)
