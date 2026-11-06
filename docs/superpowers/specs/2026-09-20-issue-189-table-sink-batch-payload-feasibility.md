# issue #189 可行性分析：table_sink 支持 list 批量载荷

日期：2026-09-20
状态：可行性分析 + 6 代理对抗式评审（终审：**go_with_changes**，见 §0）
对应 issue：<https://github.com/mic1on/onestep/issues/189>
分析基线：onestep 1.13.0 / onestep-sql 0.5.0（分支 `feat/upsert-unique-index-preflight`，`b22093c`）
验证环境：MySQL 8.0.46（3308）、PostgreSQL 16（5434）、SQLite（aiosqlite）、SQLAlchemy 2.0.48

---

## 结论摘要

issue 的核心判断正确：**executor 侧确实无需改动**，缺的只是 table_sink 接受 list 体。
`DeliveryExecutor.execute` 把 handler 返回值原样作为 emit body（`executor.py:135-162`），
`Envelope.body` 类型为 `Any`，capture/diagnostics 用通用 `encode_value` 递归编码——
全链路对 list 体天然兼容，唯一拒绝点是三个 `TableSink.send` 的 `isinstance(body, Mapping)` 守卫。

但 issue 提出的实现细节有三处**会在真实数据库上出错**，另有一处**性能收益被高估**：

| # | issue 的说法 | 实测结论 | 严重度 |
|---|---|---|---|
| 1 | 「逐行应用 serialize_json / skip_null / update_expr」 | **不可行**。`_build_statement` 把每行的值烘焙成字面量，复用同一语句跑 executemany 会把**第一行的值写进所有行** | blocker |
| 2 | 「任一元素非 Mapping → 整批 TypeError」 | 需**额外的均匀性校验**：同一批各行 key 集合不一致时，SQLAlchemy 以第一行为准生成列清单，**静默丢弃后续行的多出列** | blocker |
| 3 | （未提及） | `skip_null` 在批模式下必须改写为 `CASE WHEN excluded.col IS NULL THEN col ELSE excluded.col END`，否则 NULL 会覆盖已有值 | high |
| 4 | 「同样数据批量 executemany 约 30s」（隐含 5.3x） | 本机 MySQL 实测 18x，但**在延迟受限链路上只有 3x**——asyncmy 无法为 MySQL 8 的 upsert 打包多行 VALUES | medium |

---

## 0. 多智能体对抗式评审终审（2026-09-20 补充）

由 6 个独立子代理（不同模型路由）在真库上独立复现、找茬、生态调研后，红队综合终审。
复现环境与本文件 §分析基线一致；所有证据均来自实际运行，无静态推断。

### 终审结论：go_with_changes

**最强反对意见**：这不是「让 table_sink 接受 list」，而是把一条**按构造成立**的路径
（安全属性由编译器/驱动保证）换到一条**未经统一建模**的路径上，且新路径的失败模式大量
落在「静默错写」而非「报错」——短行丢列、缺列写 NULL、跳过被静默改成插入、空列表插
幽灵行、同批重复键 PG 硬失败而 MySQL/SQLite 静默 last-wins。本文件自身的矛盾
（§2.2 规则 3 与 §2.2.1 取交集）证明批量语义尚未统一建模。**若不接受下述硬约束，应转为 no_go。**

### 对本文件的三处修正（评审推翻/收紧）

1. **§3.0「统一走 multi-row」的前提只对 MySQL 成立**。PG 上 executemany(pipeline) 与
   multi-row 实测持平（延迟链路 300 行：263x vs 278x），且 executemany 路径**没有**
   65535 参数上限（30 万参数实测通过）——强推 PG 走 multi-row 反而白引入一个上限。
   正确形态：**MySQL 8.0.20+ 走 multi-row `AS new`，PG 走 executemany，方言各自选路**。
2. **chunk_size=500 行数分块被推翻**。PG 的上限是绑定**参数数**（65535）：200 列表
   328 行即爆，>131 列的表 chunk=500 必然失败。分块必须按
   `min(chunk_size, 65535 // ncols)` + 字节预算双限；或干脆不自动分块、超限报
   PERMANENT 让调用方降批。MySQL 侧上限是字节（max_allowed_packet），且**首次超限
   返回 2013 连接被掐断**而非干净的 1153，重试策略须先探活。
