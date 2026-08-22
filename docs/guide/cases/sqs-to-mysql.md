---
title: 实战篇：SQS 消息可靠落库到 MySQL | 指南
outline: deep
---

# 实战篇：SQS 消息可靠落库到 MySQL

本案例把 AWS SQS 队列里的业务事件可靠地写入 MySQL 表。它适合上游把事件推送到
SQS、下游需要按业务唯一键落库的场景：SQS 至少投递一次，进程崩溃或可见性超时
后消息会重投，因此落库必须幂等。

```text
sqs_queue: events
  └─ handler:to_event_row
       └─ mysql_table_sink: upsert / event_id
```

## 目标与边界

- SQS 是 at-least-once 投递：同一条消息可能被重复接收。只有 handler 成功、Sink
  写入完成后 Delivery 才 `ack()`，此时对应消息才会从 SQS 删除。
- 落库使用 `mode: upsert` + 业务唯一键 `event_id`，重投会命中已存在行并更新，
  不会产生重复行。
- handler 抛异常触发任务重试；重试耗尽后按 `on_fail` 策略处理该消息（`leave`
  保留等待 SQS Redrive Policy 投递到死信队列，`release` 立即重新可见）。
- 长任务要配置心跳续期可见性超时，否则消息会在处理中途重新可见被其他 worker
  重复领取。

## 前置条件

安装下列版本或更高版本：

```bash
pip install onestep-sqs 'onestep-sql[mysql]>=0.1.0'
```

开始前确认：

1. SQS 队列已创建，并在 AWS 控制台配置了 Redrive Policy 指向死信队列（推荐）。
2. 目标 MySQL 表有唯一键或唯一索引覆盖 `event_id`，否则 `upsert` 无法命中冲突。
3. 运行身份通过环境变量或 EC2/Lambda IAM Role 获得 SQS 权限，不要硬编码密钥。
4. handler 输出的字段名与目标表列名一致；list/dict 值会按 `serialize_json`
   规则序列化。

## 完整 YAML

保存为 `worker.yaml`。凭据只通过环境变量或 IAM Role 提供，不要写入 YAML 明文。

```yaml
apiVersion: onestep/v1alpha1
kind: App

app:
  name: sqs-to-mysql
  shutdown_timeout_s: 60
  strict_env: true
  logging:
    level: "${SQS_MYSQL_LOG_LEVEL:-INFO}"

resources:
  sqs:
    type: sqs
    region_name: "${AWS_DEFAULT_REGION:-us-east-1}"

  events:
    type: sqs_queue
    connector: sqs
    url: "${SQS_EVENTS_URL}"
    batch_size: 10
    wait_time_s: 20
    # 处理耗时较长时，靠心跳把可见性续期到安全窗口。
    heartbeat_interval_s: 15
    heartbeat_visibility_timeout: 60
    # 重试耗尽后保留消息，交给 SQS Redrive Policy 投递到死信队列。
    on_fail: leave

  mysql_main:
    type: mysql
    dsn: "${SQS_MYSQL_DSN}"

  events_sink:
    type: mysql_table_sink
    connector: mysql_main
    table: events
    mode: upsert
    keys: [event_id]
    # 只重写这些列；未列出的列（如 created_at）在冲突时保留。
    update_columns: [payload, status]
    update_expr:
      updated_at: "NOW(6)"
    serialize_json: auto

tasks:
  - name: ingest_events
    description: Ingest SQS events into MySQL with idempotent upsert
    source: events
    emit: events_sink
    concurrency: 16
    timeout_s: 45
    retry:
      type: exponential_backoff
      max_attempts: 5
      min_delay_s: 1
      max_delay_s: 30
      jitter: full
    handler: handler:to_event_row
```

`events.batch_size: 10` 是一次 SQS `ReceiveMessage` 的最大条数（SQS 上限为 10）；
`concurrency: 16` 是运行时允许在途的 Delivery 上限。心跳把可见性续到 60 秒，
`heartbeat_interval_s: 15` 满足续期频率必须显著小于可见性超时。

## Handler 契约

YAML 只负责连接资源，字段转换由 Python handler 完成。`handler:to_event_row`
接收 `(ctx, item)`，其中 `item` 是解码后的 SQS 消息体，返回可直接写入
`events` 表的列字典。

最低契约是返回稳定的 `event_id`：

```python
async def to_event_row(ctx, item):
    # event_id 必须与业务事件稳定对应，才能让 upsert 幂等去重。
    return {
        "event_id": item["id"],
        "payload": item,        # dict，会按 serialize_json=auto 序列化为 JSON
        "status": item.get("status", "received"),
    }
```

如果一条消息在业务上永远无法处理（脏数据），应让 handler 明确抛异常，通过重试
耗尽后进入死信队列，而不是静默 `ack()` 丢弃。

## 运行与恢复

### 至少一次与幂等

SQS 不保证只投递一次。任何依赖“消息只到一次”的逻辑都是错误的。可靠落库依赖
两点共同保证：

1. 只有 Sink 写入成功后 Delivery 才 `ack()`（删除 SQS 消息）；崩溃在 ack 之前
   的消息会重投。
2. `upsert` + 唯一键让重投命中已存在行，只更新不新增。

### 可见性超时与心跳

消息被领取后进入可见性超时窗口，其他消费者不可见。处理时间可能超过默认超时时，
必须开启心跳续期，否则消息会在处理中途重新可见并被重复领取。经验规则：

```text
heartbeat_interval_s 明显小于 heartbeat_visibility_timeout
```

### 死信与失败策略

- `on_fail: leave`（本案例）：重试耗尽后保留消息，由 SQS 的 Redrive Policy 在
  达到最大接收次数后投递到死信队列。推荐用于需要人工排查脏数据的场景。
- `on_fail: release`：重试耗尽后立即把消息设为可见，会被继续重投；仅在你确定
  失败是暂时性、且没有配置死信队列时使用，否则会形成投递风暴。

### 观测点

开启 INFO 日志后关注 SQS 的 CloudWatch 指标与任务事件：

| 指标/事件 | 用途 |
|---|---|
| `ApproximateNumberOfMessagesVisible` | 积压深度，判断消费是否跟得上。 |
| `ApproximateNumberOfMessagesNotVisible` | 处理中（含可见性续期）的消息数。 |
| 死信队列消息数 | 落库失败进入死信的记录，需要人工排查。 |
| 任务失败与重试事件 | 定位 handler 或 MySQL 侧的错误。 |

不要在日志中输出 DSN、AWS 密钥或消息中的敏感字段。

## 参数取舍

| 参数 | 本案例值 | 取舍 |
|---|---:|---|
| SQS `batch_size` | 10 | SQS 单次接收上限；减少 API 调用。 |
| `wait_time_s` | 20 | 长轮询降低空轮询成本；SQS 上限 20 秒。 |
| `concurrency` | 16 | 限制在途 Delivery，按 MySQL 写入能力调整。 |
| `heartbeat_visibility_timeout` | 60 | 覆盖单条最长处理时间，留出余量。 |
| `max_attempts` | 5 | 临时故障可重试；耗尽后进入死信便于排查。 |
| `on_fail` | `leave` | 配合 SQS Redrive Policy 走死信队列。 |

## 相关文档

- [AWS SQS 连接器](/broker/sqs)
- [MySQL：表输出与冲突策略](/broker/mysql#表输出-table-sink)
- [重试与死信](/core/retry)
- [YAML 任务定义](/yaml-task-definition)
