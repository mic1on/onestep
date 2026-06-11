# Feishu Bitable Source And Sink Design

## Summary

Add first-class Feishu Bitable connectors so onestep workers can sync data from
MySQL into Feishu Bitable and sync data between Feishu Bitable tables.

Update on 2026-06-09: the implementation now lives in the repo-local
`plugins/onestep-feishu-bitable` package instead of core. Use the
`onestep_feishu_bitable` import path.

The design adds a shared `FeishuBitableConnector`, an incremental Bitable source,
and a table sink whose default write mode is business-field `upsert`. The sink
does not require callers to store target Feishu `record_id` values in MySQL.

## Problem

Users commonly need these flows:

- MySQL source to Feishu Bitable sink
- Feishu Bitable source to Feishu Bitable sink

The existing `HttpSink` can technically call Feishu endpoints, but it is not a
good fit for this use case:

- token acquisition and refresh would be repeated in user code
- Bitable record pagination and filtering would live outside connector behavior
- upsert would require ad hoc search/update/create code in handlers
- retry classification, secret redaction, and YAML strict validation would not be
  integrated with the rest of onestep

## Goals

- Add a Feishu Bitable sink suitable for MySQL to Bitable sync.
- Add a Feishu Bitable incremental source suitable for Bitable to Bitable sync.
- Make sink `upsert` the default write mode.
- Match upsert records by a declared business field, not by target `record_id`.
- Keep field mapping in Python handlers, consistent with existing YAML design.
- Integrate with strict YAML validation, control-plane descriptors, and connector
  error handling.

## Non-Goals

- No Bitable task-queue source with claim/ack/nack status fields in the first
  version.
- No requirement to write target `record_id` values back into MySQL.
- No transform DSL in YAML.
- No automatic schema creation or Bitable field creation.
- No live integration test that requires real Feishu credentials in the default
  test suite.
- No user-access-token OAuth flow in the first version.

## User-Facing YAML

```yaml
resources:
  feishu:
    type: feishu_bitable
    app_id: "${FEISHU_APP_ID}"
    app_secret: "${FEISHU_APP_SECRET}"

  mysql_orders:
    type: mysql_incremental
    connector: mysql_main
    table: orders
    key: id
    cursor: [updated_at, id]

  feishu_orders:
    type: feishu_bitable_table_sink
    connector: feishu
    app_token: "${FEISHU_APP_TOKEN}"
    table_id: "${FEISHU_TABLE_ID}"
    mode: upsert
    match_fields: [order_no]

tasks:
  - name: sync_orders_to_feishu
    source: mysql_orders
    emit: feishu_orders
    handler:
      ref: worker.tasks.orders:to_feishu_fields
```

For Bitable to Bitable sync:

```yaml
resources:
  source_orders:
    type: feishu_bitable_incremental
    connector: feishu
    app_token: "${SOURCE_FEISHU_APP_TOKEN}"
    table_id: "${SOURCE_FEISHU_TABLE_ID}"
    cursor_field: updated_at
    batch_size: 100

  target_orders:
    type: feishu_bitable_table_sink
    connector: feishu
    app_token: "${TARGET_FEISHU_APP_TOKEN}"
    table_id: "${TARGET_FEISHU_TABLE_ID}"
    mode: upsert
    match_fields: [order_no]
```

Field names are passed through exactly as configured and may use Feishu display
names, including non-ASCII names, when the target table uses them.

## User-Facing Python API

```python
from onestep_feishu_bitable import FeishuBitableConnector

feishu = FeishuBitableConnector(
    app_id=os.environ["FEISHU_APP_ID"],
    app_secret=os.environ["FEISHU_APP_SECRET"],
)

source = feishu.incremental(
    app_token=os.environ["SOURCE_FEISHU_APP_TOKEN"],
    table_id=os.environ["SOURCE_FEISHU_TABLE_ID"],
    cursor_field="updated_at",
)

sink = feishu.table_sink(
    app_token=os.environ["TARGET_FEISHU_APP_TOKEN"],
    table_id=os.environ["TARGET_FEISHU_TABLE_ID"],
    mode="upsert",
    match_fields=["order_no"],
)
```

