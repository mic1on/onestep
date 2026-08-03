---
title: 日志与任务事件 | 指南
outline: deep
---

# 日志与任务事件

从 onestep 1.7.2 开始，`onestep run` 为独立进程提供默认日志配置。应用不再需要调用 `logging.basicConfig(force=True)`，也不需要为常规任务事件手动注册 `StructuredEventLogger`。

## 应用代码

应用继续使用 Python 标准库 logger。logger 名称由应用决定，不要求以 `onestep` 开头：

```python
import logging

from onestep import IntervalSource, OneStepApp

app = OneStepApp("billing-sync")
logger = logging.getLogger("billing.kpi_sync")


@app.task(source=IntervalSource.every(hours=1, immediate=True))
async def sync_billing(ctx, _):
    logger.info("sync started")
```

使用 CLI 启动：

```bash
onestep run your_package.tasks:app
```

默认情况下，INFO 及以上级别的应用日志和任务生命周期事件会写到 stdout。

## 日志级别

```bash
onestep run your_package.tasks:app --log-level DEBUG
onestep run your_package.tasks:app --log-level WARNING
```

级别按以下优先级解析：

1. 显式传入的 `--log-level`
2. 目标加载时已经配置的级别，包括 YAML `app.logging.level`
3. 默认 `INFO`

DEBUG 会包含 fetched、started 和 sink-success 等细节。INFO 主要记录应用日志以及 succeeded、retried、failed、dead-lettered 和 cancelled 等任务结果。

## 任务事件开关

CLI 默认启用 `StructuredEventLogger`。不需要任务生命周期日志时可以关闭：

```bash
onestep run your_package.tasks:app --no-task-events
```

如果应用已经注册了 `StructuredEventLogger`，CLI 会复用现有实例，不会重复输出。其他自定义 `@app.on_event` 处理器不受影响。

## 与宿主日志配置共存

CLI 会先加载目标应用，再决定是否配置日志：

- root logger 没有 handler 时，CLI 添加 stdout handler，并在运行期间设置对应的 root level。
- root logger 已有 handler 时，CLI 不替换 handler、formatter 或 root level，日志策略继续由宿主负责。
- CLI 自己添加的 handler 和 root level 会在运行成功或失败后恢复。

因此 Gunicorn、测试框架或平台启动器已经配置日志时，onestep 不会覆盖宿主设置。

## 嵌入式运行

直接调用 `app.run()` 或 `app.serve()` 不会修改进程日志，也不会自动注册任务事件。嵌入式应用可以自行配置：

```python
import logging

logging.basicConfig(level=logging.INFO)
app.enable_structured_event_logging()
app.run()
```

`enable_structured_event_logging()` 是幂等的。如果应用需要指定独立的事件 logger，可以显式注册：

```python
import logging

from onestep import StructuredEventLogger

app.on_event(
    StructuredEventLogger(logger=logging.getLogger("billing.task_events"))
)
```

## YAML 应用

YAML 可以提供目标级别：

```yaml
app:
  name: billing-sync
  logging:
    level: WARNING
```

`onestep run worker.yaml --log-level DEBUG` 会覆盖 YAML 值。未传 `--log-level` 时保留 YAML 配置。其他 YAML 日志规则见 [YAML 任务定义](/yaml-task-definition)。

## 下一步

- [事件与生命周期](/core/middleware) - 任务事件和自定义处理器
- [生产部署](/guide/deploy) - systemd、容器和 YAML 部署
- [YAML 任务定义](/yaml-task-definition) - YAML 日志级别配置
