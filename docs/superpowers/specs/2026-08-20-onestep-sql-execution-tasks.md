# `onestep-sql` 合并落地 — 执行任务清单

日期：2026-08-20
状态：draft execution plan（待 captain 确认后逐阶段推进）
来源设计：`docs/superpowers/specs/2026-08-20-onestep-sql-consolidation-design.md`（PR #134，已合并）
追踪 Issue：#133

> 本清单把设计文档第 10 节的分阶段实施计划（Phase 0–5）拆解为可执行的任务、退出门槛与验收追踪。每个 Phase 独立完成、独立 PR、独立 review；未完成的 Phase 不阻塞后续 PR 的评审，但代码合并顺序须遵循依赖关系。

---

## 0. 总原则（来自设计 §1 非目标，任何 Phase 不得违反）

- 不改变 `Source` / `Sink` / `Delivery` / runner / `ResourceRegistry` / YAML loader 的 core API。
- 不重命名、删除或新增 generic `sql_*` YAML type；不改动已有 YAML 文件。
- 不实现 PostgreSQL logical replication/CDC。
- 不把 MySQL binlog 做成 PostgreSQL feature，也不把 PostgreSQL execution backend/source 做成 MySQL feature。
- MySQL binlog CDC（`mysql_binlog`，需正 `server_id`）始终 MySQL-only。
- PostgreSQL tracked execution（`postgres_execution_source`、execution backend/source、lease/heartbeat/reclaim/cancellation）始终 PostgreSQL-only。
- 14 个 YAML type 名、catalog role、allowed fields、defaults、validators、connector boundaries 全部保持不变。

## 1. 阶段路线图

| 阶段 | 目标 | 退出门槛（设计原文） | 依赖 |
| --- | --- | --- | --- |
| **Phase 0** | 基线盘点 + 契约测试 | 两插件在当前结构中有完整 baseline；测试能证明当前 YAML/API 兼容性与 duplicate-registration failure mode | — |
| **Phase 1** | 建 canonical 包，不改消费者 | `onestep-sql[mysql]`/`[postgres]`/`[all]` 可构建、发现资源并通过对应 suites；14 个 YAML type 未变 | Phase 0 |
| **Phase 2** | 抽取共用行为、收敛测试 | 共用代码只有一份；行为、live compatibility、error-redaction contracts 均通过；无未解释的复制实现 | Phase 1 |
| **Phase 3** | 发兼容发行包 + 切换一手消费者 | 新旧同装不重复注册；legacy imports/YAML 工作；root extras 与 worker 用 canonical；release gates 通过 | Phase 2 |
| **Phase 4** | 文档与采用 | 公开入口不再把旧 distribution 作为推荐安装方式，且明确 YAML names 未变 | Phase 3 |
| **Phase 5** | 弃用收尾（不早于承诺窗口） | 破坏性动作满足时间与发布条件，且用户已提前一个 feature release 被告知 | Phase 4 + 时间窗 |

---

## 2. Phase 0 — Baseline and contract tests

**目标**：在实施任何改动前，先证明当前 `onestep-mysql` 与 `onestep-postgres` 的公开契约（YAML catalog、Python API、entry-point 组合、live 行为）是“已知良好”的，使后续 Phase 有可回归的基线。

**任务：**

- [ ] **P0.1 公开导出盘点**
  - 枚举 `plugins/onestep-mysql/src/onestep_mysql/` 与 `plugins/onestep-postgres/src/onestep_postgres/` 的全部 public 导出（`__init__.py` 与 `connector` / `resources` / `resilience` / `state_sqlalchemy`；postgres 额外 `execution_backend` / `execution_schema` / `execution_source`）。
  - 记录每个公开符号（class/function/exception）的导入路径与身份，作为 Phase 1/3 转发兼容的契约基线。
