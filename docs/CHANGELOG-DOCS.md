# 文档更新日志

## 2026-08-22 - 用户案例新增 SQS→MySQL、多连接器协调、FastAPI 调度

### 变更概述

在《用户案例 / 实战篇》原有飞书场景之外新增三篇中英文实战案例，覆盖消息队列落库、多连接器扇出协调、Web 框架下的长任务调度。

### 更新内容

- 新增 [SQS 消息可靠落库到 MySQL](/guide/cases/sqs-to-mysql)（含 [/en](/en/guide/cases/sqs-to-mysql)）：讲清 SQS at-least-once 语义、可见性超时与心跳续期、`upsert`+唯一键幂等落库、`on_fail: leave` 配合 Redrive Policy 走死信队列。
- 新增 [多连接器协调的事件分发管道](/guide/cases/multi-connector-fanout)（含 [/en](/en/guide/cases/multi-connector-fanout)）：一个任务从 Redis Streams 读取，用条件 Sink 路由 + per-sink transform 扇出到 MySQL、HTTP 回调与审计流，说明多 Sink at-least-once 不跨事务、每个目的地必须幂等、终态进入 `dead_letter`。
- 新增 [FastAPI 提交长任务并调度 Worker](/guide/cases/fastapi-execution-scheduling)（含 [/en](/en/guide/cases/fastapi-execution-scheduling)）：基于 PostgreSQL tracked execution 的 API/worker 双进程模型，覆盖 `Idempotency-Key` 提交幂等、租约心跳与 fencing、取消、部署顺序；示例导入统一为 `onestep_sql.postgres`。
- 案例总览与中英文 VitePress 侧边栏均补齐四条案例入口。VitePress 构建通过（0 错误，死链检查有效）。

## 2026-08-22 - 生产部署补充 Docker / Compose / EC2 / Lambda

### 变更概述

在中英文《生产部署》指南中新增 Docker、Docker Compose、AWS EC2、AWS Lambda 四种部署方式，所有命令与 API 均已本地验证。

### 更新内容

- Docker：官方 worker 镜像挂载工作区与派生镜像两种模式（本地 `docker build` + `docker run` 已验证）。
- Docker Compose：`docker-compose.yml` 示例、`up -d`/`logs -f`/`stop` 生命周期，`restart: unless-stopped` 与 `SIGTERM` 优雅关闭（本地 `docker compose up/logs/stop` 已验证任务正常轮询、SIGTERM 优雅退出 exit code 0）。
- AWS EC2：以 systemd 常驻为主，`onestep check` 启动前校验 + `onestep run`，凭据走 env/IAM/SSM；按 source 语义横向扩容说明。
- AWS Lambda：明确不可用 `run()`/`serve()`，改用 `OneStepApp.run_task_once(task, payload=...)` 请求-响应模型；本地验证成功返回结果字典、失败按重试策略重试 3 次后抛出。
- 环境变量表补齐 `APP_TARGET`/`ONESTEP_BIN`/`ONESTEP_TARGET`/`WORKSPACE_DIR`，并区分 systemd 模板与 worker 镜像。
- 两页「下一步」新增 Cloudflare Queues 链接。VitePress 构建通过（0 错误）。

## 2026-08-22 - 同步 main 并新增 Cloudflare Queues 连接器文档

### 变更概述

将 docs 分支同步到 `main` 最新状态（合入 #142 SNS topic sink、#143 control-plane SNS 拓扑、#144 Cloudflare Queues 连接器），并为新连接器补齐中英文文档。

### 更新内容

- 新增中英文 Cloudflare Queues 连接器页 [/broker/cf-queues](/broker/cf-queues) 与 [/en/broker/cf-queues](/en/broker/cf-queues)：基于官方 `cloudflare` Python SDK 的 HTTP 拉取消费者 source/sink，覆盖安装、前置条件（`wrangler queues consumer http add`、Queues Edit token）、`cf_queues`/`cf_queue` 资源类型、语义映射、消息元数据、content-type/base64 编码、`on_fail` 策略、短轮询与无租约续期、限制说明。
- VitePress 侧边栏中英文均在 AWS SQS 之后新增 Cloudflare Queues 条目。
- 中英文 SQS 连接器页补齐 SNS Topic Sink（`sns_topic` 扇出 sink）章节，与 `main` 一致。
- README（中英文）连接器表新增 Cloudflare Queues 行、SQS 行补充 `sns_topic` 扇出说明、拉取任务能力表新增 `cf_queue`；CHANGELOG 与 main 对齐。
- 连接器一致性清单（connector-conformance）新增 Cloudflare Queues 行（source / claim release / acknowledged sink / public errors）。
- VitePress 构建验证通过（0 错误）。

