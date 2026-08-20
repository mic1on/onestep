# Consolidate `onestep-mysql` and `onestep-postgres` into `onestep-sql`

日期：2026-08-20  
状态：captain-approved design contract  
范围：规划；不包含此设计中的运行时代码、包发布或迁移实现

## 1. 背景与目标

`onestep-mysql`（当前 `0.6.1`）和 `onestep-postgres`（当前 `0.5.0`）是两个独立 workspace 插件。两者均依赖 `onestep`、`SQLAlchemy[asyncio]` 和 `aiosqlite`；前者额外依赖 `asyncmy` 与 `mysql-replication`，后者额外依赖 `psycopg[binary]`。二者已经有大量刻意平行的实现：SQLAlchemy state/cursor store、table-sink update-policy helper 和 incremental state-key。仓库的 `scripts/check_plugin_drift.py` 逐 AST 比较这三组代码，并明确记录过 MySQL datetime cursor 修复比 PostgreSQL 早三天发布的漂移事故（issue #125）。

本设计将两个发行包收敛为一个规范发行包 `onestep-sql`，使共用 SQL 行为有一个实现和一套测试，同时保留每个数据库的语义、Python 导入和 YAML 配置。合并**不是**把 MySQL 与 PostgreSQL 当作可互换后端：MySQL binlog CDC 与 PostgreSQL tracked execution 继续是各自后端专属能力。

### 目标

- 提供一个发行包 `onestep-sql`，其中 `mysql` 和 `postgres` 是明确的后端分区；共用代码只保留一份。
- 所有现有 YAML resource type 保持原名、原 catalog/strict-validation 语义和原有资源引用关系。
- 保持已有 Python API 的导入兼容，给用户足够迁移期。
- 在同一环境安装新旧发行包时只走一个 resource registration 路径。
- 以一个 SQL plugin 的构建、测试、live compatibility 与发布流程替代两份平行流程。

### 非目标

- 不改变 `Source`、`Sink`、`Delivery`、runner、`ResourceRegistry` 或 YAML loader 的 core API。
- 不重命名、删除或引入 generic `sql_*` YAML type；不转换已有 YAML 文件。
- 不实现 PostgreSQL logical replication/CDC（现有 PostgreSQL README 也明确不支持）。
- 不把 MySQL binlog 实现为 PostgreSQL feature，也不把 PostgreSQL execution backend/source 实现为 MySQL feature。
- 不在本设计提交中创建包、改依赖、改工作流、发布 PyPI 包或修改运行时代码。

## 2. 现有证据与约束

| 证据 | 对设计的约束 |
| --- | --- |
| `plugins/onestep-mysql/src/onestep_mysql/resources.py` 注册 7 个 MySQL handler；`plugins/onestep-postgres/src/onestep_postgres/resources.py` 注册 7 个 PostgreSQL handler。 | 迁移必须保留 14 个 type 名、catalog role、allowed fields、default、topology fields、validator 与 builder 语义。 |
| `src/onestep/resource_registry.py` 的 `register_resource_type()` 只会接受完全相等的重复 handler，其他重复会抛出 `ValueError("resource type ... is already registered")`。entry-point loader 的去重键为 `group:name:value`，不是资源 type。 | 不能让新旧包各自加载不同的 registration callable；仅依赖 registry 的冲突检测不是兼容策略。 |
| 两个现有 distribution 都在 `onestep.resources` 中分别发布 `mysql = "onestep_mysql:register"` 与 `postgres = "onestep_postgres:register"` entry point。 | 新 canonical 包只能有一个明确的注册入口；兼容 distribution 不得继续发布该 entry-point group。 |
| 根 `pyproject.toml` 将两个插件列为 uv workspace members、workspace sources、`mysql`/`postgres`/`all`/`dev`/`integration` extras 的依赖。 | 新 package、core extras、workspace sources、lockfile、worker image 和 aggregate extras 必须同时迁移，不能只移动源码目录。 |
| `.github/workflows/plugin-mysql.yml` 和 `plugin-postgres.yml` 都执行 Python 3.9–3.12 测试、wheel/sdist、Twine metadata checks 与版本驱动的 PyPI publish；PostgreSQL workflow 还运行 PostgreSQL 16 live compatibility。 | 合并后的 workflow 必须覆盖两套单元测试和两个 live 后端，并发布 canonical 与 compatibility artifacts。 |
| `docker/worker/Dockerfile` 显式复制并安装两个插件；`scripts/run-reliability-checks.sh`、`scripts/run-integration-tests.sh`、contract tests 和 `tests/test_database_plugin_integration.py` 都按旧路径引用它们。 | 迁移计划必须更新构建输入、测试发现和路径断言，而非仅改 package metadata。 |
| `docs/broker/index.md` 当前分别列出 MySQL（含 binlog CDC）和 PostgreSQL；`docs/broker/mysql.md`、`docs/broker/postgres.md`、`docs/broker/postgres-execution.md` 仍指导安装旧 distribution。 | 文档须改安装名而不能改变后端页面、功能边界或 YAML 示例。 |