- [ ] **P0.2 Submodule import 路径快照**
  - 列出历史 shipping submodule 路径：`onestep_mysql.connector/resources/resilience/state_sqlalchemy` 与 `onestep_postgres.*` 同名集合 + 三个 execution 模块。
  - 写入契约测试 fixture，确保 Phase 3 转发后这些路径仍指向同一对象。
- [ ] **P0.3 YAML catalog 快照测试（当前 14 type）**
  - 对 14 个 type 名（`mysql`, `mysql_state_store`, `mysql_cursor_store`, `mysql_table_queue`, `mysql_incremental`, `mysql_binlog`, `mysql_table_sink`, `postgres`, `postgres_state_store`, `postgres_cursor_store`, `postgres_table_queue`, `postgres_incremental`, `postgres_execution_source`, `postgres_table_sink`）生成 strict catalog 快照：catalog role、allowed fields、defaults、topology fields、validators、builder 语义。
  - 作为 golden 快照存入 `tests/`，后续 Phase 不得改变。
- [ ] **P0.4 Entry-point 组合基线测试**
  - 新增安装组合测试（在隔离 venv 中）：仅 `onestep-mysql`、仅 `onestep-postgres`、两者同装，断言 `load_resource_plugins()` 注册且无重复（利用 `ResourceRegistry` 的 idempotent-equality，`src/onestep/resource_registry.py`）。
  - 明确记录 duplicate-registration 的 failure mode（不同 callable 触发 `ValueError("resource type ... is already registered")`），作为 Phase 3 的回归红线。
- [ ] **P0.5 文档与脚本消费者盘点**
  - 列出所有引用旧 package 路径的消费者：`docker/worker/Dockerfile`、`scripts/run-reliability-checks.sh`、`scripts/run-integration-tests.sh`、`tests/test_database_plugin_integration.py`、`.github/workflows/plugin-mysql.yml` / `plugin-postgres.yml`、`docs/broker/*`、`README`、skills、examples、worker 部署文档。
  - 输出迁移清单，供 Phase 3/4 逐项勾销。
- [ ] **P0.6 保持 `check_plugin_drift.py` 运行**
  - 确认 `scripts/check_plugin_drift.py`（监控 state stores / table-sink policy / incremental state-key 三对）在 CI 中仍通过；它是 Phase 2 完成前的兼容性护栏。
  - 修正任何未被测试覆盖的当前公开契约缺口（若发现则补测试，不补实现）。

**Exit**：两插件在当前结构中有完整 baseline；测试能证明当前 YAML/API 兼容性与 duplicate-registration failure mode。

---

## 3. Phase 1 — Create canonical package without changing consumers

**目标**：新增 `onestep-sql` canonical 包（namespace `onestep_sql`，后端子包 `mysql`/`postgres`/`_shared`），先以“复制 + 转发 alias”方式保留 API，**不切换** root extras / worker / 文档。

**任务：**

- [ ] **P1.1 Workspace member 与包骨架**
  - 新增 `plugins/onestep-sql/`：`pyproject.toml`、`README.md`、`src/onestep_sql/__init__.py`（仅 version + 唯一 `register`）、`resources.py`（唯一 `onestep.resources` registrar）。
  - 声明唯一 entry point：`sql = "onestep_sql.resources:register_resources"`。
  - registrar 在同一 registry 内以稳定顺序注册全部 14 个 handler；**注册阶段不得要求两种 driver 都已安装**：backend driver 导入推迟到 resource build / connector use 时。
- [ ] **P1.2 后端子包（复制现有实现）**
  - `onestep_sql.mysql`：`connector.py` / `resources.py` / `resilience.py` / `binlog.py`，公开 `MySQLConnector`、`TableSink`、`BinlogSource` 等。
  - `onestep_sql.postgres`：`connector.py` / `resources.py` / `resilience.py` / `execution_backend.py` / `execution_schema.py` / `execution_source.py`，公开 `PostgresConnector`、`PostgresTableSink`、`PostgresExecutionBackend`、`PostgresExecutionSource` 等。
  - 命名沿用当前后端术语，新文档/type hints/示例改用 `onestep_sql.*` namespace。
