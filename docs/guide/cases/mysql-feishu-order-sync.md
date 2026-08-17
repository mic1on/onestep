---
title: 实战篇：MySQL 订单流水同步到飞书多维表格 | 指南
outline: deep
---

# 实战篇：MySQL 订单流水同步到飞书多维表格

本案例把 MySQL 视图中的**不可变订单流水**增量写入飞书多维表格。它适合源端
只追加、目标端按业务唯一键去重的场景：进程故障或网络超时后允许重放，
但不能因为重放而重复创建飞书记录。

```text
view_order_sync
  └─ mysql_incremental: (orderCreateTime, orderKey)
       └─ handler:to_feishu_fields
            └─ feishu_bitable_table_sink: insert / 订单编号
```

## 目标与边界

- 源端按 `(orderCreateTime, orderKey)` 复合游标稳定排序。`orderKey` 是全局唯一、
  不会变化的最终 tie-breaker。
- 每条源记录只有在飞书确认“已存在”或“已创建”后才会确认并推进 MySQL 游标。
- 语义是 at-least-once：在飞书写入成功而游标尚未提交时崩溃，记录会重放。
  目标端以 `订单编号` 去重，重放不会新增同编号记录。
- `insert_key_index` 只适合不可变 Insert 流；它不提供更新、删除、CDC、多写者
  exactly-once 或持久化幂等账本。

## 前置条件

安装下列版本或更高版本：

```bash
pip install 'onestep-mysql>=0.5.1' 'onestep-feishu-bitable>=0.4.0'
```

`onestep-mysql 0.5.1` 能持久化并恢复 MySQL `DATETIME` 游标组件，保留微秒
精度。`onestep-feishu-bitable 0.4.0` 提供受限的启动键索引与不确定批写入恢复。

开始前确认：

1. `view_order_sync` 输出 `orderCreateTime`、`orderKey` 和 handler 所需的全部
   业务字段；同一 `orderCreateTime` 下 `orderKey` 保证唯一且排序稳定。
2. 视图底层查询具备支持 `(orderCreateTime, orderKey)` 的索引；不要在游标列上
   施加会破坏索引或排序的表达式。
3. 飞书目标表存在文本字段 `订单编号`，每个订单流水对应一个稳定且唯一的值。
4. 目标 `(app_token, table_id)` 始终只有**一个活动写入实例**。启用
   `insert_key_index` 后，第二个 worker 或人工并行写入会在启动索引之后引入竞态。
5. 运行账号能创建 `onestep_cursor`；若没有 DDL 权限，先建表并将
   `auto_create` 改为 `false`。

## 完整 YAML

保存为 `worker.yaml`。凭据只通过环境变量传入；`state_key` 是同步进度的稳定
身份，发布或重命名资源时不要随意修改它。

```yaml
apiVersion: onestep/v1alpha1
kind: App

app:
  name: feishu-order-sync
  shutdown_timeout_s: 120
  strict_env: true
  config:
    environment: "${FEISHU_ORDER_ENV:-default}"
  logging:
    level: "${FEISHU_ORDER_LOG_LEVEL:-INFO}"

resources:
  mysql_main:
    type: mysql
    dsn: "${FEISHU_ORDER_MYSQL_DSN}"

  # 持久化增量游标；首次运行自动建表。
  order_cursors:
    type: mysql_cursor_store
    connector: mysql_main
    table: onestep_cursor
    auto_create: true

  order_source:
    type: mysql_incremental
    connector: mysql_main
    table: view_order_sync
    key: orderKey
    cursor: [orderCreateTime, orderKey]
    batch_size: 1000
    poll_interval_s: 1
    state: order_cursors
    # 改名会被视为一条全新的同步进度。
    state_key: feishu-order-sync-v1

  feishu:
    type: feishu_bitable
    app_id: "${FEISHU_ORDER_FEISHU_APP_ID}"
    app_secret: "${FEISHU_ORDER_FEISHU_APP_SECRET}"

  order_table:
    type: feishu_bitable_table_sink
    connector: feishu
    app_token: "${FEISHU_ORDER_FEISHU_APP_TOKEN}"
    table_id: "${FEISHU_ORDER_FEISHU_TABLE_ID}"
    mode: insert
    match_fields: [订单编号]
    user_id_type: user_id
    batch_size: 100
    flush_interval_s: 1

    # 启动时读取已有「订单编号」；正常增量不再逐条 Search 飞书。
    insert_key_index: true
    insert_index_page_size: 500
    insert_index_max_pages: 200
    ambiguous_write_max_rounds: 3

tasks:
  - name: sync_orders
    description: Sync immutable order rows from MySQL to Feishu Bitable
    source: order_source
    emit: order_table
    concurrency: 100
    timeout_s: 120
    retry:
      type: max_attempts
      max_attempts: 5
      delay_s: 5
    handler: handler:to_feishu_fields
    config:
      batch_size: 100
```

