import asyncio
from pathlib import Path

import sqlalchemy as sa

from onestep import MySQLConnector, OneStepApp


def test_mysql_table_queue_round_trip(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'queue.db'}"
    engine = sa.create_engine(db_url, future=True)
    metadata = sa.MetaData()
    orders = sa.Table(
        "orders",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("payload", sa.String, nullable=False),
        sa.Column("status", sa.Integer, nullable=False),
        sa.Column("score", sa.Integer),
    )
    processed = sa.Table(
        "processed_orders",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("payload", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            sa.insert(orders),
            [
                {"id": 1, "payload": "A", "status": 0, "score": None},
                {"id": 2, "payload": "B", "status": 0, "score": None},
            ],
        )
    engine.dispose()

    seen_scores: list[tuple[int, int]] = []

    async def scenario() -> None:
        app = OneStepApp("table-queue")
        db = MySQLConnector(db_url)
        source = db.table_queue(
            table="orders",
            key="id",
            where="status = 0",
            claim={"status": 9},
            ack={"status": 1},
            nack={"status": 0},
            batch_size=10,
            poll_interval_s=0.01,
        )
        sink = db.table_sink(table="processed_orders", mode="upsert", keys=("id",))
        seen: list[int] = []

        @app.task(source=source, emit=sink, concurrency=2)
        async def process(ctx, row):
            await ctx.update_current_row({"score": row["id"] * 10})
            seen.append(row["id"])
            seen_scores.append((row["id"], row["score"]))
            if len(seen) == 2:
                ctx.app.request_shutdown()
            return {"id": row["id"], "payload": row["payload"], "status": "done"}

        await app.serve()
        await db.close()

    asyncio.run(scenario())

    verify_engine = sa.create_engine(db_url, future=True)
    with verify_engine.begin() as conn:
        order_rows = conn.execute(sa.select(orders.c.id, orders.c.status, orders.c.score).order_by(orders.c.id)).all()
        processed_rows = conn.execute(
            sa.select(processed.c.id, processed.c.payload, processed.c.status).order_by(processed.c.id)
        ).all()
    verify_engine.dispose()

    assert seen_scores == [(1, 10), (2, 20)]
    assert order_rows == [(1, 1, 10), (2, 1, 20)]
    assert processed_rows == [(1, "A", "done"), (2, "B", "done")]
