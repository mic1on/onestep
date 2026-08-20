---
title: 迁移到 onestep-sql
outline: deep
---

# 迁移到 onestep-sql

`onestep-sql` 是 MySQL 与 PostgreSQL 的规范发行包（tracking issue #133，
[设计文档](../superpowers/specs/2026-08-20-onestep-sql-consolidation-design.md)）。
它把原先分开的 `onestep-mysql` 与 `onestep-postgres` 收敛为一个包、一个
namespace（`onestep_sql`）和一套共用 SQL 行为实现。

本页说明如何从旧发行包迁移到 `onestep-sql`，以及旧安装在新版本下如何继续工作。

## 是否需要立即迁移

不需要。旧发行包 `onestep-mysql` / `onestep-postgres` 仍以薄转发 shim 的形式
发布，并保持至少「六个月或两个 feature releases，以较晚者为准」的兼容窗口：

- `pip install onestep-mysql` 仍可安装，并自动拉取 `onestep-sql[mysql,sqlite]`。
- `from onestep_mysql import MySQLConnector` 等导入路径保持对象 identity 兼容。
- 所有 14 个 YAML 资源类型名（`mysql_*`、`postgres_*`）不变。
- 旧 shim 不再声明自己的 `onestep.resources` entry point；资源注册由
  `onestep-sql` 的单一 `sql` entry point 统一完成，因此新旧同装不会重复注册。

新部署和新文档示例建议直接使用 `onestep-sql`。

## 安装

| 场景 | 旧命令 | 新命令（推荐） |
| --- | --- | --- |
| MySQL | `pip install onestep-mysql` | `pip install 'onestep-sql[mysql]'` |
| PostgreSQL | `pip install onestep-postgres` | `pip install 'onestep-sql[postgres]'` |
| 两者 | 分别安装两个旧包 | `pip install 'onestep-sql[mysql,postgres]'` |
| 通过 core extra | `pip install 'onestep[mysql]'` | 不变（extra 现在解析到 `onestep-sql`） |
| 全部连接器 | `pip install 'onestep[all]'` | 不变 |

`onestep[mysql]`、`onestep[postgres]`、`onestep[sql]`、`onestep[all]`、
`onestep[dev]`、`onestep[integration]` 这些 core extra 现在都解析到
`onestep-sql`，无需修改 `pyproject.toml` 中的 `pip install 'onestep[...]'`。

## Python 导入

新代码应从规范 namespace 导入：

```python
# MySQL
from onestep_sql.mysql import MySQLConnector, BinlogSource, TableSink

# PostgreSQL
from onestep_sql.postgres import PostgresConnector, PostgresExecutionSource
```

旧导入路径保持兼容（转发 shim 保证对象 identity）：

```python
# 仍可用，等价于上面的导入
from onestep_mysql import MySQLConnector, BinlogSource, TableSink
from onestep_postgres import PostgresConnector, PostgresExecutionSource
```

## YAML 配置

**无需改动。** 所有 YAML 资源类型名、字段、默认值、catalog role 和 connector
boundary 全部不变。`onestep-sql` 通过单一 `sql` entry point 注册全部 14 个类型，
YAML loader 自动发现。

```yaml
resources:
  db:
    type: mysql          # 名字不变
    dsn: "${MYSQL_DSN}"
  cursor:
    type: mysql_cursor_store
    connector: db
  users:
    type: mysql_incremental
    connector: db
    table: users
    key: id
    cursor: [updated_at, id]
    state: cursor
```

## 后端专属能力边界

合并不是把 MySQL 与 PostgreSQL 当作可互换后端：

- `mysql_binlog` 始终是 MySQL 专属（依赖同步 `mysql-replication`）。
- `postgres_execution_source` / tracked execution 始终是 PostgreSQL 专属
  （依赖 PostgreSQL 事务/锁/lease 语义）。

这两项不会出现在另一后端的 namespace 中。

## Worker 镜像

`onestep-worker` 镜像已经包含 `onestep-sql`、`onestep-mysql`、`onestep-postgres`
三个包。`onestep[all]` 现在通过 `onestep-sql` 解析 MySQL/PostgreSQL 依赖，无需
修改 worker YAML 或挂载的 `requirements.txt`。

## 迁移检查清单

- [ ] 新部署使用 `pip install 'onestep-sql[mysql]'` / `'onestep-sql[postgres]'`。
- [ ] 新代码从 `onestep_sql.mysql` / `onestep_sql.postgres` 导入。
- [ ] 现有 `pip install onestep-mysql` / `onestep-postgres` 仍可安装。
- [ ] 现有 `from onestep_mysql import ...` / `from onestep_postgres import ...` 仍可导入。
- [ ] YAML 资源类型名、字段、默认值未变。
- [ ] `pip check` 通过，没有版本冲突。
- [ ] 同一环境安装新旧包时不会重复注册资源类型（由 `onestep-sql` 单一 entry point 保证）。

## 参考

- [设计文档](../superpowers/specs/2026-08-20-onestep-sql-consolidation-design.md)
- [MySQL 连接器](/broker/mysql)
- [PostgreSQL 连接器](/broker/postgres)
- [PostgreSQL Tracked Execution](/broker/postgres-execution)
- [CHANGELOG](https://github.com/mic1on/onestep/blob/main/CHANGELOG.md)
