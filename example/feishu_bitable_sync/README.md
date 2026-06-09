# Feishu Bitable Sync Example

This example is for live debugging a Feishu Bitable to Feishu Bitable sync.

It reads records incrementally from a source Bitable table and upserts them into
a target Bitable table by a business unique field. The default mapping assumes
both tables contain these fields:

- `标题`
- `编号`

The handler uses `onestep.connectors.feishu_bitable.feishu_bitable_text(...)` to convert
Feishu rich text style values into plain strings before writing them back, which
avoids common target text-field conversion failures.
It can also map a Feishu person field with
`onestep.connectors.feishu_bitable.feishu_bitable_user(...)` when you provide a source
field that contains Feishu user IDs.
You can change the field names in `src/feishu_bitable_demo/tasks.py`.

## Environment

```bash
export FEISHU_APP_ID="cli_xxx"
export FEISHU_APP_SECRET="xxx"
export FEISHU_SOURCE_APP_TOKEN="bascnxxx"
export FEISHU_SOURCE_TABLE_ID="tblxxx"
export FEISHU_TARGET_APP_TOKEN="bascnxxx"
export FEISHU_TARGET_TABLE_ID="tblxxx"
export FEISHU_CURSOR_FIELD="最后更新时间"
export FEISHU_MATCH_FIELD="编号"
export FEISHU_USER_ID_TYPE="user_id"
```

Optional:

```bash
export FEISHU_BASE_URL="https://open.feishu.cn"
export FEISHU_OWNER_FIELD="负责人"
export FEISHU_OWNER_ID_FIELD="负责人ID"
```

When `FEISHU_OWNER_FIELD` and `FEISHU_OWNER_ID_FIELD` are set, the handler reads
the user ID from the source field and writes the target person field as
`[{"id": "..."}]`. Keep `FEISHU_USER_ID_TYPE=user_id` when those IDs are Feishu
`user_id` values.

For Lark international deployments, set:

```bash
export FEISHU_BASE_URL="https://open.larksuite.com"
```

## Run

From the repo root:

```bash
cd example/feishu_bitable_sync
PYTHONPATH=../../src:src uv run --extra yaml python -m onestep.cli check --strict worker.yaml
PYTHONPATH=../../src:src uv run --extra yaml python -m onestep.cli run worker.yaml
```

To inspect the mapped rows without writing to the target table, change the task
config in `worker.yaml`:

```yaml
config:
  dry_run: true
```

When `dry_run` is true, the handler prints mapped records and returns `None`, so
the sink is not called.

## Batch Size And Concurrency

The source `batch_size` caps how many records the connector may fetch in one
poll. The runtime also passes the task's available concurrency as `fetch(limit)`.
That means the effective fetch size is:

```text
min(task concurrency, source batch_size)
```

This example sets both to `20`:

```yaml
source_orders:
  batch_size: 20

tasks:
  - name: sync_orders
    concurrency: 20
```

If `concurrency` is omitted, onestep defaults it to `1`, so the source will only
fetch one record at a time even when `batch_size` is larger.
