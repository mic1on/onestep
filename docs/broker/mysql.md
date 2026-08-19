---
title: MySQL | Broker
outline: deep
---

# MySQL

MySQL Connector 提供三种模式：
- **表队列**: 将数据库表作为任务队列
- **增量同步**: 基于 `(updated_at, id)` 的 Logstash 风格同步
- **表输出**: 将结果写入数据库表

## 安装

```bash
pip install onestep-mysql
```

## 表队列 (Table Queue)

将数据库表作为任务队列，通过更新状态字段来"领取"任务。

### 基本用法

```python
from onestep import OneStepApp
from onestep_mysql import MySQLConnector

app = OneStepApp("orders")

# 创建连接
db = MySQLConnector("mysql+pymysql://root:root@localhost:3306/app")

# 创建表队列 Source
source = db.table_queue(
    table="orders",
    key="id",
    where="status = 0",           # 查询条件：待处理
    claim={"status": 9},          # 领取时设置：处理中
    ack={"status": 1},            # 成功后设置：已完成
    nack={"status": 0},           # 失败后设置：待处理（可重试）
    batch_size=100,               # 每次领取数量
)

# 创建表输出 Sink
sink = db.table_sink(
    table="processed_orders",
    mode="upsert",                # 插入或更新
    keys=("id",),                 # 唯一键
)


@app.task(source=source, emit=sink, concurrency=16)
async def process_order(ctx, row):
    return {
        "id": row["id"],
        "payload": row["payload"],
        "status": "done"
    }


if __name__ == "__main__":
    app.run()
```

### 工作流程

1. 查询 `status = 0` 的记录
2. 批量更新 `status = 9`（领取）
3. 执行任务
4. 成功：更新 `status = 1`
5. 失败：更新 `status = 0`（可重试）

### 状态管理

```python
# 状态流转
where="status = 'pending'"    # 待处理
claim={"status": "processing"} # 处理中
ack={"status": "completed"}   # 已完成
nack={"status": "failed"}     # 失败
```

## 增量同步 (Incremental Sync)

基于 `(updated_at, id)` 实现增量数据同步，适合数据仓库场景。

### 基本用法

```python
from onestep import MemoryQueue, OneStepApp
from onestep_mysql import MySQLConnector

app = OneStepApp("sync-users")
db = MySQLConnector("mysql+pymysql://root:root@localhost:3306/app")

# 游标存储（持久化位置）
cursor_store = db.cursor_store(table="onestep_cursor")

# 增量同步 Source
source = db.incremental(
    table="users",
    key="id",
    cursor=("updated_at", "id"),   # 游标字段
    where="deleted = 0",           # 过滤条件
    batch_size=1000,               # 每批数量
    state=cursor_store,            # 状态存储
)

# 输出到内存队列
out = MemoryQueue("dw")


@app.task(source=source, emit=out, concurrency=1)
async def sync_user(ctx, row):
    return {
        "id": row["id"],
        "name": row["name"],
        "updated_at": row["updated_at"]
    }
```

### 工作原理

1. 从 `cursor_store` 读取上次位置
2. 查询 `updated_at > last_updated OR (updated_at = last_updated AND id > last_id)`
3. 处理数据
4. 更新 `cursor_store` 中的位置

### 游标存储

```python
# 数据库存储（推荐生产环境）
cursor_store = db.cursor_store(table="sync_cursor")

# 或状态存储
state_store = db.state_store(table="onestep_state")
```

## 表输出 (Table Sink)

将处理结果写入数据库表。

### Upsert 模式

```python
sink = db.table_sink(
    table="results",
    mode="upsert",
    keys=("id",),  # 唯一键，存在则更新，不存在则插入
)

@app.task(source=..., emit=sink)
async def process(ctx, item):
    return {"id": item["id"], "data": item["data"]}
```

