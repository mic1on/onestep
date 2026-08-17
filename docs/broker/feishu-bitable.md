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

## 重要参数

| 参数 | 说明 |
|---|---|
| `cursor_field` | 增量读取的高水位字段 |
| `match_fields` | upsert 时用于匹配目标记录的业务唯一字段 |
| `batch_size` | 每次拉取的最大记录数 |
| `fallback_scan_page_limit` | 飞书拒绝游标排序时，本地 fallback 扫描最多读取的页数，默认 `100` |
| `user_id_type` | 人员字段使用的 ID 类型，例如 `open_id`、`union_id`、`user_id` |
| `relations` | 将业务键解析为关联记录 ID 的字段级 mapping |

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

内存索引要求同一 `(app_token, table_id)` 只有一个活动写入实例。手工新增或第二个
worker 会造成启动后竞态。该模式不保存 record ID，也不提供持久幂等账本、更新、删除、
CDC 或多写者 exactly-once 保证。

要将该模式与 MySQL 复合游标、重试和安全恢复组合使用，参见
[实战篇：MySQL 订单流水同步到飞书多维表格](/guide/cases/mysql-feishu-order-sync)。

## 下一步

- [YAML 任务定义](/yaml-task-definition) - 查看插件资源注册和严格校验
- [MySQL](/broker/mysql) - 从数据库增量同步到多维表格
- [HTTP Sink](/broker/http) - 对接普通 HTTP API