## 3. 规范包与 Python namespace

新增 workspace member `plugins/onestep-sql`，发行名为 `onestep-sql`，规范 import namespace 为 `onestep_sql`：

```text
plugins/onestep-sql/
  pyproject.toml
  README.md
  src/onestep_sql/
    __init__.py                 # version、唯一 register；不重导出所有后端对象
    resources.py                # 唯一的 onestep.resources registrar
    _shared/                    # 非公开：SQLAlchemy stores、queue/incremental、sink policy、通用测试 helpers
    mysql/                      # 公共后端 namespace
      __init__.py
      connector.py
      resources.py
      resilience.py
      binlog.py                 # 或保留在 mysql connector 的私有实现中
    postgres/                   # 公共后端 namespace
      __init__.py
      connector.py
      resources.py
      resilience.py
      execution_backend.py
      execution_schema.py
      execution_source.py
```

`onestep_sql.mysql` 和 `onestep_sql.postgres` 是规范公开 Python API。命名保留当前后端术语：`MySQLConnector`、`TableSink`、`BinlogSource` 等属于 `onestep_sql.mysql`；`PostgresConnector`、`PostgresTableSink`、`PostgresExecutionBackend`、`PostgresExecutionSource` 等属于 `onestep_sql.postgres`。新文档、type hints 与示例应使用这些 namespace。

`_shared` 不是 public API。它只能容纳两端语义已经一致且有同一份参数/错误/at-least-once contract 的代码；公开 backend class 的实际定义仍应在相应 backend namespace 内或从 `_shared` 以明确 backend alias 暴露。不要以 `SQLConnector`、`TableSource` 之类的 generic public class 取代当前数据库命名的 API。

### 3.1 共用与专用边界

| 归属 | 内容 | 原因 |
| --- | --- | --- |
| `onestep_sql._shared` | SQLAlchemy state/cursor store serialization；table queue 与 incremental delivery sequencing/state-key；table sink `insert`/`upsert`/`update` 的 column-write policy；共同的 payload validation、redaction contract 和测试 fixtures。 | 当前 drift check 的 `state_sqlalchemy.py`、table-sink policy 和 `_default_incremental_state_key` 已证明这些是刻意并行的同一行为。 |
| `onestep_sql.mysql` | MySQL DSN normalization/dialect、`asyncmy` execution、MySQL error classification、MySQL SQL dialect details，以及同步 `mysql-replication` thread boundary。 | 驱动、SQL 方言和 binlog replication API 都是 MySQL 特有。 |
| `onestep_sql.postgres` | PostgreSQL DSN/dialect、`psycopg` execution、PostgreSQL error classification、execution schema/backend/source、lease/heartbeat/reclaim/cancellation behavior。 | tracked execution 依赖 PostgreSQL transaction/locking schema 与 lease semantics，不能抽象成另一后端可声明的 feature。 |
| `onestep_sql.resources` | 组合两端 resource catalog 和 builder，且只在 build 时延迟导入需特定驱动的后端。 | 安装单一 backend extra 时 plugin discovery 必须仍可安全完成，且必须只注册一次。 |

提取共用代码前必须逐项对照 `scripts/check_plugin_drift.py` 的三对比较对象。该脚本的唯一已允许差异 `_async_dsn`（`mysql+asyncmy` 与 `postgresql+psycopg`）仍属于 backend adapter；不要把驱动映射塞入 shared store。只有经双后端 contract tests 证明相同行为的逻辑可进入 `_shared`。

## 4. YAML 和 resource registration 合同

