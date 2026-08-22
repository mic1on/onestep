---
title: 实战篇：多连接器协调的事件分发管道 | 指南
outline: deep
---

# 实战篇：多连接器协调的事件分发管道

本案例展示一个任务如何同时协调多个连接器：从 Redis Streams 读取事件，经 handler
归一化后，用**条件 Sink 路由**把不同事件分发到 MySQL 落库、HTTP 回调和审计流，
失败终态进入死信队列。它适合“一个源、多个目的地、按业务条件分流”的场景。

```text
redis_stream: events            ┌─ mysql_table_sink: orders (active)
  └─ handler:normalize_event ───┤
        (条件路由 + per-sink     ├─ http_sink: notify (active)
         transform)             └─ audit_stream (所有事件)
                                   ⇢ dead_letter: events_dead (终态失败)
```

## 目标与边界

- 一个任务可以 `emit` 到多个 Sink。所有选中的 Sink 都写入成功，Delivery 才 `ack()`。
- **条件路由**用 Python 谓词决定某个事件走哪个目的地；YAML 只声明拓扑，判定逻辑
  在 Python。
- **per-sink transform** 让同一份 handler 结果按每个目的地需要的形状投影，无需在
  handler 里拼多份 payload。
- 写入是 at-least-once 且**不跨 Sink 事务**：一旦开始分发，靠前的 Sink 写入成功后
  即便靠后的 Sink 失败也不会回滚。因此每个目的地都必须幂等。
- handler 或任意 transform 抛异常时，不会发出任何本次 Sink 输出，任务走重试；重试
  耗尽后进入 `dead_letter`。

## 前置条件

安装源、目的地对应的插件：

```bash
pip install onestep-redis 'onestep-sql[mysql]>=0.1.0'
# http_sink 由 onestep 核心提供，无需额外插件
```

开始前确认：

1. Redis Stream 与消费者组已存在，或允许运行时创建。
2. MySQL 目标表有唯一键覆盖 `order_id`，保证 upsert 幂等。
3. HTTP 回调端点幂等，能容忍同一事件重复投递（配合幂等键或去重）。
4. 谓词与 transform 是当前 Python 运行环境可导入的 callable。

## 完整 YAML

保存为 `worker.yaml`：

```yaml
apiVersion: onestep/v1alpha1
kind: App

app:
  name: event-fanout
  shutdown_timeout_s: 60
  strict_env: true
  logging:
    level: "${FANOUT_LOG_LEVEL:-INFO}"

resources:
  redis_main:
    type: redis
    url: "${FANOUT_REDIS_URL:-redis://localhost:6379}"

  events:
    type: redis_stream
    connector: redis_main
    stream: "events:incoming"
    group: "fanout-workers"
    batch_size: 100

  events_dead:
    type: redis_stream
    connector: redis_main
    stream: "dead_letter:events"
    group: "fanout-workers"

  mysql_main:
    type: mysql
    dsn: "${FANOUT_MYSQL_DSN}"

  orders_sink:
    type: mysql_table_sink
    connector: mysql_main
    table: orders
    mode: upsert
    keys: [order_id]
    update_columns: [status, amount]
    update_expr:
      updated_at: "NOW(6)"
    serialize_json: auto

  notify:
    type: http_sink
    url: "${FANOUT_NOTIFY_URL}"
    method: POST
    timeout_s: 5

  audit_stream:
    type: redis_stream
    connector: redis_main
    stream: "events:audit"

tasks:
  - name: fanout_events
    description: Normalize events and fan out to MySQL, HTTP, and audit
    source: events
    emit:
      # 所有事件都写审计流（无条件）。
      - audit_stream
      # 只有活跃订单事件才落库和回调；per-sink transform 投影各自的形状。
      - when:
          ref: worker.routing:is_active_order
        then:
          - sink: orders_sink
            transform: worker.transforms:to_order_row
          - sink: notify
            transform: worker.transforms:to_notify_body
    dead_letter: [events_dead]
    concurrency: 8
    timeout_s: 30
    retry:
      type: exponential_backoff
      max_attempts: 5
      min_delay_s: 1
      max_delay_s: 20
      jitter: full
    handler:
      ref: worker.tasks:normalize_event
```

