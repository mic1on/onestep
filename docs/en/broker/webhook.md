---
title: Webhook | Broker
outline: deep
---

# Webhook

WebhookSource allows external systems to push messages to your tasks via HTTP requests.

## Basic Usage

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

## Request Structure

The `event` received by the task contains:

```python
{
    "body": {...},           # Request body (parsed)
    "headers": {...},        # Request headers
    "query": {...},          # Query parameters
    "method": "POST",        # HTTP method
    "path": "/webhooks/github",  # Request path
    "client": ("127.0.0.1", 12345),  # Client address
    "received_at": 1691546688.498,  # Reception timestamp
}
```

## Configuration Options

### Basic Configuration

```python
source = WebhookSource(
    path="/webhook",         # Path
    methods=("POST", "PUT"), # Allowed methods
    host="0.0.0.0",          # Listen address
    port=8080,               # Port
)
```

### Authentication

Using Bearer Token authentication:

```python
from onestep import BearerAuth

source = WebhookSource(
    path="/webhook",
    auth=BearerAuth("your-secret-token"),
)
```

Requests need to include the token:

```bash
curl -H "Authorization: Bearer your-secret-token" \
     -X POST http://localhost:8080/webhook \
     -d '{"data": "..."}'
```

### Request Body Parsing

```python
source = WebhookSource(
    path="/webhook",
    parser="json",  # json | form | text | raw | auto
)
```

- `json`: Parse as JSON object
- `form`: Parse form data
- `text`: Raw text
- `raw`: Raw bytes
- `auto`: Auto-select based on Content-Type (default)

### Custom Response

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

## Multiple Webhook Routes

Multiple webhooks can share the same server:

```python
github = WebhookSource(
    path="/webhooks/github",
    host="127.0.0.1",
    port=8080,
)

stripe = WebhookSource(
    path="/webhooks/stripe",
    host="127.0.0.1",
    port=8080,  # Same port
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

## Example: GitHub Webhook

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
    print(f"Processing event: {event}")


if __name__ == "__main__":
    app.run()
```

## Example: Slack Command

```python
from onestep import OneStepApp, WebhookSource

app = OneStepApp("slack-commands")


@app.task(
    source=WebhookSource(
        path="/slack/command",
        methods=("POST",),
        parser="form",
    )
)
async def handle_slash_command(ctx, event):
    text = event["body"].get("text", "")
    user = event["body"].get("user_name", "")
    
    return {
        "response_type": "in_channel",
        "text": f"Received command: {text} (from {user})",
    }


if __name__ == "__main__":
    app.run()
```

## YAML Configuration

```yaml
resources:
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

## Production Deployment Recommendations

### 1. Use Reverse Proxy

Using Nginx as reverse proxy is recommended:

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

HTTPS is required in production. Terminate TLS at the reverse proxy layer (Nginx, Caddy, ALB, etc.) and forward to the local port where `WebhookSource` listens.

### 3. Signature Verification

Implement custom signature verification:

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
    
    # Process request
    ...
```

### 4. Idempotency

Webhooks may be sent repeatedly; ensure tasks are idempotent:

```python
@app.task(source=webhook_source)
async def handle_webhook(ctx, event):
    event_id = event["headers"].get("x-event-id")
    
    # Check if already processed
    if await ctx.state.get(f"event:{event_id}"):
        return {"status": "duplicate"}
    
    # Process event
    result = await process_event(event)
    
    # Mark as processed
    await ctx.state.set(f"event:{event_id}", True)
    
    return result
```
