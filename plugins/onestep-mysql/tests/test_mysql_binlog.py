from __future__ import annotations

import asyncio
from collections import deque
from types import SimpleNamespace

import pytest
from onestep_mysql import MySQLConnector, connector
from onestep_mysql.connector import _binlog_payload, _BinlogToken

from onestep.state import InMemoryCursorStore


class FailOnceStore(InMemoryCursorStore):
    def __init__(self, initial) -> None:
        super().__init__()
        self.initial = initial
        self.save_calls = 0

    async def load(self, key):
        if self.initial is not None:
            value, self.initial = self.initial, None
            return value
        return await super().load(key)

    async def save(self, key, value):
        self.save_calls += 1
        if self.save_calls == 1:
            raise RuntimeError("state unavailable")
        await super().save(key, value)


def _binlog_rows(*entries):
    return deque(
        (
            {"id": row_id, "binlog": {"file": "mysql-bin.000001", "pos": pos}},
            _BinlogToken("mysql-bin.000001", pos, row=row, rows=rows),
        )
        for row_id, pos, row, rows in entries
    )


def test_mysql_binlog_cursor_advances_in_order() -> None:
    async def scenario() -> None:
        db = MySQLConnector("sqlite:///:memory:")
        state = InMemoryCursorStore()
        source = db.binlog(
            server_id=18491,
            schemas=("onestep",),
            tables=("orders",),
            state=state,
            state_key="orders-cdc",
        )
        first = _BinlogToken(file="mysql-bin.000001", pos=120, row=0)
        second = _BinlogToken(file="mysql-bin.000001", pos=240, row=0)
        source._pending.append(first)
        source._pending.append(second)

        await source.ack_token(second)
        assert await state.load("orders-cdc") is None

        await source.ack_token(first)
        assert await state.load("orders-cdc") == {"file": "mysql-bin.000001", "pos": 240}
        await db.close()

    asyncio.run(scenario())


def test_mysql_binlog_cursor_waits_for_all_rows_in_same_event() -> None:
    async def scenario() -> None:
        db = MySQLConnector("sqlite:///:memory:")
        state = InMemoryCursorStore()
        source = db.binlog(
            server_id=18491,
            state=state,
            state_key="multi-row",
        )
        first = _BinlogToken(file="mysql-bin.000001", pos=120, row=0, rows=2)
        second = _BinlogToken(file="mysql-bin.000001", pos=120, row=1, rows=2)
        source._pending.append(first)
        source._pending.append(second)

        await source.ack_token(first)
        assert await state.load("multi-row") is None

        await source.ack_token(second)
        assert await state.load("multi-row") == {"file": "mysql-bin.000001", "pos": 120}
        await db.close()

    asyncio.run(scenario())


def test_mysql_binlog_payload_shapes_update_events() -> None:
    event = SimpleNamespace(schema="onestep", table="orders", timestamp=1710000000)
    payload = _binlog_payload(
        event,
        {
            "before_values": {"id": 1, "status": "pending"},
            "after_values": {"id": 1, "status": "paid"},
        },
        "update",
        file="mysql-bin.000001",
        pos=456,
    )

    assert payload == {
        "schema": "onestep",
        "table": "orders",
        "event": "update",
        "before_values": {"id": 1, "status": "pending"},
        "values": {"id": 1, "status": "paid"},
        "binlog": {
            "file": "mysql-bin.000001",
            "pos": 456,
            "timestamp": 1710000000,
        },
    }


@pytest.mark.parametrize(
    ("action", "expected_attempts"),
    [("retry", 1), ("release_unstarted", 0)],
)
def test_mysql_binlog_retry_and_release_redeliver_without_crossing_checkpoint(
    action: str, expected_attempts: int
) -> None:
    async def scenario() -> None:
        db = MySQLConnector("sqlite:///:memory:")
        state = InMemoryCursorStore()
        await state.save("cdc", {"file": "mysql-bin.000001", "pos": 100})
        source = db.binlog(server_id=18491, state=state, state_key="cdc")
        rows = _binlog_rows((1, 120, 0, 1), (2, 240, 0, 1))
        source._fetch_sync = lambda limit: [rows.popleft() for _ in range(min(limit, len(rows)))]

        first, = await source.fetch(1)
        await getattr(first, action)()
        redelivered, = await source.fetch(1)
        assert redelivered.payload["id"] == 1
        assert redelivered.envelope.attempts == expected_attempts
        assert await state.load("cdc") == {"file": "mysql-bin.000001", "pos": 100}

        await redelivered.ack()
        second, = await source.fetch(1)
        assert second.payload["id"] == 2
        await second.ack()
        assert await state.load("cdc") == {"file": "mysql-bin.000001", "pos": 240}
        assert await source.fetch(1) == []
        assert not source._pending
        await db.close()

    asyncio.run(scenario())