3. **「executemany 的 VALUES() 形式不能打包」被推翻**（实测 100 行 Questions=4，
   可打包）。但 MySQL 8.0.46 对 VALUES() 报 deprecation warning，最终结论（MySQL 走
   multi-row）不变，仅理由需更正。

### 评审新增的四个 blocker（本文件原未覆盖）

| # | 发现 | 实测证据 |
|---|---|---|
| B1 | **skip_null 新行语义分叉**：单行路径「全更新列被过滤 → 整行不插入」；批量共享 SET 后该行仍在 VALUES 里、**会被插入** | 两条路径对同一 payload 行为不同，且都不报错 |
| B2 | **update 模式参数键静默进 SET**：执行期任何与表列同名的参数键被**静默追加**进 SET 子句，绕过 update_columns 白名单 | 实测把不在白名单里的 `c='LEAKED'` 写入库（三方言一致） |
| B3 | **result 大小上限**：`max_result_bytes` 默认 1MiB。500 行×20KB 编码后 10MB → sink 写入**已完成** → `complete_execution` 抛 ExecutionEncodingError → delivery 标 FAIL → 重试重放整批 | 写入已生效但记为失败，破坏 at-least-once 幂等叙事 |
| B4 | **同批重复主键三方言分叉**：MySQL/SQLite 静默 last-wins，PG CardinalityViolation 硬失败 | 单行路径三方言都正常；现有 #188 预检只查表索引，不查载荷 |

### 必须落实的硬约束（红队裁定，按优先级）

1. 方言各自选路（MySQL multi-row / PG executemany），不强推统一写法；
2. 分块按**参数数 + 字节**双限（PG 65535 参数、MySQL max_allowed_packet 折半），
   或超限直接 PERMANENT 拒绝、由调用方降批；
3. **批量结果逐行等价于单行路径**——含「全过滤不插入」（B1）与「NOT NULL + skip_null
   + None → 跳过而非 IntegrityError」；做不到等价就别让批量与单行共享同一 sink 语义；
4. 空序列一律短路 no-op，**禁止 `.values([])` 幽灵行**（全默认值表上会真的插一行）；
   错误类型与 ES/ClickHouse/MongoDB 对齐用 `ConnectorOperationError(kind=PERMANENT)`
   而非 MISCONFIGURED（后者在仓库约定里保留给连接/认证/配置问题）；
5. 异构行 + **同批重复键** → 整批 PERMANENT 拒绝，报错带首个违规行下标；
6. `update_columns` 与载荷**取交集**，绝不照搬完整列表（PG/SQLite 会静默把缺的列写 NULL）；
7. result 大小**写入前预检**，禁止「写成功 → 事后抛错 → delivery FAIL → 重放」（B3）；
8. MySQL 别名陷阱必须有**真库连接级测试**（裸方言 `mysql.dialect()` 编译不出 `AS new`
   分支，纯编译单测必然漏检），测试内显式设 `server_version_info=(8,0,46)`，
   并覆盖 <8.0.20/MariaDB 分支；
9. update 模式**参数键白名单**校验：执行期把与列同名的参数键静默加进 SET 是注入级缺陷（B2），
   必须在构造/校验期拒绝未知键；
10. **update_expr 注入**（既有 bug：`"1; DROP TABLE x; --"` 经 YAML 实测可删表；
    PG 侧 psycopg3 安全）不阻塞本工作，但 update 批量会扩大其暴露面——
    同里程碑独立建单修复（顺带修裸列名 1052/AmbiguousColumn，统一表限定）。

### 裁决记录（四个争议点）

- **skip_null 新行分叉（B1）**：文档化解不了，**必须改代码**。同一 sink、同一 payload、
  两条路径行为不同且都静默 = 语义分叉，按约束 3 收敛。
- **§2.2 规则 3 vs §2.2.1**：**规则 3 作废**，采交集；但交集后缺列的行必须按单行等价
  处理（NOT NULL 跳过/报错），不是补 NULL。规则 3 防丢列的本意改由「整批拒绝」（约束 5）达成。
- **1MiB 上限（B3）**：不证伪「handler 返回整批」的**形态**，但证伪「无界返回 + 事后校验」
  的**实现**。形态可用的前提是 sink 写前预检、同一事务内定成败。
- **update_expr 注入**：既有问题，不阻塞；独立工单必建，不许顺手绕过。

### 仍未验证的假设（落地风险）

