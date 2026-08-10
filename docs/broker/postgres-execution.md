---
title: PostgreSQL Tracked Execution
outline: deep
---

# PostgreSQL Tracked Execution

本文说明 `onestep==1.9.0` 和 `onestep-postgres==0.2.0` 都发布后，业务系统如何把 PostgreSQL 用作长任务的提交、状态、结果、取消和租约存储。

适用场景：HTTP 请求提交一个可能运行数秒、数分钟甚至更久的任务，API 需要返回任务 ID，业务端再查询状态或结果。典型例子包括 Agent、报表生成、文件处理、异步导入和批量同步。

这个功能是可选的。普通 `MemoryQueue`、RabbitMQ、Redis、SQS、定时任务和现有 PostgreSQL 表队列的接入方式不需要改动。

## 0. 先确认是否需要两个包

| 业务场景 | 需要的版本 | 是否需要改业务代码 |
| --- | --- | --- |
| 继续使用普通 queue、schedule、webhook | `onestep==1.9.0` | 不需要 |
| 继续使用旧 PostgreSQL table queue、incremental、state 或 sink | `onestep==1.9.0` + 兼容的 PostgreSQL plugin | 通常不需要 |
| 使用本页的提交、查询、结果和取消能力 | `onestep==1.9.0` + `onestep-postgres==0.2.0` | 需要按本文部署 API 和 worker |

`onestep-postgres==0.2.0` 依赖 `onestep>=1.9.0`，不能与 `onestep==1.8.1` 组合。反过来，只发布或安装 `onestep==1.9.0` 不会自动启用 tracked execution；没有安装 PostgreSQL plugin 的普通 worker 可以照常运行。

## 1. 运行架构

API 进程负责提交、查询和取消，OneStep worker 进程负责领取和执行。两个进程通过同一个 PostgreSQL 数据库协作，不要在 FastAPI 或 Django 进程中启动 OneStep worker。

```text
Business API                         OneStep worker
POST /executions                     PostgresExecutionSource
GET  /executions/{id}                OneStepApp + handler
POST /executions/{id}/cancel         heartbeat + lease completion
        |                                      |
        +--------- PostgreSQL -----------------+
                  executions + attempts
```

核心对象的职责如下：

| 对象 | 进程 | 用途 |
| --- | --- | --- |
| `ExecutionClient` | API | 提交、查询、分页查询、取消、读取结果 |
| `PostgresExecutionBackend` | API、高级共享连接池场景 | 连接同一组 execution 表 |
| `PostgresExecutionSource` | worker | 按 namespace 和 task name 领取任务 |
| `OneStepApp` | worker | 调度 handler、重试、取消和关闭流程 |

一个 `PostgresExecutionSource` 只能绑定一个 task name。需要执行多个任务时，为每个 task 创建独立 source。

## 2. 发布和安装

两个包都发布后，参与同一条 execution 链路的 API 和 worker 使用同一组锁定版本：

```bash
pip install "onestep==1.9.0" "onestep-postgres==0.2.0"
```

也可以使用 core 的 extra。注意 extra 声明的是 `onestep-postgres>=0.2.0`；生产环境仍建议通过 lockfile 固定最终解析版本：

```bash
pip install "onestep[postgres]==1.9.0"
```

项目使用 uv 时：

```bash
uv add "onestep==1.9.0" "onestep-postgres==0.2.0"
uv run python -c "import onestep, onestep_postgres; print(onestep.__version__, onestep_postgres.__version__)"
uv run pip check
```

发布顺序必须是：

1. 发布 `onestep==1.9.0`。
2. 确认 `onestep==1.9.0` 已经可以从 PyPI 安装。
3. 发布 `onestep-postgres==0.2.0`。
4. 锁定依赖，完成数据库初始化。
5. 先部署 worker 并确认能连接数据库，再开放 API 提交入口。
6. 避免同一业务链路长期运行混合版本。

如果 plugin 尚未发布，`onestep[postgres]==1.9.0` 和 `onestep[all]==1.9.0` 不能完整解析依赖。普通 `onestep==1.9.0` 不依赖 PostgreSQL plugin，可以独立安装。

