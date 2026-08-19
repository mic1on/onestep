# onestep-postgres async SQLAlchemy 迁移评估与计划

状态: 评估 / 计划（未实施）
日期: 2026-08-19
参考: onestep-mysql 0.4.0 async 迁移（commit `8db759c`、`1ae1a90`）；MongoDB 插件设计（原生 async 决策）

## 1. 现状盘点：sync 表面到底有多大

| 文件 | 行数 | `asyncio.to_thread` | 说明 |
|------|------|--------------------|------|
| `connector.py` | 668 | 5 | sync `create_engine`；`_table()` sync autoload；table_queue/incremental/table_sink 的 `_*_sync` 方法 |
| `state_sqlalchemy.py` | 151 | 4 | sync engine + `threading.Lock`（datetime 编解码已对齐 mysql 0.5.1） |
| `execution_backend.py` | 1157 | 11 | 15+ 个 `_*_sync` 方法；`threading.Lock`；fork/PID 安全逻辑；advisory lock 建表 |
| `execution_source.py` | 474 | 0 | 已是 async 接口（协议层无需改动），内部调 backend 的 async 包装 |
| `execution_schema.py` / `resilience.py` / `resources.py` | 762 | 0 | 无 engine 使用，迁移零影响 |

合计约 **20 个 to_thread 包装点、18+ 个 sync 方法需要转 native async**。对外协议（Source/Sink/LeasedExecutionBackend）全部已经是 async 签名，**迁移完全发生在插件内部**。

## 2. 驱动事实（PostgreSQL 比 MySQL 当时条件更好）

- **psycopg 3 原生同时支持 sync 和 async**：同一个 `postgresql+psycopg` 方言字符串既可用于 `create_engine` 也可用于 `create_async_engine`。**不需要换驱动**（MySQL 当时必须引入 asyncmy）。
- 生产 DSN 无需用户改动；插件内部需要 `_async_dsn()` 归一化（照搬 mysql 模式）：
  - `postgresql://` / `postgresql+psycopg2://` → `postgresql+psycopg://`
  - `sqlite://` / `sqlite+pysqlite://` → `sqlite+aiosqlite://`
- 测试依赖：`aiosqlite` 需加入 onestep-postgres 依赖（uv.lock 里已有，来自 mysql 插件，但 postgres 必须自己声明）。`psycopg[binary]` 二进制 wheel 已含 async 支持。
- in-memory sqlite + async 需要 `poolclass=StaticPool`——现有测试全部用文件型 sqlite，暂无此需求（实现时验证）。

## 3. 收益评估（诚实版）

| 收益 | 强度 | 说明 |
|------|------|------|
| 消除双实现漂移 | ★★★ | datetime cursor bug 已经证明：mysql 改 5 处、pg 只跟上 1 处，落后 3 天才修。async 化后两边状态存储/source/sink 代码结构完全一致，review 和同步成本大降 |
| 取消语义正确 | ★★★ | `to_thread` 无法中断——worker shutdown/drain 时正在执行的 SQL 会跑完（lease 心跳场景下可能拖过 deadline）。native async 下 CancelledError 会传播为 psycopg 的 query cancel |
| 高并发吞吐 | ★★ | 本机默认线程池仅 14 workers（`min(32, cpu+4)`）。concurrency=100 场景下 fetch/send/ack 全部排队等线程，每次操作增加调度延迟 |
| 与仓库方向一致 | ★★ | MongoDB 插件设计文档已明确决策"不用 sync 客户端包线程"（change-stream 长占用线程、取消复杂）。新插件全是 native async，pg 是最后的 sync 堡垒 |
| 性能本身 | ★ | 单次查询开销差异不大（to_thread 开销 ~50µs 级）。**不要为性能做这个迁移** |

结论：值得做，但主要理由是**一致性、取消语义、并发调度**，不是单查询性能。

## 4. 风险清单（按严重度）

