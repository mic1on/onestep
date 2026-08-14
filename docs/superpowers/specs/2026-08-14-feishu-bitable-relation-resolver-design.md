# Feishu Bitable 关联字段解析设计

## 背景

onestep-feishu-bitable 的 Table Sink 当前将 handler 返回的字段原样写入飞书。这适合
文本、数字和已经编码完成的人员字段，但飞书关联字段要求写入被关联表的 record_id。
业务上游通常只有企业名称、企业编码等业务键，不知道飞书记录 ID。

例如，项目上游数据是：

~~~python
{
    "项目编号": "P-001",
    "项目名称": "联合建设项目",
    "企业名称": ["企业A", "企业B", "企业C"],
}
~~~

项目表的“关联企业”字段实际需要：

~~~python
{"关联企业": ["rec_a", "rec_b", "rec_c"]}
~~~

如果每个 handler 都自行查询企业表、处理零条或重复匹配、创建缺失企业并转换
record_id，相同逻辑会散落在业务代码中，错误与重试语义也难以保持一致。

## 目标

- 在现有 feishu_bitable_table_sink 上声明关联字段解析规则。
- 用目标表的业务键将上游值解析为飞书 record_id。
- 同一个关联配置同时支持单值和多值输入。
- 关联记录不存在时支持 error、empty 和 create 三种策略。
- create 策略创建关联记录后，立即把新 record_id 写入当前目标记录。
- Python API、YAML strict 校验、资源目录描述和控制面 descriptor 保持一致。
- 未配置关联规则的现有 Sink 行为完全不变。

## 非目标

- 不从飞书字段元数据自动推断哪些字段是关联字段。
- 不提供任意工作流、跨任务依赖或通用数据映射 DSL。
- 不实现模糊匹配、别名匹配或企业名称清洗。
- 不在首版增加跨请求或持久化的 record_id 缓存。
- 不保证多个 worker 进程或多个部署实例间自动创建的全局原子去重。
- 不主动维护双向关联的反向字段；反向关系仍由飞书字段配置和服务端维护。

## 推荐方案

关系解析属于飞书字段写入编码，因此扩展现有 FeishuBitableTableSink，在 send() 的
目标记录查找和写入之前增加关系解析阶段。

不新增独立 Relation Sink，也不要求 handler 调用 helper。handler 继续输出业务值，
Table Sink 统一负责：

1. 读取关联字段的输入值。
2. 在关联表按业务键精确查询。
3. 按缺失策略处理零条结果。
4. 将业务值替换成 record_id 数组。
5. 使用现有 create、update 或 upsert 流程写目标记录。

## 配置契约

### YAML

~~~yaml
resources:
  projects:
    type: feishu_bitable_table_sink
    connector: feishu
    app_token: FEISHU_APP_TOKEN
    table_id: PROJECT_TABLE_ID
    mode: upsert
    match_fields: [项目编号]
    relations:
      关联企业:
        from: 企业名称
        table_id: ENTERPRISE_TABLE_ID
        key: 企业名称
        on_missing: create
        create_fields:
          数据状态: 待完善
~~~

relations 是以目标关联字段名为键的 mapping。上述配置表示：从 payload 的“企业名称”
读取业务值，在企业表的“企业名称”字段上精确查询，并将解析后的 record_id 数组写入
项目表的“关联企业”。实际部署时 app_token 和 table_id 可继续使用环境变量插值。

### Python API

~~~python
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
            "create_fields": {"数据状态": "待完善"},
        }
    },
)
~~~

Python API 接受与 YAML 相同的 mapping，并在创建 Sink 时完成标准化和校验。

### 关系配置字段

| 字段 | 必填 | 语义 | 默认值 |
|---|---:|---|---|
| table_id | 是 | 被关联的飞书数据表 ID | 无 |
| key | 是 | 关联表中用于精确匹配的字段；业务上必须唯一 | 无 |
| from | 否 | 从目标 payload 的哪个字段读取业务值 | 当前关联字段名 |
| on_missing | 否 | error、empty 或 create | error |
| create_fields | 否 | 自动创建关联记录时附加的静态字段 | 空 mapping |
| app_token | 否 | 关联表所在 Base 的 token | 当前 Sink 的 app_token |

create_fields 只能与 on_missing: create 一起使用，并且不能包含或覆盖 key。首版只支持
静态字段，不解析 payload 模板或表达式。

## 输入和输出

### 单值

~~~python
{"企业名称": "企业A"}
~~~

解析为：

~~~python
{"关联企业": ["rec_a"]}
~~~

### 多值

~~~python
{"企业名称": ["企业A", "企业B", "企业C"]}
~~~