### 4.1 永久保留的 type 名

`onestep-sql` 必须注册且只注册以下稳定 type 名。原有 YAML 不需任何编辑：

| 后端 | connector/store | source | sink |
| --- | --- | --- | --- |
| MySQL | `mysql`、`mysql_state_store`、`mysql_cursor_store` | `mysql_table_queue`、`mysql_incremental`、`mysql_binlog` | `mysql_table_sink` |
| PostgreSQL | `postgres`、`postgres_state_store`、`postgres_cursor_store` | `postgres_table_queue`、`postgres_incremental`、`postgres_execution_source` | `postgres_table_sink` |

每个迁移后的 `ResourceSpecHandler` 必须保留当前的 `ResourceCatalogEntry`、`allowed_fields`、strict validation、default 值及 connector type。例如 `mysql_binlog` 继续要求正数 `server_id`，并保留 `schemas`、`tables`、`events`、`batch_size`、`poll_interval_s`、`state`、`state_key` 与 `blocking`；`postgres_execution_source` 继续接受 namespace、一个或多个 task name、execution/attempt tables、lease/heartbeat、size limits 与 reclaim batch 选项。不得接受未记录的 generic field 以图方便。

同名 YAML resource 的实例行为也不变：MySQL/PostgreSQL incremental source 仍可绑定各自 cursor store 和稳定 `state_key`；MySQL table sink policy 仍支持 `update_columns`/`update_expr`/`serialize_json`；PostgreSQL execution source 仍要求 PostgreSQL connector，并保留 tracked-execution validation。现有 docs、examples 和 strict-YAML catalog snapshots 成为迁移回归输入。

### 4.2 一个 canonical entry point，零 legacy entry points

`onestep-sql` 声明唯一 entry point：

```toml
[project.entry-points."onestep.resources"]
sql = "onestep_sql.resources:register_resources"
```

该 callable 是唯一的 canonical registrar：它在同一 registry 内以稳定顺序注册上表全部 handler。注册阶段不得要求已安装两种 driver；backend driver 导入应在相应 resource build 或 connector use 时发生，使 `onestep-sql[mysql]` 和 `onestep-sql[postgres]` 都能安全加载 plugin catalog。

这不是 cosmetic 选择。`load_resource_plugins()` 的 entry-point identity 由 group/name/value 组成；旧 `mysql` 和 `postgres` entry points 与新 `sql` entry point 会被分别调用。随后 `ResourceRegistry` 只有在 handler dataclass 完全相等时才容忍重复，任何 wrapper callable 或新建 handler 都可能冲突。因此 compatibility distributions **不得**声明 `onestep.resources` entry point，也不得在 import 时自动注册资源。

Direct Python `register`/`register_resources` compatibility exports 必须是 canonical registrar 的同一 callable（不是重新构造 handler 的 wrapper），以便显式注册同一 registry 时符合现有 idempotent-equality 规则。自动 discovery 仍只会看到 `sql`。

## 5. Dependencies、extras 与兼容发行包

### 5.1 Canonical distribution

`onestep-sql` 的 base dependency 仅包含在 import、catalog registration 和 shared types 中必要的 core/SQLAlchemy components。后端 driver 和 backend-specific library 以 extras 提供：

```bash
pip install "onestep-sql[mysql]"       # asyncmy + mysql-replication + shared SQL deps
pip install "onestep-sql[postgres]"    # psycopg[binary] + shared SQL deps
pip install "onestep-sql[all]"         # 两个后端
```

`mysql` extra 保留 MySQL 当前的 `asyncmy` 与 `mysql-replication`；`postgres` extra 保留 PostgreSQL 当前的 `psycopg[binary]`。测试/dev extras 应组合每个 backend 的 test dependencies 而不把数据库 driver 变成无条件基础依赖。实现时记录共享依赖的单一 lower bound，并选择不低于当前两个包中较高的 `onestep` lower bound，避免 canonical package 降低 execution API 的最小 core 版本。

根 `onestep` 的 `mysql`、`postgres`、`all`、`dev` 和 `integration` extras 保持名称，但依赖改为对应 `onestep-sql[...]` extra。用户可继续使用 `pip install "onestep[mysql]"` 或 `onestep[postgres]`；这不是一个 YAML migration。

### 5.2 Forwarding compatibility distributions

