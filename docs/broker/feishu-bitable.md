---
title: Feishu Bitable | Broker
outline: deep
---

# Feishu Bitable

`onestep-feishu-bitable` 用于把飞书多维表格作为增量 Source 或表输出 Sink。它适合 MySQL 到多维表格同步、以及多维表格之间的增量复制。

## 安装

```bash
pip install onestep-feishu-bitable
```

安装后，插件会通过 `onestep.resources` entry point 自动注册 YAML 资源类型。

## Python 用法

```python
from onestep import OneStepApp
from onestep_feishu_bitable import FeishuBitableConnector

app = OneStepApp("feishu-sync")
feishu = FeishuBitableConnector(
    app_id="cli_xxx",
    app_secret="secret",
)

source = feishu.incremental(
    app_token="bascn_source",
    table_id="tbl_source",
    cursor_field="更新时间",
    user_id_type="user_id",
    batch_size=100,
    fallback_scan_page_limit=100,
)

sink = feishu.table_sink(
    app_token="bascn_target",
    table_id="tbl_target",
    mode="upsert",
    match_fields=["编号"],
    user_id_type="user_id",
)


@app.task(source=source, emit=sink, concurrency=4)
async def copy_row(ctx, payload):
    fields = payload["fields"]
    return {
        "编号": fields["编号"],
        "标题": fields.get("标题"),
        "更新时间": fields.get("更新时间"),
    }
```

增量 Source 输出的 payload 形如：

```python
{
    "record_id": "recxxxx",
    "fields": {"编号": "A001", "更新时间": "2026-06-08T10:00:00+08:00"},
}
```

表 Sink 接受直接字段映射，也接受 `{"fields": ...}` 包装后的 payload。字段名会按原样传给飞书，可以使用多维表格里的中文显示名。

## YAML 配置

```yaml
resources:
  feishu:
    type: feishu_bitable
    app_id: "${FEISHU_APP_ID}"
    app_secret: "${FEISHU_APP_SECRET}"

  source_orders:
    type: feishu_bitable_incremental
    connector: feishu
    app_token: "${SOURCE_FEISHU_APP_TOKEN}"
    table_id: "${SOURCE_FEISHU_TABLE_ID}"
    cursor_field: 更新时间
    user_id_type: user_id
    batch_size: 100
    fallback_scan_page_limit: 100

  target_orders:
    type: feishu_bitable_table_sink
    connector: feishu
    app_token: "${TARGET_FEISHU_APP_TOKEN}"
    table_id: "${TARGET_FEISHU_TABLE_ID}"
    mode: upsert
    match_fields: [编号]
    user_id_type: user_id

tasks:
  - name: sync_orders
    source: source_orders
    emit: target_orders
    handler:
      ref: worker.tasks.orders:map_order_fields
    concurrency: 4
```

## 字段转换

飞书文本字段有时会返回富文本数组或对象。写入普通文本字段前，可以在 handler 中用插件提供的 helper 拉平：

```python
from onestep_feishu_bitable import feishu_bitable_text, feishu_bitable_user


async def map_order_fields(ctx, payload):
    fields = payload["fields"]
    return {
        "编号": feishu_bitable_text(fields.get("编号")),
        "标题": feishu_bitable_text(fields.get("标题")),
        "负责人": feishu_bitable_user(fields.get("负责人ID")),
    }
```

`feishu_bitable_user("u_xxx")` 会返回飞书人员字段需要的 `[{"id": "u_xxx"}]` 结构。`user_id_type` 需要和你提供的人员 ID 类型一致。

## 关联字段

Table Sink 可以用 `relations` 把上游业务键解析成飞书关联字段需要的 `record_id`。例如企业表按“企业名称”唯一标识企业，项目表的“关联企业”允许关联多个企业：

```yaml
resources:
  projects:
    type: feishu_bitable_table_sink
    connector: feishu
    app_token: "${FEISHU_APP_TOKEN}"
    table_id: "${PROJECT_TABLE_ID}"
    mode: upsert
    match_fields: [项目编号]
    relations:
      关联企业:
        from: 企业名称
        table_id: "${ENTERPRISE_TABLE_ID}"
        key: 企业名称
        on_missing: create
        create_fields:
          数据状态: 待完善
```

handler 不需要查询企业表，继续返回业务值：

```python
async def map_project(ctx, payload):
    return {
        "项目编号": payload["项目编号"],
        "项目名称": payload["项目名称"],
        "企业名称": ["企业A", "企业B", "企业C"],
    }
```

Sink 会按企业名称逐项查询，把项目请求转换为：

```python
{
    "项目编号": "P-001",
    "项目名称": "联合建设项目",
    "关联企业": ["rec_a", "rec_b", "rec_c"],
}
```

输入也可以是单个字符串。字符串解析成一个 ID，list 或 tuple 逐项解析；空值被忽略，重复值按第一次出现的顺序去重。`from` 省略时从关联字段本身读取业务值。

