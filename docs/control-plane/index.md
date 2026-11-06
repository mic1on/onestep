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

Worker Agent 0.1.5 起还提供**命令结果恢复**：Agent 上报 `agent.command_recovery` 能力后，每条命令在执行前落盘到 `work_dir/commands/`，终态结果先持久化再上报；重复命令 ID 直接返回已有结果，同一部署的不同命令串行执行。控制面只向具备该能力的 Agent 重投已接受的命令；Agent 重启后中断的准备以 `cancelled` 结束，退出事件保留到控制面确认回执为止。

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

`reporter: true` 仍然是推荐的最小配置，会从环境变量读取连接信息。
如果要把服务描述固定在配置文件里，可以改成 mapping：

```yaml
reporter:
  service_description: Synchronizes billing data into the warehouse
```

环境变量配置：

| 变量 | 说明 | 示例 |
|---|---|---|
| `ONESTEP_CONTROL_PLANE_URL` | Control Plane 服务地址 | `http://192.168.1.100:8080` |
| `ONESTEP_CONTROL_PLANE_TOKEN` | Worker 上报认证 Token | `my-token` |
| `ONESTEP_CONTROL_PLANE_ENVIRONMENT` | 部署环境标签 | `prod` / `staging` |
| `ONESTEP_SERVICE_NAME` | 服务名；未设置时默认使用 `app.name` | `billing-sync` |
| `ONESTEP_SERVICE_DESCRIPTION` | 服务级描述；会展示在服务目录和服务详情中 | `同步计费数据到数仓` |

`reporter.service_description` / `ONESTEP_SERVICE_DESCRIPTION` 是服务级元数据，
和 `tasks[].description` 这种任务级描述相互独立。

更多配置项请参考 `ControlPlaneReporterConfig` 的 `from_env` 方法，完整变量清单见 [环境变量](/guide/environment-variables)。

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
| `GET /api/v1/events?environment=` | 跨服务近期事件流 |

`GET /api/v1/services/{name}/events` 支持以下过滤参数：

| 参数 | 说明 |
|---|---|
| `task_name` | 按任务名过滤 |
| `kind` | 按事件类型过滤（如 `succeeded` / `failed` / `retried` / `dead_lettered`） |
| `instance_id` | 按 Worker 实例过滤 |
| `occurred_after` / `occurred_before` | 按发生时间范围过滤（UTC ISO 时间） |
| `limit` / `offset` | 分页 |

所有查询接口均需 Console Auth（用户名/密码）认证。

## 通知与 Webhook

Control Plane 支持将任务事件推送到即时通讯工具：

- **飞书**群机器人
- **企业微信**群机器人
- **自定义** Webhook

支持的事件类型：任务启动、成功、失败、重试、死信、计划任务遗漏（Missed Start）。

通道按三个独立维度分类：事件类型、服务范围（空列表 = 全部服务，含未来新增）、Missed Start 宽限期。抑制（damping）按事件种类刻意不对称：连通性翻转使用稳定确认窗口与抖动抑制（窗口内的快速上下线振荡不再触发通知风暴）；`task_failed` 按（通道, 服务）突发抑制；`task_started` / `task_succeeded` / `task_missed_start` 不抑制。抑制状态持久化，重启不会重置计数；被抑制的突发仍在摘要中带上 `suppressed_failure_count`——沉默不能被误读为健康。

`POST /channels/{id}/test` 发起**真实**的 Webhook 请求并返回 `delivered` / 状态码 / 错误信息；投递结果可在 `GET /deliveries` 查询，这是"告警有没有真的发出去"的唯一反馈回路。

## 监控与告警

控制面自带两套互补的告警平面：

- **Prometheus 规则（Plane A）**：告警规则位于 `apps/control-plane/monitoring/prometheus/rules/control-plane.yml`，配套 runbook 在 `apps/control-plane/docs/runbooks/alerts.md`（每条规则以 `runbook:` 锚点链接）。`docker-compose.monitoring.yml` 一键运行 Prometheus + Alertmanager + blackbox + postgres_exporter；`/metrics` 端点需要 bearer token。`critical` 告警的含义是"控制面宕机，**或**它已失去报告问题的能力"（扫描失败、通知投递失败、指标缺失、扫描从未运行）——后者更危险：一个静默的告警系统比一次响亮的故障更会掩盖事故。
- **通知子系统（Plane B）**：控制面自身扫描/派生事件 → 通知 outbox → Webhook。Plane B 由控制面自己驱动，控制面整体宕机时它必然沉默——这正是 Plane A 存在的原因。

修改监控配置后运行 `bash scripts/check-monitoring.sh`（也是 CI 的一项检查），它会校验规则引用的指标、runbook 锚点与 scrape job 名一致。

## 更多资源

- [Agent WS 协议参考](/agent-ws-protocol) — WebSocket 通信协议规范
- [跨仓库协作说明](/ws-cross-repo-collaboration) — 两个仓库之间的边界和协作流程
- [GitHub: onestep-control-plane](https://github.com/mic1on/onestep-control-plane) — 源码仓库
