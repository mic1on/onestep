# SQL table sink 批量写入的语义和边界

`send()` 的 list/tuple body 在一次事务中写入；`batch_size`、参数上限、MySQL
字节限制以及更新列策略都可能将其分成多条语句。任何一条失败会回滚整个 send。
事务性保证要求目标表使用事务型存储引擎（MySQL 使用 InnoDB）。

`skip_null` 按输入的 Python `None` 决定是否从 `SET` 中省略该列，不依赖驱动把
值编码成 SQL NULL 还是 JSON null。只有相邻、更新列集合相同的行才合并执行，
因此重复 update key 的先后顺序不变，未更新的列也不会触发 `UPDATE OF column`
触发器。`update` 的 NULL 键使用 `IS NULL`，绑定参数保留目标列类型。

SQLite 的全默认值输入，例如 `[{}, {}, {}]`，通过 executemany 执行三次
`DEFAULT VALUES`，保持输入行数。

## 批内唯一冲突检查

`upsert` 拒绝同一批次中受唯一索引约束的重复输入。普通唯一索引允许多个 NULL；
PostgreSQL `NULLS NOT DISTINCT` 按数据库规则拒绝冲突。部分唯一索引只有满足
谓词的输入行参与检查。检查覆盖整个 send，不能通过降低 `batch_size` 绕过。

精确重复的普通键先在内存中检查。整数键/整数输入的常规路径无需额外 DDL。
其他可反射的列唯一索引使用连接内临时表验证实际类型、排序规则、部分索引和
MySQL 前缀索引；检查后删除临时表。该表不复制目标数据、默认值、序列、外键或
触发器，不会向目标表写入验证数据。MySQL 唯一索引内联到 CREATE TEMPORARY TABLE，
避免 CREATE INDEX 隐式提交破坏事务。

这条数据库校验路径需要临时表创建权限（MySQL `CREATE TEMPORARY TABLES`、
PostgreSQL 数据库 `TEMP` 权限），并增加建表、索引和灌入校验行的开销。权限不足时
在写入目标表之前失败，不退回不完整的 Python 字符串比较。运行账户不需要目标
表的 ALTER/DROP 权限。吞吐测试应覆盖业务实际的索引与字段类型。

校验行按 `batch_size` 及方言预算分块，每块发起一次 executemany 调用，避免
逐行等待数据库响应。驱动可能继续拆分或采用其他执行策略，因此一次 API 调用
不保证只有一条 SQL 或一次网络往返。MySQL 路径在逐行检查编码后的大小之外，
还按当前连接的包上限约束 asyncmy 合并出的多值 INSERT。所有逻辑块与驱动拆出的
子批次都写入同一张带唯一索引的临时表，冲突检查仍覆盖整个 send。

检查的保证限于反射出的列唯一索引、输入显式提供的索引列，以及可提供谓词输入的
部分索引。部分索引缺失必要谓词字段时拒绝该 batch；不猜测列默认值。
表达式/函数唯一索引、未反射出的索引、服务端生成值和 payload 省略的唯一列不在
该预检保证内。它们仍受目标数据库约束，但调用者不能假设已通过完整的批内检查。

此检查不提供对目标已有行或并发 send 的跨方言等价保证。特别是 MySQL
`ON DUPLICATE KEY UPDATE` 可以命中任意唯一键；PostgreSQL/SQLite 的 conflict
目标仍是配置的 `keys`。业务需要以同一个 key 集合表达记录身份，且避免将含
secondary unique 冲突的多次 send 当成可交换操作。

## MySQL 编码后的包大小

4 MiB 估算只是初始拆批目标，不是安全保证。执行时读取当前连接的
`@@SESSION.max_allowed_packet`，在 SQLAlchemy 类型处理及当前连接的驱动编码、
转义后计算实际查询字节数（含 COM_QUERY 字节），并要求严格小于上限。尚未发送的
超限多行语句继续拆分；单行达到或超过上限则抛出 `PERMANENT` connector 错误，
并回滚前面的 chunk。

检查覆盖批内唯一校验的输入语句。使用 asyncmy 的实际 `mogrify` 和连接编码，
包含原生 JSON 序列化及连接的转义模式。executemany 的单行检查不足以约束驱动
合并出的语句，因此同时限制游标的 `max_stmt_length`，保留驱动原有的更小上限，
并为 COM_QUERY 字节及严格边界预留空间。断线错误仍按原有规则处理，不能把所有
2013 错误都视为 payload 错误。永久 connector 错误不会触发 sink 内部重试；
外层任务是否重试仍取决于 task retry policy。MySQL 会话断开后其临时表随之消失；
连接已失效时跳过显式 DROP，避免在失败事务中重新连接而掩盖原始连接错误。

## 验证

`tests/contract/test_onestep_sql_batch_regressions.py` 默认运行 SQLite 用例；设置
`ONESTEP_MYSQL_DSN` / `ONESTEP_POSTGRES_DSN` 后运行相应真实数据库用例。
MySQL 大包测试只有在 `ONESTEP_TEST_ALLOW_GLOBAL_MYSQL_SETTINGS=1` 时运行，
仅用于隔离测试实例：它临时修改全局包上限、创建新连接，并在 finally 中恢复设置。
不要对生产实例启用该测试选项。MySQL 8.0/8.4 CI 会运行该回归集并启用此选项。
低包上限用例使用 32KiB，覆盖整块超限而单行合规、UTF-8/转义及单行拒绝；该值高于
默认 16KiB 的 `net_buffer_length`，避免 1KiB 配置没有触及实际拒绝边界的假通过。