## 3. 数据库初始化

execution backend 使用两张表：

- `onestep_executions`：任务主记录、状态、payload、result、error 和当前 lease。
- `onestep_execution_attempts`：每次领取产生一条 attempt，记录 worker、心跳和终态。

生产环境建议由 migration 角色创建表，运行时使用 `auto_create=False`。PR 提供的是 SQLAlchemy create-only 初始化，不会对已经存在的同名表执行安全的列变更或版本迁移。

### 3.1 一次性初始化脚本

在部署阶段使用具有 DDL 权限的独立连接执行一次：

```python
# deploy/create_execution_tables.py
import asyncio
import os

from onestep_postgres import PostgresExecutionBackend


async def main() -> None:
    backend = PostgresExecutionBackend(
        dsn=os.environ["POSTGRES_EXECUTION_MIGRATION_DSN"],
        table=os.getenv("POSTGRES_EXECUTIONS_TABLE", "onestep_executions"),
        attempts_table=os.getenv(
            "POSTGRES_EXECUTION_ATTEMPTS_TABLE",
            "onestep_execution_attempts",
        ),
        auto_create=True,
    )
    await backend.open()
    await backend.close()


asyncio.run(main())
```

执行成功后，API 和 worker 都使用 `auto_create=False`。如果自定义了表名，初始化脚本、API 和 worker 必须完全一致。

`table` 和 `attempts_table` 只接受不带 schema 的 SQL identifier，例如 `onestep_executions`，不接受 `app.onestep_executions`。如果使用非 `public` schema，请为 migration 和 runtime 连接配置一致的 PostgreSQL `search_path`，再继续使用不带 schema 的表名。

### 3.2 运行时数据库权限

运行身份不应拥有 DDL 权限。execution-only 场景至少需要对两张表有查询、插入和更新权限，并拥有目标 schema 的 `USAGE` 权限。项目如果同时使用 PostgreSQL table queue、state store 或 sink，还需要为这些资源授予对应权限。

```sql
GRANT USAGE ON SCHEMA public TO onestep_runtime;
GRANT SELECT, INSERT, UPDATE
ON TABLE public.onestep_executions, public.onestep_execution_attempts
TO onestep_runtime;
```

上线前检查：

```sql
SELECT to_regclass('public.onestep_executions');
SELECT to_regclass('public.onestep_execution_attempts');
```

如果表已存在但结构来自其他版本，不要直接设置 `auto_create=False` 继续运行。先用独立 migration 角色核对字段、约束和索引。

## 4. 共享配置

API 和 worker 至少共享以下配置：

```bash
POSTGRES_EXECUTION_DSN=postgresql+psycopg://app_runtime:***@db.example.com/app
POSTGRES_EXECUTION_NAMESPACE=agent-api
POSTGRES_EXECUTIONS_TABLE=onestep_executions
POSTGRES_EXECUTION_ATTEMPTS_TABLE=onestep_execution_attempts
```

不要把 DSN、密码或 token 写入代码、YAML 明文或日志。`PostgresConnector` 会提供脱敏 token 给 connector error，但业务日志仍不应主动打印 DSN。

namespace 是业务隔离边界。API 和 worker 必须使用相同 namespace，其他业务可以使用不同 namespace 共享同一个数据库。task name 是路由键，提交时的 task name 必须和 worker source 的 task name 完全一致。

namespace 是逻辑路由和查询边界，不是数据库权限边界。需要强隔离的租户应使用独立数据库、schema/role，或在业务 API 层实施鉴权，不能只依赖 namespace 字符串。

## 5. API 进程

下面是一个 FastAPI 示例。生产项目应使用自己的请求模型和鉴权逻辑，示例只展示 onestep 的边界。

