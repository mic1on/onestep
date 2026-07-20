---
title: PostgreSQL | Broker
outline: deep
---

# PostgreSQL

`onestep-postgres` 提供 PostgreSQL 版的表队列、增量轮询、表输出，以及 SQLAlchemy-backed 状态/游标存储。第一版不包含 logical replication 或 CDC。

## 安装

```bash
pip install onestep-postgres
```

## 基本用法

```python
from onestep import OneStepApp
from onestep_postgres import PostgresConnector

app = OneStepApp("pg-sync")
pg = PostgresConnector("postgresql+psycopg://user:pass@localhost/app")

cursor = pg.cursor_store(table="onestep_cursor")
source = pg.incremental(
    table="users",
    key="id",
    cursor=("updated_at", "id"),
    state=cursor,
)
sink = pg.table_sink(
    table="processed_users",
    mode="upsert",
    keys=("id",),
)


@app.task(source=source, emit=sink, concurrency=4)
async def sync_user(ctx, row):
    return {
        "id": row["id"],
        "name": row["name"],
        "updated_at": row["updated_at"],
    }
```

## 表队列

表队列通过 PostgreSQL 行锁领取任务，适合把数据库表作为 durable queue。

```python
source = pg.table_queue(
    table="jobs",
    key="id",
    where="status = 'pending'",
    claim={"status": "processing"},
    ack={"status": "done"},
    nack={"status": "pending"},
    batch_size=100,
)
```

## YAML 配置

安装插件后，YAML 可以使用 `postgres*` 资源类型：

```yaml
resources:
  pg:
    type: postgres
    dsn: "${POSTGRES_DSN}"

  cursor:
    type: postgres_cursor_store
    connector: pg

  users:
    type: postgres_incremental
    connector: pg
    table: users
    key: id
    cursor: [updated_at, id]
    state: cursor

  processed:
    type: postgres_table_sink
    connector: pg
    table: processed_users
    mode: upsert
    keys: [id]

tasks:
  - name: sync_users
    source: users
    emit: processed
    handler:
      ref: worker.tasks:sync_user
```

## 注意事项

- 增量轮询会在 delivery `ack()` 后持久化游标。
- `table_sink(mode="upsert")` 需要配置 `keys`。
- 需要 CDC 时继续使用 MySQL binlog 或为 PostgreSQL 单独设计 logical replication 流程。

## 下一步

- [YAML 任务定义](/yaml-task-definition) - 查看插件资源注册和严格校验
- [核心可靠性](/core-reliability) - 理解 at-least-once 和重复输出语义