`on_missing` 支持三种策略：

| 策略 | 未找到关联记录时的行为 |
|---|---|
| `error` | 默认值；任意非空值未找到时整条目标记录失败 |
| `empty` | 跳过未找到的值；全部未找到时写 `[]`，更新已有记录时会清空旧关联 |
| `create` | 用 `key` 和 `create_fields` 创建缺失记录，并把新 ID 写入当前关联字段 |

`key` 必须在关联表中保持业务唯一。命中多条记录时 Sink 会失败，不会随机选择。`create` 在同一个 Sink 实例内会避免并发重复创建，但多个 worker 进程或部署实例之间没有全局原子去重保证。

关联表默认与目标表使用相同 `app_token`。跨多维表 Base 关联时，在关系配置中增加 `app_token`：

```yaml
relations:
  关联企业:
    from: 企业名称
    app_token: "${ENTERPRISE_FEISHU_APP_TOKEN}"
    table_id: "${ENTERPRISE_TABLE_ID}"
    key: 企业名称
    on_missing: error
```

飞书双向关联的反向字段仍由飞书服务端根据字段配置维护，插件只写当前目标表的关联字段。

### 关联字段缓存

每次关联解析都要调用一次飞书 records search 接口，而该接口限流为 **20 QPS**。批量同步时同一批业务键会在批与批之间反复出现，逐次 search 很容易打满配额并触发 `429`。给关联字段配置 `cache` 可以把解析结果缓存在 Sink 实例内存中：

```yaml
relations:
  关联企业:              # 稳定主数据，任务内高频引用 -> 启动预加载
    from: 企业名称
    table_id: "${ENTERPRISE_TABLE_ID}"
    key: 企业名称
    on_missing: create
    cache: eager
  责任部门:              # key 空间大，单次只触碰一小部分 -> 查过才缓存
    from: 部门编码
    table_id: "${DEPT_TABLE_ID}"
    key: 部门编码
    on_missing: error
    cache: lazy
```

| 取值 | 语义 | 适用场景 |
|---|---|---|
| `none` | 默认值；每个唯一业务键每次解析都 search，行为与不配置 `cache` 完全一致 | 关联表频繁变更、对一致性敏感，或单次任务只跑几条数据 |
| `lazy` | 查过才缓存 `{业务键: record_id}`；命中直接用，miss 回源 search 并回填 | 通用推荐；key 空间大而单次任务只触碰其中一小部分，不付启动扫描成本 |
| `eager` | `open()` 时分页拉取关联表 `key` 字段全量到内存；命中直接用，miss 按 `on_missing` 处理（`empty` 视为不存在、跳过 search，`error`/`create` 仍回源 search） | 关联表是稳定主数据、记录数可控，且任务运行期间对同一批 key 高频引用 |

`cache` 是**逐关联字段**配置的，同一个 Sink 里可以混用三种取值。

**缓存只是加速层，飞书仍是 source of truth**：命中是乐观使用，直接采用缓存中的 `record_id`；**未命中必须回源 search 一次**，绝不会把“缓存里没有”当成“飞书里不存在”。因此即使启动后关联表新增了记录，`on_missing: create` 也会先 search 确认，不会误建重复主数据。唯一的例外是 `eager` + `on_missing: empty`：启动快照未命中即视为确实不存在，跳过 search，既不报错也不回填。

`eager` 的启动扫描约束：

- 复用 sink 级的 `insert_index_page_size`（默认 `500`）与 `insert_index_max_pages`（默认 `200`），即单个关联表最多扫描 **10 万条**记录，不引入第二套配置项。
- 扫描只请求 `key` 字段；请求字段缺失或为空的记录跳过，重复 key 后者覆盖前者，均计入结构化日志 `feishu_relation_cache_scan`。
- `key` 可以是文本字段（type=1）：飞书返回的富文本段（如 `[{"text": "某公司", "type": "text"}]`）会自动扁平化为纯文本后进入缓存，与 `from` 侧的富文本业务值一致。其他复合字段类型（人员、附件、位置等）不保证能被正确扁平化，应优先使用返回纯值的字段（数字/公式/单选等）。
- 若扫描结束缓存为空而源表确有记录被跳过（`scan_keys == 0` 且 `missing_key_records > 0`），会额外输出一条 WARNING 日志 `warn_empty` 提示 key 字段可能无法解析，避免关联静默失效。
- 达到页数上界而飞书仍有更多记录，或分页 token 不前进时，`open()` 直接以永久错误失败，任务不会带着截断的缓存启动。
- `eager` 假设关联表在任务运行期间**没有会与本任务冲突的并发写入**（与 `insert_key_index` 相同的单写者前提）。新增记录不会导致数据错误，只会让该 key 暂付一次 search 成本；删除或修改 key 会造成悬垂/失真缓存。

