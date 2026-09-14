# Feishu Bitable Relation Cache Implementation Plan

> **For agentic workers:** Implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking. 设计依据：`docs/superpowers/specs/2026-09-11-feishu-bitable-relation-cache-design.md`（先通读再动手，不要扩展范围）。

**Goal:** 为 onestep-feishu-bitable 的 `relations` 关联字段增加 `cache: none | lazy | eager` 能力，消除批量同步时重复 search_records 导致的 429 限流。

**Architecture:** 在 `_shared.py` 的 `_FeishuRelationConfig` 增加 `cache` 字段并在 `_normalize_relations` 中归一化；在 `sink.py` 的 `FeishuBitableTableSink` 上增加进程内 `{业务键: record_id}` 缓存，lazy 在 miss 回源后回填，eager 在 `open()` 时参照 `_load_insert_key_index` 分页预加载；resources.py 增加 strict 校验与 descriptor 输出。缓存只是加速层：命中乐观使用，miss 必须回源 search 一次（防 on_missing: create 误建重复记录）。不处理删除失效、不持久化。

**Tech Stack:** Python 3.10+, asyncio, pytest, 现有 FeishuBitableConnector.search_records / batch_create_records。

**关键既有代码（动手前必须读）：**
- `plugins/onestep-feishu-bitable/src/onestep_feishu_bitable/_shared.py`：`_FeishuRelationConfig`（L97-105）、`_normalize_relations`（L401-465）、`_RELATION_FIELDS`（L33）、`_normalize_relation_values`（L468-497）
- `plugins/onestep-feishu-bitable/src/onestep_feishu_bitable/sink.py`：`open()` / `_load_insert_key_index`（L158-277，eager 扫描的直接模板）、`_batch_resolve_relations`（L910-986）、`_batch_create_relation_records`（L988-1020）、`_resolve_relation_fields`（L1116-1150）、`_find_or_create_relation_record`（L1152-1197）、`_find_relation_matches`（L1199-1221）、`control_plane_descriptor`（L1223 起）
- `plugins/onestep-feishu-bitable/src/onestep_feishu_bitable/resources.py`：`_RELATION_FIELDS`（L38）、`_validate_feishu_relations`（L248-301）

---

### Task 1: cache 配置契约（_shared.py + 校验测试）

**Files:**
- Modify: `plugins/onestep-feishu-bitable/src/onestep_feishu_bitable/_shared.py`
- Test: `plugins/onestep-feishu-bitable/tests/test_relation_cache_config.py`（新建）

- [ ] **Step 1: 写失败的配置归一化测试**

新建 `test_relation_cache_config.py`，参照 `test_insert_index_config.py` 的风格，覆盖：

```python
# 缺省为 none
sink = make_sink(relations={"companies": {"table_id": "t", "key": "name"}})
assert sink.relations[0].cache == "none"

# 显式 lazy / eager 接受
# 非法值拒绝："always"、""、"NONE"（大小写不敏感归一化后判断，见 Step 3 决策）、True、1、None 显式传入
with pytest.raises((ValueError, TypeError)):
    make_sink(relations={"companies": {"table_id": "t", "key": "name", "cache": "always"}})
```