asyncmy 对 `AS new` 的处理跨驱动版本稳定性（及 <8.0.19 回退路径）；复合唯一键/多唯一索引下
CASE skip_null 的逐行正确性；MySQL 2013 断连后连接池恢复；PG `InFailedSqlTransaction` 与
跨块原子性的交互；`max_result_bytes` 调大后的内存/背压影响。

### 评审过程注记

首轮 7 代理中 5 个因 ceeg-aigw 路由不可用失败（探测确认），改用已验证路由重跑补齐；
接口设计角度（D2）的关键结论已由生态调研（错误类型/分块命名/空序列约定）与红队裁定覆盖，
未单独补跑。全部代理已清理自建表与临时脚本，两库仅剩原有表。

---

## 1. 已验证成立的部分

### 1.1 executor 无需改动（issue 判断正确）

实测端到端：handler 返回 list → executor 原样 emit → 只有 `TableSink.send` 抛
`TypeError: TableSink only accepts mapping payloads`。

```
executor.py:135   if outcome.handler_result is not None and self.task.emit_targets:
executor.py:288       body = result          # 原样，无 Mapping 校验
envelope.py       body: Any                  # 无类型约束
capture/codec.py  encode_value() 递归支持 list/dict/标量
```

`_extract_notification_payload`（executor.py:748）对非 Mapping 返回 `None`——
list 体场景下 `notification` meta 提取被静默跳过，属可接受的既有语义延伸，但值得在文档点明。

### 1.2 单事务 + 整批原子：成立

三个后端均验证：批中某行触发 DataError/IntegrityError 时整批回滚，行数不变。

```
MySQL  (probe15 #8): 5 -> 5  ATOMIC
PostgreSQL (probe33): 2216 -> 2216  ATOMIC
SQLite (probe1 #5):  3 -> 3  ATOMIC
```

`async with engine.begin()` 包住一次 `execute(stmt, [row...])` 即单事务，符合 issue 的
「批量 = 单事务 = 整批原子」语义。

### 1.3 upsert 批量幂等：成立

MySQL/PG/SQLite 三方言连跑两次，行数不增（probe15 #2、probe33 replay、probe16）。

### 1.4 空 list 必须显式短路（issue 已正确指出）

不 guard 时 SQLAlchemy 生成 `INSERT INTO t () VALUES ()`：

```
MySQL:  (1364, "Field 'k' doesn't have a default value")
SQLite: IntegrityError NOT NULL constraint failed: dev.k
```

issue 的「空 list → no-op，不发语句」是**必须项**而非优化项。

### 1.5 不做自动分块：成立

SQLAlchemy 2.0 的 `use_insertmanyvalues` 对 MySQL/SQLite 走驱动侧 `executemany`
（`before_cursor_execute` 只触发一次，`executemany=True`，参数 2500 个一次性交给驱动），
不会自行切分。PG 侧 `insertmanyvalues_max_parameters=32700` 在 2216 行内不会触发。
超大批次的分批责任确实在调用方。

---

## 2. 必须修正的三处（blocker / high）

### 2.1 `_build_statement` 无法复用于批量（blocker）

现状（mysql/connector.py:1141-1164）把**每一行的值烘焙成语句字面量**：

```python
stmt = mysql_insert(table).values(**payload)
return stmt.on_duplicate_key_update(**update_payload)   # update_payload 里是 Python 字面量
```

若照 issue 的字面描述「逐行应用策略后 executemany」，就会用**第一行构造的语句**跑整个批次。
实测（probe3）：

```
naive literal-set_ batch result:
  k=1 -> NEW1/111   ✓
  k=2 -> NEW1/111   ✗  应为 NEW2/222  ← 第一行的值污染了第二行
```

**正确做法**：批量路径必须改为引用 `excluded` / `inserted` 伪表，让 UPDATE 侧按行取本行新值。

```python
# PostgreSQL / SQLite
ins = pg_insert(table)
stmt = ins.on_conflict_do_update(
    index_elements=list(self.keys),
    set_={col: ins.excluded[col] for col in update_columns},
)

# MySQL（注意 2.1.1 的单实例陷阱）
ins = mysql_insert(table)
stmt = ins.on_duplicate_key_update(**{col: ins.inserted[col] for col in update_columns})
```

单行路径可保持现状（性能无回归，见 §4），因此这是**新增一条批量构造分支**，
而非替换现有 `_build_statement`。

