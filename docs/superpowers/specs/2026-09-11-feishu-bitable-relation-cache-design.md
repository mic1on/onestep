# Feishu Bitable 关联字段缓存设计

## 背景与问题

onestep-feishu-bitable 的 Table Sink 通过 `relations` 配置把上游业务键解析为飞书关联字段需要的 `record_id`（见 `docs/superpowers/specs/2026-08-14-feishu-bitable-relation-resolver-design.md`）。当前实现中，每一个唯一业务键的解析都要调用一次飞书 records search 接口：

- 批量路径 `_batch_resolve_relations()`（`sink.py`）虽然做了批次内去重和并发（semaphore=20），但缓存 `cache: dict[tuple[str, str], str | None]` 是**单次 flush 的局部变量**，批次结束即丢弃。下一批相同业务键仍然重新 search。
- 单条路径 `_resolve_relation_fields()` 每次 `send()` 都逐值调用 `_find_relation_matches()`。
- `on_missing: create` 的 `_find_or_create_relation_record()` 每次也要先 search 再决定是否创建。

飞书 records search 接口的限流阈值是 **20 QPS**。批量同步场景（例如一次同步几千条项目记录，每条引用 3–5 个企业）中，绝大多数业务键在批与批之间重复出现，但每个批次都重新 search，持续打满 search QPS，最终触发 **429 限流**，同步被 runtime 反复重试甚至失败。

关键观察：关联解析的查询模式高度重复——同一批同步任务内，被引用的主数据（企业、人员、字典表）集合基本固定，重复 search 返回的结果几乎总是相同。

## 目标

- 在 `relations` 配置中新增 `cache` 字段，声明该关联字段的 record_id 缓存策略。
- `lazy`（被动缓存）：查过才缓存 `{业务键: record_id}`，后续命中直接使用，miss 回源 search 并缓存结果。
- `eager`（主动缓存）：Sink `open()` 时分页拉取关联表 key 字段全量到内存 `{业务键: record_id}`，正常处理零 search；miss 仍回源 search 一次。
- 显著降低批量同步时 search 接口的调用量，消除因重复解析导致的 429。
- 向后兼容：默认 `cache: none`，现有行为完全不变。
- Python API、YAML strict 校验、资源目录和控制面 descriptor 保持一致。

## 非目标

- **不处理删除导致的缓存失效。** 关联表中的记录被删除后，缓存中的 `record_id` 变成悬垂引用。该问题由飞书写入接口报错（`RecordIdNotFound` / `LinkFieldConvFail`）暴露，靠**重启任务重新拉取缓存**收敛。不提供 TTL、不监听删除事件、不做写入失败后的自动缓存剔除。
- 不持久化缓存。缓存只存在于 Sink 实例的进程内存中，任务重启即重建。
- 不跨 Sink 实例、不跨进程共享缓存。每个 Sink 各自持有自己的缓存。
- 不改变 `on_missing` 三种策略（error / empty / create）的既有语义。
- 不处理关联表 key 字段在任务运行期间被**修改**（原 key 改值）导致的缓存失真；与删除同理，靠重启收敛。
- 不引入新的写路径：缓存只是解析加速层，飞书仍是 source of truth。

## 核心原则：缓存只是加速层

飞书是 source of truth，缓存的一切命中都是**乐观使用**：

- **命中（hit）**：直接使用缓存中的 `record_id`，不发起 search。如果该 record_id 已被删除，写入阶段飞书会报错（这是本设计接受的显式取舍，见非目标）。
- **未命中（miss）**：**必须回源 search 一次**，绝不能把"缓存里没有"当作"飞书里不存在"。

第二条对 `eager` 模式至关重要：eager 缓存是 `open()` 时刻的快照。如果任务运行期间关联表新增了记录，新 key 不在缓存里。此时若 `on_missing: create` 直接判定"不存在"并创建，就会在飞书里产生重复主数据。因此 eager 模式的 miss 路径与 lazy 完全一致——回源 search，命中则回填缓存，确认不存在才走 `on_missing`。

