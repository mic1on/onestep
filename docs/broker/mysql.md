---
title: MySQL | Broker
outline: deep
---

# MySQL <Badge type="warning" text="即将下线" />

::: tip 迁移提示
`onestep-mysql` 现在是 `onestep-sql` 的转发 shim。新项目请安装 `onestep-sql[mysql]` 并从 `onestep_sql.mysql` 导入；旧的 `onestep-mysql` 包与 `from onestep_mysql import ...` 仍可用。详见[迁移到 onestep-sql](/guide/migrate-to-onestep-sql)。
:::

MySQL Connector 提供三种模式：
- **表队列**: 将数据库表作为任务队列
- **增量同步**: 基于 `(updated_at, id)` 的 Logstash 风格同步
- **表输出**: 将结果写入数据库表

## 安装

```bash
pip install 'onestep-sql[mysql]'
# 或使用 core extra
# pip install 'onestep[mysql]'
```

> `onestep-sql` 是 MySQL 与 PostgreSQL 的规范发行包（issue #133）。旧的 `pip install onestep-mysql` 仍可用作转发 shim，但新部署建议使用 `onestep-sql[mysql]`。所有 YAML 资源类型名不变。

## 表队列 (Table Queue)

将数据库表作为任务队列，通过更新状态字段来"领取"任务。

### 基本用法

```python
from onestep import OneStepApp
from onestep_sql.mysql import MySQLConnector

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
from onestep_sql.mysql import MySQLConnector

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

MySQL 查询将复合游标展开为上述范围条件，避免行构造器不等式在部分执行计划中
反复扫描已处理的索引前缀。仍需为完整有效游标建立同序索引；配置未包含 `key` 时
连接器会自动追加它。优化只改变查询形状，不改变同时间分页和连续 ACK 前缀提交。

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

### 按列写入策略（null 保护）

`update_columns` 的条目可以是列名（默认无条件覆盖），也可以是
`{name, policy}` 对象，按列声明载荷值与库中原值的合并方式。三种策略：

| policy | 行为 | 生成 SQL |
|---|---|---|
| `overwrite`（默认） | 无条件用载荷值覆盖，载荷 `null` 也会写入 `NULL` | `SET col = :val` |
| `skip_null` | 载荷值为 `null` 时该列不写，保留库中原值 | `null` → 列从 `SET` 剔除 |
| `backfill` | 只在库中当前值为 `NULL` 时写入载荷值，原值非空则保持 | `SET col = COALESCE(col, :val)` |

```yaml
rows_sink:
  type: mysql_table_sink
  connector: downstream_mysql
  table: bidding
  mode: update
  keys: [id]
  update_columns:
    - deadline              # 无条件覆盖
    - tender_deadline       # 无条件覆盖
    - name: tenderee
      policy: skip_null     # 载荷 null 不写，避免清空已有值
    - name: publish_date
      policy: backfill      # 只回填空值，不覆盖已有值
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

- 策略对 `update` 和 `upsert` 同样生效（`ON DUPLICATE KEY UPDATE` 子句
  应用相同规则）。
- `skip_null` 过滤后整个 `SET` 为空时，该条载荷跳过并记录一条 INFO 日志，
  不报错。
- 策略列不能是 `keys` 中的列，也不能与 `update_expr` 中同列的原始 SQL
  表达式同时配置（构造时报错）；纯列名条目与 `update_expr` 的覆盖关系
  保持不变。

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

### 2. 读取批量与处理并发

默认 `prefetch=False` 保留原行为：每次 SQL 的上限是
`min(batch_size, runtime 当前空闲并发槽)`。例如 `batch_size=500`、
`concurrency=8` 时，每次最多读 8 行，只有一个空闲槽时最多读 1 行。

从 `onestep-sql 0.3.0` 起，可对 MySQL 增量源显式启用有界预取：

```python
source = db.incremental(
    table="users", key="id", cursor=("updated_at",),
    batch_size=100, prefetch=True, state=cursor_store,
)

@app.task(source=source, concurrency=8)
async def sync_user(ctx, row):
    ...
```

YAML 对应 `mysql_incremental` 的 `prefetch: true` 和 `batch_size: 100`；
任务仍单独配置 `concurrency: 8`。SQL 一次最多读 100 行，每次只交付空闲槽所需
的行数；剩余行在 connector 缓冲，缓冲耗尽后才补读，没有后台无限抓取任务。
`prefetch` 必须是布尔值，启用时 `batch_size` 必须是正整数。

- 未交付行缓冲最多 `batch_size` 行。未提交窗口（已交付待连续 ACK 的行 + 缓冲行）
  最多为 `batch_size + 已观察到的最大 fetch(limit)`；通常是读取批量加任务并发。
  最前面一条很慢时会停止继续读，而不是让已 ACK 的后续行无限堆积。
- 预取不会提前保存持久游标。重试优先于缓冲行，失败缺口期间不继续交付新行；
  重启从连续已提交前缀恢复，未交付或未确认行可重读。
- 启用预取时，暂停/退出会等待正在进行的 SELECT 结束，再释放已取回但尚未启动的
  Delivery；应为数据库连接配置合适的超时。暂停恢复和关闭时丢弃未交付缓冲，
  不推进游标，后续从已交付边界重新查询。
- 缓冲保存读取时的行快照，不能保证每次 handler 启动时都是源的最新内容；
  较大的批量会增加内存与快照停留时间。它仍是至少一次的时间游标轮询，不是 CDC。
- fetch 日志的 `row_count` 是交付行数；`sql_limit` / `sql_row_count` 是本次实际 SQL
  的上限 / 返回行数，未查询时均为 0；`buffered_row_count` 是剩余缓冲行数。
  预取减少源查询次数，不自动减少 handler 写库或持久游标提交次数。

先用 50、100、500 条与固定处理并发做对照，观察 SQL 次数、内存和业务耗时。

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

从 `onestep-mysql 0.5.1` 起，`mysql_cursor_store` 能持久化游标中的 MySQL
`DATETIME` 组件：它以带类型标记的 ISO-8601 JSON 保存，重启后恢复为原始
`datetime`（保留微秒）再参与 keyset 查询。已有的纯 JSON 游标继续兼容；从
`0.5.0` 升级不需要迁移游标表，也不要手工推进一个因提交失败而尚未确认的游标。

```yaml
mysql_cursors:
  type: mysql_cursor_store
  connector: mysql_source
  table: onestep_cursor
  auto_create: true

order_source:
  type: mysql_incremental
  connector: mysql_source
  table: view_order_sync
  key: orderKey
  cursor: [orderCreateTime, orderKey]
  state: mysql_cursors
  state_key: feishu-order-sync-v1
```

完整的生产参数、飞书 Insert 键索引、handler 契约和故障恢复流程参见
[实战篇：MySQL 订单流水同步到飞书多维表格](/guide/cases/mysql-feishu-order-sync)。
