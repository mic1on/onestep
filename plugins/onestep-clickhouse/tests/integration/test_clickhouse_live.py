from __future__ import annotations

import os
import uuid

import clickhouse_connect
import pytest

from onestep import Envelope
from onestep_clickhouse import ClickHouseConnector

DSN = os.getenv("ONESTEP_CLICKHOUSE_DSN")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DSN, reason="ONESTEP_CLICKHOUSE_DSN is not configured"
    ),
]


@pytest.mark.asyncio
async def test_live_mapping_sequence_chunking_and_visibility() -> None:
    table = f"onestep_{uuid.uuid4().hex}"
    admin = await clickhouse_connect.get_async_client(dsn=DSN)
    await admin.command(
        f"CREATE TABLE {table} (id UInt64, note Nullable(String)) "
        "ENGINE = MergeTree ORDER BY id"
    )
    connector = ClickHouseConnector(DSN)
    sink = connector.table_sink(
        table=table, columns=("id", "note"), batch_size=2
    )
    try:
        await sink.send(Envelope(body={"id": 1, "note": None}))
        await sink.send(
            Envelope(
                body=[
                    {"id": 2, "note": "two"},
                    {"id": 3, "note": "three"},
                    {"id": 4, "note": None},
                ]
            )
        )
        rows = await admin.query(f"SELECT id, note FROM {table} ORDER BY id")
        assert rows.result_rows == [
            (1, None),
            (2, "two"),
            (3, "three"),
            (4, None),
        ]
    finally:
        await admin.command(f"DROP TABLE IF EXISTS {table}")
        await connector.close()
        await admin.close()
