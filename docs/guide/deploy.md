---
title: 生产部署 | 指南
---

# 生产部署

## CLI 部署入口

`onestep` CLI 是生产环境的部署入口点。

### 推荐模块结构

```python
# tasks.py
from onestep import IntervalSource, OneStepApp

app = OneStepApp("billing-sync")


@app.task(source=IntervalSource.every(hours=1, immediate=True, overlap="skip"))
async def sync_billing(ctx, _):
    print("syncing billing data")
```

### 运行应用

```bash
# 标准运行
onestep run your_package.tasks:app

# 简写形式
onestep your_package.tasks:app

# 检查配置
onestep check your_package.tasks:app

# JSON 输出（适合 CI/CD）
onestep check --json your_package.tasks:app

# 渲染 worker 拓扑（Mermaid 图）
onestep render your_package.tasks:app
```

`onestep run` 默认把 INFO 级别的应用日志和任务生命周期事件写到 stdout，适合由 systemd、Docker 或日志采集器接管。使用 `--log-level DEBUG` 查看更详细的 fetched、started 和 sink-success 事件，或使用 `--no-task-events` 关闭自动任务事件。完整规则见 [日志与任务事件](/guide/logging)。

### 渲染 worker 拓扑

`onestep render` 把任意 Python 或 YAML 目标的拓扑输出为 Mermaid 流程图，可直接粘贴到 GitHub README、Notion 或 Obsidian 中渲染：

```bash
onestep render worker.yaml                  # 默认输出 Mermaid
onestep render pkg.tasks:app --format mermaid
```

```text
graph LR
  %% app: billing-sync
  n0["extract_entities<br/>concurrency=4 · retry=NoRetry · timeout=300s"]
  n1["sqs-orders<br/>MemoryQueue"]
  n2["audit-log<br/>MemoryQueue"]
  n3["mysql.meta_sink<br/>MemoryQueue"]
  n1 --> n0
  n0 -->|"emit"| n2
  n0 -->|"when app.predicates:is_valid · app.transforms:to_meta"| n3
```

任务节点标注并发数、重试策略和超时；边标签为 `emit`（绑定了 transform 时附带引用）、条件路由的 `when`/`otherwise`，以及虚线的 `dead_letter`。被多个任务共享的资源只绘制一次，链式拓扑会呈现为连通图。YAML 目标同样支持 `--env-file` 与 `--strict-env`。

## systemd 部署

完整的部署模板位于：

- `deploy/README.md`
- `deploy/systemd/onestep-app.service`
- `deploy/env/onestep-app.env.example`
- `deploy/bin/onestep-preflight.sh`

### 安装步骤

```bash
# 创建配置目录
sudo mkdir -p /etc/onestep

# 复制环境变量模板
sudo cp deploy/env/onestep-app.env.example /etc/onestep/onestep-app.env

# 复制 systemd 服务文件
sudo cp deploy/systemd/onestep-app.service /etc/systemd/system/onestep-app.service

# 重载 systemd
sudo systemctl daemon-reload

# 启用并启动服务
sudo systemctl enable --now onestep-app
```

### 查看状态和日志

```bash
# 查看服务状态
sudo systemctl status onestep-app

# 查看日志
sudo journalctl -u onestep-app -f
```

## Docker 部署

官方 worker 镜像内置 `onestep[all]` 与启动脚本，会依次执行 `onestep check` 和 `onestep run`。详见 [Worker Runtime Image](/guide/worker-runtime-image)。

### 挂载工作区运行

```bash
docker run --rm \
  -e ONESTEP_TARGET=/workspace/worker.yaml \
  -v "$PWD:/workspace" \
  ghcr.io/mic1on/onestep-worker:1.11.0
```

启动脚本行为：

1. 把 `/workspace` 和 `/workspace/src` 加入 `PYTHONPATH`
2. 若存在 `/workspace/requirements.txt` 则安装其依赖；否则若存在 `/workspace/pyproject.toml` 则安装当前项目
3. 运行 `onestep check`，通过后运行 `onestep run`

### 派生镜像（推荐用于生产）

把代码和 YAML 固化进镜像，避免运行时挂载和联网装依赖：