保留 `onestep-mysql` 和 `onestep-postgres` 作为轻量 forwarding distributions，至少到 **canonical release T0 后六个自然月**且**T0 后第二个 feature release 已发布**两者中较晚者为止。该窗口结束前不得移除、空发布导致 import 失败，或停止修复阻断迁移的兼容问题。

每个 compatibility distribution 必须：

1. 依赖匹配的 `onestep-sql` backend extra（`onestep-mysql` -> `onestep-sql[mysql]`；`onestep-postgres` -> `onestep-sql[postgres]`），并使用有限的 compatible upper bound；
2. 提供历史 package namespace `onestep_mysql` 或 `onestep_postgres`；
3. 转发当前 public root exports、`__version__` 和 `register`/`register_resources`，并保留当前 shipping submodule import paths：双方的 `connector`、`resources`、`resilience`、`state_sqlalchemy`，以及 PostgreSQL 的 `execution_backend`、`execution_schema`、`execution_source`；
4. 将这些 module path 指向 canonical module objects 或同一 public class/function objects，避免 `isinstance`/exception identity 与 type hints 分叉；
5. 不声明 `onestep.resources` entry point，且不在 import 时注册 handler；
6. 在 README、package metadata 和 import warning 中标明 deprecated，并给出 `onestep_sql.mysql` 或 `onestep_sql.postgres` 的替代路径和移除窗口。

兼容发行包不再含 connector/resource 实现、独立 tests、独立 driver resolution 或独立 PyPI feature work。安全/critical bug 修复落在 `onestep-sql`，再发布很薄的 compatible forwarding wheel。窗口届满后的移除只能发生在明确的 breaking release，release notes 必须提前一个 feature release 说明最后受支持版本和迁移命令。

## 6. Python API migration

新代码使用 canonical namespace：

```python
from onestep_sql.mysql import MySQLConnector
from onestep_sql.postgres import PostgresExecutionBackend, PostgresExecutionSource
```

历史代码在兼容窗口内无需改动：

```python
from onestep_mysql import MySQLConnector
from onestep_postgres import PostgresExecutionBackend, PostgresExecutionSource
```

兼容不表示可以跨 backend 混用：`mysql_*` resource 只接受 MySQL connector，`postgres_*` resource 只接受 PostgreSQL connector，`postgres_execution_source` 只构建 PostgreSQL tracked execution。Canonical package 为两个后端共享发行生命周期，不提供允许把一种数据库资源传给另一种数据库 builder 的 generic connector。

## 7. CI、测试、构建与发布迁移

### 7.1 Workspace 和 build inputs

1. 增加 `plugins/onestep-sql` 作为 uv workspace member/source，随后让 root extras 指向它；lockfile 必须在同一个变更中重新解析。
2. 在 compatibility window 内保留旧 workspace projects，但其 metadata 只描述 forwarding distributions；当不再需要其独立源码时，不能留下 stale source discovery path。
3. `docker/worker/Dockerfile` 改为复制 canonical plugin source 并安装 `onestep-sql[all]`（或两个明确 extra）；不要同时安装旧 forwarding distributions，除非特意执行兼容 smoke test。
4. 更新 `scripts/run-reliability-checks.sh`、`scripts/run-integration-tests.sh`、official connector conformance 和 `tests/test_database_plugin_integration.py`，由 canonical test layout 和 package metadata 驱动。

### 7.2 Test matrix

新 SQL plugin workflow 必须替代 `plugin-mysql.yml` 与 `plugin-postgres.yml` 的平行实现，并保留它们当前的 Python 3.9、3.10、3.11、3.12 unit/build/Twine 覆盖。至少增加：

- MySQL unit/contract suite：queue、incremental cursor ordering/retry/commit waves、state store datetime、table sink 和 binlog；
- PostgreSQL unit/contract suite：queue、incremental、state store、table sink、execution schema/backend/source、lease heartbeat/reclaim/cancellation；
- MySQL live service compatibility（保留当前 integration harness 的 MySQL coverage）和 PostgreSQL 16 live compatibility（保留当前 dedicated workflow coverage）；
- strict YAML catalog snapshot/validation tests，逐一断言第 4.1 节的 14 个 type、role、connector type、field/default 与 backend-only validation；
- isolated-install tests：`onestep-sql[mysql]`、`onestep-sql[postgres]`、`onestep-sql[all]`、每一个 legacy package 单独安装、canonical 与对应 legacy package 同装、两个 legacy packages 同装；每种都执行 `load_resource_plugins()` 并断言没有 duplicate registration；
- Python import compatibility tests，断言 legacy 与 canonical class/function/exception identity 相同，及 legacy `register` 是 canonical registration path；
- wheel/sdist build、Twine metadata、fresh venv `pip check` 和 worker image smoke。

