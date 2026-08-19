# mysql_table_sink 按列写入策略（column write policies）设计

日期：2026-08-19
状态：已确认（与 issue #120 后续讨论一致）
版本：onestep-mysql 0.6.0

## 背景

`mode: update` / `mode: upsert` 落库时，`update_columns` 白名单列被载荷值无条件覆盖：
载荷值为 `null` 会生成 `SET col = NULL` 清空库里已有值；业务上需要两种相反的保护语义：

1. **skip_null**：载荷值为 null 时该列不写（防止清空）；
2. **backfill**：库里当前值为 null 时才写入新值，非 null 保持原值（只补空不覆盖）。

## 核心决策

- 策略与 mode 正交：mode 决定"行怎么落库"（insert/upsert/update），策略决定"列的新旧值怎么合并"。两种策略可在同一 sink 内按列混用。
- 配置内联进 `update_columns`：字符串条目 = 默认 `overwrite`（完全向后兼容）；对象条目 `{name, policy}` 承载高级定制。避免独立的 `column_policy` 映射字段带来的键漂移校验。
- 三种策略：`overwrite`（默认，现行为）/ `skip_null` / `backfill`。
- update 与 upsert 共用同一份 SET 推导（`_update_payload`），`ON DUPLICATE KEY UPDATE` 子句自动获得同样能力。

## 语法

```yaml
update_columns:
  - deadline              # 覆盖（默认）
  - name: tenderee
    policy: skip_null     # 载荷 null → 该列不进 SET
  - name: publish_date
    policy: backfill      # SET col = COALESCE(col, :val)
```

Python：`update_columns=("deadline", {"name": "tenderee", "policy": "skip_null"})`。

## SQL 语义

| policy | 生成 | 说明 |
|---|---|---|
| overwrite | `SET col = :val` | 现行为，默认 |
| skip_null | 载荷 null → 列剔除；非 null → `SET col = :val` | 按行动态 |
| backfill | `SET col = COALESCE(col, :val)` | 原子、单往返，MySQL/SQLite 通用 |

## 边界与错误处理

- skip_null 过滤后 SET 为空 → 该条跳过并记 INFO 日志，不报错（update/upsert 统一；at-least-once 下属合法的"无事可做"）。非过滤导致的空 SET 维持现有构造/构建期报错。
- 构造期校验（全部报错）：对象条目缺 `name` / 未知键 / policy 不在枚举内 / 列名重复（含字符串与对象混叠）/ 策略列命中 `keys` / 策略列与 `update_expr` 同列冲突。
- 纯字符串条目与 `update_expr` 的现有覆盖关系不变。

## Catalog / 控制面

- `update_columns` 的 catalog 字段类型 `string_list` → `json`（`CATALOG_FIELD_TYPES` 已有 `json`，如实描述混合形态）。
- `topology_fields` 含嵌套对象：plane 应按前向兼容的 JSON 容忍。发版前 verify plane 对 json 类型字段与该拓扑字段的渲染/校验（plane 源码不在本仓库，列入发版清单）。

## 兼容性

纯字符串配置零变化；默认策略 overwrite；无隐式行为变更。版本 0.5.2 → 0.6.0。
