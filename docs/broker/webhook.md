---
title: Webhook | Broker
outline: deep
---

# Webhook

WebhookSource 允许外部系统通过 HTTP 请求推送消息到你的任务。

## 基本用法

```python
from onestep import OneStepApp, WebhookSource, MemoryQueue

app = OneStepApp("webhook-demo")
results = MemoryQueue("results")


@app.task(
    source=WebhookSource(
        path="/webhooks/github",
        methods=("POST",),
        host="127.0.0.1",
        port=8080,
    ),
    emit=results,
)
async def handle_github(ctx, event):
    return {
        "event": event["headers"].get("x-github-event"),
        "payload": event["body"],
    }


if __name__ == "__main__":
    app.run()
```

## 请求结构

任务收到的 `event` 包含：

```python
{
    "body": {...},           # 请求体（已解析）
    "headers": {...},        # 请求头
    "query": {...},          # 查询参数
    "method": "POST",        # HTTP 方法
    "path": "/webhooks/github",  # 请求路径
    "client": ("127.0.0.1", 12345),  # 客户端地址
    "received_at": 1691546688.498,  # 接收时间戳
}
```

## 配置选项

### 基本配置

```python
source = WebhookSource(
    path="/webhook",         # 路径
    methods=("POST", "PUT"), # 允许的方法
    host="0.0.0.0",          # 监听地址
    port=8080,               # 端口
)
```

### 认证

使用 Bearer Token 认证：

```python
from onestep import BearerAuth

source = WebhookSource(
    path="/webhook",
    auth=BearerAuth("your-secret-token"),
)
```

请求时需要带 token：

```bash
curl -H "Authorization: Bearer your-secret-token" \
     -X POST http://localhost:8080/webhook \
     -d '{"data": "..."}'
```

### 请求体解析

```python
source = WebhookSource(
    path="/webhook",
    body_parser="json",  # json | form | text | raw | auto
)
```

- `json`: 解析为 JSON 对象
- `form`: 解析表单数据
- `text`: 原始文本
- `raw`: 原始字节
- `auto`: 根据 Content-Type 自动选择（默认）

### 自定义响应

```python
from onestep import WebhookResponse

source = WebhookSource(
    path="/webhook",
    response=WebhookResponse(
        status_code=202,
        body={"received": True},
        headers={"X-Custom": "value"},
    ),
)
```

## 多 Webhook 路由

多个 webhook 可以共享同一个服务器：

```python
github = WebhookSource(
    path="/webhooks/github",
    host="127.0.0.1",
    port=8080,
)

stripe = WebhookSource(
    path="/webhooks/stripe",
    host="127.0.0.1",
    port=8080,  # 相同端口
)


@app.task(source=github)
async def handle_github(ctx, event):
    print("GitHub event:", event["headers"].get("x-github-event"))


@app.task(source=stripe)
async def handle_stripe(ctx, event):
    print("Stripe event:", event["body"].get("type"))


if __name__ == "__main__":
    app.run()
```

## 示例：GitHub Webhook

```python
from onestep import BearerAuth, OneStepApp, WebhookSource, MemoryQueue

app = OneStepApp("github-webhook")
events = MemoryQueue("github-events")


@app.task(
    source=WebhookSource(
        path="/webhooks/github",
        methods=("POST",),
        host="0.0.0.0",
        port=8080,
        auth=BearerAuth("your-webhook-secret"),
    ),
    emit=events,
)
async def parse_github_event(ctx, event):
    event_type = event["headers"].get("x-github-event")
    payload = event["body"]
    
    if event_type == "push":
        return {
            "type": "push",
            "repo": payload["repository"]["full_name"],
            "branch": payload["ref"],
            "commits": len(payload.get("commits", [])),
        }
    elif event_type == "pull_request":
        return {
            "type": "pull_request",
            "repo": payload["repository"]["full_name"],
            "action": payload["action"],
            "pr_number": payload["number"],
        }
    
    return {"type": event_type, "payload": payload}


@app.task(source=events)
async def process_event(ctx, event):
    print(f"处理事件: {event}")


if __name__ == "__main__":
    app.run()
```

## 示例：Slack 命令

```python
from onestep import OneStepApp, WebhookSource

app = OneStepApp("slack-commands")


@app.task(
    source=WebhookSource(
        path="/slack/command",
        methods=("POST",),
        body_parser="form",
    )
)
async def handle_slash_command(ctx, event):
    text = event["body"].get("text", "")
    user = event["body"].get("user_name", "")
    
    return {
        "response_type": "in_channel",
        "text": f"收到命令: {text} (来自 {user})",
    }


if __name__ == "__main__":
    app.run()
```

## YAML 配置

```yaml
connectors:
  github_webhook:
    type: webhook
    path: "/webhooks/github"
    methods:
      - POST
    host: "0.0.0.0"
    port: 8080

tasks:
  - name: handle_github
    source: github_webhook
    handler:
      ref: myapp.handlers:handle_github
```

## 生产部署建议

### 1. 使用反向代理

推荐使用 Nginx 作为反向代理：

```nginx
server {
    listen 80;
    server_name webhooks.example.com;
    
    location /webhooks/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### 2. HTTPS

在生产环境必须使用 HTTPS，可通过 Nginx 配置或使用 `WebhookSource` 的 SSL 选项。

### 3. 签名验证

实现自定义签名验证：

```python
import hmac
import hashlib

@app.task(source=webhook_source)
async def handle_webhook(ctx, event):
    signature = event["headers"].get("x-signature")
    payload = event["body"]
    
    expected = hmac.new(
        b"your-secret",
        str(payload).encode(),
        hashlib.sha256
    ).hexdigest()
    
    if not hmac.compare_digest(signature, expected):
        raise Exception("Invalid signature")
    
    # 处理请求
    ...
```

### 4. 幂等性

Webhook 可能重复发送，确保任务幂等：

```python
@app.task(source=webhook_source)
async def handle_webhook(ctx, event):
    event_id = event["headers"].get("x-event-id")
    
    # 检查是否已处理
    if await ctx.state.get(f"event:{event_id}"):
        return {"status": "duplicate"}
    
    # 处理事件
    result = await process_event(event)
    
    # 标记已处理
    await ctx.state.set(f"event:{event_id}", True)
    
    return result
```