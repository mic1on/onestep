# onestep-elasticsearch

`onestep-elasticsearch` provides one asynchronous bulk sink for the common
Elasticsearch and OpenSearch HTTP API surface.

## Install

```bash
pip install onestep-elasticsearch
```

## Python

```python
from onestep_elasticsearch import ElasticsearchConnector

search = ElasticsearchConnector(
    ["https://search-1:9200", "https://search-2:9200"],
    distribution="auto",
    username="ingest",
    password="secret",
    verify_certs=True,
    ca_certs="/etc/ssl/search-ca.pem",
    request_timeout_s=30.0,
)
sink = search.bulk_sink(
    index="events-v1", operation="index", id_field="event_id",
    chunk_size=500, max_chunk_bytes=5_000_000, refresh=False,
)
```

## YAML

```yaml
resources:
  search:
    type: elasticsearch
    hosts: ["${SEARCH_URL}"]
    distribution: auto
    username: "${SEARCH_USERNAME}"
    password: "${SEARCH_PASSWORD}"
    verify_certs: true
    ca_certs: "${SEARCH_CA_FILE:-/etc/ssl/certs/ca-certificates.crt}"
    request_timeout_s: 30
  events:
    type: elasticsearch_bulk_sink
    connector: search
    index: events-v1
    operation: index
    id_field: event_id
    chunk_size: 500
    max_chunk_bytes: 5000000
    refresh: false
```

## Payloads and transport

`send()` accepts one mapping or one non-empty sequence of mappings. Each mapping
is the complete document `_source`; `id_field` also supplies `_id` while remaining
in `_source`. The sink chunks sequentially by action count and serialized NDJSON
bytes, and rejects invalid or oversized payloads before submission.

The common boundary uses HTTP(S), `GET /`, and `POST /_bulk`. `distribution` is
`auto`, `elasticsearch`, or `opensearch`. Configure exactly one authentication
mode: Basic `username`/`password`, `api_key`, or `bearer_token`. Custom `headers`,
`verify_certs`, `ca_certs`, `client_cert`, and `client_key` cover the supported TLS
and proxy boundary. Clients are created lazily; an injected client remains caller
owned, while a connector-owned client is closed by `await connector.close()`.

## Compatibility

The supported release matrix is Elasticsearch 8.x and 9.x plus OpenSearch 2.x and
3.x. Compatibility is defined by the shared HTTP bulk behavior, not a vendor Python
client version. The environment-gated live suite uses `ONESTEP_ELASTICSEARCH_URL`
or `ONESTEP_OPENSEARCH_URL`.

## Delivery semantics

`send()` returns only after every bulk item in every chunk is acknowledged. onestep
acknowledges the source after sink sends, so a crash between the bulk acknowledgement
and source acknowledgement can duplicate output. Multi-sink fan-out is not
transactional. Use `operation: index` with a stable `id_field` when replay must
converge; auto-generated IDs and `create` are not replay-safe.

Bulk chunks are not transactional. Item failures retain a redacted typed cause with
the item index, status, identifier, and normalized reason. A partial commit is
reported as `UNCERTAIN` unless `index` with a stable `id_field` makes replay
deterministic. `create` conflicts and malformed documents are permanent failures.

Request-level 502, 503, and 504 responses are ambiguous after the request body
has been sent. The sink retries them internally only when `operation: index` and
a present `id_field` make replay convergent. A request-level 429 is an explicit
rejection and remains retryable without stable IDs.

## Deferred features

Version 1 intentionally excludes Cloud ID, sniffing, SigV4, data streams,
administration APIs, dynamic actions, update/delete, PIT, SQL,
`elasticsearch_search_after`, and `elasticsearch_scroll`.
