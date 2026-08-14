from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from onestep import Envelope, OneStepApp
from onestep.runtime.executor import DeliveryExecutor
from onestep.state import InMemoryCursorStore
from onestep_feishu_bitable import FeishuBitableConnector
from onestep_mysql import MySQLConnector


class _RecordingCursorStore(InMemoryCursorStore):
    def __init__(self) -> None:
        super().__init__()
        self.save_count = 0

    async def save(self, key: str, value: object) -> None:
        self.save_count += 1
        await super().save(key, value)


def test_indexed_insert_real_executor_holds_cursor_until_batch_confirmation(
    tmp_path: Path,
) -> None:
    db_url = f"sqlite:///{tmp_path / 'follow_records.db'}"
    engine = sa.create_engine(db_url, future=True)
    metadata = sa.MetaData()
    rows = sa.Table(
        "follow_records",
        metadata,
        sa.Column("unionKey", sa.String, primary_key=True),
        sa.Column("dataCreateTime", sa.Integer, nullable=False),
        sa.Column("编号", sa.String, nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            sa.insert(rows),
            [
                {
                    "unionKey": f"K-{index:06d}",
                    "dataCreateTime": index,
                    "编号": f"K-{index:06d}",
                }
                for index in range(1, 101)
            ],
        )
    engine.dispose()

    async def scenario() -> None:
        mysql = MySQLConnector(db_url)
        state = _RecordingCursorStore()
        source = mysql.incremental(
            table="follow_records",
            key="unionKey",
            cursor=("dataCreateTime", "unionKey"),
            batch_size=100,
            poll_interval_s=0.01,
            state=state,
            state_key="follow-record-sync-v1",
        )
        connector = FeishuBitableConnector(app_id="id", app_secret="secret")
        sink = connector.table_sink(
            app_token="token",
            table_id="table",
            mode="insert",
            match_fields=["编号"],
            batch_size=100,
            insert_key_index=True,
        )
        sink._insert_keys = set()
        sink._index_loaded = True
        write_started = asyncio.Event()
        release_write = asyncio.Event()

        async def batch_create(**kwargs: Any) -> dict[str, Any]:
            write_started.set()
            await release_write.wait()
            return {
                "records": [
                    {"record_id": f"rec-{index}"}
                    for index, _ in enumerate(kwargs["records"])
                ]
            }

        connector.batch_create_records = batch_create  # type: ignore[assignment]
        app = OneStepApp("follow-record-chain")

        @app.task(source=source, emit=sink, concurrency=100)
        async def passthrough(ctx: Any, item: dict[str, Any]) -> dict[str, Any]:
            return {"编号": item["编号"]}

        deliveries = await source.fetch(100)
        executor = DeliveryExecutor(app, app.tasks[0])
        executions = [
            asyncio.create_task(executor.execute(delivery)) for delivery in deliveries
        ]
        await asyncio.wait_for(write_started.wait(), timeout=1.0)
        assert await state.load("follow-record-sync-v1") is None
        assert not any(task.done() for task in executions)
        release_write.set()
        outcomes = await asyncio.gather(*executions)
        assert all(outcome.completion == "succeeded" for outcome in outcomes)
        assert await state.load("follow-record-sync-v1") == [100, "K-000100"]
        assert state.save_count == 1
        await mysql.close()

    asyncio.run(scenario())


def test_indexed_insert_100k_request_count_benchmark() -> None:
    existing_count = 50_000
    incoming_count = 100_000

    async def scenario() -> None:
        scan_requests = 0
        exact_search_requests = 0
        create_requests = 0
        create_batch_sizes: list[int] = []

        async def search_records(**kwargs: Any) -> dict[str, Any]:
            nonlocal scan_requests, exact_search_requests
            body = kwargs["body"]
            if "field_names" in body:
                scan_requests += 1
                page = scan_requests - 1
                start = page * 500
                stop = min(start + 500, existing_count)
                return {
                    "items": [
                        {"fields": {"编号": f"K-{index:06d}"}}
                        for index in range(start, stop)
                    ],
                    "has_more": stop < existing_count,
                    "page_token": str(page + 1) if stop < existing_count else None,
                }
            exact_search_requests += 1
            return {"items": [], "has_more": False}

        async def batch_create(**kwargs: Any) -> dict[str, Any]:
            nonlocal create_requests
            create_requests += 1
            create_batch_sizes.append(len(kwargs["records"]))
            return {
                "records": [
                    {"record_id": f"rec-{index}"}
                    for index, _ in enumerate(kwargs["records"])
                ]
            }

        connector = FeishuBitableConnector(app_id="id", app_secret="secret")
        connector.search_records = search_records  # type: ignore[assignment]
        connector.batch_create_records = batch_create  # type: ignore[assignment]
        sink = connector.table_sink(
            app_token="token",
            table_id="table",
            mode="insert",
            match_fields=["编号"],
            batch_size=100,
            insert_key_index=True,
            insert_index_page_size=500,
            insert_index_max_pages=200,
        )
        await sink.open()
        for offset in range(0, incoming_count, 100):
            await asyncio.gather(
                *(
                    sink.send(
                        Envelope(body={"编号": f"K-{index:06d}"})
                    )
                    for index in range(offset, offset + 100)
                )
            )
        await sink.close()
        assert scan_requests == 100
        assert exact_search_requests == 0
        assert create_requests == 500
        assert scan_requests + create_requests == 600
        assert create_batch_sizes == [100] * 500
        assert sink.inflight_waiter_count == 0
        assert not hasattr(sink, "record_ids")

    asyncio.run(scenario())
