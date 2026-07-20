from __future__ import annotations

import asyncio
from types import SimpleNamespace

from onestep.state import InMemoryCursorStore
from onestep_mysql import MySQLConnector
from onestep_mysql.connector import _BinlogToken, _binlog_payload


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
