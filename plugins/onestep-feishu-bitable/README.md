# onestep-feishu-bitable

Feishu Bitable connector plugin for onestep.

Install the plugin in the same Python environment as `onestep`:

```bash
pip install onestep-feishu-bitable
```

Python API:

```python
from onestep_feishu_bitable import FeishuBitableConnector

feishu = FeishuBitableConnector(app_id="cli_xxx", app_secret="secret")
source = feishu.incremental(
    app_token="bascnxxx",
    table_id="tbl_source",
    cursor_field="最后更新时间",
    fallback_scan_page_limit=100,
)
```

`fallback_scan_page_limit` caps the unsorted fallback scan used when Feishu
rejects cursor sorting. Increase it only for tables where a full fallback scan
is acceptable.

YAML resource types registered by the plugin:

- `feishu_bitable`
- `feishu_bitable_incremental`
- `feishu_bitable_table_sink`

Table sinks can resolve business keys into Feishu relation record IDs:

```yaml
resources:
  projects:
    type: feishu_bitable_table_sink
    connector: feishu
    app_token: project-app-token
    table_id: projects
    mode: upsert
    match_fields: [project_id]
    relations:
      companies:
        from: company_names
        table_id: companies
        key: name
        on_missing: create
```

The input may contain one business key or a list. Missing related records can
fail the send, be omitted, or be created and linked. Set `relations.*.cache` to
`lazy` or `eager` to cache key-to-record-ID resolution in the sink instance and
cut the repeated `search_records` calls that cause 429 throttling. See the
[Feishu Bitable broker documentation](https://onestep.code05.com/broker/feishu-bitable)
for the complete behavior and concurrency limits.

For immutable, insert-only logs, `insert_key_index: true` preloads one unique
`match_fields` value and removes normal per-row destination searches. It is
single-writer only, cannot be combined with `relations`, retains no record IDs,
and waits for each batch member's confirmed outcome before returning from
`send()`. Ambiguous writes are searched by affected key before confirmed misses
are recreated. See `example/mysql_feishu_insert.yaml`.

Because the preloaded index is only a snapshot taken at `open()`, a key that is
reported as already present is **confirmed with one exact lookup before it is
skipped** — otherwise a row deleted upstream after startup would be dropped
while the runtime still acknowledged it, losing data permanently. This costs one
extra search per *pre-existing* key that is re-sent; keys this sink writes
itself are trusted without a lookup.

Set `insert_index_skip_verification: true` to restore the original zero-lookup
behaviour. Do this only when the destination table is append-only for the whole
run (no row is ever deleted or moved): it is the highest-throughput mode, and it
accepts that a pre-existing key whose row disappeared is silently skipped.

`close_drain_max_rounds` (default `1000`) bounds the shutdown drain; exceeding it
reports a `ConnectorOperationError` rather than looping. Sending after `close()`
raises a permanent `ConnectorOperationError` instead of silently buffering into a
sink that will never flush again.