#### 2.1.1 MySQL 别名陷阱（实测踩坑）

MySQL 8.0.20+ 的 `ON DUPLICATE KEY UPDATE` 要求 `VALUES()` 改为别名形式，
SQLAlchemy 通过 `_requires_alias_for_on_duplicate_key` 自动生成 `AS new`。
**`inserted` 伪列必须与 `on_duplicate_key_update` 来自同一个 `insert()` 实例**：

```python
ins = mysql_insert(table)                       # 同一个实例
stmt = ins.on_duplicate_key_update(a=ins.inserted.a)
# -> ... VALUES (...) AS new ON DUPLICATE KEY UPDATE a = new.a   ✓

stmt = mysql_insert(table).on_duplicate_key_update(a=mysql_insert(table).inserted.a)
# -> ... VALUES (...) AS new ON DUPLICATE KEY UPDATE a = inserted.a   ✗
#    1054 Unknown column 'inserted.a' in 'field list'
```

两次调用会产生两个独立的 `inserted_alias`，第二个实例渲染出的列引用与第一个实例的
别名不匹配，SQL 里出现 `AS new` 却引用 `inserted.a`——语法合法、语义错误、**运行时报错**。
现有逐行路径因为值直接烘焙成字面量，从未暴露这个陷阱。

#### 2.1.2 `update_expr` 的限定名问题（已存在的 bug，批量会放大）

裸列名表达式（如 `"b + 1"`）在 MySQL 8.0.20+ 的别名形式下**当载荷包含该列时**会报
`1052 Column 'b' in field list is ambiguous`（probe10、probe21）：

```
payload 含 b + expr on b  -> FAIL  (1052)
payload 不含 b + expr on b -> OK
batch + bare expr         -> FAIL  (1052)
qualified "feas10.b + 1"  -> OK
```

这是**既有 bug**（现有逐行路径同样失败，probe21 #A），不是批量引入的，
但批量会让它更常见。建议一并修正：`update_expr` 渲染时对裸列名做表限定，
或在文档明确要求写 `table.column` 形式。

### 2.2 需要整批 key 集合均匀性校验（blocker）

issue 只要求「任一元素非 Mapping → 整批报错」，但实测发现**更隐蔽的失败模式**：
同一批内各行 key 集合不一致时，SQLAlchemy 以**第一行**的 key 推导列清单，
后续行多出的列被**静默丢弃**（probe13/probe14）：

```
输入: [{"k":"1","a":"a1"},                       # 第一行短
       {"k":"2","a":"a2","b":22,"d":44}]         # 第二行有 b/d
结果: k=1 -> a1, b=NULL, d=NULL
      k=2 -> a2, b=NULL, d=NULL    ← b/d 静默丢失，无任何报错
```

反向顺序（第一行完整）则抛 `StatementError: A value is required for bind parameter 'b'`。
**同一份数据只因行序不同就产生「静默丢数据」或「报错」两种结果**，这是不可接受的。

另外，`new.col` 别名要求该列**出现在 INSERT 列表中**，否则
`1054 Unknown column 'new.b' in 'field list'`（probe14 #2、probe21 #C/D）——
即 upsert 批量的 update 列必须对**所有**行都存在，不能有的行有、有的行无。

**建议的校验规则**（在 `send` 入口，写入前一次性完成，全部报 `MISCONFIGURED`）：

1. 每个元素必须是 Mapping（issue 已有）；
2. 整批 key 集合必须**完全一致**（以 `frozenset(row.keys())` 比较），否则报错并指出
   首个不一致的行下标与差异列。这同时消除了上述两种失败模式；
3. 对 `upsert`/`update`，`keys` 与 `update_columns` 涉及的列必须都出现在该集合内
   （否则 `new.col` 引用不到）；
4. 空 list → 直接 return（no-op）。

如果希望放宽到「异构行也支持」，那就必须走 **按 key 集合分组、每组一条语句、同一事务**
的方案（probe27 已验证可行：分组后 3 组各自 executemany，默认值语义正确、原子性保持）。
代价是语句构造复杂度和 `rowcount` 聚合语义都要重新定义，**不建议放进首版**。

### 2.2.1 另外两条必须复刻的逐行语义（实测）

