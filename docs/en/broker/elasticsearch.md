---
title: Elasticsearch / OpenSearch | Broker
outline: deep
---

# Elasticsearch / OpenSearch

`onestep-elasticsearch` provides an async bulk Sink for the Elasticsearch and OpenSearch common HTTP API.

## Installation

```bash
pip install onestep-elasticsearch
```

Requires Python 3.9+ and `onestep>=1.11.0`.

## Python Usage

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

## YAML Configuration

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

## Payload and Transport

`send()` accepts a mapping or a non-empty sequence of mappings. Each mapping is a complete `_source` document; `id_field` provides `_id` while keeping it in `_source`. The Sink chunks sequentially by operation count and serialized NDJSON byte size, rejecting invalid or oversized payloads before submission.

Generic transport uses HTTP(S), `GET /` and `POST /_bulk`. `distribution` values are `auto`, `elasticsearch`, or `opensearch`. Configure one auth method: Basic `username`/`password`, `api_key`, or `bearer_token`. Custom `headers`, `verify_certs`, `ca_certs`, `client_cert`, and `client_key` override TLS and proxy boundaries. The client is lazily created; injected clients are held by the caller, and connector-owned clients are closed via `await connector.close()`.

## Compatibility

The supported version matrix is Elasticsearch 8.x and 9.x, and OpenSearch 2.x and 3.x. Compatibility is defined by shared HTTP bulk behavior, not by any specific vendor Python client version. The live suite is controlled by the `ONESTEP_ELASTICSEARCH_URL` or `ONESTEP_OPENSEARCH_URL` environment variable.

## Delivery Semantics

`send()` returns only after each bulk item in every chunk is acknowledged. onestep confirms the source after the Sink sends, so crashes between bulk acknowledgment and source confirmation may cause duplicate output. Multi-sink fan-out is not transactional. For convergent replay, use `operation: index` with a stable `id_field`; auto-generated IDs and `create` operations are not safely replayable.

Bulk chunks are not transactional. Item failures retain structured reasons with redacted types (item index, status code, identifier, and normalized reason). Partial commits are reported as `UNCERTAIN`, unless `operation: index` with stable `id_field` makes replay deterministic. `create` conflicts and malformed documents are permanent failures.

Request-level 502, 503, 504 are ambiguous after the request body has been sent. The Sink internally retries only when `operation: index` with `id_field` makes replay convergent. Request-level 429 is always retryable regardless of stable IDs.

## Not Supported in Initial Release

The first release does not provide: Cloud ID, sniffing, SigV4, data stream, admin API, dynamic action, update/delete, PIT, SQL, `elasticsearch_search_after`, and `elasticsearch_scroll`.