## Resource Types

### `feishu_bitable`

Fields:

- `type`: required, `feishu_bitable`
- `app_id`: required
- `app_secret`: required
- `base_url`: optional, default `https://open.feishu.cn`
- `timeout_s`: optional, default `10.0`

The connector uses `app_id` and `app_secret` to obtain a
`tenant_access_token`, caches it in memory, and refreshes it before expiry.

### `feishu_bitable_incremental`

Fields:

- `type`: required, `feishu_bitable_incremental`
- `connector`: required, name of a `feishu_bitable` resource
- `app_token`: required, Bitable app token
- `table_id`: required
- `cursor_field`: required, field used as the incremental high-water mark
- `batch_size`: optional, default `100`
- `poll_interval_s`: optional, default `1.0`
- `state`: optional cursor store resource
- `state_key`: optional explicit cursor state key

The source is intended for incremental sync, not work-queue claim semantics.

### `feishu_bitable_table_sink`

Fields:

- `type`: required, `feishu_bitable_table_sink`
- `connector`: required, name of a `feishu_bitable` resource
- `app_token`: required, Bitable app token
- `table_id`: required
- `mode`: optional, one of `upsert`, `create`, or `update`; default `upsert`
- `match_fields`: required for `upsert` and `update`

`match_fields` are business unique fields in the target table. The sink does not
use source or target `record_id` as its default matching key.

## Source Payload Shape

The incremental source returns each delivery body as:

```python
{
    "record_id": "recxxxx",
    "fields": {
        "order_no": "A001",
        "updated_at": "2026-06-08T10:00:00+08:00",
    },
}
```

The envelope metadata includes connector context such as:

```python
{
    "backend": "feishu_bitable",
    "app_token": "...",
    "table_id": "...",
}
```

Secrets are not included in metadata.

## Sink Payload Shape

The sink accepts either a direct mapping:

```python
{
    "order_no": "A001",
    "status": "paid",
}
```

or a Bitable-style body:

```python
{
    "fields": {
        "order_no": "A001",
        "status": "paid",
    }
}
```

In both cases the sink sends Feishu record fields as-is. Handlers own field
renaming, value conversion, validation, and enrichment.

## Incremental Source Semantics

The source stores and advances a cursor only after deliveries are acknowledged.

Flow:

1. `open()` loads the saved cursor from the configured cursor store, or starts
   from the beginning when no cursor exists.
2. `fetch(limit)` calls the Feishu Bitable record list or search endpoint and
   requests records ordered by `cursor_field`.
3. Records greater than the committed `(cursor_field_value, record_id)` cursor
   are delivered. In practice that means a later `cursor_field` value, or the
   same `cursor_field` value with a later `record_id`.
4. Each delivered record tracks a token containing
   `(cursor_field_value, record_id)`.
5. `ack()` advances the saved cursor in order.

`record_id` is only a source-side tie-breaker for records with equal cursor field
values. It is not used as the sink upsert key.

If Feishu sorting or filtering cannot express the exact tie-breaker, the
connector may fetch a page using the supported filter and sort, then apply local
tie-break filtering before constructing deliveries. This keeps behavior stable
without exposing `record_id` as a business requirement.

## Sink Write Semantics

### `create`

The sink creates a new Bitable record with the provided fields. It does not query
for existing records.

### `update`

The sink requires `match_fields`.

- If no record matches, raise a connector operation error.
- If one record matches, update that record.
- If multiple records match, raise a permanent connector operation error.

### `upsert`

The sink requires `match_fields`.

- If no record matches, create a new record.
- If one record matches, update that record.
- If multiple records match, raise a permanent connector operation error.

The payload must contain a non-empty value for every configured `match_fields`
entry. Missing or empty values are permanent payload errors.

