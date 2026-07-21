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
- **可插拔连接器**：内存、MySQL、RabbitMQ、Redis、SQS、Kafka、飞书多维表格
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

## 能做什么

| 能力 | 入口 |
| --- | --- |
| **拉取任务**：队列、调度、Webhook、DB 游标 | `MemoryQueue`、`IntervalSource`、`CronSource`、`WebhookSource`、MySQL `table_queue` / `incremental` / binlog、RabbitMQ `queue`、Redis `stream`、SQS `queue`、Kafka `kafka_topic` |
| **输出结果**：写入下游 sink | 任意 source 也可作 sink；MySQL `table_sink`；Kafka `kafka_topic`；HTTP `http_sink`；飞书多维表格 sink |
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
| **MySQL** | `table_queue`、`incremental`、binlog CDC、`table_sink`、state/cursor store | `pip install onestep-mysql` |
| **PostgreSQL** | 与 MySQL 对等的原语，后端为 PostgreSQL | `pip install onestep-postgres` |
| **RabbitMQ** | `queue`，支持 exchange/routing-key 绑定与 prefetch | `pip install onestep-mq` |
| **Redis** | `stream`，支持消费组、`XACK`、`XCLAIM`、`maxlen` | `pip install onestep-redis` |
| **SQS** | `queue`，支持批量删除与心跳可见性续期 | `pip install onestep-sqs` |
| **Kafka** | `kafka_topic` source/sink，使用手动 offset commit | `pip install onestep-kafka` |
| **飞书多维表格** | 增量 source 与 upsert sink | `pip install onestep-feishu-bitable` |

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
[`onestep-control-plane`](../onestep-control-plane) 上报运行时遥测（心跳、
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
- [`docs/stable-instance-identity.md`](docs/stable-instance-identity.md) ——
  Reporter 身份解析
- [`docs/agent-ws-protocol.md`](docs/agent-ws-protocol.md) —— Agent WS 协议
- [`deploy/`](deploy/README.md) —— 部署模板

## License

MIT