## 2026-08-21 - 同步 main 与 onestep-sql 包名对齐

### 变更概述

将 docs 分支合并到 `main` 的最新代码状态（up to #140），中英文页面统一对齐 `onestep-sql` 规范发行包口径，并补齐迁移指南英文版。

### 更新内容

- 合入 `main` 的 onestep-sql 整合（Phase 0-3，#133-#140）、PostgreSQL async SQLAlchemy 迁移（#128）、per-sink transform bindings（#117/#118）与 `onestep render` 拓扑渲染的代码与设计文档。
- 中英文连接器概览与 MySQL/PostgreSQL 连接器页安装命令统一为 `onestep-sql[mysql]` / `onestep-sql[postgres]`，并补充旧包转发 shim 说明。
- 新增 [Migrate to onestep-sql](/en/guide/migrate-to-onestep-sql) 英文页与导航条目（中文版由 `main` 合入）。
- 中英文 YAML 任务定义的插件资源类型列表更新为 `onestep-sql` 单一 entry point 注册的 14 个资源类型。
- 中英文 PostgreSQL Tracked Execution 安装示例切换到 `onestep-sql[postgres]`（保留 `>=` 下限风格），发布顺序与上线检查清单同步更新。
- MySQL 到飞书多维表格实战案例安装命令更新为 `onestep-sql[mysql]>=0.1.0`。
- 版本号口径（`1.11.0`、worker 镜像标签）保持 docs 分支审计结果。
- VitePress 构建验证通过（0 错误）。

## 2026-08-19 - 全站审计、版本号更新与 en 翻译补齐

### 变更概述

全站扫描 zh-CN 和 en-US 页面，修复版本号过时、内容漂移、链接错误和 en 页面缺失段落问题。

### 更新内容

- 版本号从 `1.9.0` 更新为 `1.11.0`：快速开始页、Docker 镜像标签（ghcr.io/mic1on/onestep-worker:1.11.0）、连接器插件最低版本要求。
- PostgreSQL 跟踪执行安装示例从固定 `==1.9.0` 改为 `>=1.9.0`。
- [MySQL](/en/broker/mysql) en 页面补齐缺失内容：Update Mode（`mode="update"`）、Per-Column Write Policies（`skip_null`/`backfill`/`overwrite`）、Update Control 节更新为覆盖 upsert 和 update 两种模式。
- 修复 en 实战篇锚点链接：`#field-mapping` → `#field-conversion`，`#reliable-persistent-cursor-and-retry` → `#reliable-persistent-cursor-with-retry`。
- 新增 en 缺失页面 [Tags](/en/tags)。
- VitePress 构建验证通过（0 错误）。

## 2026-08-19 - 新增 onestep render 拓扑渲染文档

### 变更概述

将 docs 分支同步到 `main` 的最新代码状态（PR #122、PR #123），补充 `onestep render` Mermaid 拓扑渲染命令的文档。

### 更新内容

- [生产部署](/guide/deploy) 与 [Production Deploy](/en/guide/deploy) 新增"渲染 worker 拓扑"章节：CLI 清单加入 `onestep render`，覆盖 Mermaid 输出示例、边标签语义（`emit`/`when`/`otherwise`/`dead_letter`）和共享资源去重行为。
- [特性](/guide/features) 与 [Features](/en/guide/features) 在任务编排章节补充用 `onestep render` 快速验证链式接线。
- [YAML 任务定义](/yaml-task-definition) 与 [YAML Task Definition](/en/yaml-task-definition) 新增"可视化拓扑"章节，说明与 strict 校验在 CI 中搭配使用及 `--env-file` 环境变量展开。
- 同步 `main` 上 `onestep-mysql` 表 Sink 按列空值写入策略的设计文档（PR #123）。

## 2026-08-17 - 新增 MySQL 到飞书多维表格实战案例

### 变更概述

新增匿名化的订单流水同步实战篇，并同步 `onestep-mysql 0.5.1` 的 `DATETIME`
游标持久化与恢复说明。

### 更新内容

- 新增 [用户案例 / 实战篇](/guide/cases/) 与
  [MySQL 订单流水同步到飞书多维表格](/guide/cases/mysql-feishu-order-sync)，覆盖完整
  strict YAML、handler 契约、单写者限制、批量参数、观测事件和安全恢复流程。
- [MySQL](/broker/mysql) 文档说明 `0.5.1` 对 `DATETIME` 复合游标的兼容行为：
  保留微秒恢复、无需迁移游标表，且提交失败时不应手工推进游标。
