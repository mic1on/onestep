---
title: MongoDB | Broker
outline: deep
---

# MongoDB

`onestep-mongodb` 提供确定性 collection 轮询、原生 MongoDB Change Stream 和已确认 collection insert 或稳定键 upsert Sink。

## 安装

```bash
pip install onestep-mongodb
```

要求 Python 3.9+、`onestep>=1.11.0` 且 `pymongo>=4.13`。使用 PyMongo 原生 `AsyncMongoClient`，不使用 Motor。

## Python 用法

```python
from onestep_mongodb import MongoDBConnector

mongo = MongoDBConnector(
    "mongodb://writer:secret@mongo-rs0/app?replicaSet=rs0",
    database="app",
    client_options={"serverSelectionTimeoutMS": 10_000},
)

polling = mongo.poll_collection(
    "events",
    cursor=("updated_at", "_id"),
    filter={"archived": False},
    batch_size=100,
    poll_interval_s=1.0,
    state=durable_cursor_store,
    state_key="events-poll",
)

changes = mongo.watch_collection(
    "events",
    pipeline=[{"$match": {"operationType": {"$in": ["insert", "update", "delete"]}}}],
    full_document="updateLookup",
    max_await_time_ms=1000,
    state=durable_cursor_store,
    state_key="events-change-stream",
)

sink = mongo.collection_sink(
    "events_archive",
    mode="upsert",
    keys=("event_id",),
    ordered=True,
    batch_size=1000,
)
```

Connector 惰性创建客户端并只关闭自己创建的客户端。注入的客户端由调用方管理。Source 关闭自己的查询游标或 change stream，所有 `close()` 方法幂等。

## YAML 配置

生产环境建议使用持久化游标存储。下方示例使用独立的 PostgreSQL cursor store 插件：

```yaml
resources:
  mongo:
    type: mongodb
    uri: "${MONGODB_URI}"
    database: app
    client_options:
      serverSelectionTimeoutMS: 10000

  cursor_db:
    type: postgres
    dsn: "${POSTGRES_DSN}"

  cursor_state:
    type: postgres_cursor_store
    connector: cursor_db
    table: onestep_cursor

  events_poll:
    type: mongodb_polling
    connector: mongo
    collection: events
    cursor: [updated_at, _id]
    filter:
      archived: false
    batch_size: 100
    poll_interval_s: 1
    state: cursor_state
    state_key: events-poll

  events_changes:
    type: mongodb_change_stream
    connector: mongo
    collection: events
    pipeline:
      - $match:
          operationType:
            $in: [insert, update, delete]
    full_document: updateLookup
    max_await_time_ms: 1000
    batch_size: 100
    poll_interval_s: 0.1
    state: cursor_state
    state_key: events-change-stream

  archive:
    type: mongodb_collection_sink
    connector: mongo
    collection: events_archive
    mode: upsert
    keys: [event_id]
    ordered: true
    batch_size: 1000
```

## Sources

### 轮询 (Polling)

轮询使用升序字典序游标遍历。`_id` 始终为最终 tie-breaker。只持久化最大连续 ack 的游标。`fail()` 跳过 poison document 并推进连续游标；`retry()` 和 `release_unstarted()` 使当前代次失效，仅在所有 stale delivery 完成后从上一个已提交游标重放。来自失效代次的迟确认被忽略。

轮询投影必须保留每个有效游标字段，包括隐式 `_id` tie-breaker。无效投影在 source 构建时即报错。游标状态仅在配置的 cursor store 确认保存后才在内存中更新；保存失败则代次从上次持久化位置可重放。如果某个 delivery 重试，则该 fetch 批次的每个 token 均禁止推进游标，包括已确认的更后 delivery。

轮询不输出删除事件。仅游标字段增大时看到更新，可能遗漏非单调游标更新。需要此类事件的场景请使用 Change Stream。

### Change Stream

Change Stream 要求 MongoDB 副本集或分片集群，不支持 standalone。Delivery 携带完整原始 change event：

```python
{
    "_id": {"...": "resume token"},
    "operationType": "update",
    "documentKey": {"_id": "..."},
    "fullDocument": {"...": "..."},
    "updateDescription": {"...": "..."},
}
```

resume token 仅在连续 delivery 确认后推进。如果 resume token 超出 oplog 或无效，source 永久失败，不会静默降级到当前服务端位置。要重置此类 source：停止 worker，确认永久 history-lost 错误，仅删除或重置该 source 的 `state_key`，再重启。

## Sink 与投递语义

Sink 接受恰好一个 mapping 或非空 mapping 序列。将单个序列按 `batch_size` 确定性地拆分 chunk，顺序提交并等待每个 MongoDB 确认。插件拒绝未确认的 write concern（`w=0`）。

Insert 模式下，单个 mapping 使用 `insert_one`，序列 chunk 使用 `insert_many`。仅当文档有稳定 `_id` 值时可安全重放；重复键为永久冲突，不是隐式成功。Upsert 模式要求所有配置的稳定 keys，发送 `UpdateOne` 操作，带键等值过滤器和排除键字段、不可变 `_id` 的 `$set`。

`ordered: true` 是保守默认。`ordered: false` 可获得更高吞吐并报告多项失败。bulk 操作或后续 chunk 可能部分提交。非幂等写入报告为 UNCERTAIN，只保留脱敏后的索引、代码、数量和消息。

onestep 投递是 at-least-once。Sink 输出可能在 source 确认前提交，在此期间崩溃可能导致重复输出。多 Sink 扇出不是事务性的。需要处理重复时请使 handler 和下游写入幂等。

## 暂不支持

首版不提供：database/cluster 级 stream、事务、Sink delete、replace 或 update pipeline、schema 验证或 DDL、GridFS、聚合或分区轮询、pre-image、expanded event、自定义 codec 或 MongoDB 自建 cursor store。