在 shared extraction 落地前，保留 `scripts/check_plugin_drift.py` 为现状防线。shared modules 和双后端 behavior tests 取代其全部受监控对之后，删除该脚本、CI drift job 和仅为平行路径存在的 allowlist；不要在仍有复制实现时先移除它。

### 7.3 Release order

Canonical initial release 必须先通过完整 SQL workflow、两种 live backend、isolated-install suite 和 worker smoke，然后发布 `onestep-sql`。随后发布两个 forwarding distributions，使它们依赖已可从 PyPI 解析的 canonical wheel；最后发布 core version/extras 更新或在同一经过验证的 release train 中完成它。不得让 `onestep[mysql]`、`onestep[postgres]` 或 legacy wheels 指向尚未发布的 canonical version。

新 workflow 的版本侦测和 PyPI publish gate 必须分别读取 canonical project 与两个 forwarding project，防止 compatibility metadata change 被悄悄遗漏。它还必须保留现有 release protections：版本变更、all supported Python builds、metadata checks、live compatibility 成功后才允许 publish。tag-based core `release.yml` 仍只负责 core release，除非实施时明确扩展并测试其 artifact contract；不能假设它会自动发布 plugin wheels。

## 8. 文档迁移

保留两条后端文档信息架构：`docs/broker/mysql.md` 仍是 MySQL 表队列、增量、binlog、sink 页面；`docs/broker/postgres.md` 仍是 PostgreSQL 表队列、增量和 sink 页面；`docs/broker/postgres-execution.md` 仍是 tracked execution 页面。`docs/broker/index.md` 应继续分别列出 MySQL（含 binlog CDC）与 PostgreSQL，安装列改为 `onestep-sql[mysql]` 和 `onestep-sql[postgres]`。

迁移时更新以下用户入口，并逐个保留 YAML type 名：

- root README、`docs/guide/index.md`、`docs/core/connector.md`、broker index 与 MySQL/PostgreSQL 页面；
- `docs/broker/postgres-execution.md` 的 version/installation/release-order examples；
- `skills/onestep/references/connectors.md`、YAML task definition/reference material、examples 与 worker deployment docs；
- 两个 legacy README：第一屏显示 deprecated installation/import migration 和支持窗口，而不是删除历史示例。

每个文档变更都应通过 VitePress build 和 internal link check。示例应首先展示 canonical install/import，同时至少保留一节 explicit legacy compatibility note，避免让仍锁定旧 distribution 的用户误以为 YAML `mysql_*`/`postgres_*` 已失效。

## 9. 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 新旧 entry points 均被 discovery，触发重复 type registration。 | Canonical 只有 `sql` entry point；legacy packages 无 entry point、不 import-register；安装 permutation test 是 release gate。 |
| 单后端安装在 plugin discovery 时导入缺失 driver。 | Registrar/build boundaries 延迟 backend-driver import；在 `[mysql]` 与 `[postgres]` isolated env 验证 catalog loading。 |
| Shared extraction 改变 at-least-once cursor/ack/retry behavior。 | 先将现有 MySQL/PostgreSQL suites 原样迁入；每个 shared behavior 由双后端 contract/live tests 保护，再删 drift check。 |
| Legacy namespace forwarding 产生不同的 class/exception identity。 | 使用 canonical object alias/module forwarding；在 installed wheels—not source-path-only—断言 identity。 |
| Core extras、lockfile、worker image 或 integration harness 遗漏旧 package path。 | 把 root metadata、Dockerfile、scripts、contract tests、workflow path filters 和 lockfile 列入 phase checklist，并用 repository integration assertions 覆盖。 |
| PostgreSQL execution 或 MySQL binlog 被错误“generic 化”。 | 14-type catalog test、backend-only capability tests 和文档章节明确其不跨后端；generic public API 是非目标。 |
| 兼容包过早停止维护，破坏 locked deployments。 | 发布 T0 后同时满足 six calendar months 和 two subsequent feature releases 才能破坏性移除；release notes 提前一个 feature release 预告。 |

