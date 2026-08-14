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
fail the send, be omitted, or be created and linked. See the
[Feishu Bitable broker documentation](https://onestep.code05.com/broker/feishu-bitable)
for the complete behavior and concurrency limits.
