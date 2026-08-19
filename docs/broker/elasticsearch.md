---
title: Elasticsearch / OpenSearch | Broker
outline: deep
---

# Elasticsearch / OpenSearch

`onestep-elasticsearch` 为 Elasticsearch 和 OpenSearch 通用 HTTP API 提供一个异步 bulk Sink。

## 安装

```bash
pip install onestep-elasticsearch
```

要求 Python 3.9+ 且 `onestep>=1.11.0`。

## Python 用法

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

## YAML 配置

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

## Payload 与传输

`send()` 接受一个 mapping 或一个非空 mapping 序列。每个 mapping 是完整的 `_source` 文档；`id_field` 同时提供 `_id` 并保留在 `_source` 中。Sink 按操作计数和序列化 NDJSON 字节数顺序分 chunk，提交前拒绝无效或超大 payload。

通用边界使用 HTTP(S)、`GET /` 和 `POST /_bulk`。`distribution` 值为 `auto`、`elasticsearch` 或 `opensearch`。配置一种认证方式：Basic `username`/`password`、`api_key` 或 `bearer_token`。自定义 `headers`、`verify_certs`、`ca_certs`、`client_cert` 和 `client_key` 覆盖 TLS 和代理边界。客户端惰性创建；注入的客户端由调用方持有，connector 拥有的客户端通过 `await connector.close()` 关闭。

## 兼容性

支持的版本矩阵为 Elasticsearch 8.x 和 9.x，以及 OpenSearch 2.x 和 3.x。兼容性由共享 HTTP bulk 行为定义，不依赖特定供应商 Python 客户端版本。Live 套件通过环境变量 `ONESTEP_ELASTICSEARCH_URL` 或 `ONESTEP_OPENSEARCH_URL` 控制。

## 投递语义

`send()` 仅在每个 chunk 的每个 bulk item 确认后返回。onestep 在 Sink 发送后确认 source，因此 bulk 确认后到 source 确认前之间的崩溃可能重复输出。多 Sink 扇出不是事务性的。需要收敛重放时使用 `operation: index` 和稳定 `id_field`；自动生成 ID 和 `create` 操作不可安全重放。

Bulk chunk 不是事务性的。item 失败保留带脱敏类型的结构化原因（item 索引、状态码、标识符和规范化原因）。部分提交报告为 `UNCERTAIN`，除非 `operation: index` 加稳定 `id_field` 使得重放确定。`create` 冲突和格式错误文档为永久失败。

请求级 502、503、504 在请求体已发送后语义模糊。仅当 `operation: index` 且存在 `id_field` 使得重放收敛时，Sink 才内部重试。请求级 429 始终可重试，不要求稳定 ID。

## 暂不支持

首版不提供：Cloud ID、sniffing、SigV4、data stream、管理 API、动态 action、update/delete、PIT、SQL、`elasticsearch_search_after` 和 `elasticsearch_scroll`。
