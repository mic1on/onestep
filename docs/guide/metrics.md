---
title: 指标与健康检查 | 指南
outline: deep
---

# 指标与健康检查

onestep 内置一个零依赖的 asyncio HTTP 服务，用于暴露 Prometheus `/metrics` 和 `/healthz` 端点。`onestep[metrics]` extra 本身不含额外依赖，只是为编排器和文档提供一个稳定的选择入口。

## 启动指标端点

用 `--metrics-addr` 指定监听地址：

```bash
onestep run your_package.tasks:app --metrics-addr :9100
```

地址写法：

- `HOST:PORT` —— 绑定指定地址，如 `127.0.0.1:9100`
- `:PORT` —— 绑定所有网卡，如 `:9100`
- `PORT` —— 等价于 `127.0.0.1:PORT`

未显式指定主机时默认绑定 `127.0.0.1`，默认端口 `9100`。

## /metrics

`/metrics` 输出 Prometheus 文本格式，包含以下系列（均带 `app`、`task` 标签）：

| 指标 | 类型 | 说明 |
|---|---|---|
| `onestep_deliveries_fetched_total` | counter | 从 source 拉取的消息数 |
| `onestep_tasks_processed_total` | counter | 任务终态结果（按 `status` 标签） |
| `onestep_task_duration_seconds` | histogram | 任务尝试耗时 |
| `onestep_inflight_tasks` | gauge | 当前在途的任务尝试数 |
| `onestep_tasks_retried_total` | counter | 进入重试的任务尝试数 |
| `onestep_tasks_dead_lettered_total` | counter | 投递到死信 sink 的消息数 |
| `onestep_tasks_cancelled_total` | counter | 被取消的任务尝试数 |
| `onestep_task_failures_total` | counter | 按 `failure_kind` 分类的失败数 |
| `onestep_build_info` | gauge | 版本元数据 |

任务处理函数还可以通过 `ctx.metrics` 上报自定义 counter / gauge：

```python
async def sync_users(ctx, payload):
    ...
    ctx.metrics.counter("rows_success").inc(1)
    ctx.metrics.gauge("batch_size").set(42)
```

这些自定义指标会带上 `task` 标签和用户标签，出现在 `/metrics` 输出中。

## /healthz

`/healthz` 返回 JSON 存活信息：

```json
{
  "status": "ok",
  "app": "billing-sync",
  "version": "1.11.0",
  "uptime_s": 120.5,
  "stopping": false,
  "tasks": [
    {
      "task": "sync",
      "source": {"name": "orders", "kind": "RabbitMQQueue", "alive": true},
      "inflight": 2
    }
  ]
}
```

- `status` 在所有 source 存活且未处于停止流程时为 `ok`，否则为 `degraded`。
- `source.kind` 是 source 对象的类名；`source.alive` 反映该 source 的 `is_open` 状态。
- `inflight` 是当前任务的在途尝试数。

负载均衡器可以据此做就绪/存活探测。

## 嵌入式使用

不通过 CLI 时，可以直接在代码里安装：

```python
from onestep.observability import install_metrics

handle = install_metrics(app, host="0.0.0.0", port=9100)
...
await handle.close()
```

`install_metrics` 会注册事件处理器、在资源打开后绑定监听、并在关闭时释放。`port=0` 支持自动选择端口，适合测试和嵌入式场景。

## 示例

完整可运行的 Prometheus + Grafana 监控栈见仓库 [`examples/prometheus/`](https://github.com/mic1on/onestep/tree/main/examples/prometheus)。

## 下一步

- [日志与任务事件](/guide/logging) - `--log-format json` 结构化日志
- [生产部署](/guide/deploy) - CLI 与容器部署
- [Control Plane](/control-plane/) - reporter 遥测与远程任务控制
