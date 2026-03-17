# OneStep WS Agent 双仓协同开发文档

## 1. 背景

`onestep` 与 `onestep-control-plane` 分属两个仓库，但在 Agent 通信、控制能力、协议演进和联调验证上是一个完整系统。

本次目标是将 Agent 侧 ingestion/control 通道统一为 `WebSocket only`：

- Agent 与 Control Plane 之间只保留 WS
- 前端查询、登录、健康检查继续保留 HTTP API
- 删除 Agent 侧历史 HTTP ingestion 路由
- 在无内网穿透场景下，仍可实现真正的 control

为了避免双仓实现漂移，本功能必须采用“协议先行、两仓分治、分阶段联调”的开发方式。

## 2. 目标

- 保证双仓改动始终围绕同一份协议推进
- 保证每一阶段都可以独立验证
- 降低联调阶段的不确定性
- 避免两边“各自实现、最后碰撞”
- 支持渐进式交付，而不是一次性大切换

## 3. 范围

### 3.1 本次纳入范围

- Agent WS 连接建立与重连
- Agent session 管理
- telemetry 上送
- control command 下发与结果回传
- command 生命周期追踪
- 双仓本地联调流程
- 文档、测试与迁移说明

### 3.2 本次不纳入范围

- 删除前端查询 HTTP API
- 动态热更新任务定义
- 动态调整 task concurrency
- pause/resume source 等尚未具备 runtime 基础设施的能力

## 4. 仓库职责划分

### 4.1 `onestep`

负责 Agent 侧实现，包括：

- WS transport
- `hello`
- `heartbeat`
- `telemetry`
- `command` 接收
- `command_ack`
- `command_result`
- 本地缓冲、优先级和背压
- 断线重连
- Agent 配置项与运行时行为

### 4.2 `onestep-control-plane`

负责服务端实现，包括：

- WS 接入与鉴权
- Agent session 管理
- telemetry 校验与落库
- command 创建、下发、状态流转
- 查询 API
- 前端控制台展示

### 4.3 边界原则

- 协议由服务端契约主导，但双方必须严格按同一份文档实现
- 不允许口头约定字段
- 不允许联调时临时改协议
- 协议先于实现存在

## 5. 协议治理

### 5.1 单一真相源

Agent-Control Plane WS 协议文档只保留一份，建议放在 `onestep-control-plane` 仓库：

`docs/protocols/agent-ws-protocol.md`

`onestep` 仓库不再复制一份独立协议，只引用该文档。

### 5.2 协议必须包含的内容

- WS 路由
- 鉴权方式
- 消息类型
- 字段定义
- 幂等规则
- 错误语义
- 重连规则
- 会话生命周期
- 命令状态机
- 协议版本策略
- capabilities 约定

### 5.3 协议版本要求

`hello` 必须携带：

- `protocol_version`
- `capabilities`

示例：

```json
{
  "protocol_version": "1",
  "capabilities": [
    "telemetry.sync",
    "telemetry.heartbeat",
    "telemetry.metrics",
    "telemetry.events",
    "command.shutdown",
    "command.sync_now",
    "command.flush_metrics",
    "command.flush_events"
  ]
}
```

## 6. 分支策略

两个仓库对同一需求使用相同主题分支名。

示例：

- `feat/ws-agent-session`
- `feat/ws-command-lifecycle`
- `feat/remove-http-ingestion`

推荐方式：

- `onestep`: `feat/ws-agent-session`
- `onestep-control-plane`: `feat/ws-agent-session`

这样可以保证：

- 需求映射清晰
- 联调组合明确
- PR 互相引用方便
- 后续问题回溯简单

## 7. 标准开发顺序

### 7.1 Phase 1: 协议冻结

先完成 WS 协议文档，不写实现细节代码。

至少明确以下消息：

- `hello`
- `hello_ack`
- `telemetry`
- `command`
- `command_ack`
- `command_result`
- `error`

完成标准：

- 双方字段理解一致
- 命令生命周期一致
- 幂等规则一致
- 后续实现不再口头改字段

### 7.2 Phase 2: `onestep-control-plane` 先行

服务端先做接入骨架和持久化模型。

优先完成：

- `/api/v1/agents/ws`
- token 鉴权
- `agent_sessions`
- `agent_commands`
- telemetry service 层
- command 状态流转
- 命令查询 API

完成标准：

- 假 client 可以连入
- `hello` 可建立 session
- telemetry 可落库
- command 可创建并追踪状态

### 7.3 Phase 3: `onestep` 对接

在服务端协议稳定后，Agent 再接入。

优先完成：

- WS transport
- `hello`
- `heartbeat`
- telemetry 发送
- `command_ack`
- `command_result`
- 一期命令执行器

一期命令建议仅支持：

- `ping`
- `shutdown`
- `sync_now`
- `flush_metrics`
- `flush_events`

完成标准：

- Agent 可稳定连入
- 命令闭环可走通
- 重连后可恢复上报

