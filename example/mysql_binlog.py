import os

from onestep import OneStepApp
from onestep_mysql import MySQLConnector

app = OneStepApp("orders-cdc")
db = MySQLConnector(os.getenv("MYSQL_DSN", "mysql+pymysql://root:root@localhost:3306/app"))
state = db.cursor_store(table="onestep_cursor")
source = db.binlog(
    server_id=int(os.getenv("MYSQL_BINLOG_SERVER_ID", "18492")),
    schemas=(os.getenv("MYSQL_SCHEMA", "app"),),
    tables=("orders",),
    events=("insert", "update", "delete"),
    state=state,
    state_key="orders-cdc",
    batch_size=100,
)
sink = db.table_sink(table="order_change_log", mode="upsert", keys=("binlog_file", "binlog_pos", "row_event"))


@app.task(source=source, emit=sink, concurrency=4)
async def record_order_change(ctx, change):
    values = change["values"]
    return {
        "binlog_file": change["binlog"]["file"],
        "binlog_pos": change["binlog"]["pos"],
        "row_event": change["event"],
        "order_id": values.get("id"),
        "event_type": change["event"],
        "status": values.get("status"),
    }
