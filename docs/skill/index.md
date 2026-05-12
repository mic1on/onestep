---
title: SKILL | OneStep
outline: deep
---

# SKILL

`skills/onestep/` 提供面向 AI 编程代理的 OneStep 工作流说明和可复用资料。它不是运行时 API，也不会被应用进程加载；它用于让 Codex、Claude 等代理在生成、修改、校验 OneStep worker 时遵循同一套边界和命令。

## 适用场景

当任务涉及以下内容时，代理应优先读取 OneStep Skill：

- 创建或修改 `OneStepApp` 应用。
- 编写 `worker.yaml`、资源定义、任务处理函数或 hooks。
- 配置 Memory、Cron、Webhook、RabbitMQ、Redis Streams、AWS SQS、MySQL 等连接器。
- 添加重试、死信、并发、超时或 Control Plane reporter。
- 从旧版 `step` / broker API 迁移到当前运行时。
- 为 worker 选择校验命令和测试范围。

## 目录结构

```text
skills/onestep/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── assets/
│   └── yaml-project-template/
├── references/
│   ├── quickstart.md
│   ├── yaml-task-definition.md
│   ├── python-api.md
│   ├── connectors.md
│   ├── control-plane.md
│   ├── testing.md
│   └── migration-0.5-to-1.0.md
└── scripts/
    ├── scaffold_worker.py
    └── check_worker.py
```

`SKILL.md` 是入口文件，定义代理的判断流程、默认取舍和最小示例。`references/` 按主题拆分资料，代理只需要读取与当前任务相关的文件。`assets/yaml-project-template/` 是脚手架兜底模板，`scripts/` 提供本地创建和校验 worker 的辅助命令。

## 工作原则

OneStep Skill 的默认取舍是保持 worker 足够小：

- 优先用 YAML 表达运行时 wiring，用 Python 承载业务逻辑。
- 不把 transform、条件分支、工作流 DSL 或表达式引擎塞进 YAML。
- `tasks[].config` 用于任务定义数据，运行时通过 `ctx.task_config` 读取。
- `handler.params` 用于调用 Python handler 或 hook 时传入参数。
- 不默认启用 reporter、hooks、死信、复杂重试或额外资源，除非任务需要。
- 长期维护的 YAML 使用 `apiVersion: onestep/v1alpha1`、`kind: App` 和严格校验。

## 最小 Python Worker

```python
from onestep import IntervalSource, OneStepApp

app = OneStepApp("billing-sync")


@app.task(source=IntervalSource.every(hours=1, immediate=True, overlap="skip"))
async def sync_billing(ctx, item):
    print("syncing billing data")
```

检查或运行：

```bash
onestep check your_package.tasks:app
onestep run your_package.tasks:app
```

## 最小 YAML Worker

```yaml
apiVersion: onestep/v1alpha1
kind: App

app:
  name: billing-sync

resources:
  tick:
    type: interval
    minutes: 5
    immediate: true

tasks:
  - name: sync_billing
    source: tick
    handler:
      ref: your_package.tasks.billing:sync_billing
```

长期维护或由 AI 修改的 YAML，建议使用严格校验：

```bash
onestep check --strict worker.yaml
```

## 辅助脚本

从仓库根目录可以直接运行 Skill 中的辅助脚本：

```bash
python skills/onestep/scripts/scaffold_worker.py ./billing-sync
python skills/onestep/scripts/check_worker.py ./billing-sync --pytest
```

`scaffold_worker.py` 会优先调用已安装的 `onestep init`。如果当前环境没有 `onestep` CLI，则回退到 `assets/yaml-project-template/` 里的最小模板。

`check_worker.py` 默认对 YAML worker 执行：

```bash
onestep check --strict worker.yaml
```

需要校验 Python app target 时可显式传入：

```bash
python skills/onestep/scripts/check_worker.py . --app-target your_package.tasks:app
```

## 相关文档

- [快速开始](/guide/)：面向普通使用者的安装、运行和第一条任务。
- [YAML 任务定义](/yaml-task-definition)：完整 YAML 边界、字段和严格校验。
- [连接器概览](/broker/)：Memory、Cron、Webhook、RabbitMQ、Redis、SQS 和 MySQL。
- [Control Plane](/control-plane/)：运行时 telemetry 和 WebSocket 控制面集成。
