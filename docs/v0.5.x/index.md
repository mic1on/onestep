---
title: v0.5.x 文档 (旧版)
---

# v0.5.x 文档

::: warning 已弃用
此文档适用于 **onestep 0.5.x** 版本，该版本已不再维护。

**强烈建议升级到 1.0.0**，详见 [迁移指南](https://github.com/mic1on/onestep/blob/main/MIGRATION-0.5-to-1.0.0.md)。
:::

## 概述

v0.5.x 是 onestep 的旧版本，使用以下核心概念：

- `@step` 装饰器定义任务
- `from_broker` / `to_broker` 指定消息来源和目标
- `step.start()` 启动任务

## 主要变化 (vs 1.0.0)

| 0.5.x | 1.0.0 |
| --- | --- |
| `@step(...)` | `app = OneStepApp(...)` + `@app.task(...)` |
| `from_broker=` | `source=` |
| `to_broker=` | `emit=` |
| `workers=` | `concurrency=` |
| `step.start(...)` | `app.run()` 或 `onestep run` |

## 文档目录

### 指南
- [入门教程](/v0.5.x/guide/tutorial) - 定时任务和爬虫示例

### 核心
- [Broker](/v0.5.x/core/broker) - 消息代理基础
- [Retry](/v0.5.x/core/retry) - 重试策略
- [Middleware](/v0.5.x/core/middleware) - 中间件

### Broker 实现
- [RabbitMQ](/v0.5.x/broker/rabbitmq)
- [Cron](/v0.5.x/broker/cron)
- [WebHook](/v0.5.x/broker/webhook)
- [Redis](/v0.5.x/broker/redis)
- [Kafka](/v0.5.x/broker/kafka) (TODO)