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

## 环境变量

主要配置项：

| 变量 | 说明 |
|------|------|
| `APP_CWD` | 应用工作目录 |
| `PYTHONPATH` | Python 模块搜索路径 |

部署模板会自动将 `APP_CWD` 添加到 `PYTHONPATH`，确保仓库内的模块可以正确导入。

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
- [Worker Runtime Image](/guide/worker-runtime-image) - 容器化运行 YAML worker
