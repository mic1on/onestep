---
title: AI 与自动化接口 | 指南
outline: deep
---

# AI 与自动化接口

OneStep 的命令行输出是给程序（脚本、CI、AI Agent）消费的稳定契约，而不只是给人看的文本。本页列出这些契约：退出码、`--json` 输出结构、JSON Schema，以及严格校验的错误报告方式。

## 退出码

所有子命令遵循同一套退出码语义。判断"命令是否成功"请用退出码，不要解析输出文本。

| 退出码 | 含义 | 典型场景 |
|--------|------|----------|
| `0` | 成功 | `check` 校验通过；`run` 正常结束 |
| `1` | 运行时失败 | `run` 期间抛出异常；`check --connect` 探测失败；`task run/replay` 执行失败 |
| `2` | 输入或配置无效 | YAML 严格校验失败；目标无法加载；未知参数或子命令；`task` 的 `validation_failed` |

关键区别：**`2` 表示"输入有问题，重跑也没用"，`1` 表示"输入没问题，但运行失败"**。自动化脚本应区别对待：收到 `2` 时修正配置，收到 `1` 时考虑重试。

## `--json` 输出契约

`check`、`catalog`、`build`、`task run`、`task replay` 都支持 `--json`。所有 JSON 输出都带 `schema` 与 `version` 字段，便于消费方在结构变化时明确失败，而不是静默误读：

```json
{
  "schema": "onestep/check-summary",
  "version": 1,
  "target": "worker.yaml",
  "name": "demo",
  "tasks": []
}
```

已知的契约标识：

| `schema` | 命令 | 说明 |
|----------|------|------|
| `onestep/check-summary` | `check --json` | 应用与任务摘要 |
| `onestep/cli-error` | 任意 `--json` | 失败时的错误信封 |
| `onestep/diagnostic-result` | `task run` / `task replay` | 单次投递诊断结果 |
| `onestep/connectivity-result` | `check --connect --json` | 资源连通性探测结果 |

### 失败时也输出 JSON

失败时 `--json` 会在 **stdout** 输出错误信封（同时仍在 stderr 保留人类可读的一行）：

```json
{
  "schema": "onestep/cli-error",
  "version": 1,
  "command": "check",
  "target": "worker.yaml",
  "ok": false,
  "error": {
    "type": "AppConfigValidationError",
    "message": "3 validation problem(s): ...",
    "issues": [
      {"path": "config", "message": "unsupported fields for config: bogusTop", "kind": "unknown_field"},
      {"path": "resources.tick", "message": "...", "kind": "unknown_field"},
      {"path": "tasks[0]", "message": "...", "kind": "missing_field"}
    ]
  }
}
```

`issues[].kind` 的取值：`unknown_field`、`missing_field`、`invalid_type`、`invalid_value`、`unknown_resource`。`issues[].path` 使用与报错文本一致的路径记法（`tasks[0].retry`、`resources.queue.dsn`），可直接定位到出错的行。

## 严格校验：一次报告全部问题

`check --strict` 对 YAML 有两种报告方式：

- **人类路径（默认）**：fail-fast，只报第一个错误。适合在终端里逐条修复。
- **`--json` 路径**：收集并报告**全部**问题，一次调用即可拿到完整清单。

对 AI Agent 或 CI 而言，`--json` 能把"N 轮 修复→重跑"压缩成一轮：

```bash
onestep check --strict --json worker.yaml
```

两种路径的首个错误保证一致（有契约测试守护），因此不会出现"人类看到 A、脚本看到 B"的分歧。

## JSON Schema

`onestep/v1alpha1` 的 YAML 结构有对应的 JSON Schema，可让编辑器补全、校验，或让 AI 在生成配置时自校验：

```bash
onestep schema                                   # 打印到 stdout
onestep schema --out docs/public/schema/v1alpha1.json
```

Schema 是**从运行时的严格校验契约与已安装连接器目录派生**的，不是手写副本。因此：

- 连接器注册新的资源类型或字段后，Schema 自动包含它。
- Schema 只接受 `check --strict` 真正允许的字段。例如资源目录里 `mysql` 展示了 `host`/`username`，但严格校验只接受 `dsn`/`engine_options`，Schema 与严格校验保持一致。

在 YAML 里用 `$schema` 引用它（该键仅作文档用途，不影响运行时）：

```yaml
$schema: https://onestep.code05.com/schema/v1alpha1.json
apiVersion: onestep/v1alpha1
kind: App

app:
  name: demo

resources:
  tick:
    type: interval
    minutes: 5
  out:
    type: memory
    maxsize: 10

tasks:
  - name: forward
    source: tick
    emit: out
```

也支持 `yaml-language-server` 注释形式：

```yaml
# yaml-language-server: $schema=https://onestep.code05.com/schema/v1alpha1.json
```

::: tip 关于 required
Schema **不**声明 `required` 字段。资源目录对必填项的声明比严格校验更宽（例如把 `dsn`、`path` 标为必填，而 `check --strict` 实际接受省略它们）。若 Schema 强制必填，会拒绝合法配置，因此必填性以 `onestep check --strict` 为准。
:::

## 结构化日志

`--log-format json` 让每条日志（含任务生命周期事件）输出为单行 JSON，便于 Loki/ELK 索引：

```bash
onestep run worker.yaml --log-format json
```

## 相关文档

- [YAML 任务定义](/yaml-task-definition)：完整字段与严格校验边界。
- [SKILL](/skill/)：面向 AI 编程 Agent 的工作流说明。
- [核心可靠性](/core-reliability)：投递语义与失败恢复。