- **`update_columns` 中在载荷里缺失的列要按「交集」处理**。逐行路径下
  `update_columns=["a","b","c"]` 而载荷只有 `{"k","a"}` 时，只生成 `SET a = ...`，
  `b`/`c` 保持不动（实测）。批量路径必须把 `update_columns` 与载荷 key 集合取交集，
  否则会尝试引用 `new.b` 而该列不在 INSERT 列表中 → `1054 Unknown column 'new.b'`。
- **整行被 `skip_null` 过滤掉时，该行要整条剔除**（不是退化为无更新的 upsert）。
  逐行路径 `_build_statement` 此时返回 `None`，`_send` 记 INFO 后跳过、**不插入新行**；
  批量路径必须同样过滤，实测确认被过滤的行确实不入库。

### 2.2.2 `update` 模式的 bindparam 命名陷阱（实测）

`update` 模式不能用 multi-row VALUES（§3.0），只能 bindparam + executemany。
但 **bindparam 名字不能与列 key 同名**：

```python
sa.update(t).where(t.c.k == sa.bindparam("k")).values(a=sa.bindparam("a"))
# CompileError: bindparam() name 'k' is reserved for automatic usage in the
#               VALUES or SET clause of this insert/update statement
```

三方言一致报错。必须用独立命名（如 `bk` / `pa` / `pb`），执行前把每行的列 key
重映射到这些参数名。`skip_null` 在 update 模式下则用
`sa.case((sa.bindparam("pa").is_(None), t.c.a), else_=sa.bindparam("pa"))`
——实测按行生效正确。

### 2.3 `skip_null` 必须改写为 CASE（high）

逐行路径的 `skip_null` 是**构造期过滤**（该列不进 SET）。批量路径下 SET 子句是整批共享的，
无法按行增减列，必须改写为运行时条件表达式（probe20）：

```python
# skip_null 列在批模式下：
sa.case((ins.excluded[col].is_(None), table.columns[col]), else_=ins.excluded[col])
```

实测语义正确：载荷为 NULL 的行保留库中原值，非 NULL 的行正常覆盖。
`backfill` 的 `coalesce(col, excluded.col)` 天然按行求值，批量下**无需改写**（probe15 #7）。

若不做此改写，批模式下 `skip_null` 会退化为 `overwrite`，**静默清空**库中已有值——
与 issue #120 引入该策略的初衷正好相反。

---

## 3. 性能：收益真实，但 issue 的倍数只在低延迟链路成立（medium）

### 3.0 关键修正：应改用 multi-row VALUES，而非 executemany

§3.2 的结论（executemany 在 MySQL 上只有 3x）成立，但**不必接受它**。
进一步实测发现：把行列表交给 `.values(rows)` 构造**单条多行 VALUES 语句**，
可以完全绕过 asyncmy 的 `RE_INSERT_VALUES` 正则限制（它只在 `executemany` 路径生效），
且 update 侧引用 `inserted`/`excluded` 时语义正确：

```python
ins = mysql_insert(table)
stmt = ins.values(rows)                       # 单条多行 VALUES
stmt = stmt.on_duplicate_key_update(a=stmt.inserted.a, b=stmt.inserted.b)
# -> INSERT INTO t (k,a,b) VALUES (...),(...),(...) AS new
#    ON DUPLICATE KEY UPDATE a = new.a, b = new.b
```

MySQL `Questions` 计数器（100 行）：multi-row VALUES = **6 条**语句，
executemany = 105 条，当前逐行 = 303 条。

延迟受限链路（30ms/方向，300 行）：

| 路径 | 耗时 | 相对当前 |
|---|---|---|
| A 当前逐行 | 58.6s | 1x |
| B 单事务 + executemany（issue 原文） | 19.2s | 3.0x |
| C 单事务 + multi-row VALUES（建议） | **0.2s** | **258x** |

即 B 只省掉逐行的 COMMIT，C 连语句往返一起省掉。**设计应直接采用 C。**

代价与约束（均已实测）：

- **必须分块**。单条语句有大小上限：MySQL `max_allowed_packet`（默认 64MB，实测
  50000 行 × 500B ≈ 27MB 通过）、PG 硬上限 65535 个绑定参数（实测 21845 行 × 3 参数 =
  65535 通过，25000 行即报 `number of parameters must be between 0 and 65535`）。
  分块参数建议 `chunk_size=500` + 字节预算（照抄 `ElasticsearchBulkSink` 的
  `chunk_size=500` / `max_chunk_bytes=5_000_000` 先例）。