同时断言非法 `cache` 时错误消息包含 `relations.companies.cache` 这样的字段路径（对齐既有 `on_missing` 校验的消息风格）。

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/miclon/Development/mic1on/onestep/onestep-feishu-relation-cache
uv run --all-packages pytest -q plugins/onestep-feishu-bitable/tests/test_relation_cache_config.py
```

Expected: 失败，因为 `_FeishuRelationConfig` 没有 `cache` 字段 / `_normalize_relations` 不认识 `cache`（unknown fields 报错）。

- [ ] **Step 3: 实现 cache 归一化**

在 `_shared.py`：
1. `_RELATION_FIELDS` 增加 `"cache"`。
2. 新增 `_RELATION_CACHE_POLICIES = frozenset({"none", "lazy", "eager"})`。
3. `_FeishuRelationConfig` 增加 `cache: str` 字段（放在 `create_fields` 之后；frozen dataclass 加字段即可，注意所有构造点都由 `_normalize_relations` 统一创建，无其他手工构造点——用 grep 确认）。
4. `_normalize_relations`：读取 `raw_config.get("cache", "none")`，用 `_require_non_empty_string(..., field=f"{field}.cache")` 取值后 `.lower()`，校验属于 `_RELATION_CACHE_POLICIES`，否则 `ValueError(f"'{field}.cache' must be one of 'none', 'lazy', or 'eager'")`。大小写归一化策略与 `on_missing` 完全一致（`on_missing` 现在就是 `.lower()` 后判断）。

- [ ] **Step 4: 运行测试确认通过，并跑既有 sink 配置回归**

```bash
uv run --all-packages pytest -q plugins/onestep-feishu-bitable/tests/test_relation_cache_config.py plugins/onestep-feishu-bitable/tests/test_insert_index_config.py
```

Expected: 全部通过。

### Task 2: lazy 被动缓存（sink.py 解析路径接入）

**Files:**
- Modify: `plugins/onestep-feishu-bitable/src/onestep_feishu_bitable/sink.py`
- Test: `plugins/onestep-feishu-bitable/tests/test_relation_cache_lazy.py`（新建）

- [ ] **Step 1: 写失败的 lazy 行为测试**

用计数 mock connector（参照 `test_feishu_bitable_connector.py` 里现有 relation 测试的 mock 方式；先读该文件已有的 fake connector 工具，不要重新发明）覆盖：

1. **跨批命中**：`batch_size=2` 的 upsert sink，`cache: "lazy"`，连续 flush 两批、两批引用同一业务键 → `_find_relation_matches` 对应的 search 只被调用 1 次（第一批 miss 回源 + 回填，第二批零 search）。
2. **单条路径命中**：`batch_size=1`，同一业务键 send 两次 → search 1 次。
3. **唯一命中回填**：search 返回一条 → 后续同键零 search 且 record_id 正确。
4. **零命中不缓存**：`on_missing: "empty"`，search 返回 0 条两次 → search 2 次（"不存在"不缓存）。
5. **`on_missing: error` 零命中不缓存**：第一次抛 `FeishuBitablePayloadError`；修复 mock 让 search 有结果后再 send → 成功（证明错误结果没被缓存）。
6. **create 回填**：`on_missing: "create"` 第一次创建（mock `batch_create_records` / `create_record` 返回 record_id），第二次同键 → 零 search 零 create，直接用缓存的 record_id。
7. **多条命中不缓存**：search 返回 2 条 → 永久错误；下次同键仍 search。
8. **none 回归**：不配置 `cache` 的关联字段，同键跨批 search 次数 = 2（行为不变）。

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run --all-packages pytest -q plugins/onestep-feishu-bitable/tests/test_relation_cache_lazy.py
```

Expected: 计数断言失败（search 被重复调用）。

- [ ] **Step 3: 实现 lazy 缓存**

在 `sink.py`：

1. `__init__` 新增：

```python
self._relation_caches: dict[str, dict[str, str]] = {
    rel.target_field: {} for rel in self.relations if rel.cache != "none"
}
```

2. 加一个小的内部 helper（保持最小侵入，供三条路径共用）：

```python
def _relation_cached_id(self, relation: _FeishuRelationConfig, value: str) -> str | None:
    cache = self._relation_caches.get(relation.target_field)
    if cache is None:
        return None
    return cache.get(value)

def _relation_cache_store(self, relation: _FeishuRelationConfig, value: str, record_id: str) -> None:
    cache = self._relation_caches.get(relation.target_field)
    if cache is not None:
        cache[value] = record_id
```

3. **批量路径** `_batch_resolve_relations()` 的 `search_one()`：先 `_relation_cached_id`，命中则写批次局部 `cache[(rel.target_field, value)]` 并 return（不发 search）；miss 走现有 `_find_relation_matches`，唯一命中后 `_relation_cache_store` 再写局部 cache。`to_create` / error 分支不变。
4. **批量创建回填** `_batch_create_relation_records()`：在把 `rec["record_id"]` 写入局部 `cache` 的同一处，同时 `_relation_cache_store(rel, value, rec["record_id"])`。
5. **单条路径** `_resolve_relation_fields()`：逐值先查持久缓存，命中直接使用（跳过 `_find_relation_matches`）；miss 走现有逻辑，唯一命中后回填；`on_missing == "create"` 分支不变（走 `_find_or_create_relation_record`，由第 6 点处理回填）。
6. **create 路径** `_find_or_create_relation_record()`：进入锁后、二次 search 前先查持久缓存，命中则 `entry.record_id = cached; return cached`；创建成功后（现有 `entry.record_id = _record_id(raw_record)` 处）同时 `_relation_cache_store`。

注意：不要改 `_find_relation_matches()` 本身；零命中一律不写缓存；缓存 value 只存确定唯一命中的 record_id。

- [ ] **Step 4: 运行 lazy 测试 + 既有 relation/批量测试回归**

```bash
uv run --all-packages pytest -q plugins/onestep-feishu-bitable/tests/test_relation_cache_lazy.py plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py
```