两种模式的唯一区别是**启动时是否预加载**，运行时语义相同。

## 配置契约

### YAML

```yaml
resources:
  projects:
    type: feishu_bitable_table_sink
    connector: feishu
    app_token: "${FEISHU_APP_TOKEN}"
    table_id: "${PROJECT_TABLE_ID}"
    mode: upsert
    match_fields: [项目编号]
    relations:
      关联企业:
        from: 企业名称
        table_id: "${ENTERPRISE_TABLE_ID}"
        key: 企业名称
        on_missing: create
        cache: eager        # none（默认） | lazy | eager
        create_fields:
          数据状态: 待完善
      责任部门:
        from: 部门编码
        table_id: "${DEPT_TABLE_ID}"
        key: 部门编码
        on_missing: error
        cache: lazy
```

### Python API

```python
project_sink = feishu.table_sink(
    app_token=project_app_token,
    table_id=project_table_id,
    mode="upsert",
    match_fields=["项目编号"],
    relations={
        "关联企业": {
            "from": "企业名称",
            "table_id": enterprise_table_id,
            "key": "企业名称",
            "on_missing": "create",
            "cache": "eager",
        },
        "责任部门": {
            "from": "部门编码",
            "table_id": dept_table_id,
            "key": "部门编码",
            "on_missing": "error",
            "cache": "lazy",
        },
    },
)
```

### cache 字段定义

| 取值 | 语义 | 适用场景 |
|---|---|---|
| `none` | 不缓存，每个唯一业务键每次解析都 search（现状，默认） | 关联表频繁变更且对一致性敏感；或单次任务只跑几条数据 |
| `lazy` | 查过才缓存 `{业务键: record_id}`；命中直接用，miss 回源并缓存 | 通用推荐。key 空间大而单次任务只触碰其中一小部分；不付启动扫描成本 |
| `eager` | `open()` 时分页拉取 key 字段全量到内存；命中直接用，miss 仍回源 search | 关联表是稳定主数据、记录数可控（受扫描上界约束）；任务运行期间对同一批 key 高频引用 |

约束与校验：

- `cache` 必须是 `"none"`、`"lazy"` 或 `"eager"`，其余取值构造期报错（与现有 `_normalize_relations` 的校验风格一致）。
- 省略时视为 `none`，现有 YAML / Python 配置无需任何改动。
- `cache` 是**逐关联字段**配置的；同一 Sink 可以同时存在 eager、lazy 和 none 的关联字段。
- `insert_key_index` 与 `relations` 的既有互斥（`_validate_insert_key_index_requirements`）保持不变，因此缓存与 insert_key_index 不会共存，无交互。
- `eager` 不引入新的顶层配置项；扫描参数复用 sink 级现有的 `insert_index_page_size` / `insert_index_max_pages`（语义见下节）。

### Catalog

`resources.py` 的 `_RELATION_FIELDS` 增加 `"cache"`；`_validate_feishu_relations` 校验取值合法且类型为字符串。catalog 中 `relations` 仍暴露为 `mapping`，无需新增顶层 catalog 字段。控制面 descriptor 的关系概要增加 `cache` 字段（只输出策略名，不输出缓存内容）。

## 缓存语义

### 数据结构

每个声明了 `cache` 的关联字段，Sink 持有一个进程内 dict：

```python
# 键: (relation.target_field)；值: {业务键: record_id}
self._relation_caches: dict[str, dict[str, str]] = {}
```

- key 是经过 `_normalize_relation_values` 标准化后的业务键字符串（strip、去空），与现有查询输入一致。
- 值是飞书 `record_id`。
- 生命周期与 Sink 实例一致；`close()` 后不再使用，不主动清理。

### lazy：被动缓存

解析业务键时：

