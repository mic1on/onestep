# OneStep Control Plane

Control Plane 是 OneStep 运行时的集中运维控制面，提供**远程监控**、**遥测采集**和**命令下发**能力。

## 架构概览

```
┌──────────────────┐      WebSocket       ┌──────────────────┐      ┌────────────┐
│  OneStep Agent   │ ──────────────────▶   │  Control Plane   │ ──▶  │ PostgreSQL │
│  (ControlPlane   │    onestep-agent.v1   │  API (FastAPI)   │      │            │
│   Reporter)      │ ◀──────────────────   │                  │      │  services  │
└──────────────────┘      commands         │  /api/v1/agents  │      │  instances │
                                           │       /ws        │      │  sessions  │
                                           │                  │      │   tasks    │
                                           │  /api/v1/query   │      │  commands  │
                                           │                  │      │   events   │
                                           │  Console Auth    │      │  metrics   │
                                           └────────┬─────────┘      └────────────┘
                                                    │
                                                    │ SSE / REST
                                                    ▼
                                           ┌──────────────────┐
                                           │   Web Console    │
                                           │  (React + Vite)  │
                                           │                  │
                                           │  Service List    │
                                           │  Task Dashboard  │
                                           │  Instance View   │
                                           │  Command Panel   │
                                           │  Notifications   │
                                           └──────────────────┘
```

核心思路：OneStep 运行时 Agent 通过 **WebSocket 主动外连**到 Control Plane（无需 Agent 开放入站端口），上报拓扑、心跳、指标和任务事件，同时接收运维命令。

## 核心概念

### Service / Instance / Task
- **Service**: 逻辑服务标识，由 `(name, environment)` 唯一确定
- **Instance**: 运行时实例（UUID 标识），携带节点名、主机名、PID、版本信息
- **Task**: 任务图定义（Source、Sink、并发数、超时、重试策略），通过 `topology_hash` 追踪一致性

### 拓扑描述
Reporter 的 `sync` 遥测会携带任务 Source/Sink 的类型、名称和配置摘要，用于控制面展示任务图并计算 `topology_hash`。

连接器会按稳定类型名上报，例如 `cron`、`interval`、`rabbitmq_queue`、`mysql_table_sink`、`redis_stream`、`feishu_bitable_incremental`、`feishu_bitable_table_sink` 和 `http_sink`。Redis Streams 会上报 stream、group、consumer、batch、block、trim 等拓扑配置；`HttpSink` 会上报 URL、method、timeout、query 参数名和成功状态码。

敏感信息不会原样进入拓扑：`HttpSink` 上报时会隐藏 URL 账号信息、移除 URL query/fragment，并把 header 与 `params` 的值标记为 `<redacted>`。

### WebSocket 会话生命周期
1. Agent 建立 WebSocket 连接，发送 `hello`（含协议版本、能力声明）
2. 服务端回复 `hello_ack`（含接受的 `accepted_capabilities`）
3. Agent 开始发送 `telemetry` 消息（sync / heartbeat / metrics / events）
4. 服务端可下发 `command`（ping / shutdown / drain / pause_task 等）
5. Agent 回复 `command_ack`（接受/拒绝）和 `command_result`（执行结果）

### 能力协商（Capability Negotiation）
Agent 在 `hello` 中声明自身能力（如 `command.ping`、`command.shutdown`、`telemetry.sync`），服务端仅下发 Agent 已接受能力的命令。

### 命令生命周期
```
pending → dispatched → accepted/rejected → succeeded/failed/timeout/cancelled
```
所有命令持久化到数据库，未确认的命令可在重连后重新下发。

### 在线/离线检测
- 实例 `last_seen_at` 在 `INSTANCE_OFFLINE_AFTER_S`（默认 90s）内 → 在线
- 健康参与窗口 `INSTANCE_HEALTH_PARTICIPATION_WINDOW_S`（默认 1h）内 → 计入服务健康度分母

## 集成 OneStep Agent

在 OneStep 应用中启用 Control Plane 上报：

```python
from onestep import (
    ControlPlaneReporter,
    ControlPlaneReporterConfig,
    OneStepApp,
)

app = OneStepApp("my-service")
reporter = ControlPlaneReporter(
    ControlPlaneReporterConfig.from_env(app_name=app.name)
)
reporter.attach(app)
```

YAML 也可以直接启用：

```yaml
reporter: true
```

环境变量配置：

| 变量 | 说明 | 示例 |
|---|---|---|
| `ONESTEP_CONTROL_PLANE_URL` | Control Plane 服务地址 | `http://192.168.1.100:8080` |
| `ONESTEP_CONTROL_PLANE_ENVIRONMENT` | 部署环境标签 | `prod` / `staging` |
| `ONESTEP_INGEST_TOKEN` | 认证 Token | `my-token` |

更多配置项请参考 `ControlPlaneReporterConfig` 的 `from_env` 方法。

## 部署控制面

Control Plane 是独立的仓库和部署单元，提供 Docker Compose 一键部署：

```bash
git clone https://github.com/mic1on/onestep-control-plane
cd onestep-control-plane
cp .env.example .env
# 编辑 .env 配置数据库、Token 等
docker compose up --build -d
```

启动后：
- **Web Console**: `http://127.0.0.1:4173`
- **API**: `http://127.0.0.1:8000`
- **交互式 API 文档**: `http://127.0.0.1:8000/docs`

也支持 SQLite 本地开发模式（无需 Docker）：

```bash
./scripts/start-local.sh
```

## 查询 API

控制面提供 REST 查询接口，供 Web Console 或第三方集成使用：

| 端点 | 说明 |
|---|---|
| `GET /api/v1/services` | 服务列表 |
| `GET /api/v1/services/{name}/dashboard?environment=` | 服务仪表盘 |
| `GET /api/v1/services/{name}/instances?environment=` | 实例列表 |
| `GET /api/v1/services/{name}/tasks?environment=` | 任务列表 |
| `GET /api/v1/services/{name}/tasks/{task}?environment=` | 任务详情 |
| `GET /api/v1/services/{name}/events?environment=` | 事件流 |
| `GET /api/v1/services/{name}/commands?environment=` | 命令历史 |
| `GET /api/v1/services/{name}/sessions?environment=` | 会话记录 |

所有查询接口均需 Console Auth（用户名/密码）认证。

## 通知与 Webhook

Control Plane 支持将任务事件推送到即时通讯工具：

- **飞书**群机器人
- **企业微信**群机器人

支持的事件类型：任务启动、成功、失败、重试、死信、计划任务遗漏（Missed Start）。

## 更多资源

- [Agent WS 协议参考](/agent-ws-protocol) — WebSocket 通信协议规范
- [跨仓库协作说明](/ws-cross-repo-collaboration) — 两个仓库之间的边界和协作流程
- [GitHub: onestep-control-plane](https://github.com/mic1on/onestep-control-plane) — 源码仓库
