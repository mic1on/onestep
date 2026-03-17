# WebSocket Reporter 设计文档

> 版本: 1.0.0-draft  
> 日期: 2026-03-17  
> 状态: 设计阶段

## 1. 背景与问题

### 1.1 现状

当前 onestep 使用 HTTP Reporter 与 Control Plane 通信：

```
┌─────────────┐         HTTP POST          ┌─────────────────┐
│   Worker    │ ─────────────────────────▶ │  Control Plane  │
│   (内网)     │                            │    (公网)        │
└─────────────┘                            └─────────────────┘
```

**问题**：
- Worker 在内网，Control Plane 无法主动向 Worker 发送命令
- 需要 NAT 穿透或 VPN 才能实现 Control Plane 对 Worker 的控制
- 运维复杂度高

### 1.2 目标

设计 WebSocket Reporter，实现：

1. **Worker 主动连接** - 无需 NAT 穿透
2. **双向通信** - Control Plane 可下发控制命令
3. **高可用** - 自动重连、心跳保活
4. **一致性** - 与 HTTP Reporter 功能对齐

---

## 2. 架构设计

### 2.1 连接模型

```
┌─────────────┐       WebSocket        ┌─────────────────┐
│   Worker    │ ◀═════════════════════▶│  Control Plane  │
│   (内网)     │       (双向通信)        │    (公网)        │
└─────────────┘                         └─────────────────┘
      │                                       │
      │ 主动发起连接                           │ 监听 WS 端点
      │ 支持自动重连                           │ 管理 Agent 连接
      │ 接收并执行命令                         │ 下发控制命令
```

### 2.2 消息流向

```
Worker → Control Plane (上报):
  - sync: 初始同步 / 拓扑变更
  - heartbeat: 心跳 / 健康状态
  - metrics: 性能指标
  - events: 任务事件

Control Plane → Worker (控制):
  - pause/resume: 任务控制
  - scale: 并发调整
  - shutdown: 优雅关闭
  - sync: 状态同步请求
  - ping: 健康检查
```

---

## 3. 协议设计

### 3.1 连接建立

**WebSocket URL**: `wss://control-plane.example.com/ws/agents`

**认证方式**: 通过 HTTP Headers 传递

```http
GET /ws/agents HTTP/1.1
Host: control-plane.example.com
Upgrade: websocket
Connection: Upgrade
Authorization: Bearer <token>
X-Agent-ID: <uuid>
X-Service-Name: <service_name>
X-Environment: dev|staging|prod
```

### 3.2 消息格式

所有消息使用 JSON 格式：

