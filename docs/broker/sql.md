---
title: SQL（MySQL / PostgreSQL） | Broker
outline: deep
---

# SQL <Badge type="tip" text="MySQL" /> <Badge type="tip" text="PostgreSQL" />

`onestep-sql` 是 MySQL 与 PostgreSQL 的规范发行包（issue #133），提供统一的 Python API 和 YAML 资源模型。两个后端共用同一套 Source/Sink 抽象：表队列、增量轮询、表输出、游标/状态存储，差异仅在少量 SQL 方言和驱动参数。

```bash
# MySQL
pip install 'onestep-sql[mysql]'
# PostgreSQL
pip install 'onestep-sql[postgres]'
# 两者同时
pip install 'onestep-sql[mysql,postgres]'
```

::: info
旧版 `onestep-mysql` 与 `onestep-postgres` 现已作为转发 shim 保留兼容性，新项目请直接使用 `onestep-sql`。Python 导入路径 `from onestep_mysql import ...` / `from onestep_postgres import ...` 仍然可用。
:::

除表队列、增量同步与表输出外，`onestep-sql` 还提供跟踪长任务执行（Tracked Execution），把数据库用作长任务的提交、状态、结果、取消和租约存储：MySQL 见 [MySQL Tracked Execution](/broker/mysql-execution)，PostgreSQL 见 [PostgreSQL Tracked Execution](/broker/postgres-execution)。

## 表队列 (Table Queue)

通过数据库行锁领取任务，把表作为 durable queue。

::: code-group

```python [MySQL]
from onestep_sql.mysql import MySQLConnector

db = MySQLConnector("mysql+pymysql://user:pass@localhost/app")
source = db.table_queue(
    table="orders",
    key="id",
    where="status = 0",
    claim={"status": 9},
    ack={"status": 1},
    nack={"status": 0},
    batch_size=100,
)
```

```python [PostgreSQL]
from onestep_sql.postgres import PostgresConnector

db = PostgresConnector("postgresql+psycopg://user:pass@localhost/app")
source = db.table_queue(
    table="orders",
    key="id",
    where="status = 0",
    claim={"status": 9},
    ack={"status": 1},
    nack={"status": 0},
    batch_size=100,
)
```

:::

### 工作流程

1. 查询 `status = 0` 的记录
2. 批量更新 `status = 9`（领取）
3. 执行任务
4. 成功：更新 `status = 1`
5. 失败：更新 `status = 0`（可重试）

```python
# 状态流转示意
where="status = 'pending'"      # 待处理
claim={"status": "processing"}  # 处理中
ack={"status": "completed"}     # 已完成
nack={"status": "failed"}       # 失败
```

### 单事务完成 complete()

表队列 delivery 的 `update_current_row(values)` 与 `ack()` 是两个独立事务：
两步之间进程崩溃，会留下"业务列已写、ack 未写"的行。多数场景 `where`
条件会掩盖它，但这是 at-least-once 重复窗口的根源之一。

`complete(values)` 把行回写与确认合并为**单个数据库事务**：在同一个
事务里先 `UPDATE` 业务列、再 `UPDATE` ack 字段，两步全成功才提交。

```python
@app.task(source=source, emit=sink)
async def process_order(ctx, row):
    # 单事务内写入业务列并确认完成（如 status = 1 / 'done'）
    await ctx.complete({"score": row["id"] * 10})
    return {
        "id": row["id"],
        "status": "done"
    }
```

也可以直接在 delivery 上调用：`await delivery.complete(values)`。

#### 与两步 API 的关系

- **纯增量**：`complete(values)` 是新增 API，旧的
  `update_current_row(values)` + `ack()` 两步用法行为完全不变。
- `ack={}` 时 `complete(values)` 等价于 `update_current_row(values)`：
  只剩业务列那一条 `UPDATE`。
- `values` 与 `ack` 都为空时不做任何写入。

#### 原子性与幂等

- **原子性**：`complete()` 中途失败（例如第二条语句报错）时整个事务回滚，
  两个写入都不生效，行停留在 claim 态，按正常失败路径走 `nack` /
  重试；不会出现"业务列已写、ack 未写"的半成品。
- **ack 幂等**：`complete()` 成功后会在 delivery 上置位
  `_complete_ack_applied`。executor 在 handler 返回后仍会按惯例调用
  `delivery.ack()`，此时该调用直接跳过，不再重复发一次 `UPDATE`。
  （即使跳过，重复写 ack 本身也是幂等的，这里只是省掉一次冗余写。）
- envelope body 只镜像业务 `values`，ack 字段属队列簿记，不会混入 body。
- Lint 提示：`claim` 非空而 `nack` 为空时 `onestep check` 会告警（见
  issue #180）——失败行会无声滞留在 claim 态，`complete()` 不改变该语义。

