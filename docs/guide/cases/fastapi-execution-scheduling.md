---
title: 实战篇：FastAPI 提交长任务并调度 Worker | 指南
outline: deep
---

# 实战篇：FastAPI 提交长任务并调度 Worker

本案例展示业务 API（FastAPI）如何把耗时任务提交给 OneStep，由独立的 worker 进程
异步执行，API 只负责返回任务 ID、查询状态和取消。它适合 Agent 调用、报表生成、
文件处理、批量导入等“请求触发、后台长跑”的场景。

调度存储用 PostgreSQL：API 进程通过 `ExecutionClient` 提交、查询、取消，worker
进程通过 `PostgresExecutionSource` 领取、执行、心跳续租，两个进程只通过同一个
PostgreSQL 数据库解耦。

```text
FastAPI (ExecutionClient)          OneStep worker (PostgresExecutionSource)
POST /executions        ──┐        领取 run_agent 任务
GET  /executions/{id}     ├─ PostgreSQL ─┤ OneStepApp + handler
POST /.../cancel        ──┘        心跳续租 + managed 完成
```

::: warning 不要在 API 进程里跑 worker
不要在 FastAPI（或 Django）请求生命周期内 `await` 任务完成，也不要在 API 进程内
启动 OneStep worker。API 与 worker 必须是两个独立进程。
:::

## 目标与边界

- API 收到请求后立即返回 execution ID，业务端随后轮询状态或读取结果，而不是在
  HTTP 长连接里等待。
- 提交带 `Idempotency-Key`：同一个 key 重复提交返回同一条 execution，不重复入队。
- 语义是 at-least-once：worker 崩溃后租约到期，任务会被重新领取执行。handler 内
  的下游写入必须用 `execution_id` 或业务幂等键去重。
- 一个 `PostgresExecutionSource` 只绑定一个 task name；要执行多个任务时为每个
  task 建独立 source。

## 前置条件

发布顺序要求 core 先于 plugin：

```bash
pip install "onestep>=1.9.0" "onestep-sql[postgres]>=0.1.0" fastapi uvicorn
```

`onestep-sql[postgres]` 依赖 `onestep>=1.9.0`。只安装 core 不会启用 tracked
execution；只有装了 PostgreSQL plugin 的进程才具备提交/领取能力。

## 1. 一次性初始化数据库

execution backend 使用两张表：`onestep_executions`（任务主记录）和
`onestep_execution_attempts`（每次领取一条 attempt）。用具备 DDL 权限的独立连接
在部署阶段执行一次，运行时再用 `auto_create=False`。

```python
# deploy/create_execution_tables.py
import asyncio
import os

from onestep_sql.postgres import PostgresExecutionBackend


async def main() -> None:
    backend = PostgresExecutionBackend(
        dsn=os.environ["POSTGRES_EXECUTION_MIGRATION_DSN"],
        auto_create=True,
    )
    await backend.open()
    await backend.close()


asyncio.run(main())
```

运行身份不应有 DDL 权限，最少需要对两张表的 `SELECT, INSERT, UPDATE` 和目标
schema 的 `USAGE`。

## 2. 共享配置

API 与 worker 至少共享以下配置，且 `namespace` 必须一致：

```bash
POSTGRES_EXECUTION_DSN=postgresql+psycopg://app_runtime:***@db.example.com/app
POSTGRES_EXECUTION_NAMESPACE=agent-api
```

`namespace` 是业务路由与查询边界（不是数据库权限边界）；task name 是路由键，提交时
的 task name 必须和 worker source 的 task name 完全一致。

## 3. API 进程（FastAPI）

API 通过 `ExecutionClient` 提交、查询、取消。示例只展示 onestep 的边界，生产项目
应使用自己的请求模型和鉴权。

