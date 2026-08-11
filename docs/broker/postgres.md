---
title: PostgreSQL | Broker
outline: deep
---

# PostgreSQL

`onestep-postgres` 提供 PostgreSQL 版的表队列、增量轮询、表输出，以及 SQLAlchemy-backed 状态/游标存储。第一版不包含 logical replication 或 CDC。

长任务的完整业务接入流程见 [PostgreSQL Tracked Execution](/broker/postgres-execution)。

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

## 跟踪长任务执行

`onestep-postgres` 也可以把 PostgreSQL 作为任务状态、结果和租约的单一
事实源。FastAPI 使用 core 的 `ExecutionClient`，worker 直接使用
`PostgresExecutionSource`：

```python
from onestep import ExecutionClient
from onestep_postgres import PostgresExecutionBackend, PostgresExecutionSource

backend = PostgresExecutionBackend(
    dsn="postgresql+psycopg://app:secret@db/app",
    auto_create=True,
    reclaim_batch_size=100,
)
step = ExecutionClient(backend, namespace="agent-api")

async with step:
    execution = await step.submit(
        "run_agent",
        payload,
        idempotency_key=request_id,
    )
```

```python
source = PostgresExecutionSource(
    dsn="postgresql+psycopg://app:secret@db/app",
    auto_create=False,
    namespace="agent-api",
    task_names=("run_agent",),
    worker_id="agent-worker-1",
)
```

每个 execution source 只能配置一个任务名，并且必须与绑定该 source 的 app
task 名一致。需要处理多个任务时，为每个任务创建独立的 source。

状态共有 `queued`、`running`、`retrying`、`succeeded`、`failed`、
`cancel_requested`、`cancelled` 和 `expired`。`Execution` 是不可变快照，
不会在属性访问时自动刷新；需要最新状态时重新调用 `get()` 或 `list()`。
`result()` 只查询一次，未完成状态抛出 `ExecutionNotReady`，失败、取消、
过期分别抛出对应类型异常，成功结果可以是 `None`。

默认 payload/result 内联上限各为 1 MiB，metadata 上限为 64 KiB。任务提交
通过 namespace、task 名和幂等键去重；相同内容返回原记录，不同内容抛出
冲突。租约要求 `0 < heartbeat_interval_s <= lease_duration_s / 3`。系统是
at-least-once，取消是协作式的，handler 的外部副作用仍需要业务幂等。生产
部署建议先建表，再将 `auto_create=False`，避免运行身份需要 DDL 权限。

运行时的受管完成路径会把 handler 返回值写入成功记录。直接调用 execution
delivery 的传统 `ack()` 只能记录 `succeeded` 且 `result=None`，因为公共
`Delivery.ack()` API 没有结果参数。
如果 worker 提交 `succeeded` 时 execution 已是 `cancel_requested`，取消优先
（cancel-won）：execution 最终为 `cancelled`，worker 返回的 result/error 都不
写入 execution；对应 attempt 会记为 `cancelled`、`error=NULL`，attempt 表不保存
result。这是有意的历史语义，不表示 handler 没有返回值。

worker 会在租约仍有效时，对可重试的 PostgreSQL 心跳错误执行有限指数退避；
不可重试错误、过期租约或重试耗尽会取消当前处理任务。过期执行和停滞租约的
恢复由 source 的 `claim()` 驱动，没有独立 reaper。每次 claim 对每类停滞状态
最多处理 `reclaim_batch_size` 条，活跃轮询会逐批清空积压；没有 worker 轮询时，
状态会保留到下一次 claim。`PostgresConnector.secret_tokens()` 返回用于错误脱敏
的独立副本，调用方不应记录其中内容。

## 下一步

- [YAML 任务定义](/yaml-task-definition) - 查看插件资源注册和严格校验
- [核心可靠性](/core-reliability) - 理解 at-least-once 和重复输出语义