`mysql_incremental.batch_size: 1000` 是一次 SQL 拉取的最大行数；
`order_table.batch_size: 100` 才是一次飞书批写入边界；`concurrency: 100` 是
运行时允许在途的 Delivery 上限。三者互不替代，`tasks[].config.batch_size`
仅作为 `ctx.task_config` 传给 handler，不会改变 MySQL 或飞书的批大小。

## Handler 契约

YAML 只负责连接资源，不承载字段转换。`handler:to_feishu_fields` 必须是当前
Python 运行环境可导入的 callable；它接收 `(ctx, row)`，其中 `row` 是
`view_order_sync` 的一行字典，返回可以直接写入飞书的字段字典。

最低契约是返回 `订单编号`，其值必须与本条订单流水稳定对应：

```python
async def to_feishu_fields(ctx, row):
    # 订单编号及其余飞书字段由业务项目映射；不要把此逻辑写进 YAML。
    return {
        "订单编号": ...,
        # 其余目标字段
    }
```

映射函数不应修改 `orderCreateTime` 或 `orderKey` 的源数据含义。若飞书人员字段
使用 `user_id`，返回的结构也必须符合该字段类型；详情见
[Feishu Bitable 字段转换](/broker/feishu-bitable#字段转换)。

## 首次上线

1. 先安装两个插件和业务 handler 所在包，然后执行：

   ```bash
   onestep check --strict worker.yaml
   ```

2. 完全停止任何旧 worker，确保只有一个实例写入该飞书表。
3. 启动 worker。它会先分页加载飞书已有的 `订单编号`；目标记录超过
   `500 × 200 = 100,000` 条时会安全启动失败。此时根据实际表规模调整
   `insert_index_max_pages`，不要接受截断索引。
4. 观察启动完成和第一轮写入。正常增量路径不会逐条 Search 飞书；满 100 条
   或等待 1 秒会触发批写入。

## 运行与恢复

### 游标与重试

成功记录可以乱序完成，但持久游标只推进连续成功前缀。某条记录进入重试后，
后续 SQL 拉取会暂停，直至该缺口成功或耗尽 `max_attempts`；耗尽后 Source 停在
失败行之前。未持久化的记录在重启后会重放。

`onestep-mysql 0.5.1+` 会把 `DATETIME` 游标组件存为带类型标记的 ISO-8601 JSON，
启动时恢复为原始 `datetime` 对象后再参加 MySQL keyset 查询。因此不需要把
`orderCreateTime` 改为字符串，也不需要为游标表做迁移。

如果旧的 `0.5.0` worker 在 `mysql incremental cursor commit` 后报：

```text
TypeError: Object of type datetime is not JSON serializable
```

应停止旧 worker，升级到 `onestep-mysql>=0.5.1` 后使用相同的 `state_key` 重启。
**不要手工把 `onestep_cursor` 推进到报错行之后**：飞书可能已接受一部分记录，
而尚未确认的记录不能被跳过。重放会由 `订单编号` 键索引识别为已存在并跳过。

### 观测点

开启 INFO 日志后可按结构化事件排查：

| 事件 | 关键字段 | 用途 |
|---|---|---|
| `mysql_incremental_fetch` | `row_count`、`pending_cursor_rows` | 查看源端拉取量和未提交积压。 |
| `mysql_incremental_retry` | `retry_count`、`attempt` | 查看同一逻辑行的重试。 |
| `mysql_incremental_cursor_commit` | `outcome`、`coalesced_ack_count` | 确认连续前缀已被持久化。 |
| `feishu_insert_batch_write` | `batch_size`、`outcome` | 查看飞书批写入结果。 |
| `feishu_insert_retry` | `recovery_round`、`unresolved_count` | 查看不确定批写入的精确查找恢复。 |

不要在日志或案例配置中输出 DSN、`app_secret` 或 `app_token`。

## 参数取舍

| 参数 | 本案例值 | 取舍 |
|---|---:|---|
| MySQL `batch_size` | 1000 | 减少轮询开销；仍受可用并发上限约束。 |
| 飞书 `batch_size` | 100 | 作为写入确认边界；根据飞书 API 与延迟需求调整。 |
| `flush_interval_s` | 1 | 低流量时最多等待约一秒再发送部分批次。 |
| `concurrency` | 100 | 限制在途 Delivery，不等于 100 个并发 MySQL 查询。 |
| `insert_index_max_pages` | 200 | 用确定上限保护启动扫描；必须覆盖目标表实际页数。 |
| `max_attempts` | 5 | 临时故障可重试；永久数据问题会阻塞在失败行前，便于排查。 |

## 相关文档

- [MySQL：可靠持久游标与重试](/broker/mysql#可靠持久游标与重试)
- [Feishu Bitable：高吞吐 Insert 增量同步](/broker/feishu-bitable#高吞吐-insert-增量同步)
- [YAML 任务定义](/yaml-task-definition)