def test_mysql_binlog_terminal_fail_settles_token() -> None:
    async def scenario() -> None:
        db = MySQLConnector("sqlite:///:memory:")
        state = InMemoryCursorStore()
        await state.save("cdc", {"file": "mysql-bin.000001", "pos": 100})
        source = db.binlog(server_id=18491, state=state, state_key="cdc")
        rows = _binlog_rows((1, 120, 0, 1), (2, 240, 0, 1))
        source._fetch_sync = lambda limit: [rows.popleft() for _ in range(min(limit, len(rows)))]

        first, = await source.fetch(1)
        await first.fail(RuntimeError("poison record"))
        assert await state.load("cdc") == {"file": "mysql-bin.000001", "pos": 120}

        second, = await source.fetch(1)
        await second.ack()
        assert await state.load("cdc") == {"file": "mysql-bin.000001", "pos": 240}
        assert not source._pending
        await db.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("blocking", [False, True])
def test_mysql_binlog_delayed_retry_does_not_block_later_records(monkeypatch, blocking: bool) -> None:
    async def scenario() -> None:
        db = MySQLConnector("sqlite:///:memory:")
        state = InMemoryCursorStore()
        await state.save("cdc", {"file": "mysql-bin.000001", "pos": 100})
        source = db.binlog(server_id=18491, state=state, state_key="cdc", blocking=blocking)
        rows = _binlog_rows((1, 120, 0, 1), (2, 240, 0, 1))
        source._fetch_sync = lambda limit: [rows.popleft() for _ in range(min(limit, len(rows)))]

        first, = await source.fetch(1)
        now = 1000.0
        monkeypatch.setattr(connector.time, "monotonic", lambda: now)
        await first.retry(delay_s=60)
        second, = await source.fetch(1)
        assert second.payload["id"] == 2
        await second.ack()
        assert await state.load("cdc") == {"file": "mysql-bin.000001", "pos": 100}

        now += 60
        redelivered, = await source.fetch(1)
        assert redelivered.payload["id"] == 1
        await redelivered.ack()
        assert await state.load("cdc") == {"file": "mysql-bin.000001", "pos": 240}
        assert not source._pending
        await db.close()

    asyncio.run(scenario())


def test_mysql_binlog_state_save_failure_keeps_prefix_retryable() -> None:
    async def scenario() -> None:
        db = MySQLConnector("sqlite:///:memory:")
        state = FailOnceStore({"file": "mysql-bin.000001", "pos": 100})
        source = db.binlog(server_id=18491, state=state, state_key="cdc")
        rows = _binlog_rows((1, 120, 0, 1), (2, 240, 0, 1))
        source._fetch_sync = lambda limit: [rows.popleft() for _ in range(min(limit, len(rows)))]
        first, second = await source.fetch(2)

        await second.ack()
        with pytest.raises(RuntimeError, match="state unavailable"):
            await first.ack()

        assert list(source._pending) == [first._token, second._token]
        assert source._committed == _BinlogToken("mysql-bin.000001", 100)
        assert await state.load("cdc") is None

        await first.retry()
        redelivered, = await source.fetch(1)
        assert redelivered.payload["id"] == 1
        await redelivered.ack()
        assert await state.load("cdc") == {"file": "mysql-bin.000001", "pos": 240}
        assert not source._pending
        await db.close()

    asyncio.run(scenario())


def test_mysql_binlog_retry_waits_for_every_row_in_event() -> None:
    async def scenario() -> None:
        db = MySQLConnector("sqlite:///:memory:")
        state = InMemoryCursorStore()
        await state.save("cdc", {"file": "mysql-bin.000001", "pos": 100})
        source = db.binlog(server_id=18491, state=state, state_key="cdc")
        rows = _binlog_rows((1, 120, 0, 2), (2, 120, 1, 2))
        source._fetch_sync = lambda limit: [rows.popleft() for _ in range(min(limit, len(rows)))]

        first, = await source.fetch(1)
        await first.retry()
        second, = await source.fetch(1)
        assert second.payload["id"] == 1
        third, = await source.fetch(1)
        assert third.payload["id"] == 2
        await third.ack()
        assert await state.load("cdc") == {"file": "mysql-bin.000001", "pos": 100}
        await second.ack()
        assert await state.load("cdc") == {"file": "mysql-bin.000001", "pos": 120}
        await db.close()

    asyncio.run(scenario())
