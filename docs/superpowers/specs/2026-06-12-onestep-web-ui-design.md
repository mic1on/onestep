# OneStep Web UI — 可视化管道编排器 设计文档

**日期**: 2026-06-12
**状态**: 待审阅

---

## 1. 产品定位

### 1.1 一句话描述

OneStep Web UI 是一个在线 iPaaS 可视化管道编排器，用户通过拖拽节点、配置连接器、编写处理逻辑来构建数据管道，保存即运行。

### 1.2 目标用户

兼顾两类用户：

- **开发者**：熟悉 One Step 生态，希望用可视化方式管理连接器和管道 wiring，减少手写 YAML 的繁琐。Handler 逻辑可以直接写 Python 代码。
- **非开发者 / 数据分析师**：不太会写 Python，通过字段映射和简单表达式完成数据转换和集成。

### 1.3 核心差异

| | 传统 One Step | Web UI |
|---|---|---|
| 配置方式 | 手写 Python / YAML | 拖拽 + 可视化配置 |
| 运行方式 | CLI / systemd / Docker | 配置即运行，保存就生效 |
| 管道拓扑 | 单任务线性 | DAG 多阶段管道 |
| 凭据管理 | 环境变量 | 全局凭据库，引用凭据名 |
| 可迁移性 | 天然可迁移 | 可导出为完整 Worker 项目 |

---

## 2. 管道模型

### 2.1 DAG 管道

画布上的管道是一个有向无环图（DAG），由三种节点组成：

| 节点类型 | 入边 | 出边 | 说明 |
|----------|------|------|------|
| **Source** | 无 | 1+ | 数据入口，从外部系统拉取数据 |
| **Handler** | 1+ | 1+ | 处理节点，接收上游数据，经处理/转换后输出 |
| **Sink** | 1+ | 无 | 数据出口，写入外部系统 |

### 2.2 支持的连接器节点

| 分类 | 节点类型 | 说明 |
|------|----------|------|
| Source | MySQL Source | 表队列 (`table_queue`)、增量 (`incremental`)、binlog |
| Source | RabbitMQ Source | 队列消费 |
| Source | Redis Stream Source | Stream 消费 |
| Source | SQS Source | 队列消费 |
| Source | Cron Source | 定时触发 |
| Source | Interval Source | 固定间隔触发 |
| Source | Webhook Source | HTTP 入口 |
| Source | Feishu Bitable Source | 飞书多维表格增量读取 |
| Handler | Python Handler | 用户编写的处理/转换逻辑 |
| Sink | MySQL Sink | 表写入 |
| Sink | RabbitMQ Sink | 队列发布 |
| Sink | Redis Stream Sink | Stream 发布 |
| Sink | SQS Sink | 队列发布 |
| Sink | HTTP Sink | 外部 HTTP 调用 |
| Sink | Feishu Bitable Sink | 飞书多维表格写入 |

### 2.3 连线规则

- Source 节点只能有出边，不能有入边
- Sink 节点只能有入边，不能有出边
- Handler 节点可以有多条入边和多条出边（扇出）
- 连线方向表示数据流方向
- 管道必须是一个连通的 DAG，不允许环路

---

## 3. 前端设计

### 3.1 技术选型

| 层 | 选型 | 理由 |
|----|------|------|
| 框架 | React 18 + TypeScript | 生态成熟 |
| 画布/拖拽 | ReactFlow | 原生 DAG 节点-边支持，MIT 协议 |
| 代码编辑器 | Monaco Editor (`@monaco-editor/react`) | VS Code 同源，Python 支持完善 |
| 构建 | Vite | 快速开发和生产构建 |
| 状态管理 | Zustand 或 React Context | 轻量级，够用 |

### 3.2 页面结构