- **分块后仍是单事务**：三方言实测「第 2 块失败 → 第 1 块回滚」，`ATOMIC`。
- `sa.update` **不支持** `.values(list)`（`UPDATE construct does not support multiple
  parameter sets`），因此 **update 模式只能走 bindparam + executemany**，
  收益受 §3.2 的驱动限制。这是 upsert/insert 与 update 的固有差异，需在文档写明。
- `update_expr` 的裸列名在 MySQL 8 别名形式下仍会 `1052 ambiguous`（§2.1.2），
  多行形式同样需要表限定。

### 3.1 本机（127.0.0.1，2216 行）

| 路径 | 耗时 | 相对当前 |
|---|---|---|
| A 当前模式（每行一个 `engine.begin()`） | 5.7s | 1x |
| B 提议模式（单事务 + executemany） | 0.31s | **18x** |
| C 单事务 + 逐行 execute | 0.53s | 11x |
| D 单事务 + 纯 insert executemany | 0.04s | 158x |

issue 报告的 158.7s → 30s 与上表 A 的量级一致（其环境更慢），方向正确。

### 3.2 延迟受限链路（30ms/方向，模拟远程库）——这才是生产形态

| 路径 | 耗时 | 每行 |
|---|---|---|
| A 当前模式 | 59.1s | 197 ms/行 |
| B 提议模式 | 19.4s | 65 ms/行 |
| C 纯 insert executemany | 0.2s | 1 ms/行 |

**upsert 批量只有 3x，而 insert 批量有 266x。**

根因（probe28/probe30/probe32）：asyncmy 的 `Cursor.executemany` 用正则识别可打包的语句，
其 `RE_INSERT_VALUES` 只允许 `)` 之后直接跟 `ON DUPLICATE...`；MySQL 8.0.20+ 的
`AS new` 别名插在中间，**匹配失败 → 退化为客户端逐行循环**：

```
PACKS  | INSERT ... VALUES (%s, %s)
loops  | INSERT ... VALUES (%s, %s) AS new ON DUPLICATE KEY UPDATE a = new.a
PACKS  | INSERT ... VALUES (%s, %s) ON DUPLICATE KEY UPDATE a = VALUES(a)   # 8.0.19 及更早
```

MySQL `Questions` 计数器实测（100 行）：plain insert = 6 条语句；upsert `AS new` = 105 条；
upsert `VALUES()` = 205 条；当前逐行模式 = 303 条。即 upsert 批量**只省掉逐行的 COMMIT**，
省不掉逐行的语句往返。

PG 侧无此问题：psycopg3 的 executemany 默认走 pipeline，500 行批 = 1 事务（probe35）。

**影响**：issue 的验收标准应写「批量显著快于逐行」而非固定倍数；
若要把 MySQL upsert 也压到接近 insert 的水平，需另开 issue（例如让 asyncmy 识别别名形式，
或 sink 侧改用 `VALUES()` 形式——后者在 8.0.46 上仍可用但已 deprecated 并打 warning）。
本机 18x / 远程 3x 都应写进文档，避免使用者对收益有错误预期。

---

## 4. 向后兼容与测试面

### 4.1 现有测试大量依赖「语句内烘焙字面量」

`test_mysql_table_sink.py` 有 19 处、`test_postgres_table_sink.py` 有 20 处调用
`_build_statement(payload, table)` 并断言编译后的 SQL 文本（如 `"updated_at = NOW(6)"`、
`"coalesce(...)"`、`"title=" in set_clause`）。

只要**单行路径不改**（保留 `_build_statement` 现状 + 新增批量分支），这些测试全部不受影响。
这正是 §2.1 建议「新增分支」而非「替换实现」的原因。
issue 的验收项「Mapping 单行路径行为与性能不回归」由此可低成本满足。

### 4.2 基线

当前测试全绿（隔离进程运行，因两个插件测试目录有同名文件）：

```
plugins/onestep-mysql/tests      205 passed,  3 skipped
plugins/onestep-postgres/tests   142 passed,  2 skipped
tests/contract/*onestep_sql*     168 passed
```

### 4.3 新增测试建议

- 三方言各覆盖：正常批 / 空 list / 混入非 Mapping / key 集合不一致 / 单事务原子性 /
  upsert 两次幂等 / `skip_null` 按行生效 / `backfill` 按行生效 / `update_expr` 批量 /
  `update` 模式批量 / rowcount 聚合 / 分块边界（跨块失败整批回滚）/ `update_columns`
  交集语义 / 整行被 skip_null 剔除；
