---
title: ClickHouse | Broker
outline: deep
---

# ClickHouse

`onestep-clickhouse` 提供异步 ClickHouse 表输出 Sink，每批插入都会等待服务端确认。

## 安装

```bash
pip install onestep-clickhouse
```

要求 Python 3.9+ 且 `onestep>=1.11.0`。

## Python 用法

```python
from onestep_clickhouse import ClickHouseConnector

clickhouse = ClickHouseConnector(
    dsn="https://writer:secret@clickhouse:8443/analytics",
    client_options={
        "connect_timeout": 10,
        "send_receive_timeout": 30,
    },
)

sink = clickhouse.table_sink(
    table="events",
    columns=("event_id", "occurred_at", "kind", "payload"),
    batch_size=1000,
    settings={"async_insert": 0},
)
```

Connector 在首次插入时惰性创建异步客户端。Connector 关闭后释放自己所创建的客户端；注入的客户端由调用方管理生命周期。`close()` 幂等。

## YAML 配置

```yaml
resources:
  analytics:
    type: clickhouse
    dsn: "${CLICKHOUSE_DSN}"
    client_options:
      connect_timeout: 10
      send_receive_timeout: 30

  events:
    type: clickhouse_table_sink
    connector: analytics
    table: events
    columns: [event_id, occurred_at, kind, payload]
    batch_size: 1000
    settings:
      async_insert: 0
```

使用 `onestep check --strict worker.yaml` 做静态校验。`dsn` 和 `client_options` 属于密文元数据，不会出现在拓扑描述中。

## 行与列

每次 `send()` 接受一个 mapping 或一个非空 mapping 序列。字符串、空序列、混合类型或非 mapping 项在首次网络调用前即被拒绝。

配置 `columns` 时每行必须恰好包含这些键，值按配置顺序排列。省略 `columns` 时，首个 mapping 的插入顺序固定该次逻辑发送的列顺序，后续 mapping 的键集必须完全一致。插件不推断数据库 schema，不转换类型。

`batch_size` 默认 1000 行。较大的逻辑批次会拆分 chunk 并逐个插入和等待。Sink 没有定时器、隐藏刷新队列或跨 send 批处理；通过任务并发度和 ClickHouse 客户端连接池控制并发。

## 已确认异步插入

一次成功的 send 意味着每个 chunk 收到了已确认的服务端响应。`async_insert: 0` 是唯一接受的未声明等待模式。启用了 async insert 时必须同时设置 `wait_for_async_insert: 1`：

```yaml
settings:
  async_insert: 1
  wait_for_async_insert: 1
```

## 投递与重复语义

Sink 会等待每个 ClickHouse 插入 chunk 并确认。ClickHouse 确认 chunk 后到 onestep 确认 source 前如果发生崩溃可能产生重复行。后续 chunk 失败将报告为 UNCERTAIN，因为已确认的 chunk 无法回滚。需要幂等时，通过表设计使用稳定事件键和去重引擎（如 `ReplacingMergeTree`）。

多 Sink 扇出不是事务性的。后续 Sink 失败时，已成功写入 ClickHouse 的数据不会回滚。显式重试可能导致已提交的行或 chunk 重复。

例如，数据表可以保留稳定事件键和版本字段用于最终替换：

```sql
CREATE TABLE events
(
    event_id String,
    version DateTime64(3),
    payload String
)
ENGINE = ReplacingMergeTree(version)
ORDER BY event_id;
```

这仅是部署建议。插件不执行 DDL 或生成去重标记。

## 暂不支持

首个版本不包含：定时合并、DDL/迁移、查询 Source、流式格式、Arrow 或 DataFrame API、schema 推断与转换、分布式表路由、插件生成去重标记、mutations 或 upsert。