```
┌───────────────────────────────────────────────┐
│  Header: Logo | 管道名称 | [保存] [启动/停止] [导出] │
├─────────┬───────────────────────┬─────────────┤
│ 左侧面板 │      画布区域          │ 右侧属性面板  │
│ (节点库) │   (ReactFlow 画布)    │ (当前选中节点 │
│         │                       │  的配置)     │
│ Source  │   [n1]──→[n2]──→[n3]  │             │
│ ──────  │       ↘[n4]           │ 节点类型     │
│ ·MQ     │                       │ 连接配置     │
│ ·MySQL  │                       │ Handler代码  │
│ ·Redis  │                       │ ...         │
│ ·SQS    │                       │             │
│ ·Cron   │                       │             │
│ ·Webhook│                       │             │
│         │                       │             │
│ Handler │                       │             │
│ ──────  │                       │             │
│ ·Python │                       │             │
│         │                       │             │
│ Sink    │                       │             │
│ ──────  │                       │             │
│ ·MQ     │                       │             │
│ ·MySQL  │                       │             │
│ ·Redis  │                       │             │
│ ·SQS    │                       │             │
│ ·HTTP   │                       │             │
└─────────┴───────────────────────┴─────────────┘
```

### 3.3 连接器配置面板

选中连接器节点后，右侧属性面板展示：

**连接信息**：

```
连接方式:  [● 引用凭据]  [○ 直接填写]

凭据:     [▼ 选择凭据...]
          ├─ PROD_RABBITMQ
          ├─ DEV_RABBITMQ
          └─ + 新建凭据...

Queue:    [billing.incoming          ]
Exchange: [billing.events            ]  (可选)
Routing:  [billing.created           ]  (可选)
Prefetch: [10                        ]
```

### 3.4 凭据管理

独立的全局凭据管理页面，凭据值加密存储：

```
凭据名称:    PROD_RABBITMQ
类型:        RabbitMQ
URL:         amqp://user:${PASSWORD}@host:5672/
                     ↑ 支持 ${ENV_VAR} 引用

环境变量:    ┌─────────────────────────────┐
            │ PASSWORD = ********          │
            │ + 添加环境变量                │
            └─────────────────────────────┘
```

### 3.5 Handler 编辑器（双模式）

两种编辑模式，可随时切换。

#### 模式一：字段映射（可视化模式）

左侧展示上游输出结构，右侧通过表达式映射：

```
┌──────── 字段映射 ────────┬────── 输出 ────────────┐
│                          │                        │
│  输入字段:                │  输出字段:              │
│  ┌──────────────────────┐│ ┌──────────────────────┐│
│  │ order_id: "A001"    →│→│ id: {{order_id}}     ││
│  │ amount: 99.5        →│→│ price: {{amount * 1.1}}│
│  │ status: "new"       →│→│ status: {{status}}   ││
│  └──────────────────────┘│ └──────────────────────┘│
│                          │                        │
│  [+ 添加映射]             │  输出格式: [JSON ▼]     │
└──────────────────────────┴────────────────────────┘
```

- 左侧字段从上游节点的输出 schema 自动推断或手动定义
- `{{expression}}` 支持简单 Jinja2 风格表达式
- 非开发者无需写代码

#### 模式二：代码编辑器（开发者模式）

内嵌 Monaco Editor，用户直接写 Python handler 函数体：

```python
async def handler(ctx, payload):
    """接收上游 payload，返回处理后的结果"""
    return {
        "id": payload["order_id"],
        "price": payload["amount"] * 1.1,
        "status": payload["status"],
    }
```

- 完整的 Python 语法高亮和代码补全
- `ctx` 和 `payload` 的类型提示由上游 schema 注入
- 可以写任意复杂逻辑（条件、循环、外部 API 调用等）

---

## 4. 后端设计

### 4.1 技术选型

| 层 | 选型 | 理由 |
|----|------|------|
| Web 框架 | FastAPI (Python) | 异步原生，与 One Step 同语言 |
| 数据库 | SQLite（默认）/ PostgreSQL（可选） | SQLite 零运维，单机够用 |
| ORM | SQLAlchemy (async) | 成熟稳定的异步 ORM |
| 凭据加密 | Fernet (cryptography) | 对称加密，实现简单 |
| 前端服务 | 后端内嵌静态文件 | 简化部署，单进程 |

### 4.2 分层架构