## Python 侧

handler 归一化事件；谓词决定分流；transform 为每个 Sink 投影 payload。三者都放在
Python，YAML 只声明静态拓扑。

```python
# worker/tasks.py
async def normalize_event(ctx, item):
    # 归一化上游事件；返回给下游各 Sink 复用的中间结果。
    return {
        "order_id": item["id"],
        "kind": item.get("kind", "unknown"),
        "status": item.get("status", "pending"),
        "amount": item.get("amount", 0),
        "customer": item.get("customer"),
    }


# worker/routing.py
def is_active_order(ctx, payload, result) -> bool:
    # 谓词可接收 ctx / payload(原始 source 消息) / result(handler 返回)。
    return result["kind"] == "order" and result["status"] == "active"


# worker/transforms.py
async def to_order_row(ctx, payload, result):
    # 投影成 orders 表需要的列。
    return {
        "order_id": result["order_id"],
        "status": result["status"],
        "amount": result["amount"],
    }


def to_notify_body(ctx, payload, result):
    # 投影成 HTTP 回调需要的 body。
    return {
        "order_id": result["order_id"],
        "event": "order.active",
    }
```

规则回顾（详见 [YAML 任务定义](/yaml-task-definition#第-4-阶段：添加条件-sink-路由)）：

- `emit` 条目可混合无条件 Sink 与条件路由映射，按顺序求值。
- `when` 是谓词 ref；`then` / `otherwise` 可以是 Sink 名、名列表，或 `{sink, transform}`
  绑定列表。省略 `otherwise` 时谓词为假就跳过该路由。
- 单个路由只会走 `then` 或 `otherwise` 之一。
- transform 接收 `(ctx, 原始 payload, handler 结果)`，返回发给该 Sink 的 body。

## 运行与恢复

### 多 Sink 的 at-least-once 语义

一个任务写多个目的地时，OneStep 先求值所有选中的 transform，全部成功后才开始逐个
发送。关键性质：

- 任意 transform 失败 ⇒ 本次一个 Sink 都不发，任务重试。
- 分发过程中靠后的 Sink 失败 ⇒ 靠前已成功的写入**不回滚**，整条 Delivery 视为失败
  并重试，重投时靠前的 Sink 会再次收到。

因此每个目的地都要幂等：MySQL 用 `upsert` + 唯一键，HTTP 回调用幂等键或去重，
审计流可容忍重复。

### 死信

重试耗尽的终态失败消息写入 `events_dead`。用一个独立任务消费死信流做人工排查或
补偿，不要直接丢弃。

### 观测点

| 事件 | 用途 |
|---|---|
| 任务失败与重试事件 | 定位是 handler、某个 transform 还是某个 Sink 出错。 |
| Redis PEL（pending）深度 | 判断消费是否跟得上、是否有卡住的 Delivery。 |
| 死信流长度 | 需要人工排查的终态失败量。 |
| HTTP 回调非 2xx 比例 | 下游可用性。 |

不要在日志中输出 DSN、Redis URL 或 HTTP 目标里的 token。

## 参数取舍

| 参数 | 本案例值 | 取舍 |
|---|---:|---|
| Redis `batch_size` | 100 | 高吞吐；受任务并发进一步封顶。 |
| `concurrency` | 8 | 限制在途 Delivery，按最慢 Sink（通常是 HTTP）调整。 |
| HTTP `timeout_s` | 5 | 防止下游慢响应拖垮整条管道。 |
| `max_attempts` | 5 | 临时故障可重试；耗尽进入死信。 |

## 相关文档

- [Redis Streams 连接器](/broker/redis)
- [MySQL：表输出与冲突策略](/broker/mysql#表输出-table-sink)
- [HTTP Sink](/broker/http)
- [条件 Sink 路由与 Per-Sink Transform](/yaml-task-definition#第-4-阶段：添加条件-sink-路由)
- [重试与死信](/core/retry)
