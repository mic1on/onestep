---
title: HTTP Sink | Broker
outline: deep
---

# HTTP Sink

`HttpSink` 用于把任务返回值以 JSON 请求发送到外部 HTTP 端点。它只实现 `Sink`，适合通知 Webhook、调用内部服务或把处理结果转发给只提供 HTTP 接口的系统。

## 基本用法

```python
import os

from onestep import HttpSink, MemoryQueue, OneStepApp

app = OneStepApp("notify-demo")
source = MemoryQueue("events")
notify = HttpSink(
    "notify",
    url="https://example.com/hooks/events",
    headers={"Authorization": f"Bearer {os.environ['NOTIFY_TOKEN']}"},
    timeout_s=2.5,
)


@app.task(source=source, emit=notify)
async def forward_event(ctx, event):
    return {
        "id": event["id"],
        "kind": event["kind"],
    }
```

任务返回值会作为 JSON body 发送。`HttpSink` 默认使用 `POST`，并在没有显式设置时自动添加 `Content-Type: application/json`。

## 配置选项

```python
sink = HttpSink(
    "notify",
    url="https://example.com/hooks/events",
    method="POST",
    headers={"X-Api-Key": "secret-token"},
    params={"source": "onestep"},
    timeout_s=5.0,
    success_statuses=[200, 201, 202, 204],
)
```

| 参数 | 说明 | 默认值 |
|---|---|---|
| `url` | HTTP 或 HTTPS 目标地址 | 必填 |
| `method` | 请求方法，会转成大写 | `POST` |
| `headers` | 请求头映射，值会转成字符串 | `{}` |
| `params` | 静态查询参数映射，值会转成字符串或字符串列表 | `{}` |
| `timeout_s` | 单次请求超时时间，必须大于 0 | `5.0` |
| `success_statuses` | 视为成功的 HTTP 状态码列表 | `[200, 201, 202, 204]` |

如果响应状态码不在 `success_statuses` 中，发送会失败并抛出连接器错误。`429` 会被归类为限流，`408`、`425` 和 `5xx` 会被归类为临时错误，其余非成功状态会被归类为永久错误。

`GET` 和 `DELETE` 不发送 JSON body。静态 `params` 和任务返回的 mapping payload 会被编码到 query string 中：

```python
lookup = HttpSink(
    "lookup",
    url="https://example.com/users",
    method="GET",
    params={"api_key": "secret"},
    success_statuses=[200],
)
```

## YAML 配置

```yaml
resources:
  incoming:
    type: memory

  notify:
    type: http_sink
    url: "https://example.com/hooks/events"
    method: POST
    headers:
      Authorization: "Bearer ${NOTIFY_TOKEN}"
    params:
      source: onestep
    timeout_s: 5
    success_statuses: [200, 202]

tasks:
  - name: forward_event
    source: incoming
    emit: notify
    handler:
      ref: myapp.handlers:normalize_event
```

`http_sink` 支持的字段是 `url`、`method`、`headers`、`params`、`timeout_s` 和 `success_statuses`。严格校验会拒绝未知字段。

## YAML 直接转发

YAML 任务可以省略 `handler`，只配置 `emit`。运行时会把 source 收到的 payload 原样转发给 sink。

```yaml
resources:
  incoming:
    type: memory

  notify:
    type: http_sink
    url: "https://example.com/hooks/events"
    headers:
      X-Api-Key: "${NOTIFY_TOKEN}"

tasks:
  - name: forward_raw_event
    source: incoming
    emit: notify
```

直接转发只适合 payload 已经是目标接口需要的结构时使用。需要字段转换、签名、幂等校验或错误归一化时，仍然在 Python handler 中处理后再返回。

## Control Plane

启用 Control Plane reporter 后，`HttpSink` 会作为 `http_sink` 出现在任务拓扑中。上报时会隐藏 URL 中的账号信息，移除 URL query 和 fragment，并把 header 与 `params` 的值标记为 `<redacted>`，避免 token 出现在拓扑 payload 里。

## 下一步

- [Webhook](/broker/webhook) - 接收外部 HTTP 请求
- [YAML 任务定义](/yaml-task-definition) - 使用 `http_sink` 和直接转发任务
- [自定义 Source/Sink](/broker/custom) - 实现自己的输出目标
