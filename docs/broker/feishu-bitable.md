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

## 重要参数

| 参数 | 说明 |
|---|---|
| `cursor_field` | 增量读取的高水位字段 |
| `match_fields` | upsert 时用于匹配目标记录的业务唯一字段 |
| `batch_size` | 每次拉取的最大记录数 |
| `fallback_scan_page_limit` | 飞书拒绝游标排序时，本地 fallback 扫描最多读取的页数，默认 `100` |
| `user_id_type` | 人员字段使用的 ID 类型，例如 `open_id`、`union_id`、`user_id` |

`fallback_scan_page_limit` 是防护阀。只有确认表规模和调用配额允许 fallback 扫描时，才提高这个值。

## 下一步

- [YAML 任务定义](/yaml-task-definition) - 查看插件资源注册和严格校验
- [MySQL](/broker/mysql) - 从数据库增量同步到多维表格
- [HTTP Sink](/broker/http) - 对接普通 HTTP API