```python
# app/api.py
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Query
from onestep import (
    Execution,
    ExecutionCancelled,
    ExecutionClient,
    ExecutionConflict,
    ExecutionEncodingError,
    ExecutionFailed,
    ExecutionExpired,
    ExecutionNotFound,
    ExecutionNotReady,
    ExecutionStatus,
)
from onestep_postgres import PostgresExecutionBackend
from pydantic import BaseModel, Field


backend = PostgresExecutionBackend(
    dsn=os.environ["POSTGRES_EXECUTION_DSN"],
    table=os.getenv("POSTGRES_EXECUTIONS_TABLE", "onestep_executions"),
    attempts_table=os.getenv(
        "POSTGRES_EXECUTION_ATTEMPTS_TABLE",
        "onestep_execution_attempts",
    ),
    auto_create=False,
)
executions = ExecutionClient(
    backend,
    namespace=os.getenv("POSTGRES_EXECUTION_NAMESPACE", "agent-api"),
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    async with executions:
        yield


api = FastAPI(lifespan=lifespan)
ALLOWED_TASKS = {"run_agent"}


class SubmitExecutionBody(BaseModel):
    task_name: str
    payload: Any
    metadata: dict[str, Any] = Field(default_factory=dict)
    delay_s: float | None = None
    expires_at: datetime | None = None


class CancelExecutionBody(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


def execution_view(execution: Execution) -> dict[str, Any]:
    return {
        "id": str(execution.id),
        "namespace": execution.namespace,
        "task_name": execution.task_name,
        "status": execution.status.value,
        "attempts": execution.attempts,
        "metadata": dict(execution.metadata),
        "version": execution.version,
        "created_at": execution.created_at.isoformat(),
        "available_at": execution.available_at.isoformat(),
        "started_at": (
            None if execution.started_at is None else execution.started_at.isoformat()
        ),
        "finished_at": (
            None if execution.finished_at is None else execution.finished_at.isoformat()
        ),
        "cancel_requested_at": (
            None
            if execution.cancel_requested_at is None
            else execution.cancel_requested_at.isoformat()
        ),
        "expires_at": (
            None if execution.expires_at is None else execution.expires_at.isoformat()
        ),
        "error": (
            None
            if execution.error is None
            else {
                "kind": execution.error.kind,
                "exception_type": execution.error.exception_type,
                "stage": execution.error.stage,
                "backend": execution.error.backend,
                "operation": execution.error.operation,
                "connector_kind": execution.error.connector_kind,
            }
        ),
    }


@api.post("/v1/executions", status_code=202)
async def submit_execution(
    body: SubmitExecutionBody,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
    ),
) -> dict[str, Any]:
    if body.task_name not in ALLOWED_TASKS:
        raise HTTPException(status_code=422, detail="unsupported task_name")
    if body.expires_at is not None and body.expires_at.tzinfo is None:
        raise HTTPException(status_code=422, detail="expires_at must include a timezone")
    try:
        execution = await executions.submit(
            body.task_name,
            body.payload,
            idempotency_key=idempotency_key,
            metadata=body.metadata,
            delay_s=body.delay_s,
            expires_at=body.expires_at,
        )
    except ExecutionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ExecutionEncodingError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return execution_view(execution)


@api.get("/v1/executions")
async def list_executions(
    task_name: str | None = None,
    status: ExecutionStatus | None = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
) -> dict[str, Any]:
    try:
        page = await executions.list(
            task_name=task_name,
            status=status,
            limit=limit,
            cursor=cursor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "items": [execution_view(item) for item in page.items],
        "next_cursor": page.next_cursor,
    }


@api.get("/v1/executions/{execution_id}")
async def get_execution(execution_id: UUID) -> dict[str, Any]:
    execution = await executions.get(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="execution not found")
    return execution_view(execution)


@api.post("/v1/executions/{execution_id}/cancel")
async def cancel_execution(
    execution_id: UUID,
    body: CancelExecutionBody,
) -> dict[str, Any]:
    execution = await executions.cancel(
        execution_id,
        reason=body.reason,
    )
    if execution is None:
        raise HTTPException(status_code=404, detail="execution not found")
    return execution_view(execution)


@api.get("/v1/executions/{execution_id}/result")
async def get_execution_result(execution_id: UUID) -> dict[str, Any]:
    try:
        return {"result": await executions.result(execution_id)}
    except ExecutionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExecutionNotReady as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ExecutionCancelled as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ExecutionExpired as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except ExecutionFailed as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
```

### 5.1 提交请求

