---
title: 用户案例 / 实战篇 | 指南
outline: deep
---

# 用户案例 / 实战篇

这里收录面向生产运行的 OneStep 实战案例。案例使用匿名的业务名称和环境变量，
重点说明可复用的连接器组合、可靠性边界、上线检查与故障恢复；业务字段转换
仍应由应用自己的 Python handler 实现。

## 案例

- [订单流水增量同步到飞书多维表格](/guide/cases/mysql-feishu-order-sync)：
  使用 MySQL 复合游标、持久化进度和飞书 Insert 键索引，把不可变订单流水
  可靠地写入多维表格。
- [SQS 消息可靠落库到 MySQL](/guide/cases/sqs-to-mysql)：
  处理 SQS 的 at-least-once 投递，用可见性心跳和 `upsert` 幂等键把消息
  可靠写入 MySQL，失败进入死信队列。
- [多连接器协调的事件分发管道](/guide/cases/multi-connector-fanout)：
  一个任务从 Redis Streams 读取，经条件路由和 per-sink transform 分发到
  MySQL、HTTP 回调和审计流，终态失败进入死信。
- [FastAPI 提交长任务并调度 Worker](/guide/cases/fastapi-execution-scheduling)：
  用 PostgreSQL tracked execution 让 FastAPI 提交任务、返回 ID，独立 worker
  异步领取执行，支持幂等提交、租约心跳和取消。

## 阅读方式

先阅读案例中的前置条件和完整 YAML，再将资源名称、环境变量、视图名与字段
映射替换为自己的业务值。案例中的 `handler` 只定义输入/输出契约；不要把
业务转换、查询或分支逻辑写进 YAML。