## 10. 分阶段实施计划

### Phase 0 — Baseline and contract tests

盘点两插件的 public exports、submodule imports、catalog snapshots、docs examples、Docker/script/workflow consumers。新增不改行为的 YAML, import, entry-point-permutation 和 live baseline tests；修正任何未被测试的当前公开 contract。保持 `check_plugin_drift.py` 运行。

**Exit:** 两插件在当前结构中有完整 baseline；测试能证明 current YAML/API 兼容性与 duplicate-registration failure mode。

### Phase 1 — Create canonical package without changing consumers

加入 `plugins/onestep-sql`、`onestep_sql` namespace、backend subpackages、extras、唯一 `sql` entry point 和 canonical tests。先复制实现并以 forwarding aliases 而不是删旧路径的方式保留 API；root extras/worker 尚不切换。验证 canonical package 独立安装和所有 backend tests。

**Exit:** `onestep-sql[mysql]`、`[postgres]` 和 `[all]` 可构建、发现资源并通过对应 suites；所有 14 YAML type 未变。

### Phase 2 — Extract shared behavior and converge tests

将 drift script 监控的 state stores、table-sink policy 和 incremental state-key 迁入 `_shared`；保留 backend adapters。迁移测试到 canonical layout，同时保持 MySQL binlog 与 PostgreSQL execution tests 处于专属分区。以双后端 tests 取代所有 drift pairs 后，删除 drift script/job。

**Exit:** 共用代码只有一份；行为、live compatibility 和 error-redaction contracts 均通过；没有未解释的复制实现。

### Phase 3 — Ship compatibility distributions and switch first-party consumers

把 `onestep-mysql` 与 `onestep-postgres` 转为薄 forwarding distributions，移除它们的 resource entry points；加入 all installation permutations。更新 root extras/workspace/lockfile、worker image、scripts、CI path filters 和 plugin SQL workflow。发布 canonical 后发布 compatibility wheels，并开始 T0 兼容时钟。

**Exit:** 新旧同装不重复注册；legacy imports/YAML 工作；root extras 和 worker 使用 canonical distribution；release gates 通过。

### Phase 4 — Documentation and adoption

按第 8 节更新 docs、skills、examples、READMEs、release notes 和 migration guide。所有新示例使用 canonical install/import；保留 legacy compatibility section。执行 VitePress build/link checks。

**Exit:** 公开入口不再把旧 distribution 作为推荐安装方式，且明确 YAML names 没有改变。

### Phase 5 — Deprecation closeout (not before the promised window)

当 T0 后六个自然月以及两个 subsequent feature releases 都完成后，检查下载/issue 反馈、发布 final migration notice，并仅在明确 breaking release 中移除 forwarding packages 或停止其兼容承诺。保留迁移文档和 historical release notes。

**Exit:** 破坏性动作满足时间与发布条件，且用户已提前一个 feature release 被告知。

## 11. Acceptance criteria

该设计实施完成后必须满足：

- `onestep-sql` 是 MySQL 与 PostgreSQL 的唯一 canonical distribution 和唯一自动 resource registration path；
- `onestep_sql.mysql` 与 `onestep_sql.postgres` 为新代码的 public API，legacy import paths 在所承诺窗口内保持对象 identity 兼容；
- 14 个现有 YAML type 名、其 strict catalog/validation、defaults 和 connector boundaries 全部保留；
- `mysql_binlog` 始终是 MySQL 特有；`postgres_execution_source` 和 tracked execution 始终是 PostgreSQL 特有；
- `onestep-mysql`、`onestep-postgres` 作为无独立 resource entry point 的 forwarding distributions 至少保留“六个月或两个 feature releases，以较晚者为准”；
- canonical/legacy 任意支持安装组合不会重复注册 resource handlers；
- canonical extras、core extras、workspace/lockfile、worker image、CI/release and live tests、documentation 都完成迁移；
- MySQL and PostgreSQL unit/contract/live suites、wheel metadata/fresh-install checks、documentation/link checks 和最终 diff review 通过；
- 本设计本身不改变 runtime code、发布包或合并 PR。
