# onestep

<div align=center><img src="https://onestep.code05.com/logo-3.svg" width="300"></div>
<div align=center>
<a href="https://pypi.org/project/onestep" target="_blank">
    <img src="https://img.shields.io/pypi/pyversions/onestep.svg" alt="Supported Python versions">
</a>
</div>

[English](README.md) | [简体中文](README.zh-CN.md)

<hr />

**onestep** 是一个轻量的异步任务运行时，适用于队列、轮询、定时调度和
Webhook 场景。你只需用 `source` 和可选的 `sink` 声明一个任务，运行时会自动
处理拉取、并发、重试、死信和遥测上报。

- **一个装饰器**，把任意 async 函数变成被托管的任务
- **可插拔连接器**：内存、MySQL、RabbitMQ、Redis、SQS、Kafka、
  Elasticsearch/OpenSearch、ClickHouse、MongoDB、飞书多维表格
- **多种调度方式**：间隔、Cron、Webhook、基于数据库的队列
- **生产可用**：重试、死信、超时、状态存储、指标、控制面 Reporter
- **两种配置方式**：纯 Python，或声明式 YAML
- 支持 Python 3.9+

## 快速开始

安装：

```bash
pip install onestep
# 可选扩展：
pip install 'onestep[yaml]'          # YAML 任务定义
pip install 'onestep[control-plane]' # 向 onestep-control-plane 上报遥测
pip install 'onestep[kafka]'         # Kafka topic source/sink，Python 3.10+
pip install 'onestep[elasticsearch]' # Elasticsearch/OpenSearch bulk sink
pip install 'onestep[clickhouse]'    # ClickHouse 表 sink
pip install 'onestep[mongodb]'       # MongoDB 轮询、变更流和 sink
```

定义一个 app，然后用 `onestep` CLI 运行：

```python
from onestep import IntervalSource, OneStepApp

app = OneStepApp("billing-sync")


@app.task(source=IntervalSource.every(hours=1, immediate=True, overlap="skip"))
async def sync_billing(ctx, _):
    print("syncing billing data")
```

```bash
onestep run your_package.tasks:app
onestep check your_package.tasks:app   # 启动前校验目标
```

### 日志

`onestep run` 默认以 INFO 级别将应用日志、框架日志和任务生命周期事件写到
stdout。业务 logger 名称不需要以 `onestep` 开头，应用也不再需要调用
`logging.basicConfig(force=True)` 或注册标准 `StructuredEventLogger`：

```python
import logging

logger = logging.getLogger("billing.kpi_sync")
```

需要查看 fetched、started 和 sink 成功等细节时使用 `--log-level DEBUG`；
不需要 CLI 自动输出任务事件时使用 `--no-task-events`：

```bash
onestep run your_package.tasks:app --log-level DEBUG
onestep run your_package.tasks:app --no-task-events
```

显式 `--log-level` 的优先级高于目标加载时配置的级别，包括 YAML 的
`app.logging.level`。未传该参数时保留目标已配置的级别，否则默认使用 INFO。
CLI 自行安装 stdout handler 时，解析后的级别同时适用于任意名称的业务 logger
和 `onestep` 命名空间。已有 logging handler 和自定义 `StructuredEventLogger`
不会被覆盖或重复注册；宿主已配置 handler 时，也继续控制自己的 root level。

直接调用 `app.run()` 或 `app.serve()` 不会修改宿主进程日志，也不会自动安装
任务事件 logger；嵌入式应用仍完全控制自己的日志配置。

## 本地任务诊断

无需启动 worker 或控制面，即可用 JSON 执行一次任务，或重放捕获的失败：

```bash
onestep task run your_package.tasks:app --task sync_billing --input input.json
onestep task replay your_package.tasks:app --task sync_billing --envelope captures/failure.json
onestep check your_package.tasks:app --connect
```

诊断会执行真实的 handler、任务 hooks、重试决策和 sink 路由。默认不调用 sink；
传入 `--send` 后才会打开、发送并关闭选中的 sink。无论哪种模式，handler 和 hook
仍可能产生外部副作用。`--timeout` 默认 60 秒，并通过独立子进程限制整体执行
时间，同步阻塞代码也在限制范围内。