```json
{
  "type": "<message_type>",
  "sequence": <int>,
  "timestamp": "<ISO8601>",
  "payload": { ... }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| type | string | 消息类型 |
| sequence | int | 消息序号（单调递增） |
| timestamp | string | 发送时间 (ISO 8601) |
| payload | object | 消息内容 |

### 3.3 Worker 上报消息

#### 3.3.1 sync - 初始同步

Worker 连接成功后发送，或拓扑变更时发送。

```json
{
  "type": "sync",
  "sequence": 1,
  "timestamp": "2026-03-17T10:00:00Z",
  "payload": {
    "service": {
      "name": "order-processor",
      "instance_id": "550e8400-e29b-41d4-a716-446655440000",
      "environment": "prod"
    },
    "state": "running",
    "tasks": [
      {
        "name": "process_orders",
        "source": "orders-queue",
        "concurrency": 4,
        "timeout_s": 30
      }
    ]
  }
}
```

#### 3.3.2 heartbeat - 心跳

定时发送（默认 30s），携带健康状态。

```json
{
  "type": "heartbeat",
  "sequence": 42,
  "timestamp": "2026-03-17T10:01:00Z",
  "payload": {
    "service": {
      "name": "order-processor",
      "instance_id": "550e8400-e29b-41d4-a716-446655440000",
      "environment": "prod"
    },
    "state": "running",
    "health": {
      "healthy": true,
      "stopping": false
    }
  }
}
```

#### 3.3.3 metrics - 性能指标

定时发送（默认 30s），批量上报任务指标。

```json
{
  "type": "metrics",
  "sequence": 43,
  "timestamp": "2026-03-17T10:01:30Z",
  "payload": {
    "service": {
      "name": "order-processor",
      "instance_id": "550e8400-e29b-41d4-a716-446655440000",
      "environment": "prod"
    },
    "metrics": {
      "tasks": [
        {
          "task_name": "process_orders",
          "fetched": 100,
          "succeeded": 95,
          "failed": 3,
          "retried": 2,
          "avg_duration_ms": 125,
          "p95_duration_ms": 350
        }
      ]
    }
  }
}
```

#### 3.3.4 events - 任务事件

实时或批量发送任务事件。

```json
{
  "type": "events",
  "sequence": 44,
  "timestamp": "2026-03-17T10:02:00Z",
  "payload": {
    "service": {
      "name": "order-processor",
      "instance_id": "550e8400-e29b-41d4-a716-446655440000",
      "environment": "prod"
    },
    "events": [
      {
        "event_id": "evt-001",
        "kind": "succeeded",
        "task_name": "process_orders",
        "occurred_at": "2026-03-17T10:01:55Z",
        "attempts": 1,
        "duration_ms": 120,
        "meta": {}
      }
    ]
  }
}
```

**事件类型 (kind)**:
- `fetched`: 消息拉取
- `started`: 任务开始
- `succeeded`: 执行成功
- `failed`: 执行失败
- `retried`: 重试
- `dead_lettered`: 进入死信
- `cancelled`: 取消
- `timeout`: 超时

### 3.4 Control Plane 下发消息

#### 3.4.1 command - 控制命令

```json
{
  "type": "command",
  "command": "pause",
  "params": {}
}
```

**支持的命令**:

| 命令 | 参数 | 说明 |
|------|------|------|
| `pause` | `{}` | 暂停所有任务 |
| `resume` | `{}` | 恢复所有任务 |
| `pause_task` | `{"task": "task_name"}` | 暂停指定任务 |
| `resume_task` | `{"task": "task_name"}` | 恢复指定任务 |
| `scale` | `{"task": "name", "concurrency": 5}` | 调整并发数 |
| `shutdown` | `{}` | 优雅关闭 |
| `sync` | `{}` | 请求状态同步 |
| `ping` | `{}` | 健康检查 |

#### 3.4.2 ack - 确认

```json
{
  "type": "ack",
  "sequence": 42
}
```

---

## 4. Worker 状态机

```
        ┌──────────────┐
        │   STARTING   │
        └──────┬───────┘
               │ 连接成功
               ▼
        ┌──────────────┐
   ┌───▶│   RUNNING    │◀───┐
   │    └──────┬───────┘    │
   │           │            │
   │    pause  │  resume    │
   │           ▼            │
   │    ┌──────────────┐    │
   └────│   PAUSED     │────┘
        └──────┬───────┘
               │ shutdown
               ▼
        ┌──────────────┐
        │   STOPPING   │
        └──────┬───────┘
               │
               ▼
        ┌──────────────┐
        │   STOPPED    │
        └──────────────┘