```http
POST /v1/executions
Content-Type: application/json
Idempotency-Key: req-20260810-0001

{
  "task_name": "run_agent",
  "payload": {
    "prompt": "summarize this document",
    "document_id": "doc-123"
  },
  "metadata": {
    "tenant_id": "tenant-a",
    "requested_by": "user-42"
  }
}
```

业务 API 应使用稳定的业务请求号作为 `idempotency_key`。同一个 namespace、task name 和幂等键再次提交相同内容时，会返回原 execution；如果 payload、metadata 或其他提交参数不同，会得到 `ExecutionConflict`，通常映射为 HTTP 409。

上面的业务 API 强制要求 `Idempotency-Key` 请求头；底层 `ExecutionClient` 允许不传，但面向网络请求时不建议省略。对外部调用者不要开放任意 task name，应该像示例一样使用 allowlist，或直接在每个 endpoint 中固定 task name。`tenant_id`、`requested_by` 等鉴权信息应由服务端注入，不应直接信任请求体。

`Execution` 是不可变快照。`submit()` 返回的是提交时快照，`get()` 返回的是查询时快照，不会因为访问属性而自动刷新。

分页查询使用 keyset cursor：把响应的 `next_cursor` 原样传给下一次 `GET /v1/executions?cursor=...`。cursor 是不透明值，业务端不要解析或修改。

### 5.2 数据类型和大小限制

默认限制按编码后的 JSON 大小计算：payload 1 MiB、metadata 64 KiB、result 1 MiB。大文件、模型上下文和二进制产物应先放对象存储，只把 URI、校验和和必要元数据提交给 execution。

HTTP 业务通常只提交标准 JSON。Python 客户端还支持有限的 tagged 类型，例如 timezone-aware `datetime`、`UUID`、`Decimal`、`bytes`、tuple、set 和可重放的 Enum；不支持任意 Python 对象，dict key 必须是字符串，float 不能是 NaN 或 Infinity。编码失败或超过限制会抛 `ExecutionEncodingError`。

metadata 键 `onestep.execution` 由 runtime 保留，业务提交时不能使用。handler 返回值也必须满足同样的可编码约束；上线前应对最大结果样本做测试。

### 5.3 业务调用流程

提交成功后保存响应中的 execution ID。业务端不应保持数据库事务或 HTTP 长连接等待 handler，而是轮询状态、接收自己实现的通知，或稍后读取结果。

```bash
# 1. 提交；网络超时后仍用同一个 Idempotency-Key 和相同请求体重试
curl -X POST https://api.example.com/v1/executions \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: req-20260810-0001' \
  -d '{"task_name":"run_agent","payload":{"document_id":"doc-123"}}'

# 2. 查询状态
curl https://api.example.com/v1/executions/2a31b3a6-72c9-4ae3-8e8e-4d6f78c00f3a

# 3. succeeded 后读取结果
curl https://api.example.com/v1/executions/2a31b3a6-72c9-4ae3-8e8e-4d6f78c00f3a/result

# 4. 不再需要时请求取消；running 状态不会保证立即停止
curl -X POST \
  https://api.example.com/v1/executions/2a31b3a6-72c9-4ae3-8e8e-4d6f78c00f3a/cancel \
  -H 'Content-Type: application/json' \
  -d '{"reason":"user left the page"}'
```

建议轮询时使用退避，例如 1、2、4、8 秒后固定在 10 至 30 秒，并给客户端设置总等待上限。`queued`、`running`、`retrying`、`cancel_requested` 都是可继续等待的非终态；只有 `succeeded`、`failed`、`cancelled`、`expired` 是终态。

## 6. Worker 进程