1. 查缓存，命中 → 使用该 `record_id`，**不发 search**。
2. miss → 调用现有 `_find_relation_matches()` 回源 search：
   - 唯一命中 → 写入缓存，使用 record_id。
   - 零命中 → 按 `on_missing` 处理；若为 `create`，创建成功后把新 record_id 写入缓存。零命中本身**不缓存**（不缓存"不存在"这一事实），与现有行为一致：后续同值仍走完整 miss 路径。
   - 多条命中 → 永久错误，不缓存。

### eager：主动缓存

1. **启动扫描（`open()`）**：对每个 `cache: eager` 的关联字段，分页调用 `search_records`（只请求 key 字段，`field_names: [relation.key]`），把每页的 `{业务键: record_id}` 灌入内存 dict。扫描完成后该关联字段标记为已加载。
2. **运行时解析**：与 lazy 完全相同——先查缓存，命中直接用；**miss 必须回源 search 一次**（防新增 key 误判，见"核心原则"），唯一命中后回填缓存。

### 业务键提取与多条扫描记录

eager 扫描页中的业务键从 `item["fields"][relation.key]` 提取。与 `_load_insert_key_index` 不同的是，关联 key 可能以富文本数组等复合形态返回，因此提取时复用 `_normalize_relation_values()`（它把字符串/数字/数组统一规范化为 strip 后的字符串元组）把字段值规范化为业务键：

- 规范化产出空元组（字段缺失、空值、空串）→ 计入 `missing_key_records`，跳过该记录。
- 规范化产出多个键（复合字段异常形态）→ 每个键都映射到同一 record_id；与运行时的精确匹配（`operator: is`）语义保持一致可能有偏差，但这种形态本身已违反"key 字段是单值业务键"的配置前提，按容错处理并计入日志。
- 同一业务键出现在多条记录中 → 后者覆盖前者，计入 `duplicate_keys`。业务上 key 必须唯一（relation 设计文档已声明），重复属于配置/数据问题，仅记录不报错。

### 失效语义（明确不做的事）

- **删除**：缓存不感知。悬垂 record_id 在写入时被飞书拒绝（`RecordIdNotFound` / `LinkFieldConvFail`），错误沿现有 `ConnectorOperationError` 分类上抛，由 runtime 按既有策略重试或失败。运维手段是重启任务。
- **key 值变更**：不感知。旧 key 仍映射到原 record_id，新 key miss 后回源能查到。靠重启收敛。
- **TTL / 容量上限**：不提供。eager 的内存占用由扫描上界约束（见下节）；lazy 的内存占用与任务触碰到的唯一业务键数成正比，属于业务固有规模，不设驱逐。

## eager 启动扫描约束

eager 的启动扫描复用现有 `insert_key_index` 的 `_load_insert_key_index()`（`sink.py`）已确立的工程约束，保证行为可预期：

| 约束 | 取值/行为 | 说明 |
|---|---|---|
| 请求字段 | `{"field_names": [relation.key]}` | 只拉 key 字段，减少传输 |
| `page_size` | 复用 sink 的 `insert_index_page_size`（默认 500，上限 `_MAX_PAGE_SIZE=500`） | 不新增配置项 |
| `max_pages` | 复用 sink 的 `insert_index_max_pages`（默认 200） | 单个关联表最多扫描 200×500 = 10 万条记录 |
| 耗尽上界 | `has_more` 仍为真但已达 `max_pages` → `open()` 抛 `ConnectorOperationError(PERMANENT)`，任务启动失败 | 拒绝使用截断缓存（截断会导致大量运行时 miss，eager 失去意义且掩盖配置错误） |
| 分页不进位 | `page_token` 缺失/重复 → 启动失败 | 同 insert_key_index |
| 扫描时 operation | `ConnectorOperation.OPEN` | 与 `_load_insert_key_index` 一致 |
| 扫描失败 | 包装为 `ConnectorOperationError(OPEN, PERMANENT)` 上抛 | 启动即失败，不带病运行 |
| 观测 | 结构化日志 `feishu_relation_cache_scan`：target_field、table_id、scan_pages、scan_keys、missing_key_records、duplicate_keys、duration_s、page_size、max_pages | 对齐 `feishu_insert_index_scan` 日志形态 |
| 顺序 | 与 insert_key_index 扫描一样在 `open()` 内串行执行；多个 eager 关联字段按配置顺序依次扫描 | 启动耗时线性叠加，由 max_pages 兜底 |