`delivery_action` 始终是预测，因为 source 的 `ack`/`retry`/`fail` 是合成动作。
dry-run 中的 `would_dead_letter` 表示“若死信 sink 发布成功，则会进入死信”；使用
`--send` 才能观察实际发布结果。`--send` 过程中被超时强杀，可能留下部分外部
写入，后续重试也可能产生重复。

`check --connect` 只对同时具有可调用 `open()` 和 `close()` 的资源执行生命周期
探测。不具备该生命周期的 state/cursor store 会报告为 `not_probeable`；命令不会
用 `load()`、`save()` 或 `delete()` 进行连接探测。

失败捕获是显式启用的生产能力：

```python
from onestep import FailureCaptureConfig, OneStepApp

app = OneStepApp(
    "billing-sync",
    failure_capture=FailureCaptureConfig(
        directory="captures",
        mode="terminal",
        redact_paths=("/body/customer/token",),
    ),
)
```

捕获文件带版本、使用私有权限并原子写入，且拒绝有损序列化。`terminal` 只记录
最终有效的终止失败；`all` 还会记录可重试 attempt。datetime、UUID、bytes、
Decimal、enum、tuple/namedtuple、set 和 frozenset 等常见值可无损往返。遇到不
支持的自定义值时会明确记录 capture 错误且不生成有损文件。YAML 策略见
[`docs/yaml-task-definition.md`](docs/yaml-task-definition.md)。

## 渲染 worker 拓扑