- [ ] **P1.3 `_shared` 私有骨架**
  - 建立 `onestep_sql/_shared/`（SQLAlchemy stores、queue/incremental sequencing、sink policy、通用测试 helpers 占位）；Phase 1 暂不强求去重，允许与后端内实现并存。
- [ ] **P1.4 extras 声明**
  - canonical extras：`onestep-sql[mysql]`（asyncmy + mysql-replication + 共享 SQL deps）、`[postgres]`（psycopg[binary] + 共享）、`[all]`。
  - 共享依赖取单一 lower bound，且不低于当前两包中较高的 `onestep` lower bound。
- [ ] **P1.5 转发 alias 保留 API**
  - 在 canonical 包内以 alias 保留旧公开符号（不删除旧路径），使后续 Phase 3 转发能指向同一对象，保证 `isinstance`/异常身份一致。
- [ ] **P1.6 独立可构建 + 测试**
  - 验证 `onestep-sql[mysql]` / `[postgres]` / `[all]` 各自可构建、可发现资源、通过对应 suites；14 个 YAML type 未变（对照 P0.3 快照）。

**Exit**：`onestep-sql[mysql]`、`[postgres]` 和 `[all]` 可构建、发现资源并通过对应 suites；所有 14 YAML type 未变。

---

## 4. Phase 2 — Extract shared behavior and converge tests

**目标**：把 drift 脚本监控的三对（state stores / table-sink policy / incremental state-key）迁入 `_shared`，双后端 contract/live 测试通过后删除 drift 脚本与 CI job。

**任务：**

- [ ] **P2.1 抽取 state stores**
  - 将 MySQL/Postgres 的 SQLAlchemy state/cursor store 序列化迁入 `onestep_sql._shared`；保留后端 adapter（dialect/driver 差异）。
- [ ] **P2.2 抽取 table-sink 写入策略**
  - 将 `insert`/`upsert`/`update` 的 column-write policy 迁入 `_shared`；保留 backend-specific 边界（如 null write policy、update_columns）。
- [ ] **P2.3 抽取 incremental state-key 与 sequencing**
  - 将 table queue / incremental delivery sequencing 与 `_default_incremental_state_key` 迁入 `_shared`；确保 at-least-once cursor/ack/retry 行为不变。
- [ ] **P2.4 保留后端专属分区**
  - MySQL binlog 与 PostgreSQL execution tests 留在各自后端分区，明确不 cross-backend。
- [ ] **P2.5 双后端 contract/live 测试替换 drift pairs**
  - 对每一对抽取行为，补双后端 contract/live 测试；覆盖 error-redaction contract。
  - 测试通过、无未解释复制实现后，删除 `scripts/check_plugin_drift.py` 及其 CI job。

**Exit**：共用代码只有一份；行为、live compatibility 和 error-redaction contracts 均通过；没有未解释的复制实现。

---

## 5. Phase 3 — Ship compatibility distributions and switch first-party consumers

**目标**：把 `onestep-mysql` / `onestep-postgres` 转为薄转发兼容发行包（无 `onestep.resources` entry point），切换 root extras / workspace / lockfile / worker / scripts / CI 到 canonical，开启 T0 兼容时钟。

**任务：**

- [ ] **P3.1 转发兼容发行包**
  - 两包依赖对应 `onestep-sql[...]` extra，用有限 compatible upper bound。
  - 提供历史 namespace `onestep_mysql` / `onestep_postgres`，转发当前 public root exports、`__version__`、`register`/`register_resources` 与当前 shipping submodule 路径（connector/resources/resilience/state_sqlalchemy + postgres 三个 execution 模块）指向 canonical 同一对象。
  - **不声明** `onestep.resources` entry point，import 时不注册 handler。
  - README/metadata/import warning 标注 deprecated，给出 `onestep_sql.mysql` / `onestep_sql.postgres` 替代路径与移除窗口（T0 后 ≥6 自然月且 ≥2 个 feature release，取较晚者）。
