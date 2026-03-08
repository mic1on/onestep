from onestep import MySQLConnector, OneStepApp

app = OneStepApp("orders")
db = MySQLConnector("mysql+pymysql://root:root@localhost:3306/app")
source = db.table_queue(
    table="orders",
    key="id",
    where="status = 0",
    claim={"status": 9},
    ack={"status": 1},
    nack={"status": 0},
    batch_size=100,
)
sink = db.table_sink(table="processed_orders", mode="upsert", keys=("id",))


@app.task(source=source, emit=sink, concurrency=16)
async def process_order(ctx, row):
    return {"id": row["id"], "payload": row["payload"], "status": "done"}
