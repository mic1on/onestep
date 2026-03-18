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
```

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

connectors:
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

## 生产建议

### 状态持久化

生产环境推荐使用 `db.cursor_store(...)` 或 `db.state_store(...)`，确保游标和任务状态在进程重启后保持：

```python
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