```python
# app/worker.py
import os
from typing import Any

from onestep import ExponentialBackoff, OneStepApp
from onestep_postgres import PostgresExecutionSource


app = OneStepApp("agent-worker", shutdown_timeout_s=30.0)
jobs = PostgresExecutionSource(
    dsn=os.environ["POSTGRES_EXECUTION_DSN"],
    table=os.getenv("POSTGRES_EXECUTIONS_TABLE", "onestep_executions"),
    attempts_table=os.getenv(
        "POSTGRES_EXECUTION_ATTEMPTS_TABLE",
        "onestep_execution_attempts",
    ),
    auto_create=False,
    reclaim_batch_size=100,
    namespace=os.getenv("POSTGRES_EXECUTION_NAMESPACE", "agent-api"),
    task_names=("run_agent",),
    batch_size=4,
    poll_interval_s=1.0,
    lease_duration_s=90.0,
    heartbeat_interval_s=30.0,
    worker_id=os.getenv("HOSTNAME", "agent-worker-local"),
)


@app.task(
    name="run_agent",
    source=jobs,
    concurrency=4,
    retry=ExponentialBackoff(
        max_attempts=3,
        min_delay_s=2.0,
        max_delay_s=30.0,
        jitter="full",
    ),
    timeout_s=1800.0,
)
async def run_agent(ctx, payload: dict[str, Any]) -> dict[str, Any]:
    execution_meta = ctx.current.meta.get("onestep.execution", {})
    execution_id = execution_meta.get("id")

    # 下游写入必须使用 execution_id 或业务幂等键去重。
    result = await run_agent_model(
        payload,
        execution_id=execution_id,
    )
    return {"execution_id": execution_id, "output": result}


async def run_agent_model(
    payload: dict[str, Any],
    *,
    execution_id: str | None,
) -> Any:
    # Replace this with business logic. Do not call delivery.ack() manually.
    return {"document_id": payload["document_id"], "summary": "..."}


```

启动和检查：

```bash
onestep check app.worker:app
onestep run app.worker:app
```

handler 返回值会由 managed runtime 写入 execution 的 `result`。业务 handler 不需要也不应该手动调用 `ack()`、`retry()` 或 `fail()`。

Python worker 的 `OneStepApp` 会打开和关闭 source。直接传入 DSN 的
`PostgresExecutionSource` 会在当前 worker 进程内惰性创建并关闭自己的连接池。
需要把 `PostgresConnector` 同时用于 table queue、sink 或 state store 时，可以使用
`PostgresExecutionSource.from_connector(pg, ...)` 或先创建
`PostgresExecutionBackend.from_connector(pg, ...)`；这种共享 connector 仍由调用方关闭，
YAML resource 的生命周期由 app resource 管理器处理。

如果 handler 是同步阻塞函数，使用 `asyncio.to_thread()` 或其他线程池方式隔离，确保 heartbeat task 能够持续运行。`heartbeat_interval_s` 必须满足：

```text
0 < heartbeat_interval_s <= lease_duration_s / 3
```

多个 worker 副本可以使用同一 source 配置，但 `worker_id` 应使用 pod name、hostname 或其他实例唯一标识，便于诊断 lease 和 attempt。

多进程或 pre-fork 部署时，推荐每个进程使用
`PostgresExecutionSource(dsn=...)` 或 `PostgresExecutionBackend(dsn=...)`。
DSN 方式是惰性的，即使对象在 fork 前创建，连接池也会在子进程中独立创建。
不要把 `PostgresConnector` 或 `from_connector()` 创建的外部连接池跨进程复用；应在每个
子进程内创建 connector。数据库最大连接数需要按 API/worker 进程数和每个进程的池配置
统一核算。

## 7. YAML Worker 配置

如果 worker 使用 YAML，API 仍然使用 Python `ExecutionClient`。YAML 只负责 worker wiring，不承载 HTTP API。

```yaml
apiVersion: onestep/v1alpha1
kind: App

app:
  name: agent-worker

resources:
  pg:
    type: postgres
    dsn: "${POSTGRES_EXECUTION_DSN}"

  agent_jobs:
    type: postgres_execution_source
    connector: pg
    namespace: agent-api
    task_names: [run_agent]
    table: onestep_executions
    attempts_table: onestep_execution_attempts
    batch_size: 4
    poll_interval_s: 1.0
    lease_duration_s: 90.0
    heartbeat_interval_s: 30.0
    worker_id: "${HOSTNAME:-agent-worker}"
    auto_create: false
    reclaim_batch_size: 100

tasks:
  - name: run_agent
    source: agent_jobs
    handler:
      ref: app.handlers:run_agent
    concurrency: 4
    retry:
      type: exponential_backoff
      max_attempts: 3
      min_delay_s: 2.0
      max_delay_s: 30.0
      jitter: full
    timeout_s: 1800.0
```