- [Feishu Bitable](/broker/feishu-bitable) 文档链接高吞吐 Insert 键索引的完整实战。
- `example/mysql_feishu_insert.yaml` 和连接器示例统一为匿名订单流水命名。

## 2026-08-14 - 同步 Feishu Bitable 关联解析器

### 变更概述

将 docs 分支同步到 `main` 的最新代码状态（PR #112），补充 Feishu Bitable 关联字段解析器文档。

### 更新内容

- [Feishu Bitable](/broker/feishu-bitable) 文档新增 `relations` 关联字段章节，覆盖声明式业务键到关联记录 ID 的解析、`error`/`empty`/`create` 三种缺失策略、跨 Base 关联和并发创建保护。
- 新增关联解析器设计文档和实现计划。
- 同步 `onestep-feishu-bitable` 插件 README 更新至 0.2.0。
- 合入 `onestep-feishu-bitable` 0.2.0 发布与 `deploy-docs.yml` CI 修复。

## 2026-08-11 - 同步 1.9.0 与新连接器文档

### 变更概述

将 docs 分支同步到 `main` 的 1.9.0 代码状态，补充 v1.8.0 和 v1.9.0 的全部新增功能和连接器文档。

### 更新内容

- 快速开始页版本号更新为 `1.9.0`，部署与 Worker Runtime Image 示例同步到 `ghcr.io/mic1on/onestep-worker:1.9.0`。
- 新增 [MongoDB](/broker/mongodb)、[Elasticsearch / OpenSearch](/broker/elasticsearch) 和 [ClickHouse](/broker/clickhouse) 连接器页面。
- 连接器概览、Connector 表及首页 features 列表补充新增插件。
- 导航新增 [Connector Conformance](/connector-conformance) 页面。
- 核心概念页面新增 Managed Execution 章节，覆盖 `ExecutionClient`/`ExecutionBackend`/`PostgresExecutionSource` 架构、状态机、租约和可靠性。
- 合入本地 handler loop、YAML `cancel_requested` 状态、连接器 conformarce 测试套件、diagnostics 和 failure capture 框架。

## 2026-07-27 - 同步 1.7.2 与 CLI 日志能力

### 变更概述

将 docs 分支同步到 `main` 的 1.7.2 代码状态，补充 CLI 托管日志、任务生命周期事件和嵌入式运行的配置边界。

### 更新内容

- 快速开始页版本号更新为 `1.7.2`，说明应用 logger 名称不要求使用 `onestep` 前缀。
- 新增日志与任务事件指南，覆盖 `--log-level`、`--no-task-events`、YAML 优先级和宿主 handler 保留规则。
- 事件页区分 CLI 自动注册与 `app.run()` / `app.serve()` 嵌入式运行。
- 部署与 Worker Runtime Image 示例同步到 `ghcr.io/mic1on/onestep-worker:1.7.2`。
- 同步 Worker Agent 迁入 `apps/work-agent` 后的源码、测试和发布工作流。

## 2026-07-21 - 同步 1.7.1 与服务描述文档

### 变更概述

将 docs 分支同步到 `main` 的 1.7.1 代码与文档状态，并补充 Control Plane 服务级描述的配置说明。

### 更新内容

- 快速开始页版本号更新为 `1.7.1`。
- 部署与 Worker Runtime Image 示例同步到 `ghcr.io/mic1on/onestep-worker:1.7.1`。
- YAML 任务定义文档补充 `reporter.service_description`、`ONESTEP_SERVICE_DESCRIPTION` 和 `tasks[].description` 的边界。
- Control Plane 页面补充服务描述配置、环境变量和 `reporter: true` 的兼容说明。
- 合入 resource catalog、control-plane reporter plugin 发布和插件拓扑字段更新。

## 2026-07-20 - 同步 1.6.0 与新插件文档

### 变更概述

将 docs 分支同步到 `main` 的 1.6.0 代码与文档状态，并更新文档站入口、导航和连接器页面。

### 更新内容

- 快速开始页版本号更新为 `1.6.0`，补充 PostgreSQL、Kafka、control-plane 和 `onestep build`。
- 连接器导航新增 PostgreSQL 与 Kafka，并补充 MySQL binlog CDC 描述。
- 部署与 Worker Runtime Image 页面同步到 `ghcr.io/mic1on/onestep-worker:1.6.0`。
- 新增 [PostgreSQL](/broker/postgres) 与 [Kafka](/broker/kafka) 连接器页面。
- 导航新增 [核心可靠性](/core-reliability)，指向 at-least-once、ack/retry 和插件兼容契约。

