# OneStep Agent / Control Plane Monorepo 协同开发文档

## 1. 背景

OneStep runtime、control-plane reporter 与 control-plane 服务端在通信、控制能力、
协议演进和联调验证上构成一个完整系统。代码位于同一仓库，但保持独立构建和发布边界：

```text
onestep/
├── src/onestep/                         # runtime
├── plugins/onestep-control-plane/       # reporter / WS client
└── apps/control-plane/                  # FastAPI + React + Electron + Docker
```

Agent 与 Control Plane 之间只使用 WebSocket。前端查询、登录和健康检查继续使用 HTTP。
同仓的目标是让协议相关变更在一个 PR 中完成并验证，不是把三个发布单元合成一个包。

## 2. 组件职责

### 2.1 Runtime 与 reporter

`src/onestep/` 和 `plugins/onestep-control-plane/` 负责：

- WS transport
- `hello`、`heartbeat` 和 telemetry
- command 接收、确认和结果返回
- 本地缓冲、优先级和背压
- 断线重连
- runtime identity 与 task control state

### 2.2 Control Plane

`apps/control-plane/` 负责：

- WS 接入与鉴权
- Agent session 管理
- telemetry 校验与落库
- command 创建、下发和状态流转
- 查询 API、Web Console 与 Desktop

### 2.3 边界原则

- 协议先于实现存在，不允许用口头约定补字段。
- 服务端必须容忍旧 Agent；Agent 必须通过 capability negotiation 使用新能力。
- Reporter payload、事件语义、identity 或远程控制行为变化必须在同一个 PR 中检查双方。
- 同仓不等于同时发布。兼容性仍需支持独立升级与回滚。

## 3. 协议治理

Agent-Control Plane WS 协议的单一真相源是：

`apps/control-plane/docs/protocols/agent-ws-protocol.md`

协议至少定义：

- WS 路由与鉴权
- 消息类型和字段
- 幂等规则
- 错误语义与重连规则
- session 与 command 生命周期
- `protocol_version` 和 capabilities
- `heartbeat.health.task_controls` 等 runtime 状态字段

协议变更必须优先采用兼容扩展。删除字段、重命名字段或改变字段含义时，必须提供
升级顺序、兼容窗口和回滚说明。

## 4. 标准开发顺序

### Phase 1: 协议与兼容性

先更新协议文档，明确消息方向、字段、幂等键、旧版本行为和 capability。

完成标准：

- 新旧 Agent 行为明确
- 服务端接受范围明确
- 发布与回滚顺序明确

### Phase 2: 服务端契约

更新 `apps/control-plane/backend/` 的 schema、持久化和 API 行为，并使用假 client
验证握手、telemetry 和 command 生命周期。

### Phase 3: Runtime 与 reporter

更新 `src/onestep/` 或 `plugins/onestep-control-plane/`，覆盖建连、重连、上报、
command ack/result 和 capability negotiation。

### Phase 4: 同仓契约验证

`.github/workflows/control-plane-contract.yml` 必须使用当前 checkout 的 runtime 和 reporter，
而不是只依赖 PyPI 已发布版本。至少验证：

- reporter 与 WS client 单元测试
- 服务端握手和 telemetry schema
- E2E ingestion/control workflow
- resource catalog 与 topology payload

### Phase 5: 独立发布

需要发布时按以下顺序进行：

1. 发布兼容的 core 与 reporter 包。
2. 更新 control-plane 的依赖下限和 `apps/control-plane/uv.lock`。
3. 构建并发布 control-plane 镜像。
4. 用旧 Agent 和新 Agent 各执行一次 smoke test。

## 5. PR 规则

协议相关需求使用一个 PR，PR 描述必须列出：

- 协议变化与兼容性
- 受影响组件
- package/image 发布顺序
- 测试与 smoke 证据
- 不在本 PR 范围内的工作

实现可以拆成多个清晰提交，例如：

```text
docs: define service description payload
feat: report service description
feat: store and expose service description
test: cover reporter and plane contract
```

不允许：

- 只改 reporter 或服务端一侧并跳过契约检查
- 在联调时临时决定字段名或错误语义
- 用本地 path dependency 替代正式发布版本后忘记恢复
- 因为代码同仓而取消版本兼容和 capability negotiation

## 6. 测试策略

### Runtime / reporter

必须覆盖建连、`hello`、heartbeat、telemetry、command ack/result、重连退避、
buffer flush 和 identity persistence。

### Control Plane

必须覆盖 WS 鉴权、session、telemetry 入库、command 状态流转、消息幂等、
断线和数据库迁移。

### E2E

最小闭环：

1. 启动 control plane。
2. 启动启用了 reporter 的 Agent。
3. 确认 session 在线并完成 sync。
4. 下发一条受支持 command。
5. 收到 `command_ack` 和 `command_result`。
6. 验证 telemetry 落库及 UI 查询结果。

## 7. 本地开发

从仓库根目录分别操作两个 Python 项目：

```bash
# Runtime / plugins, Python 3.9+
uv sync --all-packages --extra test

# Control plane, Python 3.11+
uv sync --project apps/control-plane --extra dev

# Control plane frontend
pnpm --dir apps/control-plane install --frozen-lockfile
```

`apps/control-plane` 不加入根 uv workspace。服务端与 reporter 当前使用相同的
Python distribution name，且 Python 版本范围不同；强行合并 workspace 会破坏
Python 3.9 的 core 测试。

## 8. CI 边界

```text
src/** or plugins/onestep-control-plane/**
  ├── core/plugin CI
  └── control-plane contract CI

apps/control-plane/backend/**
  ├── control-plane full CI
  └── control-plane contract CI

apps/control-plane/frontend/** or desktop/**
  └── control-plane full CI
```

控制面镜像名保持 `ghcr.io/mic1on/onestep-control-plane`。迁入目录不改变用户的
拉取地址、Compose 环境变量或数据库迁移方式。

## 9. 完成标准

- 协议文档和实现一致。
- 当前 checkout 的 runtime、reporter 和服务端契约测试通过。
- 两套 `uv.lock` 均通过 frozen 检查。
- 控制面后端、前端、E2E、Docker build 和 smoke 通过。
- 发布顺序、兼容窗口和回滚方式写入 PR。
- 没有新增跨组件的隐式 path dependency。