### 单写者约束

eager 假设关联表在任务运行期间**没有会与本任务冲突的并发写入**：

- 新增记录不会造成数据错误（miss 回源兜底），只是该 key 暂付一次 search 成本，缓存回填后恢复正常。
- 删除/改 key 会造成悬垂或失真缓存（见失效语义），靠重启收敛。

因此 eager 的正确性前提与 `insert_key_index` 相同：同一 `(app_token, table_id)` 的关联表在任务生命周期内应只有可预期的写入来源。这是部署约束，文档中必须明示。

### 与 insert_key_index 的关系

- **机制相似，目标不同**：`insert_key_index` 缓存的是目标表"哪些业务键已存在"（set，用于跳过 insert 前查重）；本设计缓存的是关联表"业务键 → record_id"（dict，用于跳过关系解析 search）。两者互不依赖。
- **互斥保持不变**：`insert_key_index` 要求 `mode=insert` 且不支持 `relations`（`_validate_insert_key_index_requirements`），本设计只作用于 `relations`，因此两种缓存永远不会同时激活，无需处理交互。
- **实现可参照**：`_load_insert_key_index()` 的分页循环、token 防重、耗尽报错、结构化日志均可作为 `_load_relation_eager_cache()` 的直接模板。扫描参数直接复用同一组 `insert_index_page_size` / `insert_index_max_pages`，不为关联缓存引入第二套参数——如果未来出现"目标表和关联表需要不同上界"的真实需求，再拆分。

## 运行时结构

### `_FeishuRelationConfig`

`_shared.py` 的 frozen dataclass 增加字段：

```python
@dataclass(frozen=True)
class _FeishuRelationConfig:
    target_field: str
    source_field: str
    app_token: str
    table_id: str
    key: str
    on_missing: str
    create_fields: Mapping[str, Any]
    cache: str  # "none" | "lazy" | "eager"
```

`_normalize_relations()` 解析 `cache`（缺省 `"none"`），校验取值属于 `_RELATION_CACHE_POLICIES = frozenset({"none", "lazy", "eager"})`，非法值抛 `ValueError`。

### Sink 初始化与 open()

`FeishuBitableTableSink.__init__()`：

- 新增 `self._relation_caches: dict[str, dict[str, str]]`（只为 `cache != "none"` 的关联字段建空 dict，eager 字段同时记录"未加载"状态）。
- `relations` 归一化在 `_normalize_relations` 中完成，构造期即拒绝非法 `cache` 值。

`open()`：

```python
async def open(self) -> None:
    if self.insert_key_index and not self._index_loaded:
        await self._load_insert_key_index()
    await self._load_relation_eager_caches()   # 仅处理 cache == "eager" 且未加载的关联字段
```

### 解析路径接入点

缓存挂在现有解析函数的**唯一查询出口** `_find_relation_matches()` 的调用方一侧，不改该函数本身：

- 批量路径 `_batch_resolve_relations()` 的 `search_one()`：查缓存优先；miss 走现有 search；唯一命中/创建成功后回填。批次内局部 `cache` 变量仍然保留（它同时承载"本批未命中待创建"等瞬态），持久缓存只负责跨批的 hit 加速。
- 单条路径 `_resolve_relation_fields()` 与 `_find_or_create_relation_record()`：同样先查持久缓存。`_find_or_create_relation_record` 的 single-flight 锁内"二次查询"改为"先查缓存，没有再 search"，缓存命中即可跳过 search。

接入遵循最小侵入：未配置 `cache` 的关联字段走原有代码路径，一行行为都不变。

### 错误模型