Expected: 全部通过。

### Task 3: eager 主动缓存（open() 启动扫描）

**Files:**
- Modify: `plugins/onestep-feishu-bitable/src/onestep_feishu_bitable/sink.py`
- Test: `plugins/onestep-feishu-bitable/tests/test_relation_cache_eager.py`（新建）

- [ ] **Step 1: 写失败的 eager 扫描测试**

参照 `test_insert_index_scan.py` 的 mock 结构与断言风格，覆盖：

1. **全量加载**：`cache: "eager"`，`open()` 时 `search_records` 被分页调用，body 为 `{"field_names": [relation.key]}`、`page_size == sink.insert_index_page_size`、`operation == ConnectorOperation.OPEN`；两页扫描后缓存包含全部 `{业务键: record_id}`；之后 send 命中键零 search。
2. **多 eager 关联字段**：两个 eager 字段按配置顺序依次扫描（可断言 search_records 调用的 table_id 顺序）。
3. **缺 key / 空 key 跳过**：扫描页中一条记录 fields 缺 key 或值为空 → 不进缓存，扫描继续，日志计数 `missing_key_records`。
4. **重复 key 覆盖**：两条记录同 key → 后者覆盖前者，计数 `duplicate_keys`。
5. **max_pages 耗尽**：`insert_index_max_pages=1` 且第一页 `has_more=True` → `open()` 抛 `ConnectorOperationError`（kind=PERMANENT，operation=OPEN）。
6. **page_token 不前进**：返回重复 token → 抛 PERMANENT。
7. **扫描异常包装**：connector 抛非 ConnectorOperationError → 包装为 OPEN/PERMANENT。
8. **运行时 miss 回源**：扫描完成后，mock 一个不在缓存中的新键 → send 时 search 一次（operation=SEND），唯一命中后回填；再 send 同键零 search。
9. **防重复创建回归**：`on_missing: "create"`，键不在缓存但 search 实际能查到（模拟启动后新增的记录）→ 走 search 命中分支，**不调用 create**。
10. **lazy 字段不被扫描**：`cache: "lazy"` 的关联字段在 `open()` 时不触发任何 search。
11. **结构化日志**：扫描成功产生 `feishu_relation_cache_scan` 日志，含 target_field、scan_pages、scan_keys、duration_s、outcome="success"（用 `caplog` 断言关键字段）。

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run --all-packages pytest -q plugins/onestep-feishu-bitable/tests/test_relation_cache_eager.py
```

Expected: 失败（open() 不做关联扫描）。

- [ ] **Step 3: 实现 eager 扫描**

在 `sink.py`：

1. `__init__` 增加已加载标记：

```python
self._relation_eager_loaded: set[str] = set()  # target_field 集合
```

2. 新增 `_load_relation_eager_caches()`，逐 eager 关联字段调用 `_load_relation_eager_cache(relation)`。后者**以 `_load_insert_key_index()` 为直接模板**：相同的分页循环、token 防重（`seen_tokens`）、max_pages 耗尽报错、异常包装为 `ConnectorOperationError(OPEN, PERMANENT)`。差异点：
   - `app_token` / `table_id` 用 `relation.app_token` / `relation.table_id`。
   - body 为 `{"field_names": [relation.key]}`。
   - 每条记录：取 `raw_fields.get(relation.key)`，用 `_normalize_relation_values(value, field=relation.key)` 规范化；空元组 → `missing_key_records += 1` 跳过；否则对每个业务键 `loaded[biz_key] = _record_id(raw_item)`，重复键计 `duplicate_keys`。
   - 完成后写入 `self._relation_caches[relation.target_field] = loaded`，加入 `self._relation_eager_loaded`，并发结构化日志 `feishu_relation_cache_scan`（字段对齐设计文档：target_field、table_id、scan_pages、scan_keys、missing_key_records、duplicate_keys、duration_s、outcome、page_size、max_pages）。
3. `open()` 改为：

```python
async def open(self) -> None:
    if self.insert_key_index and not self._index_loaded:
        await self._load_insert_key_index()
    await self._load_relation_eager_caches()
