from onestep import MemoryQueue, MySQLConnector, OneStepApp

app = OneStepApp("sync-users")
db = MySQLConnector("mysql+pymysql://root:root@localhost:3306/app")
state = db.cursor_store(table="onestep_cursor")
source = db.incremental(
    table="users",
    key="id",
    cursor=("updated_at", "id"),
    where="deleted = 0",
    batch_size=1000,
    state=state,
)
out = MemoryQueue("dw-users")


@app.task(source=source, emit=out, concurrency=1)
async def sync_user(ctx, row):
    return {"id": row["id"], "name": row["name"], "updated_at": row["updated_at"]}
