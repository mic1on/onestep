---
title: 长程任务如何工作 | Broker
outline: deep
---

# 长程任务如何工作

这一页用三张图说明**应用**长程任务（Tracked Execution）时，你的系统如何与 onestep 协作跑完一个长任务：谁负责什么、数据流向哪里、接入前要准备什么。两个后端的部署细节见 [MySQL Tracked Execution](/broker/mysql-execution) 与 [PostgreSQL Tracked Execution](/broker/postgres-execution)。

## 三个角色

整个应用一共三个组件。虚线框外是 onestep 提供的，你只写 API 路由和任务处理函数。

```mermaid
flowchart LR
    subgraph api["业务 API 进程"]
        direction TB
        routes["HTTP 路由（你写）"]
        client["ExecutionClient（框架提供）"]
        routes --> client
    end
    t1[("executions 表<br/>任务主记录 + 状态 + result")]
    t2[("attempts 表<br/>每次领取一条 attempt")]
    subgraph worker["OneStep worker 进程"]
        direction TB
        source["MySQLExecutionSource /<br/>PostgresExecutionSource（框架提供）"]
        app["OneStepApp（框架提供）"]
        handler["任务处理函数（你写）"]
        source --> app --> handler
    end
    client -- "提交 / 查询 / 取消" --> t1
    source -- "领取 / 心跳 / 回写终态" --> t1
    source -- "每次领取写一条" --> t2
    handler -- "result / error" --> t1
```

| 代码 | 谁提供 |
| --- | --- |
| HTTP 路由（提交 / 查询 / 取消） | 你写 |
| 任务处理函数（handler） | 你写 |
| `ExecutionClient` / `ExecutionSource` / `OneStepApp` | onestep |
| 两张表的 DDL 与状态流转 | onestep（建议 migration 角色建表，运行时 `auto_create=False`） |

任务状态就是数据库里的行，不经过任何消息队列。API 进程和 worker 进程互不直连，只通过同一组表协作。

## 一次任务的完整流程

```mermaid
sequenceDiagram
    participant C as 客户端
    participant API as 业务 API（ExecutionClient）
    participant DB as executions / attempts 表
    participant W as OneStep worker（你的 handler）

    C->>API: POST /executions（payload + 幂等键）
    API->>DB: INSERT，状态 queued
    API-->>C: 返回 execution_id
    W->>DB: 领取任务，写一条 attempt 并持有租约
    Note over W,DB: 状态 queued → running，心跳续租
    W->>W: 执行 handler
    alt handler 成功
        W->>DB: 持久化 result，状态 succeeded
    else handler 失败
        W->>DB: 状态 retrying，重试后仍失败则 failed
    end
    loop 客户端轮询（1 / 2 / 4 / 8 秒，退避到 10~30 秒）
        C->>API: GET /executions/{id}
        API->>DB: 读状态
        API-->>C: 非终态继续等，终态返回 result
    end
    opt 不再需要结果
        C->>API: POST /executions/{id}/cancel
        API->>DB: 状态 running → cancel_requested
        W->>DB: handler 到达检查点后收敛，状态 cancelled
    end
```

要点：

- **提交带幂等键**：同一幂等键重复提交返回同一条 execution，不会重复执行。
- **终态只有四个**：`succeeded` / `failed` / `cancelled` / `expired`；`queued` / `running` / `retrying` / `cancel_requested` 都是非终态，客户端继续等待。
- **取消是协作式的**：`cancel_requested` 只是请求，worker 在 handler 的下一个检查点收敛为 `cancelled`，不保证立即停止。

## 接入前要准备什么

```mermaid
mindmap
  root((接入一个长程任务))
    准备数据库
      MySQL ≥ 8.0.16 或 PostgreSQL
      executions 与 attempts 两张表
        migration 角色建表，运行时 auto_create=False
    API 进程
      安装 ExecutionClient 与对应后端
      提交时带幂等键
      轮询用退避并设总等待上限
      取消走 cancel 接口
    Worker 进程
      MySQLExecutionSource 或 PostgresExecutionSource
      一个 source 只绑定一个 task name
      每个 worker 唯一 worker_id，主机时钟同步
      handler 自带幂等保护
    异常映射
      ExecutionNotFound 404
      ExecutionNotReady 202 或 409
      ExecutionFailed 422
      ExecutionCancelled 409
      ExecutionExpired 410
```

各状态的完整业务语义、异常映射表、部署步骤、上线清单与回滚，见对应后端页：

- [MySQL Tracked Execution](/broker/mysql-execution)
- [PostgreSQL Tracked Execution](/broker/postgres-execution)