```python
# app/api.py
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from onestep import (
    Execution,
    ExecutionClient,
    ExecutionConflict,
    ExecutionEncodingError,
    ExecutionNotFound,
    ExecutionNotReady,
    ExecutionStatus,
)
from onestep_sql.postgres import PostgresExecutionBackend

backend = PostgresExecutionBackend(
    dsn=os.environ["POSTGRES_EXECUTION_DSN"],
    auto_create=False,
)
executions = ExecutionClient(
    backend,
    namespace=os.getenv("POSTGRES_EXECUTION_NAMESPACE", "agent-api"),
)
ALLOWED_TASKS = {"run_agent"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    async with executions:
        yield


api = FastAPI(lifespan=lifespan)


class SubmitBody(BaseModel):
    task_name: str
    payload: Any
    metadata: dict[str, Any] = Field(default_factory=dict)


def view(e: Execution) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "task_name": e.task_name,
        "status": e.status.value,
        "attempts": e.attempts,
    }


@api.post("/v1/executions", status_code=202)
async def submit(
    body: SubmitBody,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1),
) -> dict[str, Any]:
    if body.task_name not in ALLOWED_TASKS:
        raise HTTPException(status_code=422, detail="unsupported task_name")
    try:
        execution = await executions.submit(
            body.task_name,
            body.payload,
            idempotency_key=idempotency_key,
            metadata=body.metadata,
        )
    except ExecutionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ExecutionEncodingError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return view(execution)


@api.get("/v1/executions/{execution_id}")
async def get(execution_id: UUID) -> dict[str, Any]:
    execution = await executions.get(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="execution not found")
    return view(execution)


@api.get("/v1/executions/{execution_id}/result")
async def result(execution_id: UUID) -> dict[str, Any]:
    try:
        return {"result": await executions.result(execution_id)}
    except ExecutionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExecutionNotReady as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.post("/v1/executions/{execution_id}/cancel")
async def cancel(execution_id: UUID) -> dict[str, Any]:
    execution = await executions.cancel(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="execution not found")
    return view(execution)
```

状态机：`queued`、`running`、`retrying`、`cancel_requested` 是可继续等待的非终态；
`succeeded`、`failed`、`cancelled`、`expired` 是终态。轮询建议退避（如 1、2、4、8 秒
后固定在 10–30 秒）并设总等待上限。

## 4. Worker 进程

worker 用 `PostgresExecutionSource` 领取任务，`OneStepApp` 负责调度 handler、重试、
心跳与关闭。handler 返回值由 managed runtime 写入 execution 的 `result`，**不要**
手动 `ack()` / `retry()` / `fail()`。

```python
# app/worker.py
import os
from typing import Any

from onestep import ExponentialBackoff, OneStepApp
from onestep_sql.postgres import PostgresExecutionSource

app = OneStepApp("agent-worker", shutdown_timeout_s=30.0)

jobs = PostgresExecutionSource(
    dsn=os.environ["POSTGRES_EXECUTION_DSN"],
    auto_create=False,
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
    # 下游写入必须用 execution_id 或业务幂等键去重（at-least-once）。
    result = await run_agent_model(payload, execution_id=execution_id)
    return {"execution_id": execution_id, "output": result}


async def run_agent_model(payload, *, execution_id):
    # 替换为业务逻辑；不要手动调用 delivery.ack()。
    return {"document_id": payload["document_id"], "summary": "..."}
```

启动与检查：

```bash
onestep check app.worker:app
onestep run app.worker:app
```

`heartbeat_interval_s` 必须满足 `0 < heartbeat_interval_s <= lease_duration_s / 3`。
handler 若是同步阻塞函数，用 `asyncio.to_thread()` 隔离，避免阻塞心跳 task。

## 运行与恢复

### 幂等与 at-least-once

- **提交幂等**：`Idempotency-Key` 让重复提交返回同一 execution，不重复入队。
- **执行 at-least-once**：worker 崩溃后租约到期，任务被重新领取。handler 的下游
  副作用必须用 `execution_id` 去重，否则会重复。

### 租约、心跳与 fencing

worker 领取任务后持有租约，靠心跳续期。心跳停止（进程卡死/崩溃）后租约到期，任务
被其他 worker 重新领取；旧 worker 迟到的写入会被 fencing 拒绝。用 PostgreSQL 数据库
时间作为租约权威时钟。

### 取消

`POST /.../cancel` 把 execution 置为 `cancel_requested`。运行中的 handler 通过
`ctx` 感知取消请求并优雅退出；已终态的 execution 不受影响。

### 部署顺序

1. 先初始化数据库表。
2. 先部署 worker 并确认能连接数据库。
3. 再开放 API 提交入口。
4. 避免同一 execution 链路长期跑混合版本。

## 参数取舍

| 参数 | 本案例值 | 取舍 |
|---|---:|---|
| `batch_size`（source） | 4 | 一次领取的任务数；按 worker 并发调整。 |
| `concurrency` | 4 | 同时执行的 handler 数。 |
| `lease_duration_s` | 90 | 租约时长，需大于单次执行心跳窗口。 |
| `heartbeat_interval_s` | 30 | 必须 ≤ lease/3，保证及时续租。 |
| `timeout_s` | 1800 | 单次执行上限；超时按重试策略处理。 |
| `max_attempts` | 3 | 临时故障可重试；耗尽后 execution 置 `failed`。 |

## 相关文档

- [PostgreSQL Tracked Execution](/broker/postgres-execution)（完整 API、状态机、schema）
- [PostgreSQL 连接器](/broker/postgres)
- [重试与死信](/core/retry)
- [核心可靠性](/core-reliability)
