# 拆分 `src/onestep/app.py` 的 `OneStepApp` god-object

日期：2026-08-27
状态：captain-approved design contract；跟踪 issue：[#146](https://github.com/mic1on/onestep/issues/146)
范围：规划；本设计提交只包含文档与任务拆分，运行时代码实现由 issue 跟踪的开发任务落实。开发顺序：T1 EventHub → T2 TaskOperations → T3 LifecycleController → T4 测试与文档收口。

## 1. 背景与目标

`src/onestep/app.py` 当前 1136 行，`OneStepApp` 是事实上的 god-object：生命周期管理、runner 注册与单任务启停、关闭/排空/暂停信号与等待者、控制面状态快照查询、死信重放/手动运行、资源与 hook/event、序列化/加载全部混在同一类里。其余模块（`runtime/runner.py`、`runtime/executor.py`、`reporter.py`、`resource_registry.py` 等）拆分良好，唯独 `app.py` 持续膨胀，回归风险集中。

> 引自架构 review（2026-08-27）："`src/onestep/app.py` 单文件 1136 行，是事实上的 god-object（生命周期、runner 管理、reporter、signal、shutdown/drain/pause 全在一处）。建议下一步把 runner/scheduler/control-plane 编排从 `OneStepApp` 中再剥离一层，降低回归面。"

本设计在不改变任何公共 API 语义、不引入新依赖（核心保持 stdlib-only）、不触及 `Source`/`Sink`/`Delivery`/`TaskRunner`/`executor` 接口的前提下，把 `OneStepApp` 拆成职责单一的子组件，让 `OneStepApp` 收缩为门面（facade），委托给子组件。

### 目标

- 将 `app.py` 从 1136 行降到 < 400 行（门面 + 合成投递辅助类）。
- 每个子组件 < 650 行，单一职责、可独立测试。
- 100% 保持公共方法名、签名与返回结构不变，外部调用方零改动。
- 核心继续 stdlib-only，零新增运行时依赖。

### 非目标

- 不新增业务功能，不改变 YAML/Python 配置契约。
- 不改变 `Source`/`Sink`/`Delivery`/`TaskRunner`/`executor` 的接口与行为。
- 不引入任何第三方依赖或新的抽象层（interface/factory 等）。
- 不改 `tests/contract` 与 `connector_conformance` 的契约断言，只作为回归护栏。

## 2. 现有证据与约束

| 证据 | 对设计的约束 |
| --- | --- |
| `app.py` 共 1136 行，`OneStepApp` 承载以下独立职责块（行号见下） | 拆分必须按职责边界迁移，不能随机切文件 |
| 生命周期：`startup()`(691) `shutdown()`(734) `serve()`(763) `run()`(833) `_install_signal_handlers()`(1066) `_open_resource`/`_close_resource`(module-level 1099/1108) | 这组构成 `LifecycleController` 的核心，含 `serve()` 主循环与资源开闭 |
| 信号与等待者：`request_shutdown`(126) `request_restart`(139) `request_drain`(143) `request_task_pause`(156) `request_task_resume`(161) `is_stopping`(112) `is_draining`(116) `restart_requested`(120) `is_task_paused`(123) `wait_for_shutdown`(346) `wait_for_drain_request`(350) `wait_for_task_pause_request`(354) `wait_for_stop_fetching`(360) `wait_for_drain`(381) `wait_for_task_pause`(390) `wait_for_task_resume`(399) `_ensure_shutdown_event`(960) `_ensure_drain_event`(969) `notify_runner_state_changed`(501) `_ensure_runner_state_event`(1058) | 全部围绕 asyncio.Event 状态机，归 `LifecycleController` |
| Runner 注册与单任务控制：`register_runners`(408) `stop_task_runner`(439) `start_task_runner`(468) `restart_task_runner`(493) `_require_controllable_task`(978) `_require_task_runners`(986) | 持有 `_runners`/`_runner_tasks`，与 `serve()` 主循环强耦合，归 `LifecycleController` |
| 控制面状态查询：`drain_status`(512) `task_pause_status`(535) `task_control_snapshot`(556) `task_control_snapshots`(587) `task_supported_commands`(594) `supports_dead_letter_replay_commands`(605) `supports_dead_letter_discard_commands`(608) `supports_manual_run_commands`(611) `task_resume_status`(614) `_task_runtime_status`(1048) | 只读快照，归 `LifecycleController`；依赖 task 能力检测 |
| 死信/手动运行：`replay_task_dead_letters`(176) `discard_task_dead_letters`(248) `run_task_once`(293) `_require_dead_letter_replay_task`(990) `_require_dead_letter_discard_task`(998) `_require_manual_run_task`(1006) `_task_supports_dead_letter_discard`(1014) `_task_supports_dead_letter_replay`(1017) `_task_supports_manual_run`(1024) `_build_dead_letter_replay_envelope`(1027) | 构成 `TaskOperations`，归 `LifecycleController` 持有或独立 `TaskOperations` |
| Hook/Event：`on_startup`(633) `on_shutdown`(636) `on_event`(639) `enable_structured_event_logging`(642) `_register_hook`(911) `_run_hooks`(924) `emit_event`(930) | 构成 `EventHub`（小，约 60 行） |
| 资源/任务定义：`bind_resources`(166) `register_resource`(169) `set_reporter_summary`(173) `task()`(650) `tasks` property(104) `resources` property(108) `_task_resources`(412) `_referenced_resource_ids`(421) | 属于领域根，保留在 `OneStepApp` 门面 |
| 序列化/加载：`describe()`(840) `load()` classmethod(889) `_invoke_app_factory`(1117) `_describe_resource`(1090) | `describe`/`load` 保留在门面；`_describe_resource` 为模块级辅助 |
| `src/onestep/testing/connector_conformance.py` 调用：`app.serve()`(145/212) `app.request_drain()`(153) `app.request_task_pause("connector_contract")`(155) `app.request_shutdown()`(157/172/179/189/235) | 公共方法名签名必须保持不变，否则 conformance 测试直接破 |
| `src/onestep/cli.py` 调用：`**app.describe()`(447) | `describe()` 返回结构不变 |
| `tests/contract/*` 依赖 `OneStepApp` 公共行为 | 拆出的子组件不得改变对外可观察行为；用 contract + conformance 作回归护栏 |
| 项目约定：核心零运行时依赖，std-only | 子组件不得引入任何第三方库 |

## 3. 设计

采用**组合 + 委托（facade）**模式，而非继承。`OneStepApp` 仍是对外唯一的公共类，内部持有一个或多个子组件实例，将原方法实现迁移到子组件后，在 `OneStepApp` 上保留同名、同签名、同返回结构的薄委托方法。这样 `connector_conformance.py`、`cli.py`、全部 contract 测试、用户代码调用路径**零改动**。

### 3.1 新增子组件（全部位于 `src/onestep/runtime/`，stdlib-only）

**`runtime/lifecycle.py` → `LifecycleController`**

拥有全部运行期状态与编排逻辑：

- 状态：`_shutdown`/`_shutdown_requested`、`_drain`/`_drain_requested`、`_restart_requested`、`_paused_tasks`、`_runner_state`、`_runners`、`_runner_tasks`、`_loop`、`_resources`、`_events_logger`。
- 生命周期：`startup()`、`shutdown()`、`serve()`、`run()`、`_install_signal_handlers()`、模块级 `_open_resource`/`_close_resource`。
- 信号 API：`request_shutdown`/`request_restart`/`request_drain`/`request_task_pause`/`request_task_resume`、`is_stopping`/`is_draining`/`restart_requested`/`is_task_paused`、`notify_runner_state_changed`、`_ensure_shutdown_event`/`_ensure_drain_event`/`_ensure_runner_state_event`。
- 等待者：`wait_for_shutdown` / `wait_for_drain_request` / `wait_for_task_pause_request` / `wait_for_stop_fetching` / `wait_for_drain` / `wait_for_task_pause` / `wait_for_task_resume`。
- Runner 注册与单任务控制：`register_runners`、`stop_task_runner`、`start_task_runner`、`restart_task_runner`、`_require_controllable_task`、`_require_task_runners`。
- 控制面状态查询：`drain_status`、`task_pause_status`、`task_control_snapshot`、`task_control_snapshots`、`task_supported_commands`、`supports_dead_letter_replay_commands`、`supports_dead_letter_discard_commands`、`supports_manual_run_commands`、`task_resume_status`、`_task_runtime_status`。
- 持有并委托 `TaskOperations` 与 `EventHub`（构造时注入），或直接调用其方法。

**`runtime/task_ops.py` → `TaskOperations`**

- `replay_task_dead_letters`、`discard_task_dead_letters`、`run_task_once`。
- 能力检测与守卫：`_require_dead_letter_replay_task`、`_require_dead_letter_discard_task`、`_require_manual_run_task`、`_task_supports_dead_letter_discard`、`_task_supports_dead_letter_replay`、`_task_supports_manual_run`、`_build_dead_letter_replay_envelope`。
- 依赖 `LifecycleController` 提供的 task 列表与 `TaskRunner`，通过构造注入。

**`runtime/event_hub.py` → `EventHub`**

- `on_startup`、`on_shutdown`、`on_event`、`enable_structured_event_logging`、`_register_hook`、`_run_hooks`、`emit_event`。

### 3.2 `OneStepApp` 收缩为门面

构造器创建 `LifecycleController`（内含 `TaskOperations`、`EventHub`）：

```python
class OneStepApp:
    def __init__(self, name, *, config=None, state=None,
                 shutdown_timeout_s=30.0, failure_capture=None):
        # ...原有字段校验...
        self._lifecycle = LifecycleController(
            name=name, state=self.state,
            shutdown_timeout_s=shutdown_timeout_s,
            failure_capture=self._failure_capture_writer,
            custom_metrics=self.custom_metrics,
        )
        # 资源/hook/event 委托到 _lifecycle
```

保留在 `OneStepApp` 的门面方法（仅委托，无逻辑）：

- `tasks` / `resources` 属性（`_lifecycle` 持有 `_tasks` 与 `_named_resources`，或门面持有、`LifecycleController` 引用）。
- `bind_resources` / `register_resource` / `set_reporter_summary`（写入共享状态）。
- `task()` 装饰器（注册 `TaskSpec` 到共享 task 列表）。
- `describe()` / `load()`（`describe` 汇总来自 `_lifecycle` 的运行时状态 + 门面的 task/resource 定义）。
- `replay_task_dead_letters` / `discard_task_dead_letters` / `run_task_once` → 委托 `_lifecycle.operations`。
- 所有 `request_*` / `is_*` / `wait_for_*` / `task_control_*` / `drain_status` / `task_pause_status` / `task_resume_status` / `supports_*_commands` → 委托 `_lifecycle`。
- `on_startup` / `on_shutdown` / `on_event` / `enable_structured_event_logging` / `emit_event` → 委托 `_lifecycle.events`。
- `register_runners` / `stop_task_runner` / `start_task_runner` / `restart_task_runner` → 委托 `_lifecycle`。

`_SyntheticManualRunDelivery`（26-54）保留在 `app.py` 或随 `TaskOperations` 迁移（建议保留在 `app.py`，仅 manual run 使用）。

### 3.3 状态所有权（关键）

原 `serve()` 主循环直接读写 `self._runners`/`self._runner_tasks`/`self._shutdown`/`self._drain`/`self._paused_tasks` 等约 20 处内部属性。拆分后这些状态**唯一归属** `LifecycleController`，`OneStepApp` 不再重复持有，只通过委托方法访问。避免跨对象双重持有导致状态不一致。

## 4. 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| `serve()` 主循环与内部状态高度耦合，拆分易引入并发/竞态回归 | 状态所有权一次性整体迁移到 `LifecycleController`；以 `tests/contract` + `connector_conformance.py` 的 drain/pause/shutdown 路径作为回归护栏 |
| 外部调用方（conformance、cli、用户代码）依赖方法名与返回结构 | 委托层保持同名同签名同返回；CI 跑 `{pytest -m "not integration"}` 基线 606 passed 必须全绿 |
| 子组件之间循环依赖（Lifecycle ↔ TaskOperations ↔ EventHub） | 构造期单向注入：`LifecycleController` 拥有 `TaskOperations` 与 `EventHub`，子组件不反向引用 `OneStepApp` |
| 拆文件改变 import 路径，波及下游 | 仅新增 `runtime/lifecycle.py` 等文件，`OneStepApp` 仍从 `onestep` 包顶层导出，公开 import 路径不变 |

## 5. 任务拆分（供开发）

按风险从低到高、可独立验证的顺序：

- **T1 — 抽 `EventHub`**：新建 `runtime/event_hub.py`，迁移 hook/event 7 个方法；`OneStepApp` 委托。最小、独立、低风险，先验证门面委托模式可行。
- **T2 — 抽 `TaskOperations`**：新建 `runtime/task_ops.py`，迁移死信重放/手动运行 + 能力检测辅助；`OneStepApp` 委托。
- **T3 — 抽 `LifecycleController`**：新建 `runtime/lifecycle.py`，整体迁移 `serve`/`startup`/`shutdown`/`run`/信号/runner registry/waiter/状态查询；`OneStepApp` 仅委托。最大块，依赖 T1/T2 落地。
- **T4 — 收尾验证**：跑 `uv run pytest -m "not integration"`（基线 606 passed）+ `tests/contract`，确认零回归；更新 `AGENTS.md` 记录新模块边界（`OneStepApp` 为门面，`runtime/lifecycle.py` 为编排核心）。

## 6. 验收标准

- `app.py` < 400 行（`OneStepApp` 门面 + `_SyntheticManualRunDelivery` + 模块级辅助）。
- `runtime/lifecycle.py` / `runtime/task_ops.py` / `runtime/event_hub.py` 各 < 650 行，单一职责。
- 公共方法名、签名、返回结构与拆分前**逐字节等价**（除内部实现迁移）。
- `uv run pytest -m "not integration"` 全绿（基线 606 passed），`tests/contract` 全绿。
- 核心运行时依赖不变（stdlib-only）。