> 注意：`upsert` 生成 `INSERT ... ON DUPLICATE KEY UPDATE`。即使键已存在、
> 实际走更新分支，MySQL 仍会对 INSERT 部分做约束检查——目标表存在无默认值的
> `NOT NULL` 列且载荷未提供这些列时，会产生
> `Field 'xxx' doesn't have a default value` warning（更新本身仍会成功）。
> 只需要更新已有行时，请改用 `mode="update"`。

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
    keys=("id",),  # WHERE 匹配条件
    update_columns=("deadline", "tender_deadline"),  # 只重写这些列
)
```

- 适合"目标行由其他流程创建、本任务只回填部分字段"的场景。
- 目标行不存在时跳过该条并记录一条 INFO 日志，不报错；MySQL 下"值未变化"
  的重复更新同样按 0 行处理。
- 不生成 `INSERT` 语句，目标表存在无默认值的 `NOT NULL` 列时也不会触发
  warning，且不存在误插新行的风险。

### 更新控制（Upsert / Update 行为）

`upsert` 与 `update` 模式下，可通过 `update_columns`、`update_expr` 精确
控制写入的列：

```python
sink = db.table_sink(
    table="results",
    mode="upsert",
    keys=("id",),
    update_columns=("data",),          # 只重写这些列
    update_expr={"updated_at": "NOW(6)"},  # 写入时执行的原始 SQL 表达式
)
```

- `update_columns`：允许重写的白名单列；默认重写除 `keys` 外的所有载荷列。
  设为空列表 `()` 表示不更新任何载荷列，只应用 `update_expr`。
- `update_expr`：列名到原始 SQL 表达式的映射，写入时渲染执行（例如
  `updated_at=NOW(6)`）。
- 两者仅适用于 `upsert` 和 `update` 模式；`update_columns` 为空且没有
  `update_expr` 时配置无效。

### JSON 序列化控制

载荷中的 list/dict 值默认按目标列类型自动处理（`serialize_json="auto"`）：
列类型为 JSON 时原样写入，否则序列化为 JSON 字符串：

```python
sink = db.table_sink(
    table="results",
    mode="insert",
    serialize_json="always",  # 强制序列化为 JSON 字符串
)
```

`serialize_json` 可选值：`auto`（默认）、`always`（始终序列化为字符串）、
`never`（永不序列化）。

## 状态存储

### State Store

键值对存储，用于任务状态：

```python
state = db.state_store(table="onestep_state")

# 在任务中使用
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

```yaml
resources:
  db:
    type: mysql
    dsn: "mysql+pymysql://root:root@localhost:3306/app"
  
  order_queue:
    type: mysql_table_queue
    connector: db
    table: "orders"
    key: "id"
    where: "status = 0"
    claim:
      status: 9
    ack:
      status: 1
    batch_size: 100
  
  results:
    type: mysql_table_sink
    connector: db
    table: "results"
    mode: "upsert"
    keys:
      - "id"
    update_columns:
      - "data"
    update_expr:
      updated_at: "NOW(6)"
    serialize_json: "auto"
  
  cursor:
    type: mysql_cursor_store
    connector: db
    table: "sync_cursor"

tasks:
  - name: process_orders
    source: order_queue
    emit: results
    concurrency: 16
```

## 最佳实践

### 1. 索引优化

```sql
-- 表队列：确保查询条件有索引
CREATE INDEX idx_status ON orders(status);

-- 增量同步：确保游标字段有索引
CREATE INDEX idx_cursor ON users(updated_at, id);
```

### 2. 批量大小

```python
# 小批量：低延迟
batch_size=10

# 大批量：高吞吐
batch_size=1000
```

### 3. 并发控制

```python
# 表队列：可高并发（行级锁）
@app.task(source=source, concurrency=16)

# 增量同步可并发处理；Runner 每轮仍只调用一次 fetch(limit)
# concurrency 限制处理中 Delivery，不会发起 100 条并发 SELECT
@app.task(source=incremental, concurrency=100)
```

### 4. 连接池

```python
# URL 参数配置连接池
db = MySQLConnector(
    "mysql+pymysql://user:pass@host/db"
    "?pool_size=10"
    "&max_overflow=20"
    "&pool_recycle=3600"
)
```

### 5. 可靠持久游标与重试

生产增量同步应显式绑定 `mysql_cursor_store` 和稳定 `state_key`。成功记录可以乱序
完成，但持久游标只推进到连续成功前缀；同一批同时释放的确认会合并为一个状态写。
失败重试会重新投递同一逻辑行并增加 `Envelope.attempts`，缺口重试期间不会继续发出
后续 SQL 查询。达到任务 `max_attempts` 后 Source 停在失败行之前。进程重启从已持久
游标恢复，未提交的行会重放。

```yaml
mysql_cursors:
  type: mysql_cursor_store
  connector: mysql_source
  table: onestep_cursor
  auto_create: true

follow_records:
  type: mysql_incremental
  connector: mysql_source
  table: view_follow_record_sync
  key: unionKey
  cursor: [dataCreateTime, unionKey]
  state: mysql_cursors
  state_key: follow-record-sync-v1
```