验证：

```bash
onestep check --strict worker.yaml
onestep run worker.yaml
```

## 8. 状态和业务语义

| 状态 | 含义 | 业务端处理 |
| --- | --- | --- |
| `queued` | 已提交，等待 worker | 查询或继续等待 |
| `running` | 某个 worker 已领取 | 查询进度，必要时取消 |
| `retrying` | handler 失败后等待下一次尝试 | 继续等待 |
| `succeeded` | handler 成功，result 已持久化 | 调用 result 接口 |
| `failed` | 达到重试上限或明确失败 | 展示失败并人工处理 |
| `cancel_requested` | 运行中任务收到取消请求 | 等待 worker 收敛 |
| `cancelled` | 任务已取消 | 不再读取 result |
| `expired` | 在被领取前超过业务 expires_at | 重新提交或人工处理 |

`expires_at` 是“最晚开始处理时间”，不是运行时 deadline：健康 worker 已经领取的任务可以越过该时间继续执行。限制单次 handler 运行时长应使用 task 的 `timeout_s`。

`result()` 的异常建议映射为：

| 异常 | 含义 | 示例 HTTP 状态 |
| --- | --- | --- |
| `ExecutionNotFound` | execution 不存在 | 404 |
| `ExecutionNotReady` | 还没有终态 | 409 或 202 |
| `ExecutionFailed` | 终态为 failed | 422 或业务定义的失败状态 |
| `ExecutionCancelled` | 终态为 cancelled | 409 |
| `ExecutionExpired` | 终态为 expired | 410 |

取消是协作式的：

1. queued/retrying 状态的取消会直接变成 `cancelled`。
2. running 状态先变成 `cancel_requested`。
3. worker 的 heartbeat 观察到取消后取消 handler task。
4. worker 完成取消收敛后变成 `cancelled`。

如果取消和 handler 成功同时发生，取消优先。execution 不保存 handler 的 result/error，对应 attempt 为 `cancelled`，`error` 为 NULL，也不保存 result。这是有意设计，不表示 handler 没有返回值。

## 9. 重试、租约和重复执行

系统是 at-least-once，不是 exactly-once。下面这些情况都可能让 handler 或外部副作用再次执行：

- worker 在外部写入后、完成 execution 前崩溃；
- lease 过期后由其他 worker 接管；
- 数据库连接在提交结果时断开，业务端无法判断提交是否成功；
- handler 按 retry policy 进入下一次 attempt。

下游写入必须使用 execution ID 或业务幂等键去重。例如：

```sql
CREATE UNIQUE INDEX uq_business_result_request
ON business_results (request_id);
```

不要把“查不到 result”当作“任务一定没有执行”。如果 API 在提交后连接中断，应使用同一个 `idempotency_key` 重试提交，而不是生成新的请求号。

lease 相关参数建议：

| 参数 | 默认值 | 建议 |
| --- | --- | --- |
| `lease_duration_s` | 90 | 根据最长正常数据库抖动和 heartbeat 延迟调整 |
| `heartbeat_interval_s` | 30 | 不超过 lease 的三分之一 |
| `reclaim_batch_size` | 100 | 按数据库负载和恢复速度调节 |
| `batch_size` | 100 | 通常不应大于 worker 并发很多倍 |
| `poll_interval_s` | 1 | 影响空闲时的领取延迟 |

过期 execution 和停滞 lease 由下一次 `claim()` 驱动恢复，没有独立 reaper。所有 worker 都停止时，不会有新的 reclaim；恢复 worker 后会按 `reclaim_batch_size` 分批处理积压。

lease deadline 由 worker 进程的系统时间计算。所有 API/worker 主机应启用 NTP 或等价的时间同步，并监控时钟偏差；明显偏差可能导致 lease 提前回收和重复执行。

## 10. 观测和排查

execution source 会把关联信息放在 envelope metadata 中，handler 可以读取：

```python
execution_meta = ctx.current.meta["onestep.execution"]
execution_id = execution_meta["id"]
attempt_id = execution_meta["attempt_id"]
```

