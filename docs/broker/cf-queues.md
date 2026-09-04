---
title: Cloudflare Queues | Broker
outline: deep
---

# Cloudflare Queues

[Cloudflare Queues](https://developers.cloudflare.com/queues/) 是 Cloudflare 提供的托管消息队列。onestep 通过官方
[`cloudflare` Python SDK](https://github.com/cloudflare/cloudflare-python) 调用其
[HTTP 拉取消费者（pull consumer）REST API](https://developers.cloudflare.com/queues/configuration/pull-consumers/)
接入，因此可以在 Cloudflare Workers 之外的任意环境中消费和投递消息。

## 安装

```bash
pip install onestep-cf-queues
# 或作为 onestep 的可选依赖
pip install 'onestep[cloudflare]'
```

## 前置条件

1. 为队列启用 HTTP 拉取：

   ```bash
   npx wrangler queues consumer http add <QUEUE-NAME>
   ```

   一个队列不能同时拥有 Worker（推送）消费者和 HTTP（拉取）消费者。

2. 创建具有 **Queues** `Edit`（读 + 写）权限的 API Token。拉取消费者需要写权限来确认（ack）消息。

## 配置

Python：

```python
from onestep import OneStepApp
from onestep_cf_queues import CFQueuesConnector

app = OneStepApp("cf-queues-demo")
cf = CFQueuesConnector(account_id="<account-id>", api_token="<api-token>")
jobs = cf.queue("<queue-id>", batch_size=10, visibility_timeout_ms=30000)


@app.task(source=jobs)
async def consume(ctx, item):
    print("processing", item)
```

YAML：

```yaml
resources:
  cf:
    type: cf_queues
    account_id: "${CF_ACCOUNT_ID}"
    api_token: "${CF_QUEUES_TOKEN}"

  jobs:
    type: cf_queue
    connector: cf
    queue_id: "${CF_QUEUE_ID}"
    batch_size: 10
    visibility_timeout_ms: 30000
    on_fail: leave

tasks:
  - name: consume
    source: jobs
    handler:
      ref: your_package.tasks:consume
```

## 资源类型

- `cf_queues`：连接器，持有 `account_id`、`api_token`，以及可选 `base_url`、`timeout_s`。
- `cf_queue`：既是 source 又是 sink，通过 `connector` 引用连接器。

`cf_queue` 字段：

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `queue_id` | （必填） | Cloudflare 队列 ID |
| `batch_size` | 5 | 每次拉取返回的消息数（1–100） |
| `visibility_timeout_ms` | 服务端默认 30s | 租约时长，最大 12 小时 |
| `poll_interval_s` | 1.0 | 短轮询间隔 |
| `on_fail` | `leave` | 失败处理：`leave` / `retry` / `ack` |
| `ack_batch_size` | 100 | 单次 `/ack` 请求合并的 lease 数（1–100） |
| `ack_flush_interval_s` | 0.5 | ack/retry 定时刷新间隔 |

## 语义映射

连接器封装官方 `cloudflare` SDK 的异步客户端（`AsyncCloudflare().queues.messages`）：

| onestep | cloudflare SDK 调用 |
| --- | --- |
| `Source.fetch` | `queues.messages.pull(queue_id, account_id=...)` |
| `Delivery.ack` | `queues.messages.ack(..., acks=[{lease_id}])` |
| `Delivery.retry(delay_s)` | `queues.messages.ack(..., retries=[{lease_id, delay_seconds}])` |
| `Sink.send` | `queues.messages.push(...)` |

ack 与 retry 会被缓冲，达到 `ack_batch_size` 或经过 `ack_flush_interval_s` 后合并为单次 `/ack` 请求。

## 消息元数据

拉取到的消息会解码标准 onestep envelope，并在 `delivery.envelope.meta["cf_queues"]` 暴露 Cloudflare 元数据：

```python
{
    "id": "1ad27d24c83de78953da635dc2ea208f",
    "timestamp_ms": 1689615013586,
    "attempts": 2,
    "metadata": {"CF-Content-Type": "json"},
}
```

`lease_id` 仅用于内部 ack/retry/release，不会暴露到 envelope。

## Content type 与编码

拉取消费者只能处理 `text`、`bytes`、`json` content type（默认 `json`），无法解码
Workers 专用的 `v8`。对于 `json` 和 `bytes`，body 会以 base64 传输，连接器按消息
`CF-Content-Type` 元数据解码后再交给 envelope 编解码器；`bytes` 负载若不是有效
UTF-8/JSON，解码结果（字符串或原始 bytes）会原样作为 envelope body。

## 失败处理（`on_fail`）

- `leave`（默认）：失败时不动，等 `visibility_timeout` 到期后由服务端重投。
- `retry`：立即标记重试，消息马上回到队列。
- `ack`：失败时确认（丢弃）消息，例如死信 sink 已处理完毕。

## 短轮询与无租约续期

与 SQS 长轮询不同，Cloudflare 拉取是**短轮询**：`fetch` 立即返回（无消息时返回空），
因此 `fetch_is_cancel_safe` 为 `True`，用 `poll_interval_s` 控制轮询频率。

Cloudflare Queues **没有租约续期（heartbeat）端点**，租约时长固定为
`visibility_timeout`（默认 30s，最大 12 小时）。对于长耗时 handler，需要把
`visibility_timeout_ms` 设得足够大以覆盖最坏处理时间，因为处理中途无法延长租约。

## 限制

- 消息大小：128 KB。
- 消费者批量：最多 100 条。
- `visibility_timeout`：最大 12 小时。
- retry `delay_seconds`：最大 24 小时。
- 单队列吞吐：5,000 消息/秒。

投递语义为 at-least-once，重复敏感的场景请保证 handler 幂等。