- [ ] **P3.2 安装组合测试（release gate）**
  - 增加全部安装组合：每个 extra 单独、每个 legacy 单独、canonical+legacy、两个 legacy 同装——均断言 `load_resource_plugins()` 不重复注册（在 installed wheels 上断言，而非仅 source-path）。
- [ ] **P3.3 切换 root metadata**
  - 根 `onestep` extras（`mysql`/`postgres`/`all`/`dev`/`integration`）保持名称但依赖 `onestep-sql[...]`；`pip install "onestep[mysql]"` 仍可用。
  - 更新 uv workspace members、workspace sources、lockfile。
- [ ] **P3.4 切换 worker image 与脚本**
  - 更新 `docker/worker/Dockerfile`（复制并安装 canonical 而非两旧包）。
  - 更新 `scripts/run-reliability-checks.sh`、`scripts/run-integration-tests.sh`、contract tests、`tests/test_database_plugin_integration.py` 到 canonical package 与路径。
- [ ] **P3.5 合并 CI workflow**
  - 用单一 SQL plugin workflow 替代 `plugin-mysql.yml` + `plugin-postgres.yml`：保留 Python 3.9–3.12 unit/build/Twine；MySQL live + PostgreSQL 16 live compatibility；发布 canonical + 两个 forwarding artifacts（按项目版本检测）。更新 CI path filters。
- [ ] **P3.6 开启 T0 时钟**
  - 发布 canonical 后发布 compatibility wheels，记录 T0（canonical 首个发布时间），作为弃用窗口计时起点。

**Exit**：新旧同装不重复注册；legacy imports/YAML 工作；root extras 和 worker 使用 canonical distribution；release gates 通过。

---

## 6. Phase 4 — Documentation and adoption

**目标**：公开入口不再推荐旧 distribution，但明确 YAML names 未变；所有新示例使用 canonical namespace；保留 legacy 兼容说明。

**任务：**

- [ ] **P4.1 Broker 文档**
  - 更新 `docs/broker/index.md`（保留 MySQL/PostgreSQL 分列行）、`mysql.md`、`postgres.md`、`postgres-execution.md`：安装名改为 `onestep-sql[mysql]` / `onestep-sql[postgres]`，功能边界与 YAML 示例不变。
- [ ] **P4.2 根 README / guide / connector docs / skills / examples / worker 部署文档**
  - 新示例使用 `from onestep_sql.mysql import ...` / `from onestep_sql.postgres import ...` 与 `pip install "onestep-sql[...]"`。
  - legacy READMEs 以 deprecation/migration notice 开头。
- [ ] **P4.3 VitePress 校验**
  - 运行 `pnpm run build`，确保无死链、spec 页正常渲染（设计 PR #134 已验证新 spec 页可渲染）。

**Exit**：公开入口不再把旧 distribution 作为推荐安装方式，且明确 YAML names 没有改变。

---

## 7. Phase 5 — Deprecation closeout（不早于承诺窗口）

**目标**：在 T0 后同时满足“≥6 自然月”且“≥2 个 subsequent feature releases”后，才可考虑移除 forwarding packages。

**任务：**

- [ ] **P5.1 条件核查**
  - 检查下载/issue 反馈；确认时间与发布条件均满足。
- [ ] **P5.2 预告与最终迁移通知**
  - 在移除前至少一个 feature release，于 release notes 预告最后受支持版本与迁移命令。
- [ ] **P5.3 仅在明确 breaking release 中移除**
  - 仅在此类 release 移除 forwarding packages 或停止兼容承诺；保留迁移文档与 historical release notes。