TaskEvent 也会携带同一关联 metadata。日志至少记录：

- `execution_id`
- `attempt_id`
- `task_name`
- `worker_id`
- 当前 execution status
- 业务幂等键或 request ID

execution 的结构化 error 只包含 kind、exception type、失败 stage 和 connector 分类，不包含原始异常 message 或 traceback。业务若需要可检索的详细诊断，应在 handler 日志中记录并用 `execution_id`、`attempt_id` 关联，同时遵守敏感信息脱敏规则。

常用排查 SQL：

```sql
SELECT status, count(*)
FROM onestep_executions
WHERE namespace = 'agent-api'
GROUP BY status
ORDER BY status;

SELECT id, task_name, status, attempts, worker_id,
       lease_expires_at, created_at, updated_at
FROM onestep_executions
WHERE namespace = 'agent-api'
ORDER BY created_at DESC
LIMIT 50;

SELECT execution_id, attempt_no, worker_id, status,
       started_at, heartbeat_at, finished_at
FROM onestep_execution_attempts
WHERE execution_id = '<execution-id>'
ORDER BY attempt_no;
```

重点告警：

- `queued` 长时间增长：worker 未启动、task name/namespace 不匹配或数据库不可用。
- `running` 数量长期不降：handler 阻塞、heartbeat 不运行或 worker 崩溃。
- `retrying` 持续增长：业务失败率或下游依赖异常。
- `cancel_requested` 长时间存在：worker 没有 heartbeat 或无法完成取消。
- `expired` 增长：业务设置的 `expires_at` 过短或 worker 领取能力不足。

## 11. 上线检查清单

上线前按顺序确认：

- [ ] PyPI 中同时存在 `onestep==1.9.0` 和 `onestep-postgres==0.2.0`。
- [ ] API 和 worker 的 `pip check` 通过，两个进程使用相同版本组合。
- [ ] API 和 worker 都打印并核对过 `onestep`、`onestep-postgres` 的实际版本。
- [ ] 以 migration 身份完成 execution 两张表的初始化。
- [ ] API 和 worker 都使用 `auto_create=False`。
- [ ] API 和 worker 的 DSN、namespace、表名一致。
- [ ] 如果使用非 `public` schema，migration 和 runtime 连接的 `search_path` 一致。
- [ ] 每个 task 使用独立 `PostgresExecutionSource`，task name 完全一致。
- [ ] 每个 worker 实例有唯一 `worker_id`，主机时钟已同步。
- [ ] handler 的数据库写入、消息发送和文件写入具备幂等保护。
- [ ] 已验证成功、失败重试、取消、重复提交和 worker 重启恢复。
- [ ] 已配置 queued/running/retrying/cancel_requested 的告警。

最小 smoke test：

1. 提交一个短任务，确认状态从 `queued` 进入 `running` 再进入 `succeeded`。
2. 使用同一个 request ID 重复提交，确认返回同一个 execution ID。
3. 提交不同 payload 但复用 request ID，确认 API 返回 409。
4. 提交一个可取消长任务，确认最终状态为 `cancelled`。
5. 在任务运行期间重启 worker，确认后续 worker 能重新领取并产生新 attempt。
6. 分页查询两页数据，确认 `next_cursor` 不重复、不漏项。
7. 提交超限或不可编码结果，确认监控能够发现且不会误报成功。

## 12. 回滚

如果新 execution backend 出现问题：

1. 先停止 API 继续提交新的 tracked execution。
2. 等待或人工处理当前 `running`、`cancel_requested` 和 `retrying` 记录。
3. API 和 worker 一起回滚到兼容的 core/plugin 版本，例如 `onestep==1.8.1` 与 `onestep-postgres==0.1.3`。
4. 保留 `onestep_executions` 和 `onestep_execution_attempts` 表，不要直接 drop。它们包含审计和恢复信息。
5. 如果恢复到新版本，先执行 smoke test，再重新放开业务提交。

旧版本 worker 不会处理新 execution 表中的任务，因此回滚期间不要让新 execution 继续进入数据库，除非已经准备了对应的新版本 worker。