```

---

## 5. 配置参数

### 5.1 Worker 配置

```python
WebSocketReporterConfig(
    # 连接
    ws_url="wss://control-plane.example.com/ws/agents",
    token="your-auth-token",
    
    # 身份
    environment="prod",              # dev|staging|prod
    service_name="order-processor",  # 服务名
    instance_id=UUID("..."),         # 实例 ID (自动生成)
    
    # 心跳与重连
    heartbeat_interval_s=30.0,       # 心跳间隔
    reconnect_delay_s=1.0,           # 初始重连延迟
    reconnect_backoff_factor=2.0,    # 指数退避因子
    max_reconnect_delay_s=60.0,      # 最大重连延迟
    
    # WebSocket 配置
    ping_interval_s=20.0,            # WebSocket ping 间隔
    ping_timeout_s=10.0,             # WebSocket ping 超时
    
    # 批量上报
    event_batch_size=100,            # 事件批量大小
    event_flush_interval_s=5.0,      # 事件刷新间隔
    metrics_interval_s=30.0,         # 指标上报间隔
)
```

### 5.2 环境变量

| 变量 | 说明 |
|------|------|
| `ONESTEP_CONTROL_PLANE_WS_URL` | WebSocket URL |
| `ONESTEP_CONTROL_PLANE_TOKEN` | 认证 Token |
| `ONESTEP_CONTROL_PLANE_ENVIRONMENT` | 环境 (dev/staging/prod) |
| `ONESTEP_SERVICE_NAME` | 服务名 |
| `ONESTEP_INSTANCE_ID` | 实例 ID |

---

## 6. 错误处理

### 6.1 连接失败

```
连接失败 → 等待 reconnect_delay_s → 重连
         ↓
等待时间 *= reconnect_backoff_factor
         ↓
最大等待 max_reconnect_delay_s
         ↓
连接成功 → 重置等待时间
```

### 6.2 消息发送失败

- 消息放入队列，等待重连后发送
- 队列满时丢弃旧消息（可配置）
- 记录日志，不影响 Worker 正常运行

### 6.3 命令执行失败

- 记录日志
- 通过心跳上报状态
- 不影响 Worker 正常运行

---

## 7. Control Plane 服务端实现要点

### 7.1 连接管理

```python
# 伪代码
class AgentConnectionManager:
    def __init__(self):
        self.connections: dict[str, WebSocket] = {}  # instance_id -> ws
    
    async def handle_connection(self, ws, headers):
        # 1. 验证 token
        # 2. 提取 agent_id, service_name, environment
        # 3. 注册连接
        # 4. 接收消息循环
        pass
    
    async def send_command(self, instance_id: str, command: dict):
        ws = self.connections.get(instance_id)
        if ws:
            await ws.send(json.dumps(command))
    
    async def broadcast(self, command: dict):
        for ws in self.connections.values():
            await ws.send(json.dumps(command))
```

### 7.2 消息处理

```python
async def handle_message(self, data: dict):
    msg_type = data["type"]
    payload = data["payload"]
    
    if msg_type == "sync":
        # 更新 Agent 拓扑信息
        await self.update_agent_topology(payload)
    
    elif msg_type == "heartbeat":
        # 更新心跳时间，标记存活
        await self.update_heartbeat(payload)
    
    elif msg_type == "metrics":
        # 存储指标数据
        await self.store_metrics(payload)
    
    elif msg_type == "events":
        # 处理事件（存储/触发告警等）
        await self.process_events(payload)
```

### 7.3 命令下发 API

```python
# REST API
POST /api/v1/agents/{instance_id}/command
{
    "command": "pause",
    "params": {}
}

# 或批量
POST /api/v1/agents/command
{
    "filter": {"service_name": "order-processor"},
    "command": "scale",
    "params": {"task": "process_orders", "concurrency": 8}
}
```

### 7.4 数据存储建议

```
agents (表)
├── instance_id (PK)
├── service_name
├── environment
├── state
├── last_heartbeat_at
├── topology (JSON)
└── created_at / updated_at

agent_metrics (表)
├── id (PK)
├── instance_id (FK)
├── task_name
├── metrics (JSON)
├── window_start
├── window_end
└── created_at

agent_events (表)
├── id (PK)
├── instance_id (FK)
├── event_id
├── kind
├── task_name
├── occurred_at
├── payload (JSON)
└── created_at
```

---

## 8. 安全考虑

### 8.1 认证

- 使用 Bearer Token 认证
- Token 在连接建立时验证
- 建议使用 JWT，包含 `service_name`, `environment` 等声明

### 8.2 授权

- 验证 Token 对应的服务是否有权限连接
- 验证环境是否匹配（生产 Token 不能连接测试环境）

### 8.3 传输安全

- 必须使用 `wss://` (TLS)
- 建议启用双向 TLS 验证（可选）