```dockerfile
FROM ghcr.io/mic1on/onestep-worker:1.11.0

WORKDIR /workspace
COPY . /workspace
ENV ONESTEP_TARGET=/workspace/worker.yaml
```

```bash
docker build -t my-worker .
docker run --rm my-worker
```

worker 是长驻进程，`onestep run` 把 INFO 日志和任务事件写到 stdout，交给容器日志驱动采集即可。若 YAML 使用镜像未内置的插件（如 `onestep-feishu-bitable`），在工作区的 `requirements.txt` 或 `pyproject.toml` 中声明。

## Docker Compose 部署

```yaml
# docker-compose.yml
services:
  worker:
    image: ghcr.io/mic1on/onestep-worker:1.11.0
    environment:
      ONESTEP_TARGET: /workspace/worker.yaml
    volumes:
      - ./:/workspace
    restart: unless-stopped
```

```bash
docker compose up -d          # 后台启动
docker compose logs -f worker # 跟随日志
docker compose stop worker    # 发送 SIGTERM，触发优雅关闭
```

生产环境建议改为派生镜像（`build:` 指向包含代码和 YAML 的 Dockerfile），而不是挂载源码目录。`restart: unless-stopped` 让 worker 在异常退出后自动重启；`docker compose stop` 发送 `SIGTERM`，`OneStepApp` 将其作为正常关闭请求处理，等待 inflight 任务完成。

多连接器 worker 可以在同一个 compose 文件里与其依赖（如 RabbitMQ、Redis）一起编排，通过 `depends_on` 控制启动顺序，用环境变量注入 DSN/令牌。

## AWS EC2 部署

EC2 上以 systemd 常驻运行是推荐方式（见上文「systemd 部署」）。典型步骤：

1. 准备实例：安装与 onestep 兼容的 Python（3.9+），克隆应用仓库到 `/srv/onestep-app`，在 `/srv/onestep-app/.venv` 建虚拟环境并 `pip install` 应用及所需插件。
2. 配置服务：

   ```bash
   sudo mkdir -p /etc/onestep
   sudo cp deploy/env/onestep-app.env.example /etc/onestep/onestep-app.env
   # 编辑 APP_CWD / APP_TARGET / ONESTEP_BIN
   sudo cp deploy/systemd/onestep-app.service /etc/systemd/system/onestep-app.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now onestep-app
   ```

3. `ExecStartPre` 会先跑 `onestep check` 做启动前校验，失败则不启动；`ExecStart` 再执行 `onestep run`。单元里 `Restart=on-failure` 负责崩溃自愈，`TimeoutStopSec=45` 给优雅关闭留出时间。

也可以在 EC2 上直接跑上面的 Docker / Docker Compose 方案（安装 Docker Engine 后即可），适合已经容器化的团队。无论哪种方式，把凭据放进 `/etc/onestep/*.env` 或实例的 IAM Role / SSM 参数，不要写进代码。

扩容时按 source 的语义横向加实例：队列型 source（SQS、RabbitMQ、Redis Stream、Cloudflare Queues 等）可多实例并行消费；定时/轮询型 source（interval、cron、DB 增量）通常只应运行单实例，或用 `overlap: skip` 与持久化游标避免重复。

## AWS Lambda 部署

Lambda 是请求-响应式的短生命周期模型，与 `onestep run` 的长驻循环不匹配。**不要**在 Lambda 里调用 `app.run()` / `app.serve()`。正确做法是用 `OneStepApp.run_task_once()` 在一次调用中同步处理一条 payload，复用同一套 handler 和重试逻辑：

```python
# handler.py
import asyncio
from onestep import MemoryQueue, OneStepApp

app = OneStepApp("lambda-worker")


@app.task(source=MemoryQueue("in"))
async def handle(ctx, item):
    # 业务逻辑；返回值会作为处理结果
    return {"ok": True, "echo": item}


def lambda_handler(event, context=None):
    # 每次调用同步处理一条 payload，run_task_once 会走完 handler 与重试
    return asyncio.run(app.run_task_once("handle", payload=event))
```

要点：