> 该 API 由 `onestep-sql` 的共享实现（`onestep_sql/_shared/table_queue.py`）
> 提供，MySQL、PostgreSQL、SQLite 三后端语义一致。

## 增量同步 (Incremental Sync)

基于 `(updated_at, id)` 实现增量数据同步，适合数据仓库场景。

::: code-group

```python [MySQL]
from onestep_sql.mysql import MySQLConnector

db = MySQLConnector("mysql+pymysql://user:pass@localhost/app")
cursor = db.cursor_store(table="onestep_cursor")

source = db.incremental(
    table="users",
    key="id",
    cursor=("updated_at", "id"),
    where="deleted = 0",
    batch_size=1000,
    state=cursor,
)
```

```python [PostgreSQL]
from onestep_sql.postgres import PostgresConnector

db = PostgresConnector("postgresql+psycopg://user:pass@localhost/app")
cursor = db.cursor_store(table="onestep_cursor")

source = db.incremental(
    table="users",
    key="id",
    cursor=("updated_at", "id"),
    where="deleted = 0",
    batch_size=1000,
    state=cursor,
)
```

:::

### 工作原理

1. 从 `cursor_store` 读取上次位置
2. 查询 `updated_at > last_updated OR (updated_at = last_updated AND id > last_id)`
3. 处理数据
4. 更新 `cursor_store` 中的位置

MySQL 查询将复合游标展开为范围条件，避免行构造器不等式在部分执行计划中反复扫描已处理的索引前缀。仍需为完整有效游标建立同序索引；配置未包含 `key` 时连接器会自动追加它。

### 游标存储

```python
# 数据库存储（推荐生产环境）
cursor_store = db.cursor_store(table="sync_cursor")

# 或状态存储
state_store = db.state_store(table="onestep_state")
```

## 表输出 (Table Sink)

将处理结果写入数据库表。Python API 在两个后端下签名完全一致。

### Upsert 模式

```python
sink = db.table_sink(
    table="results",
    mode="upsert",
    keys=("id",),  # 唯一键，存在则更新，不存在则插入
)
```

> 注意：MySQL 的 `upsert` 生成 `INSERT ... ON DUPLICATE KEY UPDATE`。即使键已存在走更新分支，MySQL 仍会对 INSERT 部分做约束检查——目标表存在无默认值的 `NOT NULL` 列且载荷未提供这些列时，会产生 `Field 'xxx' doesn't have a default value` warning（更新本身仍会成功）。只需要更新已有行时请改用 `mode="update"`。

### Insert 模式

```python
sink = db.table_sink(
    table="logs",
    mode="insert",  # 仅插入
)
```

### Update 模式

只更新已存在的行，绝不插入新行（`UPDATE ... WHERE`）：

```python
sink = db.table_sink(
    table="bidding",
    mode="update",
    keys=("id",),
    update_columns=("deadline", "tender_deadline"),
)
```

- 适合"目标行由其他流程创建、本任务只回填部分字段"的场景。
- 目标行不存在时跳过该条并记录 INFO 日志，不报错。
- 不生成 `INSERT` 语句，不存在误插新行的风险。

### 更新控制（Upsert / Update 行为）

```python
sink = db.table_sink(
    table="results",
    mode="upsert",
    keys=("id",),
    update_columns=("data",),                # 只重写这些列
    update_expr={"updated_at": "NOW(6)"},    # 原始 SQL 表达式
)
```

- `update_columns`：允许重写的白名单列；默认重写除 `keys` 外的所有载荷列。
- `update_expr`：列名到原始 SQL 表达式的映射（例如 `updated_at=NOW(6)`）。
- 两者仅适用于 `upsert` 和 `update` 模式。

### 按列写入策略（null 保护）

`update_columns` 的条目可以是列名（默认无条件覆盖），也可以是 `{name, policy}` 对象：

| policy | 行为 | 生成 SQL |
|---|---|---|
| `overwrite`（默认） | 无条件用载荷值覆盖，载荷 `null` 也会写入 `NULL` | `SET col = :val` |
| `skip_null` | 载荷值为 `null` 时该列不写，保留库中原值 | `null` → 列从 `SET` 剔除 |
| `backfill` | 只在库中当前值为 `NULL` 时写入载荷值 | `SET col = COALESCE(col, :val)` |

```yaml
results:
  type: mysql_table_sink       # 或 postgres_table_sink
  connector: db
  table: bidding
  mode: update
  keys: [id]
  update_columns:
    - deadline                # 无条件覆盖
    - name: tenderee
      policy: skip_null       # 载荷 null 不写
    - name: publish_date
      policy: backfill        # 只回填空值
```