### 8.4 命令校验

- 验证命令格式
- 验证参数合法性
- 记录所有命令操作日志

---

## 9. 监控与告警

### 9.1 Worker 端监控

- 连接状态
- 重连次数
- 消息发送成功/失败数
- 命令执行成功/失败数

### 9.2 Control Plane 端监控

- 在线 Agent 数量
- 连接建立/断开事件
- 心跳超时告警
- 消息处理延迟

### 9.3 告警规则建议

| 告警 | 条件 | 级别 |
|------|------|------|
| Agent 离线 | 心跳超时 > 2x heartbeat_interval | Warning |
| Agent 离线 | 心跳超时 > 5x heartbeat_interval | Critical |
| 重连频繁 | 5分钟内重连 > 10 次 | Warning |
| 命令失败 | 命令执行失败率 > 10% | Warning |

---

## 10. 与 HTTP Reporter 对比

| 特性 | HTTP Reporter | WebSocket Reporter |
|------|--------------|-------------------|
| 连接方向 | Worker → Control Plane | Worker → Control Plane |
| 控制能力 | ❌ 无 | ✅ 支持命令下发 |
| 内网穿透 | 需要才可控制 | 不需要 |
| 实时性 | 批量上报 | 实时双向 |
| 断线重连 | 无 | 自动重连 |
| 实现复杂度 | 低 | 中 |
| 资源消耗 | 低 | 中 (维持长连接) |
| 适用场景 | 简单监控 | 需要控制的场景 |

---

## 11. 迁移建议

### 11.1 渐进式迁移

1. **阶段 1**: 两个 Reporter 并行运行
   - HTTP Reporter 继续使用
   - WebSocket Reporter 仅上报，不处理命令

2. **阶段 2**: 启用命令处理
   - 通过 WebSocket 下发命令
   - 验证命令执行正确性

3. **阶段 3**: 下线 HTTP Reporter
   - 完全切换到 WebSocket

### 11.2 兼容性

- 保留 HTTP API 作为备用上报通道
- 支持环境变量配置 Reporter 类型

---

## 12. 附录

### 12.1 完整示例

**Worker 端**:

```python
from onestep import (
    OneStepApp,
    RedisConnector,
    WebSocketReporter,
    WebSocketReporterConfig,
)

# 配置 WebSocket Reporter
reporter = WebSocketReporter(WebSocketReporterConfig(
    ws_url="wss://control-plane.example.com/ws/agents",
    token=os.getenv("CONTROL_PLANE_TOKEN"),
    environment="prod",
))

# 创建应用
app = OneStepApp("order-processor", reporter=reporter)

# 配置任务
redis = RedisConnector("redis://localhost:6379")
orders = redis.stream("orders", group="processors")

@app.task(source=orders, concurrency=4)
async def process_order(ctx, order):
    # 处理订单
    pass

app.run()
```

**Control Plane 端 (伪代码)**:

```python
from fastapi import FastAPI, WebSocket

app = FastAPI()
agents: dict[str, WebSocket] = {}

@app.websocket("/ws/agents")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    
    # 验证 token
    token = websocket.headers.get("Authorization", "").replace("Bearer ", "")
    agent_id = websocket.headers.get("X-Agent-ID")
    
    agents[agent_id] = websocket
    
    try:
        while True:
            data = await websocket.receive_json()
            await handle_message(agent_id, data)
    finally:
        del agents[agent_id]

async def pause_agent(instance_id: str):
    ws = agents.get(instance_id)
    if ws:
        await ws.send_json({
            "type": "command",
            "command": "pause",
            "params": {}
        })
```

### 12.2 参考

- [WebSocket RFC 6455](https://datatracker.ietf.org/doc/html/rfc6455)
- [websockets 库文档](https://websockets.readthedocs.io/)
- [onestep HTTP Reporter](../src/onestep/reporter.py)

---

## 修订历史

| 版本 | 日期 | 作者 | 变更 |
|------|------|------|------|
| 1.0.0-draft | 2026-03-17 | - | 初始设计 |