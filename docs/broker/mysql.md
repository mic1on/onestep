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
pip install 'onestep[mysql]'
```

## 表队列 (Table Queue)

将数据库表作为任务队列，通过更新状态字段来"领取"任务。

### 基本用法

```python
from onestep import MySQLConnector, OneStepApp

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
from onestep import MySQLConnector, OneStepApp

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

### Insert 模式

```python
sink = db.table_sink(
    table="logs",
    mode="insert",  # 仅插入
)
```

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
connectors:
  db:
    type: mysql
    url: "mysql+pymysql://root:root@localhost:3306/app"
  
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

# 增量同步：建议单并发（保持顺序）
@app.task(source=incremental, concurrency=1)
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