Python 侧同样接受混合条目：

```python
sink = db.table_sink(
    table="bidding",
    mode="update",
    keys=("id",),
    update_columns=(
        "deadline",
        {"name": "tenderee", "policy": "skip_null"},
    ),
)
```

注意事项：
- 策略对 `update` 和 `upsert` 同样生效。
- `skip_null` 过滤后整个 `SET` 为空时，该条跳过并记录 INFO 日志。
- 策略列不能是 `keys` 中的列，也不能与 `update_expr` 中同列的原始 SQL 表达式同时配置。

### JSON 序列化控制

```python
sink = db.table_sink(
    table="results",
    mode="insert",
    serialize_json="always",   # 强制序列化为 JSON 字符串
)
```

`serialize_json` 可选值：`auto`（默认，JSON 列原样写入、其他列序列化为字符串）、`always`、`never`。

## 状态存储

### State Store

键值对存储，用于任务状态：

```python
state = db.state_store(table="onestep_state")

@app.task(source=...)
async def process(ctx, item):
    count = await ctx.state.get("processed_count", 0)
    await ctx.state.set("processed_count", count + 1)
```

### Cursor Store

游标存储，用于增量同步位置：

```python
cursor = db.cursor_store(table="sync_cursor")

source = db.incremental(
    table="orders",
    key="id",
    cursor=("updated_at", "id"),
    state=cursor,
)
```

## YAML 配置

MySQL 与 PostgreSQL 在 YAML 中使用不同的资源类型前缀。

::: code-group

```yaml [MySQL]
resources:
  db:
    type: mysql
    dsn: "mysql+pymysql://root:root@localhost:3306/app"

  order_queue:
    type: mysql_table_queue
    connector: db
    table: orders
    key: id
    where: "status = 0"
    claim: {status: 9}
    ack: {status: 1}
    batch_size: 100

  results:
    type: mysql_table_sink
    connector: db
    table: results
    mode: upsert
    keys: [id]

  cursor:
    type: mysql_cursor_store
    connector: db
    table: sync_cursor

tasks:
  - name: process_orders
    source: order_queue
    emit: results
    concurrency: 16
```

```yaml [PostgreSQL]
resources:
  db:
    type: postgres
    dsn: "postgresql+psycopg://user:pass@localhost/app"

  order_queue:
    type: postgres_table_queue
    connector: db
    table: orders
    key: id
    where: "status = 0"
    claim: {status: 9}
    ack: {status: 1}
    batch_size: 100

  results:
    type: postgres_table_sink
    connector: db
    table: results
    mode: upsert
    keys: [id]

  cursor:
    type: postgres_cursor_store
    connector: db
    table: sync_cursor

tasks:
  - name: process_orders
    source: order_queue
    emit: results
    concurrency: 16
```

:::

## 最佳实践

### 1. 索引优化

```sql
-- 表队列：确保查询条件有索引
CREATE INDEX idx_status ON orders(status);

-- 增量同步：确保游标字段有索引
CREATE INDEX idx_cursor ON users(updated_at, id);
```

### 2. 读取批量与处理并发

默认 `prefetch=False`：每次 SQL 的上限是 `min(batch_size, runtime 当前空闲并发槽)`。例如 `batch_size=500`、`concurrency=8` 时，每次最多读 8 行。

从 `onestep-sql 0.3.0` 起，可对增量源显式启用有界预取：

```python
source = db.incremental(
    table="users", key="id", cursor=("updated_at",),
    batch_size=100, prefetch=True, state=cursor_store,
)

@app.task(source=source, concurrency=8)
async def sync_user(ctx, row):
    ...
```

YAML 对应 `prefetch: true` 和 `batch_size: 100`。SQL 一次最多读 100 行，每次只交付空闲槽所需行数；剩余行在 connector 缓冲，缓冲耗尽后才补读。

- 未交付行缓冲最多 `batch_size` 行。
- 预取不会提前保存持久游标。重试优先于缓冲行。
- 暂停/退出会等待正在进行的 SELECT 结束。
- 先用 50、100、500 条与固定处理并发做对照，观察 SQL 次数、内存和业务耗时。

### 3. 并发控制

```python
# 表队列：可高并发（行级锁）
@app.task(source=source, concurrency=16)

# 增量同步：可并发处理
@app.task(source=incremental, concurrency=100)
```

### 4. 连接池

```python
db = MySQLConnector(
    "mysql+pymysql://user:pass@host/db"
    "?pool_size=10&max_overflow=20&pool_recycle=3600"
)
```

### 5. 可靠持久游标与重试