### R1 fork/进程安全（最大风险，mysql 没有这块）
PG execution backend 有刻意的 fork 安全设计（有专测：`test_owned_backend_rebuilds_pool_after_process_boundary`、`test_external_connector_rejects_use_after_process_boundary`）：
- 子进程检测到 pid 变化后，owned 模式丢弃继承的 engine 并懒重建；external 模式直接报错。
- 现状用 `engine.dispose(close=False)`（sync，立即返回）丢弃继承连接池。
- **AsyncEngine 的 `dispose(close=False)` 是协程**，fork 后的子进程里不能安全 await。

决策：子进程路径**跳过 dispose，直接丢弃引用**。分析：子进程的 fd 是父进程 socket 的副本，GC 关闭只影响子进程自己的 fd，不会向服务器发 FIN（父进程仍持有自己的 fd），安全。父进程行为不变（正常 close 路径仍是 `await engine.dispose()`）。此偏离需在代码注释和测试断言中写明。

### R2 `threading.Lock` → `asyncio.Lock`
- backend 的 `_ready_lock` 在 to_thread 里跨 DB 调用持有；转 async 后自然是 `asyncio.Lock`。
- 必须用**懒绑定 + 按事件循环重绑**（mysql `1ae1a90` 的教训 + PG incremental `_runtime_commit_lock` 已有现成模式）：构造发生在 loop 启动前，onestep 可能跨 loop 重启。
- fork 检测逻辑本身保留（比较 pid 不依赖 engine 类型）。

### R3 对外破坏性变更
`PostgresConnector.engine` 从 `Engine` 变 `AsyncEngine`。直接用 `db.engine.begin()` 的用户代码会破坏。与 mysql 0.4.0 的先例一致（当时也是直接破坏）。YAML 表面零变化（docs/broker/postgres.md 不引用 `.engine`）。版本号 **0.4.0**，CHANGELOG 显著标注 breaking。

### R4 execution backend 事务语义回归面（最大的测试面）
claim/heartbeat/complete/release/expire/reclaim 的 lease fencing、advisory-lock 建表（`conn.run_sync` + `pg_advisory_xact_lock`，async engine 原生支持 `await conn.run_sync(...)`）、DB 时间权威化 SQL 均不变——只是执行方式从 sync 包线程变 native。11 个包装方法逐一机械转换，但**每一步都要跑全部 57 个 execution 测试**（现有 backend+source 测试已很完备，这是迁移的安全网）。

### R5 `sa.inspect(engine)`（auto_create=False 路径）
sync `sa.inspect` 不能用于 AsyncEngine。改为 `async with engine.connect() as conn: await conn.run_sync(lambda c: sa.inspect(c)...)`。

## 5. 方案对比

| 方案 | 说明 | 结论 |
|------|------|------|
| A. 全量迁移（照搬 mysql 模式） | connector + state + sources + sink + execution backend 全部 native async | **推荐** |
| B. 只迁数据路径，execution backend 留 sync | 一个 app 里两个 engine（YAML 里 execution source 和 incremental 共享同一 connector） | 否决：YAML `postgres_execution_source.connector` 与其他资源共享 connector，双引擎会导致同一 DSN 两个连接池，且 `_build_postgres_execution_source` 直接用 `connector.execution_backend()` |
| C. 维持 sync | to_thread 可用 | 否决：漂移已实际造成 bug（#125 落后 3 天）；与 mongodb/mysql 方向背行；并发 100 时线程池排队 |
| D. 先抽 `onestep-sqlalchemy` 公共基座，async 一起做 | 一次解决两个问题 | 否决（仅顺序问题）：改动面爆炸。先 async（有 mysql 现成参照），后抽公共层（届时两边结构已一致，抽取反而更容易） |

## 6. 分期实施计划

每期独立 PR、独立可合并、独立回滚；全程以 mysql 实现为对照（代码结构逐方法对齐）。