```

4. 运行时 miss 回源 + 回填：Task 2 已实现的 lazy 语义天然覆盖（eager 字段也有 `_relation_caches` 条目，miss 走同一逻辑），无需额外代码——确认测试 8/9 通过即可。

注意 `_normalize_relation_values` 对 dict/set 会抛 `FeishuBitablePayloadError`：扫描中捕获该异常并按 missing_key_records 计数跳过（容错，不让单条脏数据毁掉整个启动扫描），与 `_load_insert_key_index` 捕获 `FeishuBitablePayloadError` 的处理一致。

- [ ] **Step 4: 运行 eager 测试 + insert_index 回归 + 全量插件测试**

```bash
uv run --all-packages pytest -q plugins/onestep-feishu-bitable/tests/
```

Expected: 全部通过。

### Task 4: YAML strict 校验、catalog、descriptor

**Files:**
- Modify: `plugins/onestep-feishu-bitable/src/onestep_feishu_bitable/resources.py`
- Test: `plugins/onestep-feishu-bitable/tests/test_relation_cache_config.py`（追加）

- [ ] **Step 1: 写失败的 YAML/descriptor 测试**

1. strict YAML：relations 中 `cache: eager` / `lazy` 通过校验；`cache: always`、非字符串值报 ValueError/TypeError，错误消息含字段路径。（参照 `test_feishu_bitable_plugin.py` 中现有 relations strict 校验用例的构造方式。）
2. descriptor：`control_plane_descriptor()` 的关系概要包含 `"cache": "eager"`；JSON 序列化后不含 app_token 原值。

- [ ] **Step 2: 运行确认失败**

```bash
uv run --all-packages pytest -q plugins/onestep-feishu-bitable/tests/test_relation_cache_config.py -k "yaml or descriptor"
```

- [ ] **Step 3: 实现**

`resources.py`：
1. `_RELATION_FIELDS` 增加 `"cache"`。
2. 新增 `_RELATION_CACHE_POLICIES = frozenset({"none", "lazy", "eager"})`（模块级，与 _shared 保持一致）。
3. `_validate_feishu_relations`：在 `on_missing` 校验之后加：

```python
if "cache" in raw_config:
    cache = ctx.string_value(raw_config.get("cache"), field=f"{relation_field}.cache").strip().lower()
    if cache not in _RELATION_CACHE_POLICIES:
        raise ValueError(
            f"'{relation_field}.cache' must be one of 'none', 'lazy', or 'eager'"
        )
```

catalog 不变（relations 仍是 mapping）。

`sink.py` `control_plane_descriptor()`：关系概要 dict 增加 `"cache": relation.cache`（位置与现有 target_field/from/table_id/key/on_missing/create_field_names 并列）。

- [ ] **Step 4: 运行插件全量测试**

```bash
uv run --all-packages pytest -q plugins/onestep-feishu-bitable/tests/
```

Expected: 全部通过。

### Task 5: 文档与最终验证

**Files:**
- Modify: `docs/broker/feishu-bitable.md`
- Modify: `plugins/onestep-feishu-bitable/README.md`

- [ ] **Step 1: 更新用户文档**

`docs/broker/feishu-bitable.md` 的"关联字段"一节，在 `on_missing` 策略表之后新增 `cache` 小节：

- 配置示例（一个 eager 主数据 + 一个 lazy 关联）。
- 三取值语义对照表（none 默认 / lazy 查过才缓存 / eager 启动全量预加载）。
- eager 约束：默认上界 200 页 × 500 条 = 10 万条（复用 `insert_index_page_size` / `insert_index_max_pages`）；关联表应为稳定主数据；任务运行期间新增 key 自动回源兜底。
- **显式运维说明**：缓存不感知关联表中的删除与 key 修改；悬垂 record_id 会在写入时被飞书拒绝（RecordIdNotFound / LinkFieldConvFail），此时重启任务即可重建缓存。缓存不持久化、不跨进程共享。
- "重要参数"表加一行 `relations.*.cache`。

`plugins/onestep-feishu-bitable/README.md`：在 relations 提及处加一句话 + 链接 docs/broker/feishu-bitable.md，不复制整段说明。

- [ ] **Step 2: 最终验证**

```bash
cd /Users/miclon/Development/mic1on/onestep/onestep-feishu-relation-cache
uv run --all-packages pytest -q plugins/onestep-feishu-bitable/tests/
git diff --check
```

Expected: 测试全过、无空白错误。本计划不改版本号（发布 bump 由后续流程处理）。

- [ ] **Step 3: 对照 spec 自查最终 diff**

逐条核对 `docs/superpowers/specs/2026-09-11-feishu-bitable-relation-cache-design.md`：cache 三取值与默认 none；lazy 语义（查过才缓存、零命中不缓存、create 回填）；eager 语义（open 扫描 + miss 回源）；不做删除失效/不持久化；扫描约束复用 insert_index_page_size/max_pages；descriptor/YAML/catalog 一致；未配置 cache 的既有行为零变化。确认 diff 中没有超出 spec 范围的改动（尤其：没有 TTL、没有驱逐、没有持久化、没有触碰 on_missing 语义）。