`onestep render` 将任意 Python 或 YAML 目标的拓扑输出为
[Mermaid](https://mermaid.js.org) 流程图，可直接粘贴到 GitHub、Notion 或
Obsidian 中渲染：

```bash
onestep render worker.yaml
```

```text
graph LR
  %% app: billing-sync
  n0["extract_entities<br/>concurrency=4 · retry=NoRetry · timeout=300s"]
  n1["sqs-orders<br/>MemoryQueue"]
  n2["mysql.meta_sink<br/>MemoryQueue"]
  n1 --> n0
  n0 -->|"emit"| n2
```

任务节点标注并发数、重试策略和超时。边的标签为 `emit`（绑定了 transform 时附
带 transform 引用）、条件路由的 `when`/`otherwise`，以及虚线的 `dead_letter`。
被多个任务共享的资源只绘制一次，链式拓扑会呈现为连通图。

## 能做什么

| 能力 | 入口 |
| --- | --- |
| **拉取任务**：队列、调度、Webhook、DB 游标 | `MemoryQueue`、`IntervalSource`、`CronSource`、`WebhookSource`、MySQL `table_queue` / `incremental` / binlog、RabbitMQ `queue`、Redis `stream`、SQS `queue`、Cloudflare `cf_queue`、Kafka `kafka_topic`、MongoDB `mongodb_polling` / `mongodb_change_stream` |
| **输出结果**：写入下游 sink | 任意 source 也可作 sink；MySQL `table_sink`；Kafka `kafka_topic`；Elasticsearch/OpenSearch `elasticsearch_bulk_sink`；ClickHouse `clickhouse_table_sink`；MongoDB `mongodb_collection_sink`；HTTP `http_sink`；飞书多维表格 sink |
| **定时调度**：周期任务 | `IntervalSource.every(...)`、`CronSource(...)`，支持重叠控制（`allow` / `skip` / `queue`） |
| **接收外部事件** | `WebhookSource`，支持 Bearer 鉴权、共享监听、多种 body 解析 |
| **容错**：重试、死信、超时 | 重试策略、`dead_letter` sink、单任务 `timeout_s`、失败分类（`error` / `timeout` / `cancelled`） |
| **状态管理** | `InMemoryStateStore`、MySQL state/cursor store；`ctx.state` 按任务命名空间隔离 |
| **可观测** | `@app.on_event` 钩子、`InMemoryMetrics`、`StructuredEventLogger`、执行事件 |
| **远程控制** | 控制面 Reporter，支持远程命令：`ping`、`shutdown`、`restart`、`drain`、`pause_task`、`resume_task`、`sync_now` |

## 核心概念

整个运行时只围绕四个抽象：

- **`OneStepApp`** —— 任务注册表与生命周期管理器
- **`Source`** —— 从队列、调度、Webhook 或轮询后端拉取数据
- **`Sink`** —— 把处理结果发布到下游
- **`Delivery`** —— 单个被拉取到的数据项，提供 `ack` / `retry` / `fail`

```python
from onestep import MemoryQueue, OneStepApp

app = OneStepApp("demo")
source = MemoryQueue("incoming")
sink = MemoryQueue("processed")


@app.task(source=source, emit=sink, concurrency=4)
async def double(ctx, item):
    return {"value": item["value"] * 2}


async def main():
    await source.publish({"value": 21})
    await app.serve()
```

## 连接器

每个后端都以独立包形式提供，按需安装：

| 包 | 提供 | 安装 |
| --- | --- | --- |
| **核心** | `MemoryQueue`、`IntervalSource`、`CronSource`、`WebhookSource`、`http_sink`、运行时、Reporter | `pip install onestep` |
| **MySQL** | `table_queue`、`incremental`、binlog CDC、`table_sink`、state/cursor store | `pip install 'onestep-sql[mysql]'`（`onestep-mysql` shim 仍可用） |
| **PostgreSQL** | 与 MySQL 对等的原语，后端为 PostgreSQL | `pip install 'onestep-sql[postgres]'`（`onestep-postgres` shim 仍可用） |
| **RabbitMQ** | `queue`，支持 exchange/routing-key 绑定与 prefetch | `pip install onestep-mq` |
| **Redis** | `stream`，支持消费组、`XACK`、`XCLAIM`、`maxlen` | `pip install onestep-redis` |
| **SQS** | `queue`，支持批量删除与心跳可见性续期，并提供 `sns_topic` 扇出 sink | `pip install onestep-sqs` |
| **Cloudflare Queues** | `cf_queue` HTTP 拉取消费者 source/sink（官方 `cloudflare` SDK），支持批量 lease ack/retry | `pip install 'onestep[cloudflare]'`（`onestep-cf-queues`） |
| **Kafka** | `kafka_topic` source/sink，使用手动 offset commit | `pip install onestep-kafka` |
| **飞书多维表格** | 增量 source 与 upsert sink | `pip install onestep-feishu-bitable` |
| **Elasticsearch/OpenSearch** | `elasticsearch` 连接器和基于共同 REST bulk 边界、等待确认的 `elasticsearch_bulk_sink` | `pip install 'onestep[elasticsearch]'`（`onestep-elasticsearch`） |
| **ClickHouse** | `clickhouse` 连接器和向现有表执行确认写入的 `clickhouse_table_sink` | `pip install 'onestep[clickhouse]'`（`onestep-clickhouse`） |
| **MongoDB** | `mongodb_polling`、原始 `mongodb_change_stream` 事件和 insert/upsert `mongodb_collection_sink` | `pip install 'onestep[mongodb]'`（`onestep-mongodb`） |

三个数据库 bulk sink 都接受一个 mapping 或非空 mapping 序列，并等待每个后端
分块确认。onestep 仍采用 at-least-once 语义：重试可能重复已经提交的数据项或
分块；对重复敏感时，应使用稳定文档 ID、upsert key，或支持去重的 ClickHouse
表结构。若部分提交后的最终写入集合无法确定，错误会分类为 `UNCERTAIN`，不会
被自动重放。

Elasticsearch 插件面向 Elasticsearch/OpenSearch 共同的 HTTP bulk 能力，不以
任一厂商 Python client 作为兼容边界。MongoDB 轮询和变更流在开发环境可以使用
内存状态，但生产环境的重启保证要求显式配置 durable cursor store；变更流输出
原始事件，默认 `full_document: updateLookup`。

或一次性安装全部：

```bash
pip install 'onestep[all]'
```

## 配置方式

### 纯 Python

适合应用代码，每个连接器就是一个可实例化的类：

```python
from onestep import OneStepApp
from onestep_redis import RedisConnector

app = OneStepApp("redis-demo")
redis = RedisConnector("redis://localhost:6379")
source = redis.stream("jobs", group="workers", batch_size=100)
out = redis.stream("processed")


@app.task(source=source, emit=out, concurrency=8)
async def process_job(ctx, item):
    return {"job": item["job"], "status": "done"}
```

### YAML

适合部署编排。业务逻辑留在 Python，YAML 只声明运行时 —— app、resources、
hooks、tasks。

```yaml
app:
  name: billing-sync

resources:
  tick:
    type: interval
    minutes: 5
    immediate: true

tasks:
  - name: sync_billing
    source: tick
    handler:
      ref: your_package.handlers.billing:sync_billing
```

```bash
onestep run worker.yaml
onestep check --strict worker.yaml   # schema 校验、未知字段检测
onestep render worker.yaml           # 以 Mermaid 图渲染 worker 拓扑
onestep init billing-sync            # 脚手架生成最小 YAML 工程
onestep build worker.yaml --out dist/worker.zip
```

完整的 YAML schema、资源类型、条件路由、状态绑定，见
[`docs/yaml-task-definition.md`](docs/yaml-task-definition.md)。

### 构建可部署 worker 包

`onestep build` 会把 YAML worker 工程打成可由 worker agent 下载运行的 zip。
它会先校验目标，然后收集 YAML 入口、handler/hook/条件路由引用到的本地
Python 模块、`pyproject.toml`、`requirements.txt`、`uv.lock` 等依赖声明文件、
README 和 license 等打包元数据文件，并把 `onestep-package.json` manifest
写入 zip。

```bash
onestep build worker.yaml --strict --out dist/worker.zip
```

无法从 Python 引用自动推断的文件，可以在 `pyproject.toml` 中添加构建提示：

```toml
[tool.onestep.build]
entrypoint = "worker.yaml"
include = ["templates/**"]
exclude = ["templates/private/**"]
```

使用 `--env-file .env` 可以为构建前校验提供本地环境变量值。`.env` 文件默认
不会进入包；部署时配置应由 worker agent 或控制面提供。package manifest 会记录
入口文件，兼容的控制面上传接口可以自动读取；上传到旧控制面时，请显式传入同一个
entrypoint。使用 `--json` 可以输出适合自动化流程消费的构建报告。

## 部署

- **systemd** —— 最小 unit + 启动前校验模板，见
  [`deploy/`](deploy/README.md)
- **官方 worker 镜像** —— 无需打包即可在 Docker 中运行 YAML worker：
  ```bash
  docker run --rm \
    -e ONESTEP_TARGET=/workspace/worker.yaml \
    -v "$PWD:/workspace" \
    ghcr.io/mic1on/onestep-worker:1.7.1
  ```
  详见 [`deploy/worker-runtime-image.md`](deploy/worker-runtime-image.md)。
- **嵌入 Web 应用** —— FastAPI/Django 的推荐形态，见
  [`deploy/web-service-integration.md`](deploy/web-service-integration.md)。

## 控制面

`onestep` 可通过单条 WebSocket 长连接向
[`onestep-control-plane`](apps/control-plane) 应用上报运行时遥测（心跳、
拓扑、指标、事件），并接收远程命令 —— 无需新增连接器或改动任务代码。

```yaml
app:
  name: billing-sync

reporter: true
```

必需环境变量：`ONESTEP_CONTROL_PLANE_URL`、`ONESTEP_CONTROL_PLANE_TOKEN`。
可选：设置 `reporter.service_description` 或 `ONESTEP_SERVICE_DESCRIPTION` 后，
Control Plane 会在服务目录展示服务描述。

身份解析、多副本指引、环境变量、本地 demo，见
[`docs/stable-instance-identity.md`](docs/stable-instance-identity.md)。

## 示例

可运行示例见 [`example/`](example/README.md)。推荐入口：

```bash
# 每 5 秒触发的间隔任务
SYNC_INTERVAL_SECONDS=5 PYTHONPATH=src onestep run example.cli_app:app

# 端到端：webhook -> 队列 -> worker -> 死信，含指标与结构化日志
PYTHONPATH=src python3 example/runtime_showcase.py
```

## 升级

`1.0.0` 是一次运行时重写。如果你从 `0.5.x` 升级，请参阅
[`MIGRATION-0.5-to-1.0.0.md`](MIGRATION-0.5-to-1.0.0.md)，包含新旧 API
映射、不再支持的特性，以及灰度建议。

## 更多

- [`docs/yaml-task-definition.md`](docs/yaml-task-definition.md) —— YAML schema
- [`docs/core-reliability.md`](docs/core-reliability.md) —— 稳定 API、
  交付语义、插件兼容性与发布检查清单
- [`docs/framework-evolution-roadmap.md`](docs/framework-evolution-roadmap.md) ——
  框架演进顺序、里程碑与退出标准
- [`docs/stable-instance-identity.md`](docs/stable-instance-identity.md) ——
  Reporter 身份解析
- [`docs/agent-ws-protocol.md`](docs/agent-ws-protocol.md) —— Agent WS 协议
- [`deploy/`](deploy/README.md) —— 部署模板

## License

MIT