## Authentication

The first version supports Feishu self-built app authentication with
`app_id + app_secret` and `tenant_access_token`.

Behavior:

- token requests are made lazily before the first Feishu API call
- the token is cached in memory
- the connector refreshes the token before expiry
- failed token acquisition is surfaced as `ConnectorOperationError`
- app credentials are redacted from control-plane descriptors

The connector allows `base_url` configuration so the same code path can support
Lark international deployments when pointed at `https://open.larksuite.com`.

## HTTP Client

Use Python standard-library HTTP APIs for the first version so the connector does
not add a required dependency. Blocking calls run through `asyncio.to_thread`,
matching the style of existing connectors such as MySQL and HTTP sink.

Request handling should be centralized inside the Feishu connector:

- JSON encoding and decoding
- `Authorization` header injection
- timeout handling
- Feishu response envelope validation
- error classification

## Error Classification

All source and sink failures should surface as `ConnectorOperationError` where
possible.

Classification:

- network timeout or connection error: `DISCONNECTED`
- HTTP 429 or Feishu rate-limit error code: `THROTTLED`
- HTTP 5xx: `TRANSIENT`
- invalid credentials, missing app permission, inaccessible app/table:
  `MISCONFIGURED`
- missing field, malformed filter, duplicate upsert matches, invalid payload:
  `PERMANENT`
- ambiguous Feishu errors that may succeed on retry: `TRANSIENT`

`retry_delay_s` should use the source `poll_interval_s` for fetch failures and a
small default for sink send failures unless Feishu returns a better retry hint.

## Control-Plane Descriptor

Add `control_plane_descriptor()` to connector-created sources and sinks.

Descriptors should include:

- kind
- resource name
- base URL host
- app token redacted or shortened
- table ID
- mode and match field for sinks
- cursor field and batch size for sources

Descriptors must not include:

- `app_secret`
- `tenant_access_token`
- full authorization headers

## Strict YAML Validation

Extend strict resource validation for:

- `feishu_bitable`
- `feishu_bitable_incremental`
- `feishu_bitable_table_sink`

Validation should reject:

- unknown fields
- missing required strings
- invalid sink `mode`
- missing `match_fields` when mode is `upsert` or `update`
- non-mapping values where mappings are required
- invalid numeric timeout, batch size, or poll interval values

## Documentation

Update connector docs with:

- Feishu Bitable connector overview
- MySQL to Bitable YAML example
- Bitable to Bitable YAML example
- explanation of `cursor_field` versus `match_fields`
- note that `record_id` is not required for default upsert
- credential environment variable guidance

## Testing Strategy

Add focused unit tests using local fake HTTP servers or patched connector request
methods. The default suite should not require real Feishu credentials.

Tests:

- token acquisition and in-memory token reuse
- token refresh after expiry
- source fetch pagination and envelope body shape
- source cursor advancement only after ordered ack
- source resumes from saved cursor
- sink `create` sends a create request
- sink `upsert` creates when there are zero matches
- sink `upsert` updates when there is one match
- sink `upsert` raises permanent error when there are multiple matches
- sink `update` raises when there is no match
- sink rejects missing or empty `match_fields` payload values
- YAML strict mode accepts valid Feishu resources
- YAML strict mode rejects unknown Feishu fields
- control-plane descriptors redact credentials
- error classification covers timeout, 429, 5xx, permission, table, field, and
  duplicate-match failures

## Backward Compatibility

This is additive.

- Existing YAML files keep working.
- Existing Python imports keep working.
- Existing connector behavior is unchanged.
- New symbols are exported from `onestep` and `onestep.connectors`.

## Open Implementation Notes

- The exact Feishu record search payload should be aligned with the current
  official Bitable record API during implementation.
- Feishu field values may have type-specific shapes. The first connector version
  passes values through and leaves type conversion to handlers.
- A future version can add a table-queue source once there is a clear need for
  Bitable status-field claim/ack/nack semantics.