**Exit**：破坏性动作满足时间与发布条件，且用户已提前一个 feature release 被告知。

---

## 8. 跨阶段检查清单（每次合并前核对）

- [ ] 14 个 YAML type 名与 strict catalog/validation/defaults/connector boundaries 未变（对照 P0.3 golden 快照）
- [ ] `mysql_binlog` 始终 MySQL-only；`postgres_execution_source` 与 tracked execution 始终 PostgreSQL-only
- [ ] 任意 canonical/legacy 安装组合不重复注册 resource handlers（P3.2 为 release gate）
- [ ] legacy namespace 转发保持 class/exception 身份一致（在 installed wheels 上断言）
- [ ] root extras / workspace / lockfile / worker image / CI / live tests / docs 全部迁移到 canonical
- [ ] MySQL & PostgreSQL unit/contract/live suites、wheel metadata/fresh-install checks、docs/link checks 通过
- [ ] `check_plugin_drift.py` 仅在 Phase 2 完成后移除

## 9. 验收标准追踪（来自设计 §11）

| # | 验收项 | 满足阶段 |
| --- | --- | --- |
| 1 | `onestep-sql` 是 MySQL/PostgreSQL 唯一 canonical distribution 与唯一自动 registration path | P1 / P3 |
| 2 | `onestep_sql.mysql` / `.postgres` 为新代码 public API；legacy import 在窗口内保持对象 identity 兼容 | P1 / P3 |
| 3 | 14 个 YAML type 名、strict catalog/validation、defaults、connector boundaries 全部保留 | P0 / P1 / P2 |
| 4 | `mysql_binlog` MySQL-only；`postgres_execution_source` 与 tracked execution PostgreSQL-only | 全程 |
| 5 | forwarding distributions 保留“≥6 月或 ≥2 feature releases（取较晚）” | P3 / P5 |
| 6 | canonical/legacy 任意组合不重复注册 | P3 |
| 7 | canonical extras、core extras、workspace/lockfile、worker image、CI/release/live tests、docs 完成迁移 | P3 / P4 |
| 8 | MySQL/PostgreSQL unit/contract/live suites、wheel/fresh-install checks、docs/link checks、最终 diff review 通过 | P2 / P3 / P4 |

## 10. 分支与 PR 节奏建议

- 每个 Phase 一个分支：`fm/onestep-sql-phase0` … `fm/onestep-sql-phase3`；Phase 4/5 可合并或独立。
- 命名沿用已合并设计 PR 的 `fm/` 前缀（设计 PR 为 `fm/onestep-sql-consolidation`）。
- 每个 PR 标题遵循 conventional commit（`docs:` / `feat:` / `test:` / `chore:`）。
- Phase 3 为关键发布门：需在合并前确认安装组合测试（P3.2）全绿。

## 11. 风险登记（摘要，详见设计 §9）

- 新旧 entry points 均被 discovery → 触发重复注册：canonical 仅 `sql` entry point，legacy 无 entry point 且不 import-register（P3.1/P3.2 缓解）。
- 单后端安装 import 缺失 driver：registrar/build 边界延迟 driver import（P1.1 缓解）。
- Shared 抽取改变 at-least-once 行为：先原样迁入 MySQL/PostgreSQL suites，双后端测试保护后再删 drift check（P2 缓解）。
- Legacy 转发产生不同身份：用 canonical object alias/module 转发，installed wheels 上断言身份（P3.1/P3.2 缓解）。
- 元数据/lockfile/worker/integration 遗漏旧路径：列入 phase checklist 并以 repo integration 断言覆盖（P3.3/P3.4 缓解）。
- binlog / execution 被错误 generic 化：14-type catalog test + backend-only capability tests + 文档章节（全程缓解）。
- 兼容包过早停维护：T0 后同时满足 6 月与 2 feature releases 才移除，提前一个 feature release 预告（P5 缓解）。