生产增量同步应显式绑定 `*_cursor_store` 和稳定 `state_key`。持久游标只推进到连续成功前缀；失败重试会重新投递同一逻辑行，缺口重试期间不会继续发出后续 SQL 查询。进程重启从已持久游标恢复，未提交的行会重放。

从 `onestep-sql 0.3.0` 起，游标中的 MySQL `DATETIME` 组件以带类型标记的 ISO-8601 JSON 保存，重启后恢复为原始 `datetime`（保留微秒）。已有的纯 JSON 游标继续兼容。

### 6. MySQL Binlog CDC

MySQL 增量源支持 binlog CDC 模式：

```python
from onestep_sql.mysql import MySQLConnector

db = MySQLConnector("mysql+pymysql://user:pass@localhost/app")

source = db.binlog(
    server_id=100,
    schemas=("myapp",),
    tables=("orders",),
    events=("insert", "update", "delete"),
    batch_size=100,
    state=cursor_store,
)

@app.task(source=source, concurrency=1)
async def handle_row_change(ctx, row):
    # row.schema, row.table, row.event (insert/update/delete)
    # row.values 是变更后的列值；update 还带 row.before_values
    ...
```

参数说明：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `server_id` | 必填 | 此 consumer 在 MySQL 主从协议上的唯一 ID，不可重复 |
| `schemas` | `()` | 监听的数据库名（空 = 全部） |
| `tables` | `()` | 监听的表名（空 = 全部） |
| `events` | `("insert","update","delete")` | 监听的事件类型 |
| `batch_size` | `100` | 每次拉取的 binlog 事件批量 |
| `poll_interval_s` | `1.0` | 无事件时轮询间隔 |
| `state` | 内存存储 | 持久化游标位置 |
| `blocking` | `False` | 是否阻塞等待新事件 |

安装 `onestep-sql[mysql]` 即包含 `mysql-replication` 依赖。该模式需要 MySQL 开启 binlog 且为 `ROW` 格式。

#### 确认与恢复

`mysql_binlog` 按 binlog event 的完整连续前缀保存 `{file, pos}`。
同一 event 的多行即使分批获取、乱序处理，也必须全部完成后才能推进到该 event 末尾。
游标存储写入失败或被取消时，内存中的未完成屏障保留，允许再次结算。

- `retry()` 将原记录放入本地重投队列，保留游标屏障，并将 `Envelope.attempts` 加一。
  `MaxAttempts` 因而能到达终态；重试延迟只控制再次投递的时间，不阻塞结算调用。
- `release_unstarted()` 重投未启动的记录，不增加尝试次数。
- 终态 `fail()` 完成该行的结算。配置了死信输出时，运行时先成功写入死信，再推进游标；
  未配置死信时，终态失败表示丢弃该记录。需要保留失败数据的任务应配置死信输出。
- 重投队列和尝试次数仅在当前进程内保存。进程重启或关闭后重开 source，会从持久化游标
  重放尚未完整确认的 event，尝试次数从零开始，其中已成功的行也可能重复执行；handler
  和 sink 需要幂等。单个 delivery 重复调用结算方法不会重复重投或跨过新的 delivery。

`blocking=True` 在没有记录时等待；内部使用非阻塞 binlog 读取与 `poll_interval_s` 间隔的
异步轮询，因此暂停或关闭不需要等待下一条数据库变更。取消 fetch 时，已启动的读取结果
仍由 source 保留，恢复获取时继续交付；关闭时先等待当前读取结束，再关闭连接。

## PostgreSQL 跟踪长任务执行

`onestep-sql[postgres]` 可以将 PostgreSQL 作为任务状态、结果和租约的单一事实源。FastAPI 使用 core 的 `ExecutionClient`，worker 使用 `PostgresExecutionSource`：

```python
from onestep import ExecutionClient
from onestep_sql.postgres import PostgresExecutionBackend, PostgresExecutionSource

backend = PostgresExecutionBackend(
    dsn="postgresql+psycopg://app:secret@db/app",
    auto_create=True,
    reclaim_batch_size=100,
)
client = ExecutionClient(backend, namespace="agent-api")
```

完整业务接入流程和状态机说明见 [PostgreSQL Tracked Execution](/broker/postgres-execution)。

## 下一步

- [PostgreSQL Tracked Execution](/broker/postgres-execution) - 长任务调度、状态机、租约
- [迁移到 onestep-sql](/guide/migrate-to-onestep-sql) - 从 `onestep-mysql` / `onestep-postgres` 迁移
- [YAML 任务定义](/yaml-task-definition) - 插件资源注册和严格校验
- [核心可靠性](/core-reliability) - at-least-once 和重复输出语义