### 7.4 Phase 4: 联调

使用两个 feature branch 进行联调，而不是一边先合并主干再赌兼容。

联调至少覆盖：

- 连接建立
- `hello`
- `sync`
- `heartbeat`
- `metrics`
- `events`
- `command`
- `command_ack`
- `command_result`
- 断线重连

### 7.5 Phase 5: 收尾与迁移

稳定后再进行：

- 删除历史 HTTP ingestion
- 补 README
- 补迁移文档
- 补 demo
- 补 smoke test
- 前端控制入口对接

## 8. PR 规则

### 8.1 双仓分别提 PR

每个仓库单独提 PR，不做“跨仓单 PR”。

### 8.2 PR 必须互相引用

每个 PR 描述里必须包含：

- 对应需求名称
- 配套仓库分支或 PR 链接
- 当前阶段
- 本 PR 范围
- 非本 PR 范围
- 联调方法

示例：

```text
Depends on:
- onestep-control-plane: feat/ws-agent-session / PR #123

This PR covers:
- WS transport
- hello/heartbeat
- command_ack/result

Not included:
- frontend command UI
- deletion of legacy HTTP ingestion
```

### 8.3 不允许的 PR 方式

- 一边协议未定，一边直接实现
- 联调时再决定字段名
- 一个 PR 同时承载协议变更、底层模型变更、前端 UI 变更

## 9. 数据与状态设计原则

### 9.1 服务端必须持久化的对象

- `agent_sessions`
- `agent_commands`

### 9.2 command 必须具备完整生命周期

建议状态：

- `created`
- `sent`
- `acked`
- `running`
- `succeeded`
- `failed`
- `expired`

### 9.3 幂等约束

- `sync/heartbeat`: 基于 `instance_id + sequence (+ sent_at)` 去重
- `metrics`: 基于 `instance_id + task_name + window_id` 去重
- `events`: 基于 `event_id` 去重
- `command`: 基于 `command_id` 幂等执行

## 10. 测试策略

### 10.1 `onestep-control-plane`

必须覆盖：

- WS 握手鉴权
- session 建立
- telemetry 入库
- command 状态流转
- 重复消息幂等
- 断线后的 session 状态变化

### 10.2 `onestep`

必须覆盖：

- 建连
- `hello`
- `heartbeat`
- 命令接收
- `command_ack`
- `command_result`
- 重连退避
- telemetry 缓冲与 flush

### 10.3 E2E 联调

必须有最小联调路径验证：

1. 启动 control plane
2. 启动 demo agent
3. 确认 session 在线
4. 发送一条 command
5. 收到 `command_ack`
6. 收到 `command_result`
7. 验证 telemetry 正常落库

## 11. 本地联调约定

推荐目录结构：

```text
~/development/
  onestep/
  onestep-control-plane/
```

推荐联调顺序：

1. 用假的 WS client 验证服务端
2. 用真的 Agent 验证 telemetry
3. 再验证 command
4. 最后验证断线与重连

禁止事项：

- 不要联调时临时改协议字段
- 不要直接手工 patch 数据库代替真实消息流
- 不要前后端、Agent、服务端三方同时第一次联调

## 12. 里程碑

### M1: Session

- WS 握手
- `hello`
- `hello_ack`
- 基础 session 查询

### M2: Telemetry

- `sync`
- `heartbeat`
- `metrics`
- `events`

### M3: Command

- `command`
- `command_ack`
- `command_result`
- `shutdown`

### M4: 稳定性

- 重连
- 背压
- 幂等
- 命令过期
- 错误处理

### M5: 清理旧链路

- 删除 HTTP ingestion
- 更新文档
- 补 smoke test
- 前端补完控制入口

## 13. 与 AI 协作方式

为了降低上下文污染，建议按仓库拆会话：

- 会话 A：只处理 `onestep-control-plane`
- 会话 B：只处理 `onestep`

每一轮任务明确说明：

- 目标仓库
- 当前分支
- 这轮是否允许跨仓读取
- 这轮是否允许跨仓修改

推荐任务表达方式：

- “这轮只写协议文档”
- “这轮只改 control-plane backend”
- “这轮只改 onestep agent transport”
- “这轮只做联调和测试补齐”

不推荐：

- “把两个仓库一起改完”
- “顺便把前端也一块补了”
- “边写边想协议”

## 14. 结论

本功能不是普通单仓需求，而是一个协议驱动的双仓系统演进任务。

正确执行方式是：

1. 先冻结协议
2. 再由 `onestep-control-plane` 落地服务端骨架
3. 再由 `onestep` 接入 Agent WS transport
4. 最后用同名分支做联调
5. 稳定后再删除历史 HTTP ingestion

只有这样，才能避免双仓协作中最常见的问题：

- 协议漂移
- 联调爆雷
- 状态语义不一致
- 命令生命周期不可追踪
- 发布节奏失控