- `run_task_once(task_name, payload=...)` 要求该任务的 source 支持手动运行（`MemoryQueue`、`interval`、`cron` 等 `supports_manual_run=True` 的 source）。它会执行 handler，成功返回结果字典，失败按任务的重试策略重试、最终抛出异常（Lambda 会记为调用失败）。
- 用它把 Lambda 的事件源（API Gateway、SQS 触发器、EventBridge 等）转成 payload 传入。注意此时消息的 ack/重试由 Lambda 事件源接管，而不是 onestep 的 source 循环。
- 依赖打包：用 Lambda 容器镜像（基于官方 worker 镜像或自建，把 `onestep` 及插件装进镜像）或 Layer/zip。注意 boto3 类插件与二进制依赖的体积和平台（`manylinux`）匹配。
- 若你的工作负载本质是「持续消费一个队列」，通常用 EC2/容器常驻 worker 比 Lambda 更合适；Lambda 更适合事件驱动、突发、按次计费的场景。

## 环境变量

主要配置项：

| 变量 | 说明 |
|------|------|
| `APP_CWD` | 应用工作目录（systemd 模板） |
| `APP_TARGET` | 应用 target，如 `your_package.tasks:app`（systemd 模板） |
| `ONESTEP_BIN` | `onestep` 可执行文件路径（systemd 模板） |
| `ONESTEP_TARGET` | YAML 路径或 Python target（worker 镜像） |
| `WORKSPACE_DIR` | 工作区路径，默认 `/workspace`（worker 镜像） |
| `PYTHONPATH` | Python 模块搜索路径 |

systemd 部署模板会自动将 `APP_CWD` 添加到 `PYTHONPATH`；worker 镜像会自动将 `WORKSPACE_DIR` 及其 `src/` 添加到 `PYTHONPATH`，确保仓库内的模块可以正确导入。

## YAML 配置

支持 YAML 应用定义，`handler.ref` 指向 Python 可调用对象：

```yaml
app:
  name: billing-sync

resources:
  tick:
    type: interval
    minutes: 5
    immediate: true
  processed:
    type: memory

tasks:
  - name: sync_billing
    source: tick
    handler:
      ref: your_package.handlers.billing:sync_billing
      params:
        region: cn
    emit: [processed]
    retry:
      type: max_attempts
      max_attempts: 3
      delay_s: 10
```

运行 YAML 应用：

```bash
onestep check worker.yaml
onestep run worker.yaml
```

如果要上传给 worker agent 或控制面，可以先构建可部署 zip：

```bash
onestep build worker.yaml --strict --out dist/worker.zip
```

容器部署可以使用官方 worker runtime image。镜像会把工作区加入 `PYTHONPATH`，安装项目依赖，先执行 `onestep check`，再启动 `onestep run`：

```bash
docker run --rm \
  -e ONESTEP_TARGET=/workspace/worker.yaml \
  -v "$PWD:/workspace" \
  ghcr.io/mic1on/onestep-worker:1.11.0
```

详细说明见 [Worker Runtime Image](/guide/worker-runtime-image)。

## 生产建议

### 状态持久化

生产环境推荐使用 `db.cursor_store(...)` 或 `db.state_store(...)`，确保游标和任务状态在进程重启后保持：

```python
from onestep_mysql import MySQLConnector

db = MySQLConnector("mysql+pymysql://...")
state = db.cursor_store(table="onestep_cursor")

source = db.incremental(
    table="users",
    key="id",
    cursor=("updated_at", "id"),
    state=state,  # 持久化游标
)
```

### 优雅关闭

配置关闭超时时间，确保 inflight 任务有足够时间完成：

```python
app = OneStepApp("my-app", shutdown_timeout_s=30.0)
```

## 下一步

- [RabbitMQ](/broker/rabbitmq) - 分布式消息队列
- [Redis Streams](/broker/redis) - 轻量级消息队列
- [MySQL](/broker/mysql) - 数据库集成
- [PostgreSQL](/broker/postgres) - PostgreSQL 集成
- [Kafka](/broker/kafka) - Kafka topic source/sink
- [Cloudflare Queues](/broker/cf-queues) - HTTP 拉取消费者
- [Worker Runtime Image](/guide/worker-runtime-image) - 容器化运行 YAML worker