- 纯语句构造测试可覆盖大部分（现有风格），原子性与真实语义需 live 用例；
- MySQL live 用例应包含「载荷含 update_expr 目标列」的组合，锁死 §2.1.2。

---

## 5. 建议的验收标准修订

issue 原验收基本可用，建议补充/收紧：

- [ ] list 体（正常批 / 空 list / 混入非 Mapping 元素 / **行间 key 集合不一致**）在三方言行为符合规则；
- [ ] **同一批内各行 key 集合必须一致**；不一致时整批 `MISCONFIGURED`，不写一半；
- [ ] 单事务性：批中某行失败 → 整批无部分写入（三方言，含 live；**含跨分块场景**）；
- [ ] upsert 批量两次连跑幂等（行数不增）；
- [ ] `skip_null` 在批模式下按行生效（NULL 不清空已有值）；`backfill` 同理；
- [ ] **整行被 `skip_null` 过滤时不插入新行**（与逐行语义一致）；
- [ ] **`update_columns` 中载荷缺失的列不被写入**（与逐行语义一致）；
- [ ] `update_expr` 在批模式下可用（含 MySQL 8.0.20+ 别名形式）；
- [ ] Mapping 单行路径行为与性能不回归；
- [ ] 现有测试不回归；
- [ ] 文档写明：批量 = 单事务 = 整批原子；emit 路由谓词 / transform 拿到的 result 是 list；
      **insert/upsert 走 multi-row VALUES，`update` 模式因 SQLAlchemy 限制只能走
      executemany、收益较低**；
- [ ] CHANGELOG + 版本号（插件包改动需同时升 `pyproject.toml` / `uv.lock` / CHANGELOG）。

---

## 6. 落地拆解建议

1. **共享层**（`_shared/table_sink_policy.py`）：
   - 批载荷校验（Mapping 元素、key 集合均匀性、keys/update 列存在性、空批短路）；
   - 批量 SET 推导（`inserted`/`excluded` 引用、`skip_null` CASE、`backfill` coalesce、
     `update_expr` 透传、`update_columns` ∩ 载荷）；
   - 分块器（**参数数 + 字节双限**：PG `min(chunk_size, 65535 // ncols)`、MySQL 按
     max_allowed_packet 折半；固定 chunk_size=500 已被评审推翻，见 §0 修正 2）；
   - 被 `skip_null` 整行剔除的过滤。
   三个后端共用，符合 #133 的「一份实现」原则。
2. **各后端**：`send` 分派 Mapping / list；list 走新的 `_send_batch`
   （insert/upsert 用 multi-row VALUES 分块；update 用 bindparam 重映射 + executemany）；
   `_build_statement` 保持不动。
3. **文档**：`docs/broker/{mysql,postgres,sql}.md` + `docs/yaml-task-definition.md`
   （注意仓库纪律：文档站更新在 `docs` 分支，代码从 `main` 切特性分支）。
4. **测试**：三方言契约测试 + MySQL/PG live 用例。

预估工作量：共享层 + 三后端接线约 0.5–1 天；三方言测试与 live 用例约 1 天；
文档约 0.5 天。**主要风险不在实现，而在 §2.2 的语义决策**（是否允许异构行）——
建议首版直接拒绝异构行，把分组方案留作后续增强。

---

## 附：复现脚本

本文数字来自分析期临时脚本（`/tmp/feas189/`、`/tmp/fb4/`，均未入库）。
关键复现命令：

```bash
# 单元级（SQLite 内存库，无需外部服务）
probe3.py    # 字面量语句污染批次
probe13.py   # 异构行静默丢列
probe20.py   # skip_null 需 CASE
p2.py        # update 模式 bindparam 命名陷阱
p5.py        # update 模式不支持 multi-row VALUES

# 真库（MySQL 3308 / PG 5434）
probe15.py   # 三模式批量语义 + 原子性
probe29.py   # 性能归因（executemany 只省 COMMIT）
probe32.py   # asyncmy 打包正则
p3.py        # multi-row VALUES 语句数 6 vs 105
p4.py        # 延迟链路 258x vs 3x
p7.py        # 分块原子性 + PG 65535 参数上限
p8.py        # 跨分块失败整批回滚（三方言）
```