- 缓存查询是纯内存操作，不引入新错误类型。
- 回源 search / create 继续抛原有 `ConnectorOperationError`，分类与重试语义不变。
- eager 扫描失败 → `ConnectorOperationError(OPEN, PERMANENT)`，任务启动失败，符合"截断缓存宁可不启动"。
- 悬垂 record_id 导致的写入报错（`RecordIdNotFound` / `LinkFieldConvFail`）按现有 `_classify_api_error` 走 PERMANENT 分类上抛。

## YAML 与资源目录

`resources.py`：

- `_RELATION_FIELDS` 增加 `"cache"`。
- `_validate_feishu_relations()`：`cache` 若提供必须是字符串且属于 `{"none", "lazy", "eager"}`。
- catalog 的 `relations` 字段类型不变（`mapping`）。
- descriptor 关系概要增加 `"cache": relation.cache`。

## 兼容性

- `cache` 缺省 `none`：现有 YAML、Python 代码零改动，行为逐字节不变。
- 纯插件级改动，不触碰 onestep core runtime。
- 与 `insert_key_index`、`batch_size`、`flush_interval_s`、三种 `on_missing` 策略无交互冲突。

## 测试计划

### 配置契约

- `cache` 缺省归一化为 `"none"`；`lazy` / `eager` 接受；`"always"`、`""`、`True`、`None` 显式传入等非法值构造期报错。
- `_FeishuRelationConfig` 携带正确的 `cache` 值。
- strict YAML：合法 `cache` 通过；非法值、非字符串值被拒。
- descriptor 关系概要包含 `cache`，仍不含任何 token。

### lazy 行为（Mock connector）

- 首次解析某业务键调用一次 search；第二次（新一批 / 新一次 send）同键解析**零 search**，直接使用缓存 record_id。
- 唯一命中后回填缓存；多条命中报永久错误且不污染缓存。
- `on_missing: error` 的零命中不缓存，下次同键仍 search。
- `on_missing: create` 创建成功后新 record_id 入缓存，下次同键零 search。
- `on_missing: empty` 的零命中不缓存。
- 跨 flush 批次命中（batch_size > 1 时第二批复用第一批缓存）。

### eager 行为（Mock connector）

- `open()` 按 `insert_index_page_size` / `insert_index_max_pages` 分页扫描，body 为 `{"field_names": [relation.key]}`；扫描后缓存包含全部 `{业务键: record_id}`。
- 扫描页中 key 缺失/空 → 跳过并计数；重复 key → 覆盖并计数。
- `max_pages` 耗尽且 `has_more` → `open()` 抛 PERMANENT 错误。
- `page_token` 不前进 → `open()` 抛 PERMANENT 错误。
- 运行时命中缓存的键零 search。
- 运行时 miss（模拟启动后新增的 key）→ 回源 search 一次，唯一命中回填缓存。
- miss 且确实不存在 + `on_missing: create` → search 后创建，不重复创建已在飞书存在但不在缓存的键（防重复主数据回归测试）。

### 缓存与删除的边界（文档行为）

- 缓存命中一个已被"删除"的 record_id（mock 写入阶段抛 `LinkFieldConvFail`）→ 错误按 PERMANENT 上抛，缓存不自动剔除（断言缓存内容不变）。

### 回归

- 未配置 `cache` 的关联字段：search 调用次数与现状完全一致（用计数 mock 断言）。
- 插件全部既有测试通过。
- `insert_key_index` 相关测试不受影响。

## 文档

- `docs/broker/feishu-bitable.md` 的"关联字段"一节：新增 `cache` 配置说明、三种取值语义对照表、eager 的扫描上界（10 万条默认）与单写者前提、"删除不感知、靠重启收敛"的显式运维说明。
- 插件 `README.md` 只加一行最小提及并链接详细文档。

## 发布

实现与验证完成后按插件发布约定 bump `onestep-feishu-bitable` 小版本（向后兼容的新能力）。设计阶段不改版本。
