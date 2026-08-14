# 文档更新日志

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