### PR-1: connector + state store（数据基座）
范围：
- `PostgresConnector`: `create_async_engine(_async_dsn(dsn))`、`async close()`、懒绑定 `asyncio.Lock` 的 `async _table()`（`conn.run_sync` autoload）
- `state_sqlalchemy.py`: 整文件对齐 mysql 版（native async load/save/delete/close/`_ensure_ready`、懒 asyncio.Lock；datetime 编解码已就位）
- `pyproject.toml`: + `SQLAlchemy[asyncio]`、`aiosqlite`、（test）`pytest-asyncio`
- 测试：state/incremental/table_queue/plugin 测试的 DSN 从 `sqlite:///` → `sqlite+aiosqlite:///`；测试夹具建表仍可用独立 sync engine（减少 churn）
- 预估：~250 行改动，~50 个测试触碰

验收门：postgres 全套非 integration 测试绿；mysql 全套绿（对照无回归）。

### PR-2: sources + table sink
范围：
- `PostgresTableQueueSource`: `async _fetch`/`_update_row`（去 to_thread，`async with engine.begin()`）
- `PostgresIncrementalSource`: 同上
- `PostgresTableSink`: `_send_sync` → `async _send`（昨天新写的 update mode/policies 逻辑仅外壳转换，`_build_statement`/`_update_payload`/`_coerce_json_values` 保持 sync 纯函数）
- 预估：~200 行改动

验收门：同 PR-1 + live PG DSN 冒烟（若配置了 `ONESTEP_POSTGRES_DSN`）。

### PR-3: execution backend + source（最大单块）
范围：
- 11 个 to_thread 包装删除，15+ 个 `_*_sync` 方法转 native async（`_submit/_get/_list/_request_cancel/_claim/_expire_queued/_expire_cancel_requests/_release_expired_leases/_heartbeat/_complete/_release`）
- `_ready_lock` → 懒绑定 + per-loop 重绑的 asyncio.Lock；`_ensure_connector` 变 async（`create_async_engine` 本身是 sync 调用，`engine` property 可保持 sync 返回类型 Any）
- fork 路径：R1 决策（子进程跳过 dispose 直接丢弃引用），保留 pid 比较与 external-connector 拒绝语义；改写两个 fork 测试的断言方式
- `_ensure_ready`: `sa.inspect` → `run_sync`（R5）；advisory-lock 建表 → `await conn.run_sync(...)`
- 预估：~600 行改动，execution 的 57 个测试是安全网

验收门：postgres 全套 + `ONESTEP_POSTGRES_DSN` 下 live execution 契约测试绿。

### PR-4: 发布收尾
- 版本 0.3.0 → **0.4.0**（breaking: engine 类型），CHANGELOG、`docs/broker/postgres.md`/`postgres-execution.md` 补 async 说明与 breaking note
- README 示例如有 `db.engine` 用法则更新

### 明确不在本计划范围内（另立 issue）
1. **incremental commit waves / retry rows 移植**（mysql 0.5.0 的语义增强）：这是行为变更不是驱动迁移，混在一起会把回归归因搞混。建议单独 issue，且在 async 迁移**之后**做——与 mysql 当年顺序一致（0.4.0 async → 0.5.0 commit waves）。
2. **`onestep-sqlalchemy` 公共基座抽取**（前一轮讨论的方案 A）：等两边都 native async、结构一致后做，抽取成本更低。

## 7. 关键技术决策摘要

| 决策点 | 结论 |
|--------|------|
| 驱动 | psycopg3 不换（原生双模）；测试 sqlite→aiosqlite |
| DSN 归一化 | 新增 `_async_dsn()`：`postgresql[+psycopg2]://`→`postgresql+psycopg://`，`sqlite[+pysqlite]://`→`sqlite+aiosqlite://` |
| 锁策略 | 懒绑定 asyncio.Lock + per-loop 重绑（复用 `_runtime_commit_lock` 模式） |
| fork 丢弃 | 子进程不 dispose 直接丢引用（安全分析见 R1），父进程正常 await dispose |
| engine property | 保持 sync（create_async_engine 不连接，无 IO） |
| 纯函数保持 | `_build_statement`/`_update_payload`/`_coerce_json_values`/encode 系列保持 sync 纯函数，便于单测 |
| 协议层 | 零改动（Source/Sink/LeasedExecutionBackend 已是 async） |
| 版本 | 0.4.0（minor，含 breaking note；0.x 阶段遵循现有惯例不升 major） |