```
┌──────────────────────────────────────────┐
│           前端 (React SPA)                │
└─────────────────┬────────────────────────┘
                  │ REST API + WebSocket
┌─────────────────┴────────────────────────┐
│      后端 API Server (FastAPI)            │
│                                          │
│  API 层                                   │
│  ├─ 管道 CRUD      POST/GET/PUT/DELETE   │
│  ├─ 连接器注册      GET /connectors        │
│  ├─ 凭据管理        CRUD /credentials      │
│  ├─ 管道运行控制    POST start/stop        │
│  ├─ 管道导出        POST export            │
│  └─ 管道日志/事件   WebSocket              │
│                                          │
│  服务层                                    │
│  ├─ PipelineCompiler    graph → app       │
│  ├─ PipelineRuntimePool 运行时管理         │
│  └─ ConnectorRegistry   插件发现           │
│                                          │
│  持久化层                                  │
│  ├─ SQLAlchemy models                     │
│  └─ Fernet 加密凭据                       │
└──────────────────────────────────────────┘
```

### 4.3 管道定义存储

数据库存储管道为节点-边图 JSON：

```json
{
  "id": "pipe_abc123",
  "name": "订单同步管道",
  "status": "running",
  "graph": {
    "nodes": [
      {
        "id": "n1",
        "type": "rabbitmq_source",
        "credential_ref": "PROD_RABBITMQ",
        "config": { "queue": "orders.incoming", "prefetch": 10 }
      },
      {
        "id": "n2",
        "type": "handler",
        "mode": "visual",
        "mapping": { "id": "{{order_id}}", "price": "{{amount * 1.1}}" }
      },
      {
        "id": "n3",
        "type": "mysql_sink",
        "credential_ref": "PROD_MYSQL",
        "config": { "table": "dw_orders", "mode": "upsert", "keys": ["id"] }
      }
    ],
    "edges": [
      { "from": "n1", "to": "n2" },
      { "from": "n2", "to": "n3" }
    ]
  }
}
```

### 4.4 管道编译器 (PipelineCompiler)

编译流程：

1. **拓扑排序**：对 graph 做拓扑排序，检测环路
2. **校验**：节点连接合法性、凭据存在性、Handler 代码语法
3. **构建 MemoryQueue**：为每条边创建一个内部 `MemoryQueue` 作为节点间数据通道
4. **生成 app.task**：为每个节点创建对应的 task 定义
5. **创建 OneStepApp**：组装所有 task，返回 app 实例

编译结果示例（对应上面的 3 节点管道）：

```python
app = OneStepApp("pipe_abc123")
q12 = MemoryQueue("n1→n2")
q23 = MemoryQueue("n2→n3")

@app.task(source=rabbitmq_source, emit=[q12])
async def node_n1(ctx, item): ...

@app.task(source=q12, emit=[q23])
async def node_n2(ctx, item): ...

@app.task(source=q23, emit=mysql_sink)
async def node_n3(ctx, item): return item
```

### 4.5 运行时池 (PipelineRuntimePool)

进程中维护所有运行中管道的 `OneStepApp` 实例：

```python
class PipelineRuntimePool:
    _apps: dict[str, OneStepApp]
    _tasks: dict[str, asyncio.Task]

    async def start(self, pipeline_id: str, graph: dict, credentials: dict)
    async def stop(self, pipeline_id: str)
    async def restart(self, pipeline_id: str, graph: dict, credentials: dict)
    def get_status(self, pipeline_id: str) -> PipelineStatus
```

**生命周期操作**：

- **启动**：编译 graph → 创建 asyncio.Task → 运行 `app.serve()` → 连接器开始拉取数据
- **停止**：`app.request_shutdown()` → 等待 shutdown_timeout_s 优雅退出
- **重启**：stop → 重新编译 → start（配置变更后）
- **日志/事件**：通过 `app.on_event` hook 收集，推送到前端 WebSocket

### 4.6 Worker 导出

`POST /api/pipelines/{id}/export` 返回 zip 下载：

```
{project_name}/
├── pyproject.toml
├── worker.yaml              ← 编译器生成的标准 OneStep YAML
├── .env.example             ← 凭据中定义的环境变量模板
├── requirements.txt         ← onestep + 所需插件包
└── src/{package_name}/
    ├── __init__.py
    └── handlers.py          ← Handler 代码
```

- 代码模式的 handler 原样写入 `handlers.py`
- 映射模式的 handler 生成等效的 Python 函数
- `worker.yaml` 通过 `resources` 和 `tasks` 定义完整管道
- 用户解压后 `pip install -e . && onestep run worker.yaml` 即可在任何地方独立运行