解析为：

~~~python
{"关联企业": ["rec_a", "rec_b", "rec_c"]}
~~~

不增加 multiple 配置项。输入值的形状直接表达基数，输出始终是飞书关联字段要求的
ID 数组。

### 输入标准化

- 字符串是一个业务键。
- list 或 tuple 是多个业务键；字符串本身始终作为一个业务键。
- None、空字符串和列表中的空项被忽略。
- 全部为空时不调用查询 API，目标关联字段编码为 []。
- 重复业务值按首次出现顺序去重，同一条 payload 内只解析一次。
- 除字符串、list、tuple 和 None 外的输入类型属于永久 payload 错误。
- 解析后的 record_id 按标准化业务值的首次出现顺序输出。

## 查找语义

每个唯一、非空业务值使用飞书 records search API 查询，page_size=2，只区分以下状态：

- 一条：使用该记录的 record_id。
- 零条：应用 on_missing。
- 两条：业务键不唯一，整条 payload 永久失败。

插件不选择第一条，也不尝试模糊匹配。关联表的 key 唯一性是使用方必须维护的业务
约束。

## 缺失策略

on_missing 只处理查询成功但返回零条的业务值。网络错误、鉴权错误、限流、非法查询
或响应格式错误不得被解释为“缺失”。

### error

这是默认策略。任意非空业务值未命中时，整条目标 payload 失败，项目记录不写入。

多值输入中，即使其他企业已成功解析，只要一项缺失，整条项目仍失败。这样不会写入
不完整关系。

### empty

未命中的业务值被跳过：

- 单值未命中时，目标关联字段为 []。
- 多值部分命中时，只写已命中的 record_id。
- 多值全部未命中时，目标关联字段为 []。

对于 update 或 upsert 命中的已有项目，写入 [] 会清空旧关联。empty 的含义是“明确
写空”，不是“保留目标表旧值”。需要保留旧值的业务不应返回空输入或使用 empty。

### create

每个未命中的业务值都创建一条关联记录。创建字段为：

~~~python
{
    **relation.create_fields,
    relation.key: business_value,
}
~~~

创建成功后，从响应中的记录对象读取新 record_id，与已找到的 ID 一起按输入顺序写入
目标关联字段。

任意创建失败时，当前项目记录不写入。此前已经成功创建的关联记录不会回滚；运行时
重试会重新查询，找到这些记录后继续处理。因此关联表的业务键必须保持唯一，写入流程
也按 at-least-once 语义设计。

## 自动创建的并发边界

同一个 Sink 实例内，为 (app_token, table_id, key, business_value) 建立按需
single-flight 锁。锁只覆盖 create 策略的“二次查询 -> 创建”临界区：

1. 第一次查询未命中。
2. 获取该业务值的锁。
3. 在锁内再次查询。
4. 二次查询仍未命中才创建。
5. 释放并清理无人等待的锁。

这能避免同一 worker 进程内并发项目为同一企业重复创建。

飞书 Bitable 没有由插件控制的数据库唯一约束，多个进程或部署实例仍可能同时完成
二次查询并分别创建。因此首版明确不承诺全局原子去重。生产上应优先使用稳定业务键，
并控制会自动创建相同主数据的并发写入来源。

## 运行时结构

### 关系配置模型

connector.py 增加内部不可变配置模型 _FeishuRelationConfig，保存：

- 目标字段名
- 源字段名
- 关联表 app_token
- 关联表 table_id
- key
- on_missing
- create_fields

FeishuBitableConnector.table_sink() 和 FeishuBitableTableSink.__init__() 增加可选 relations
参数。初始化时完成全部配置标准化，send() 不再重复解释原始 mapping。

### 解析阶段

FeishuBitableTableSink.send() 的顺序为：

1. _payload_fields() 提取目标字段 mapping。
2. _resolve_relation_fields() 从同一份原始字段快照读取所有关系输入。
3. 所有关系解析成功后，消费与目标字段不同的 from 输入别名，再写入解析后的关联字段。
4. 使用解析后的字段执行现有 match、create 或 update 流程。

关系解析不修改传入的 Envelope.body 或嵌套 fields mapping。

如果 from 与目标关联字段名不同，from 是只供关系解析消费的输入别名，所有关系解析成功
后会从最终飞书字段中删除。这防止把仅用于关系查找的“企业名称”误写入项目表。多个
关系可以共享同一个 from；它们都从删除前的原始快照读取，因此结果不依赖配置顺序。