运维须知：**缓存不感知关联表中的删除与 key 修改，也不持久化、不跨进程共享**。悬垂 `record_id` 会在写入时被飞书拒绝（`RecordIdNotFound` / `LinkFieldConvFail`），此时**重启任务即可重建缓存**收敛。

## 重要参数

| 参数 | 说明 |
|---|---|
| `cursor_field` | 增量读取的高水位字段 |
| `match_fields` | upsert 时用于匹配目标记录的业务唯一字段 |
| `batch_size` | 每次拉取的最大记录数 |
| `fallback_scan_page_limit` | 飞书拒绝游标排序时，本地 fallback 扫描最多读取的页数，默认 `100` |
| `user_id_type` | 人员字段使用的 ID 类型，例如 `open_id`、`union_id`、`user_id` |
| `relations` | 将业务键解析为关联记录 ID 的字段级 mapping |
| `relations.*.cache` | 关联解析缓存策略：`none`（默认）/ `lazy` / `eager`，见“关联字段缓存” |

`fallback_scan_page_limit` 是防护阀。只有确认表规模和调用配额允许 fallback 扫描时，才提高这个值。

## 高吞吐 Insert 增量同步

不可变操作记录可以为 `insert` Sink 开启 `insert_key_index`：Sink 启动时只分页读取
一个 `match_fields` 字段到内存集合，正常处理不再逐条调用 Search。目标表 5 万条、
页大小 500 时，启动扫描约 100 次。扫描达到 `insert_index_max_pages` 仍未结束会
直接启动失败，不会使用截断索引。

```yaml
order_table:
  type: feishu_bitable_table_sink
  connector: feishu
  app_token: "${FEISHU_APP_TOKEN}"
  table_id: "${FEISHU_TABLE_ID}"
  mode: insert
  match_fields: [订单编号]
  batch_size: 100
  flush_interval_s: 1
  insert_key_index: true
  insert_index_page_size: 500
  insert_index_max_pages: 200
  ambiguous_write_max_rounds: 3
```

该模式仅支持一个匹配字段且不能同时配置 `relations`。每次 `send()` 只有在记录已
存在或所属批次确认创建成功后才返回，因此上游 Delivery 不会在私有缓冲区仍未落盘时
提前确认。超时、断链或不完整响应会先精确查询受影响批次，再只创建明确缺失的键；
查询失败永远不会被当作“不存在”。

启动索引只是 `open()` 时刻的内存快照，因此命中该快照的键在跳过前会先做一次精确查询
确认记录仍然存在。否则目标行在启动后被删除时，记录会被静默丢弃，而上游仍会确认并推进
游标，造成永久数据丢失。代价是：**被重复发送的既有键每个多一次查询**；本实例自己写入
成功的键则无需查询。

若目标表在整个运行期间只追加（不会删除或移动行），可设置
`insert_index_skip_verification: true` 恢复“零查询”快路径。这是吞吐最高的模式，同时
也接受“既有键对应的行已消失时被静默跳过”的风险。

`close_drain_max_rounds`（默认 `1000`）为关闭阶段的排空设置上限，超过上限会抛出
`ConnectorOperationError` 而不会无限循环；`close()` 之后再调用 `send()` 会立刻抛出
永久性 `ConnectorOperationError`，不会静默写入一个再也不会刷新的缓冲区。

内存索引要求同一 `(app_token, table_id)` 只有一个活动写入实例。手工新增或第二个
worker 会造成启动后竞态。该模式不保存 record ID，也不提供持久幂等账本、更新、删除、
CDC 或多写者 exactly-once 保证。

要将该模式与 MySQL 复合游标、重试和安全恢复组合使用，参见
[实战篇：MySQL 订单流水同步到飞书多维表格](/guide/cases/mysql-feishu-order-sync)。

## 错误分类与重试

Bitable 的大部分错误在 HTTP 200 响应体中返回，插件按以下优先级判定可重试性：

1. HTTP status 对 `429` / `5xx` 保持权威，直接按可重试处理。
2. 其余情况读取响应体中的飞书业务 `code` 数字码：官方标记为可重试的限流/瞬时错误（如 `1254290`、`1254291`、`1254607`、`1254002`）会正常重试；配额类永久错误（如 `1254104`）按永久错误快速失败，不会因为消息文本包含 "limit" 被误判成限流而进入稳定重试循环。
3. 没有已知 `code` 时才回退到双语消息启发式。

游标与批次可靠性：暂停、停止或排空任务时，未确认批次会整体释放，不再卡住持久游标；`retry()` 会真正重投同一行；`fail()` 丢弃毒行令牌，单行坏数据不会永久冻结游标。重启后从持久游标恢复，未提交的行会重放。

## 下一步

- [YAML 任务定义](/yaml-task-definition) - 查看插件资源注册和严格校验
- [SQL（MySQL / PostgreSQL）](/broker/sql) - 从数据库增量同步到多维表格
- [HTTP Sink](/broker/http) - 对接普通 HTTP API