---

## 5. 数据模型

### 5.1 Pipeline（管道）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string (UUID) | 主键 |
| name | string | 管道名称 |
| description | string | 描述 |
| graph | JSON | 节点-边图 |
| status | enum | draft / running / stopped / error |
| created_at | datetime | |
| updated_at | datetime | |

### 5.2 Credential（凭据）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string (UUID) | 主键 |
| name | string | 唯一名称（如 PROD_MYSQL） |
| connector_type | enum | mysql / rabbitmq / redis / sqs / feishu_bitable |
| config | JSON (encrypted) | 连接信息（DSN、URL 等），Fernet 加密 |
| env_vars | JSON (encrypted) | 环境变量键值对，Fernet 加密 |
| created_at | datetime | |
| updated_at | datetime | |

### 5.3 PipelineLog（管道日志）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int | 自增主键 |
| pipeline_id | string | 外键 |
| event_kind | string | fetched / started / succeeded / retried / failed / dead_lettered |
| task_name | string | 节点名称 |
| message | text | 日志内容 |
| timestamp | datetime | |

---

## 6. API 设计摘要

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/pipelines` | 管道列表 |
| POST | `/api/pipelines` | 创建管道 |
| GET | `/api/pipelines/{id}` | 获取管道详情（含 graph） |
| PUT | `/api/pipelines/{id}` | 更新管道 |
| DELETE | `/api/pipelines/{id}` | 删除管道 |
| POST | `/api/pipelines/{id}/start` | 启动管道 |
| POST | `/api/pipelines/{id}/stop` | 停止管道 |
| POST | `/api/pipelines/{id}/export` | 导出 Worker 项目 zip |
| GET | `/api/connectors` | 可用连接器类型列表 |
| GET | `/api/credentials` | 凭据列表 |
| POST | `/api/credentials` | 创建凭据 |
| PUT | `/api/credentials/{id}` | 更新凭据 |
| DELETE | `/api/credentials/{id}` | 删除凭据 |
| WS | `/ws/pipelines/{id}` | 管道实时日志/事件推送 |

---

## 7. 管道全生命周期流程

```
用户操作                       后端/系统行为
────────                      ────────────
拖拽节点到画布                前端维护 graph 状态
配置连接器 → 引用凭据          校验凭据引用存在
配置 Handler 映射 / 代码      存储配置到节点 JSON
点击"保存"                   写入数据库，状态=draft
点击"启动"       ──────→      1. 校验拓扑 (DAG/环路/连接)
                              2. 编译 graph → OneStepApp
                              3. RuntimePool.start()
                              4. app.serve() 开始拉取数据
                              5. 状态=running
                              6. WebSocket 推送运行事件
点击"停止"       ──────→      1. app.request_shutdown()
                              2. 取消 asyncio.Task
                              3. 状态=stopped
点击"导出"       ──────→      1. 编译 graph → YAML + Python
                              2. 打包项目 zip
                              3. 返回下载
```

---

## 8. 部署形态

### 8.1 本地开发 / 单机

```bash
pip install onestep-web
onestep-web serve
# → 后端 http://localhost:8000，前端内嵌在同一端口
```

### 8.2 Docker

```dockerfile
FROM python:3.12-slim
RUN pip install onestep-web onestep-mysql onestep-mq onestep-redis onestep-sqs onestep-feishu-bitable
CMD ["onestep-web", "serve", "--host", "0.0.0.0", "--port", "8000"]
```

内嵌 SQLite，无需外部数据库。前端构建产物内嵌在后端静态文件 serve。

---

## 9. 限制与未来演进

### 9.1 MVP 限制

- 管道在单个进程中运行，不支持跨节点分布式
- 管道更新需要重启（stop → recompile → start）
- 不支持条件路由（conditional branching），仅扇出（fan-out）到所有下游
- 连接器类型限定为 One Step 已支持的类型

### 9.2 未来可扩展方向

- 条件路由：Handler 按条件发送到不同下游 Sink
- 管道版本管理与回滚
- 管道间依赖与编排
- 多 Worker 分布式运行
- 外部密钥源集成（Vault、K8s Secret）
- 管道模板市场