首版禁止关系目标字段出现在 match_fields 中，也禁止与目标字段不同的 from 出现在
match_fields 中。这样关系解析不会改变目标记录 upsert 的业务定位值，也避免把
record_id 数组作为现有精确匹配过滤条件。如果项目表还需要保存企业名称，handler 应
另外输出项目表中的实际目标字段，不能复用被消费的 from 别名。

### 错误模型

- 飞书 search/create 请求继续抛出原有 ConnectorOperationError。
- 关系解析发生在 Sink 内，因此 operation 使用 ConnectorOperation.SEND。
- 限流、网络和服务端错误沿用现有分类，可由 runtime 重试。
- 非法关系配置在构建 Sink 时失败。
- 非法输入类型、重复匹配、缺失且策略为 error 属于永久错误。
- 错误信息可以包含字段名、table_id 和 key，但不能包含 app_token 或凭据。

## YAML 和资源目录

resources.py 的 Table Sink allowed fields 增加 relations，catalog 中将其描述为 mapping。

strict 校验递归检查：

- relations 必须是非空 mapping。
- 每个关系字段名必须是非空字符串。
- 每个配置必须是 mapping。
- 只允许 from、app_token、table_id、key、on_missing 和 create_fields。
- from、app_token、table_id 和 key 必须是非空字符串。
- on_missing 必须是 error、empty 或 create。
- create_fields 必须是 mapping，只能与 create 一起使用，且不能包含 key。
- 关系目标字段不得出现在 Sink 的 match_fields 中。
- 与目标字段不同的 from 不得出现在 Sink 的 match_fields 中。

资源构建器将原始 relations 传入 connector.table_sink()；运行时构造函数作为 Python API
的第二道校验，不能只依赖 YAML strict 模式。

## 控制面描述

Table Sink descriptor 增加关系概要，包含目标关联字段名、from、table_id、key、
on_missing、create_fields 的字段名，以及是否使用独立 app token。

descriptor 不输出任何 app_token 原值。跨 Base 关系的 token 与 Sink token 一样必须脱敏。

## 兼容性

- relations 默认为空，未配置时不执行任何额外 API 调用或字段转换。
- 现有 create、update、upsert、match_fields 和 user_id_type 行为不变。
- 现有直接传入 ["rec_xxx"] 的关联字段继续原样透传，只要该字段没有配置在 relations 中。
- 该能力是插件级新增配置，不需要修改 onestep core runtime。

## 测试

### Connector 行为

- 单值唯一命中后写一个 record_id。
- 三个企业名称按输入顺序写三个 record_id。
- 重复输入只查询一次，输出按首次出现顺序去重。
- 空值和全空列表不发关系查询并写 []。
- 非法输入类型返回永久错误。
- 重复匹配返回永久错误且不写项目。
- error 任一缺失时不写项目。
- empty 单值缺失写 []，部分缺失保留已命中 ID。
- create 创建一个或多个缺失企业，并使用响应 ID 写项目。
- create 部分创建失败时不写项目，重试可查到此前已创建记录。
- 同一 Sink 内并发创建相同企业只发生一次 create 请求。
- 查询限流、网络或服务端错误不走 missing 分支。
- 关系字段转换不修改输入 envelope。
- from 与目标字段不同后，源字段不出现在飞书目标请求中。
- 多个关系共享同一 from 时都从原始字段快照正确解析。
- 关系目标字段或被消费的 from 与 match_fields 冲突时构造失败。
- 跨 Base 查询和创建使用关系配置的 app_token。

### 配置和描述

- Python API 接受合法关系 mapping 并拒绝非法配置。
- strict YAML 接受合法的单个和多个 relation 配置。
- strict YAML 拒绝未知字段、缺少 table_id/key、非法策略和非法 create_fields。
- catalog 将 relations 暴露为 mapping。
- descriptor 展示安全概要，且序列化结果不包含任何 app token 或 secret。

### 回归

- 现有 Feishu Bitable 插件测试全部通过。
- 未配置 relations 的 create/update/upsert 请求体保持不变。
- 插件包构建和元数据检查通过。

## 文档

更新 docs/broker/feishu-bitable.md：

- 增加企业表和项目表的完整 YAML 示例。
- 展示单企业和多企业 handler payload。
- 解释三个缺失策略，特别是 empty 会清空已有关系。
- 说明 key 必须业务唯一。
- 说明自动创建在多实例下不提供全局原子去重。
- 说明跨 Base 时可覆盖关系的 app_token。

插件 README 只加入最小配置示例并链接详细文档，避免复制整份行为说明。

## 发布

实现和验证完成后，按插件发布约定更新 onestep-feishu-bitable 小版本及相应 release
metadata。设计阶段不提前修改版本。
