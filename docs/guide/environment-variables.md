---
title: 环境变量 | 指南
outline: deep
---

# 环境变量

OneStep 用环境变量把部署环境相关的配置注入进程：运行目标、实例身份、Control Plane 连接信息、服务端参数以及第三方 SDK 凭据。本页按子系统汇总全部变量、默认值与用途，作为部署和排障时的单一参考。

页面范围：

- [环境变量的来源与优先级](#环境变量的来源与优先级) —— 变量从哪里读、谁覆盖谁
- [核心运行时](#核心运行时) —— 运行时与 YAML 配置直接读取的变量
- [systemd 部署模板](#systemd-部署模板) —— `deploy/` 模板与 preflight 脚本
- [Worker Runtime Image](#worker-runtime-image) —— 官方容器镜像入口脚本
- [Control Plane Reporter](#control-plane-reporter) —— worker 侧的遥测上报
- [实例身份](#实例身份) —— `instance_id` 的解析顺序与固定方式
- [Control Plane 服务端](#control-plane-服务端) —— `ONESTEP_CP_*` 全量参数
- [Worker Agent](#worker-agent) —— `onestep-agent` 执行主机
- [连接器与第三方 SDK](#连接器与第三方-sdk) —— AWS 凭据、时区
- [本地开发与集成测试](#本地开发与集成测试)
- [系统注入的变量](#系统注入的变量) —— 由框架写入，不要手工设置

## 环境变量的来源与优先级

一次 `onestep run` 或 `onestep check` 看到的变量，可能来自下面三个来源，优先级从高到低：

1. **进程环境**：systemd 的 `EnvironmentFile`、`docker run -e`、`docker compose` 的 `environment`、shell 里的 `export`。
2. **`.env` 文件**（仅 YAML 目标）：解析顺序为 `--env-file` 命令行参数 → YAML 中的 `app.env_file` → 与 YAML 同目录的 `.env`（自动探测，不存在则跳过）。
3. **YAML 内的 `${VAR}` 展开**：配置值中的变量引用在加载时替换。

`.env` 使用 `setdefault` 语义写入进程环境，**已存在的进程环境变量优先**，文件里的同名键会被忽略。加载数量会写入 `onestep` logger 的 DEBUG 日志。

### 变量展开语法

YAML 中所有字符串值都会做变量展开，支持三种写法：

| 写法 | 含义 |
| --- | --- |
| `${VAR}` | 读取 `VAR`，未设置时替换为空字符串 |
| `${VAR:-default}` | 读取 `VAR`，未设置时使用 `default` |
| `${VAR:default}` | 同上，单冒号写法 |

两点容易踩坑的行为：

- **纯引用会保留类型**：当整个字符串就是一个 `${VAR}` 时，展开结果会按 JSON 字面量解码，`"30"` 会变成整数 `30`、`"true"` 会变成布尔值。`prefix-${VAR}` 这类混合字符串始终是字符串。
- **不做隐式类型猜测**：`yes`、`0123`、ISO 日期这类值不会因为像 YAML 标量而被转换，仍按 JSON 规则保持字符串。

### 严格模式

默认情况下，缺少的变量会展开成空字符串，问题往往到运行期才暴露。需要提前失败时启用严格检查：

```bash
onestep check --strict-env worker.yaml
onestep run worker.yaml --strict-env
```

也可以固化在 YAML 里，让 `check`、`run`、`render`、`build` 一致生效：

```yaml
app:
  name: billing-sync
  env_file: .env          # 可选，指定变量文件
  strict_env: true        # 可选，缺变量直接报错
```

`strict_env` 只检查**没有默认值**的 `${VAR}` 引用，并列出变量名和引用位置；带 `${VAR:-default}` 的引用不会报错。相关命令行为见 [YAML 任务定义](/yaml-task-definition)。

## 核心运行时

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `TZ` | 系统本地时区 | `CronSource` / `IntervalSource` 未显式配置时区时的回退值。容器和 systemd 部署建议显式设置，避免宿主机时区漂移导致调度时间偏移 |
| `PYTHONPATH` | 空 | Python 模块搜索路径。systemd 模板与 worker 镜像会自动追加应用目录，无需手工维护 |

运行时**没有** `ONESTEP_LOG_LEVEL` 之类的变量：日志级别来自 `--log-level` 命令行参数、YAML 的 `app.logging.level` 或代码中的 `logging` 配置，详见 [日志与任务事件](/guide/logging)。指标端点同理，由 `--metrics-addr` 开启，见 [指标与健康检查](/guide/metrics)。

## systemd 部署模板

`deploy/systemd/onestep-app.service` 与 `deploy/bin/onestep-preflight.sh` 读取以下变量，配置写在 `/etc/onestep/onestep-app.env`（从 `deploy/env/onestep-app.env.example` 复制）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `APP_CWD` | 无（必填） | 应用工作目录。preflight 会把它前置到 `PYTHONPATH`，`ExecStart` 也在这里启动 |
| `APP_TARGET` | 无（必填） | `onestep` 的 target，如 `your_package.tasks:app` 或 `worker.yaml` |
| `ONESTEP_BIN` | 无（必填） | `onestep` 可执行文件路径，通常是 `/srv/onestep-app/.venv/bin/onestep`；不存在时会回退到 `PATH` 中查找 |

模板另外在 unit 里固定了 `PYTHONUNBUFFERED=1`，保证 stdout 日志实时进入 journald。`ExecStartPre` 先执行 `onestep check "$APP_TARGET"`，校验失败则服务不启动。完整安装步骤见 [生产部署](/guide/deploy)。

## Worker Runtime Image

官方镜像 `ghcr.io/mic1on/onestep-worker` 的入口脚本读取：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ONESTEP_TARGET` | 无（必填） | YAML 文件路径或 Python import target；缺失时容器直接退出并提示 `ONESTEP_TARGET is required` |
| `WORKSPACE_DIR` | `/workspace` | 工作区路径。入口脚本把它和它的 `src/` 加入 `PYTHONPATH`，并在其中查找 `requirements.txt` / `pyproject.toml` 安装依赖 |

启动顺序为：解析 `ONESTEP_TARGET` → 安装工作区依赖 → `onestep check` → `onestep run`。两种运行方式见 [Worker Runtime Image](/guide/worker-runtime-image)。

## Control Plane Reporter

`onestep-control-plane` reporter 通过 `ControlPlaneReporterConfig.from_env()` 读取以下变量。`base_url` 与 `token` 缺失时启动即失败，其余都有默认值。

### 连接与服务标识

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ONESTEP_CONTROL_PLANE_URL` | 无（必填） | Control Plane 基地址。接受 `http://`、`https://`、`ws://`、`wss://`；默认 sender 会据此推导 WebSocket 端点（`http` → `ws`、`https` → `wss`，路径后缀 `/api/v1/agents/ws`）。别名：`ONESTEP_CONTROL_URL` |
| `ONESTEP_CONTROL_PLANE_TOKEN` | 无（必填） | 上报鉴权 Token，对应服务端的 `ONESTEP_CP_INGEST_TOKENS`。别名：`ONESTEP_CONTROL_TOKEN` |
| `ONESTEP_CONTROL_PLANE_ENVIRONMENT` | `dev` | 部署环境标签，只接受 `dev`、`staging`、`prod`。别名：`ONESTEP_ENV` |
| `ONESTEP_SERVICE_NAME` | 应用 `app.name` | 服务名，`(service_name, environment)` 唯一确定一个服务 |
| `ONESTEP_SERVICE_DESCRIPTION` | 空 | 服务级描述，展示在控制面服务目录；与 `tasks[].description` 相互独立 |
| `ONESTEP_NODE_NAME` | 空 | 节点名，多机部署时用于区分实例所在机器 |
| `ONESTEP_DEPLOYMENT_VERSION` | 空 | 部署版本，展示在实例详情。别名：`ONESTEP_VERSION` |

### 上报节奏与缓冲

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ONESTEP_CONTROL_PLANE_HEARTBEAT_INTERVAL_S` | `30.0` | 心跳间隔（秒） |
| `ONESTEP_CONTROL_PLANE_METRICS_INTERVAL_S` | `30.0` | 指标批次上报间隔（秒） |
| `ONESTEP_CONTROL_PLANE_EVENT_FLUSH_INTERVAL_S` | `5.0` | 任务事件批量刷写间隔（秒） |
| `ONESTEP_CONTROL_PLANE_EVENT_BATCH_SIZE` | `100` | 单个事件批次的最大条数 |
| `ONESTEP_CONTROL_PLANE_MAX_PENDING_EVENTS` | `1000` | 断线期间本地缓存的事件上限，超出后按策略丢弃最旧数据 |
| `ONESTEP_CONTROL_PLANE_MAX_PENDING_METRIC_BATCHES` | `120` | 断线期间本地缓存的指标批次数上限 |
| `ONESTEP_CONTROL_PLANE_TIMEOUT_S` | `5.0` | 单次 HTTP 请求超时（秒） |
| `ONESTEP_CONTROL_PLANE_RECONNECT_BASE_DELAY_S` | `0.5` | 重连初始退避（秒） |
| `ONESTEP_CONTROL_PLANE_RECONNECT_MAX_DELAY_S` | `30.0` | 重连退避上限（秒），必须 ≥ 初始退避 |
| `ONESTEP_CONTROL_PLANE_SHUTDOWN_FLUSH_TIMEOUT_S` | `0.5` | 关闭前等待未发送数据刷写的时长（秒），可为 `0` |

### 实例身份变量

`instance_id` 由三个变量决定，解析顺序见 [实例身份](#实例身份)：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ONESTEP_INSTANCE_ID` | 空 | 显式指定 UUID，优先级最高 |
| `ONESTEP_REPLICA_KEY` | 空 | 副本槽位名，如 `worker-0`；按 `service_name + environment + replica_key` 生成确定性 UUIDv5 |
| `ONESTEP_STATE_DIR` | `~/.onestep/control-plane-state/<environment>/<service_name>` | 本地身份状态目录，保存 `identity.json` 与序列号 |

示例：

```bash
export ONESTEP_CONTROL_PLANE_URL=https://control-plane.example.com
export ONESTEP_CONTROL_PLANE_TOKEN=replace-me
export ONESTEP_CONTROL_PLANE_ENVIRONMENT=prod
export ONESTEP_SERVICE_NAME=billing-sync
export ONESTEP_REPLICA_KEY=worker-0
```

YAML 也可以直接写死部分字段（`reporter: true` 仍从环境变量解析连接信息）：

```yaml
reporter:
  base_url: https://control-plane.example.com
  token: ${ONESTEP_CONTROL_PLANE_TOKEN}
  service_name: billing-sync-worker
  service_description: Synchronizes billing data into the warehouse
```

更多接线方式见 [Control Plane](/control-plane/) 与 [稳定实例身份](/stable-instance-identity)。

## 实例身份

`ControlPlaneReporterConfig.from_env()` 按固定顺序解析实例身份，三个来源只需要命中一个：

1. `ONESTEP_INSTANCE_ID` —— 显式 UUID，永远优先。
2. `ONESTEP_REPLICA_KEY` —— 由 `service_name + environment + replica_key` 派生 UUIDv5，同一个 key 永远映射到同一个实例。
3. `ONESTEP_STATE_DIR` 下的 `identity.json` —— 首次启动生成并落盘，重启后复用，`heartbeat_sequence` / `sync_sequence` 也从此文件继续累加。

选型建议：

| 场景 | 推荐做法 |
| --- | --- |
| 单机单进程（systemd、长期运行 VM） | 持久化 `ONESTEP_STATE_DIR`，例如 `/var/lib/onestep/billing-sync` |
| 多副本、Kubernetes StatefulSet | 设置 `ONESTEP_REPLICA_KEY`（可用 `$(HOSTNAME)` 之类的稳定槽位名） |
| 手工调试 | 临时设置 `ONESTEP_INSTANCE_ID`，注意不要给多个活进程发同一个 UUID |

注意：同一时刻两个活进程不能共用同一个状态目录，否则启动时会因身份锁直接失败。完整说明见 [稳定实例身份](/stable-instance-identity)。

## Control Plane 服务端

Control Plane 服务端（`apps/control-plane`）通过 pydantic-settings 读取 `ONESTEP_CP_` 前缀的变量，字段名大写即为变量名；同时会读取当前目录的 `.env`。变量含义与默认值：

### 基础与数据库

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ONESTEP_CP_APP_ENV` | `dev`（Compose 默认传入 `docker`） | 运行环境标签。影响登录 Cookie 的 `Secure` 标记（`prod` 才带）与开发模式回退逻辑 |
| `ONESTEP_CP_DEBUG` | `false` | 调试开关，透传给 FastAPI 应用 |
| `ONESTEP_CP_DATABASE_URL` | `postgresql+psycopg://postgres:postgres@localhost:5432/onestep_control_plane` | SQLAlchemy DSN；留空回退到该默认值。桌面版使用 SQLite |
| `ONESTEP_CP_HOST` | `127.0.0.1`（桌面入口）/ `0.0.0.0`（`start-local.sh`） | 监听地址。由桌面入口与本地脚本读取，不属于后端 Settings |
| `ONESTEP_CP_PORT` | `4173` | 监听端口。同上 |
| `ONESTEP_CP_LOG_LEVEL` | `info` | uvicorn 日志级别。同上 |
| `ONESTEP_CP_WORKER_PACKAGE_STORAGE_DIR` | `.onestep-control-plane/packages` | 接收到的 worker 包存放目录 |
| `ONESTEP_CP_UI_DIST_DIR` | `frontend/dist`（本地）/ `/app/frontend/dist`（Docker） | 前端静态资源目录 |
| `ONESTEP_CP_UI_API_BASE_URL` | `/` | 打包镜像经 `/app-config.js` 下发的前端 API 基路径；`pnpm dev` 不使用 |

### 鉴权与令牌

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ONESTEP_CP_INGEST_TOKENS` | 空 | 遥测上报与 Agent WS 接入的 Bearer Token，支持单个、逗号分隔或 JSON 数组；为空时接入端点返回 503，必须先配置才能接收上报 |
| `ONESTEP_CP_WORKER_AGENT_REGISTRATION_TOKENS` | 空 | Worker Agent 注册 Token，同样支持逗号分隔或 JSON 数组；为空时注册接口返回 503 |
| `ONESTEP_CP_CONNECTOR_SECRET` | 空 | 用于派生 Connector 密钥加密密钥的普通字符串；使用 Connectors 功能时必须设置，且需跨重启、跨副本保持一致 |

```bash
export ONESTEP_CP_INGEST_TOKENS='dev-token'
export ONESTEP_CP_INGEST_TOKENS='token-a,token-b'
export ONESTEP_CP_INGEST_TOKENS='["token-a","token-b"]'
```

### Console 认证与安全

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ONESTEP_CP_CONSOLE_AUTH_USERNAME` | 空 | 控制台共享用户名，必须与密码同时设置，只设一个会启动失败 |
| `ONESTEP_CP_CONSOLE_AUTH_PASSWORD` | 空 | 控制台共享密码 |
| `ONESTEP_CP_CONSOLE_AUTH_SESSION_TTL_S` | `604800` | 登录会话有效期（秒），默认 7 天 |
| `ONESTEP_CP_CONSOLE_SENSITIVE_AUTH_WINDOW_S` | `900` | 危险命令要求「近期重新认证」的时间窗口（秒） |
| `ONESTEP_CP_CONSOLE_LOGIN_MAX_FAILURES` | `5` | 触发锁定前的失败登录次数 |
| `ONESTEP_CP_CONSOLE_LOGIN_FAILURE_WINDOW_S` | `900` | 失败计数的时间窗口（秒） |
| `ONESTEP_CP_CONSOLE_LOGIN_LOCKOUT_S` | `900` | 锁定持续时间（秒） |
| `ONESTEP_CP_CORS_ALLOW_ORIGINS` | 空 | 允许的浏览器来源，支持逗号分隔或 JSON 数组；默认不允许跨域 |
| `ONESTEP_CP_CONSOLE_BASE_URL` | 空 | 控制台公开地址，设置后 webhook 通知渲染绝对链接 |

### 通知与保留策略

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ONESTEP_CP_NOTIFICATION_OUTBOX_MAX_ATTEMPTS` | `5` | webhook 投递最大尝试次数，超过后标记永久失败 |
| `ONESTEP_CP_NOTIFICATION_OUTBOX_BATCH_SIZE` | `50` | 单次 outbox drain 处理的最大请求数 |
| `ONESTEP_CP_NOTIFICATION_OUTBOX_DRAIN_INTERVAL_S` | `2.0` | outbox drain 间隔（秒） |
| `ONESTEP_CP_NOTIFICATION_OUTBOX_BACKOFF_BASE_S` | `2.0` | 失败后的初始重试退避（秒） |
| `ONESTEP_CP_NOTIFICATION_OUTBOX_BACKOFF_MAX_S` | `300.0` | 指数退避上限（秒） |
| `ONESTEP_CP_NOTIFICATION_DELIVERY_TIMEOUT_S` | `5.0` | 单次 webhook 投递超时（秒） |
| `ONESTEP_CP_NOTIFICATION_MISSED_START_SCAN_INTERVAL_S` | `60` | 计划任务遗漏扫描间隔（秒） |
| `ONESTEP_CP_RETENTION_TASK_EVENTS_DAYS` | `30` | `task_events` 保留天数 |
| `ONESTEP_CP_RETENTION_TASK_METRIC_WINDOWS_DAYS` | `90` | `task_metric_windows` 保留天数 |
| `ONESTEP_CP_RETENTION_AGENT_COMMANDS_DAYS` | `30` | 终态 `agent_commands` 保留天数，未完成命令不删除 |
| `ONESTEP_CP_RETENTION_DELETE_BATCH_SIZE` | `1000` | 单批删除行数上限 |
| `ONESTEP_CP_RETENTION_RUN_INTERVAL_S` | `86400` | 自动清理执行间隔（秒），默认每天一次 |

### 在线状态、指标与响应格式

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ONESTEP_CP_INSTANCE_OFFLINE_AFTER_S` | `90` | `last_seen_at` 超过该时长即判定实例离线（秒） |
| `ONESTEP_CP_INSTANCE_HEALTH_PARTICIPATION_WINDOW_S` | `3600` | 实例计入服务健康度分母的时间窗口（秒），必须 ≥ 离线阈值 |
| `ONESTEP_CP_PROMETHEUS_CACHE_TTL_S` | `15.0` | `/metrics` 响应进程内缓存时长（秒），`0` 表示关闭缓存 |
| `ONESTEP_CP_API_RESPONSE_TIMEZONE` | 空（回退 `TZ`，再回退 `UTC`） | 查询 API 时间字段的输出时区 |
| `ONESTEP_CP_BACKGROUND_WORKER_LEADER_POLL_INTERVAL_S` | `5` | 后台 worker 抢主轮询间隔（秒） |
| `ONESTEP_CP_READINESS_TASK_STALE_AFTER_S` | `120` | 就绪检查判定后台任务过期的阈值（秒） |

### 部署与本地脚本

以下变量主要由 Compose 文件、桌面入口或本地脚本使用，不进入后端 Settings：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ONESTEP_CP_IMAGE` | 无 | Compose 使用的服务端镜像地址 |
| `ONESTEP_CP_TIMEZONE` | `Asia/Shanghai` | 以 `TZ` 注入容器，同时影响后端响应时区 |
| `ONESTEP_CP_POSTGRES_DB` | `onestep_control_plane` | 内置 PostgreSQL 数据库名 |
| `ONESTEP_CP_POSTGRES_USER` | `postgres` | 内置 PostgreSQL 用户名 |
| `ONESTEP_CP_POSTGRES_PASSWORD` | `postgres`（示例）/ 必填（deploy compose） | 内置 PostgreSQL 密码，生产必须修改 |
| `ONESTEP_CP_POSTGRES_PORT` | `5432` | 内置 PostgreSQL 暴露端口 |
| `ONESTEP_CP_SQLITE_PATH` | `.data/control-plane-dev.db` | `scripts/start-local.sh` 的 SQLite 文件路径 |
| `ONESTEP_CP_ALEMBIC_INI` | 仓库根 `alembic.ini` | 桌面入口使用的 Alembic 配置路径 |
| `ONESTEP_CP_ALEMBIC_SCRIPT_LOCATION` | `backend/alembic` | 迁移脚本目录 |
| `ONESTEP_CP_REPO_ROOT` | 自动推导 | 桌面入口的仓库根路径 |
| `ONESTEP_CP_SMOKE_READY_TIMEOUT_S` | `120` | 冒烟脚本等待就绪的超时（秒） |
| `ONESTEP_CP_SMOKE_BASE_URL` | `http://127.0.0.1:4173` | 冒烟脚本访问的基地址 |
| `ONESTEP_CP_SMOKE_API_URL` | 同 `BASE_URL` | 冒烟脚本访问的 API 地址 |
| `ONESTEP_CP_SMOKE_FRONTEND_URL` | 同 `BASE_URL` | 冒烟脚本访问的前端地址 |

前端开发变量（`apps/control-plane/frontend/.env`，与运行时变量无关）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `VITE_API_BASE_URL` | 空（同源） | Vite 开发时请求的 API 基地址 |

## Worker Agent

`onestep-agent`（`apps/work-agent`）用 `setup` 把配置写入 `~/.onestep/worker-agent/config.json`，环境变量在运行时**覆盖**配置文件：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ONESTEP_PLANE_URL` | 配置文件 `plane_url` | Control Plane 地址 |
| `ONESTEP_AGENT_REGISTRATION_TOKEN` | 配置文件 `registration_token` | 一次性注册 Token |
| `ONESTEP_WORKER_AGENT_DIR` | `~/.onestep/worker-agent` | 工作目录：身份、部署状态、venv、日志 |
| `ONESTEP_WORKER_AGENT_NAME` | `worker-agent` | 控制台中展示的 Agent 名称 |
| `ONESTEP_WORKER_AGENT_MAX_CONCURRENCY` | `1` | 同时运行的部署数上限 |
| `ONESTEP_WORKER_AGENT_CONFIG_DIR` | `ONESTEP_WORKER_AGENT_DIR` | 配置文件目录，等价于 `--config-dir` |

## 连接器与第三方 SDK

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `AWS_ACCESS_KEY_ID` | 空 | SQS / SNS 插件通过 boto3 标准凭据链读取；EC2/Lambda 上可改用 IAM Role，无需配置 |
| `AWS_SECRET_ACCESS_KEY` | 空 | 同上 |
| `AWS_SESSION_TOKEN` | 空 | 临时凭据（STS）会话 Token |
| `AWS_DEFAULT_REGION` | 空 | boto3 默认区域；资源里显式配置 `region_name` 时优先 |
| `AWS_REGION` | 空 | 与 `AWS_DEFAULT_REGION` 等价，AWS 标准链会同时读取 |
| `AWS_ENDPOINT_URL` | 空 | 覆盖 AWS 端点，用于 LocalStack 或自建兼容服务 |
| `TZ` | 系统本地时区 | 调度时区回退值，见[核心运行时](#核心运行时) |

其他连接器（RabbitMQ、Redis、Kafka、MySQL/PostgreSQL、MongoDB、Elasticsearch、ClickHouse、Feishu）的地址与凭据都通过构造函数参数或 YAML 资源字段传入，不读取 `ONESTEP_*` 环境变量；需要走环境变量时，用 `${VAR}` 展开或 `os.environ` 在代码里读取。

## 本地开发与集成测试

以下变量只在仓库的开发脚本和集成测试中生效，不会影响生产运行时。默认值来自 `scripts/setup-integration-env.sh`：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ONESTEP_PYTHON_BIN` | `.venv/bin/python`，否则 `python3` | 可靠性检查与集成测试使用的解释器 |
| `LOCALSTACK_ENDPOINT` | `http://127.0.0.1:4566` | LocalStack 端点，同时导出为 `AWS_ENDPOINT_URL` |
| `KEEP_INTEGRATION_SERVICES` | `0` | 设为 `1` 时测试结束不销毁 Compose 依赖服务 |
| `ONESTEP_RABBITMQ_URL` | `amqp://guest:guest@127.0.0.1:5672/` | RabbitMQ 集成测试地址 |
| `ONESTEP_RABBITMQ_QUEUE` | `onestep.integration` | RabbitMQ 测试队列 |
| `REDIS_URL` | `redis://127.0.0.1:6379` | Redis Streams 集成测试地址 |
| `ONESTEP_KAFKA_BOOTSTRAP_SERVERS` | `127.0.0.1:9092` | Kafka 集成测试地址 |
| `ONESTEP_KAFKA_TOPIC_PREFIX` | `onestep.integration` | Kafka 测试 topic 前缀 |
| `ONESTEP_SQS_QUEUE_NAME` | `onestep-integration.fifo` | LocalStack 中创建的队列名 |
| `ONESTEP_SQS_QUEUE_URL` | 由脚本创建后导出 | SQS 集成测试队列 URL |
| `ONESTEP_SQS_GROUP_ID` | `workers` | SQS FIFO 消费组 |
| `ONESTEP_MYSQL_HOST` / `ONESTEP_MYSQL_PORT` / `ONESTEP_MYSQL_DATABASE` / `ONESTEP_MYSQL_USER` / `ONESTEP_MYSQL_PASSWORD` | `127.0.0.1` / `3306` / `onestep` / `root` / `root` | MySQL 集成测试连接参数 |
| `ONESTEP_MYSQL_DSN` | 由脚本拼装后导出 | MySQL 集成测试 DSN |
| `ONESTEP_POSTGRES_HOST` / `ONESTEP_POSTGRES_PORT` / `ONESTEP_POSTGRES_DATABASE` / `ONESTEP_POSTGRES_USER` / `ONESTEP_POSTGRES_PASSWORD` | `127.0.0.1` / `5432` / `onestep` / `onestep` / `onestep` | PostgreSQL 集成测试连接参数 |
| `ONESTEP_POSTGRES_DSN` | 由脚本拼装后导出 | PostgreSQL 集成测试 DSN |
| `ONESTEP_CLICKHOUSE_DSN` | `http://default:clickhouse@127.0.0.1:8123/onestep` | ClickHouse 集成测试 DSN |
| `ONESTEP_MONGODB_URI` | `mongodb://127.0.0.1:27017/onestep?replicaSet=rs0` | MongoDB 集成测试 URI |
| `ONESTEP_ELASTICSEARCH_URL` / `ONESTEP_OPENSEARCH_URL` | 无 | Elasticsearch / OpenSearch 集成测试地址 |

控制面冒烟与演示脚本（`scripts/run-control-plane-smoke.sh`、`run-control-plane-demo.sh`）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ONESTEP_CONTROL_PLANE_DIR` | `../onestep-control-plane` | 控制面仓库路径 |
| `ONESTEP_CONTROL_PLANE_SMOKE_TIMEOUT_S` | `45` | 等待控制面启动的超时（秒） |
| `ONESTEP_CONTROL_PLANE_SMOKE_POLL_S` | `1` | 就绪轮询间隔（秒） |
| `ONESTEP_CONTROL_PLANE_WAIT_TIMEOUT_S` | `15` | 演示脚本等待上报数据的超时（秒） |

示例 Compose 文件（如 `examples/prometheus/docker-compose.yml`）使用 `ONESTEP_WORKER_IMAGE` 指定 onestep 服务镜像；示例中默认指向 `ghcr.io/mic1on/onestep-worker` 的一个已发布标签。

## 系统注入的变量

以下变量由框架或控制面在运行期写入子进程，**不要手工设置**：

| 变量 | 注入方 | 说明 |
| --- | --- | --- |
| `ONESTEP_DEPLOYMENT_ID` | Worker Agent supervisor | 当前部署 ID |
| `ONESTEP_WORKER_AGENT_ID` | Worker Agent supervisor | 执行主机 Agent ID |
| `ONESTEP_RUNTIME_INSTANCE_ID` | Worker Agent supervisor | 运行时实例 ID |
| `ONESTEP_INSTANCE_ID` | Worker Agent supervisor | 与上一项同值，供 reporter 直接使用 |
| `ONESTEP_WORKER_REPORTING_TOKEN` | Control Plane（启动部署时注入） | 自定义上报模式下，编译出的 worker.yaml 用 `${ONESTEP_WORKER_REPORTING_TOKEN}` 引用它 |

## 升级注意事项

- 升级后对照 `deploy/env/onestep-app.env.example`、`apps/control-plane/.env.example` 与 `.env.deploy.example` 检查现有配置文件，确认是否有新增或更名的变量。
- 控制面服务端的变量名与 pydantic 字段一一对应，字段改名即变量改名；升级前先核对本文档与对应版本的示例文件。
- 生产环境的 Token、密码、`ONESTEP_CP_CONNECTOR_SECRET` 应放入 systemd `EnvironmentFile`、容器 Secret 或平台密钥管理，不要提交进仓库。

## 下一步

- [生产部署](/guide/deploy) - systemd、Docker、EC2 与 Lambda 部署形态
- [Control Plane](/control-plane/) - reporter 遥测与远程任务控制
- [稳定实例身份](/stable-instance-identity) - `instance_id` 的完整解析规则
- [YAML 任务定义](/yaml-task-definition) - `${VAR}` 展开与 `strict_env` 契约
- [Worker Runtime Image](/guide/worker-runtime-image) - 容器化 YAML worker