## 2026-03-17 - 全面迁移到 1.0.0 API

### 变更概述

将文档全面更新为 onestep 1.0.0 版本，旧版 0.5.x 文档已归档到 `v0.5.x/` 目录。

### 新增文档

#### 指南 (guide/)
- ✅ `index.md` - 快速开始（5 分钟上手）
- ✅ `features.md` - 功能特性总览
- ✅ `tutorial.md` - 入门教程（使用 1.0.0 API）

#### 核心 (core/)
- ✅ `index.md` - 核心概念（OneStepApp/Source/Sink/Delivery）
- ✅ `connector.md` - 连接器详解
- ✅ `retry.md` - 重试策略（MaxAttempts、自定义策略）
- ✅ `middleware.md` - 事件钩子（替代旧版中间件）

#### Broker (broker/)
- ✅ `index.md` - Broker 索引和选择指南
- ✅ `memory.md` - 内存队列
- ✅ `rabbitmq.md` - RabbitMQ 完整示例
- ✅ `mysql.md` - MySQL 表队列/增量同步
- ✅ `webhook.md` - Webhook 接收
- ✅ `cron.md` - Cron 和 Interval 定时器
- ✅ `sqs.md` - AWS SQS 集成
- ✅ `custom.md` - 自定义 Broker 实现

### 归档文档 (v0.5.x/)

以下旧版文档已移至 `v0.5.x/` 目录：

```
v0.5.x/
├── index.md              # v0.5.x 文档入口（含弃用警告）
├── guide/
│   └── tutorial.md       # 旧版@step API 教程
├── core/
│   ├── broker.md         # 旧版 Broker 概念
│   ├── middleware.md     # 旧版中间件
│   └── retry.md          # 旧版重试策略
└── broker/
    ├── rabbitmq.md       # 旧版 RabbitMQBroker
    ├── cron.md           # 旧版 CronBroker
    ├── webhook.md        # 旧版 WebHookBroker
    ├── redis.md          # 旧版 RedisBroker
    └── kafka.md          # 占位符 (TODO)
```

### API 变更对比

| 概念 | 0.5.x | 1.0.0 |
|------|-------|-------|
| 应用定义 | `@step` | `app = OneStepApp()` + `@app.task()` |
| 消息来源 | `from_broker=` | `source=` |
| 消息输出 | `to_broker=` | `emit=` |
| 并发控制 | `workers=` | `concurrency=` |
| 启动方式 | `step.start()` | `app.run()` / `onestep run` |
| 重试策略 | `TimesRetry` 等 | `MaxAttempts` |
| 中间件 | `BaseMiddleware` | 事件钩子 (`@app.on_event`) |
| 状态管理 | ❌ | `ctx.state` |
| 配置管理 | ❌ | `ctx.config` |
| 生命周期 | 有限 | `@app.on_startup/shutdown` |

### 示例代码对比

#### 定时任务

**0.5.x:**
```python
from onestep import step, CronBroker

@step(from_broker=CronBroker("* * * * * */3"))
def cron_task(message):
    print(message)

step.start(block=True)
```

**1.0.0:**
```python
from onestep import CronSource, OneStepApp

app = OneStepApp("demo")

@app.task(source=CronSource("*/3 * * * *"))
async def cron_task(ctx, _):
    print(ctx.current.meta)

app.run()
```

#### 消息队列处理

**0.5.x:**
```python
from onestep import step, RabbitMQBroker

rmq = RabbitMQBroker("queue", {"username": "admin", "password": "admin"})

@step(from_broker=rmq, to_broker=rmq2)
def process(message):
    return message.body

step.start(block=True)
```

**1.0.0:**
```python
from onestep import OneStepApp, RabbitMQConnector

app = OneStepApp("demo")
rmq = RabbitMQConnector("amqp://admin:admin@localhost/")

@app.task(
    source=rmq.queue("queue"),
    emit=rmq.queue("results"),
    concurrency=8
)
async def process(ctx, item):
    return item

app.run()
```

### 下一步

- [ ] 添加部署指南文档 (`guide/deploy.md`)
- [ ] 添加 Control Plane 集成文档
- [ ] 添加更多示例代码
- [ ] 补充 API 参考文档

### 迁移建议

1. **新用户**: 直接阅读主文档 (`/guide/`)
2. **老用户**: 查看 [MIGRATION-0.5-to-1.0.0.md](https://github.com/mic1on/onestep/blob/main/MIGRATION-0.5-to-1.0.0.md)
3. **维护旧项目**: 参考 `v0.5.x/` 文档

---

更新时间：2026-03-17
