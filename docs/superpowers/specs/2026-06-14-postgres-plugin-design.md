# PostgreSQL 插件设计

## 背景

onestep 已经通过 `onestep.resources` entry point 支持 repo-local 插件注册 YAML resource type。MySQL 插件也已经覆盖关系型数据库的常见 runtime 能力：连接资源、table queue、incremental source、table sink、SQLAlchemy state store 和 cursor store。

PostgreSQL 第一版沿用这个插件边界，目标是支持稳定的表级轮询和写入，不引入 logical replication CDC。CDC 涉及 replication slot、publication、LSN 确认、delete payload、schema 变更等独立语义，放到后续设计中处理。

## 目标

- 新增 repo-local 插件包 `plugins/onestep-postgres`。
- 插件包名为 `onestep-postgres`，Python import 包名为 `onestep_postgres`。
- 注册 YAML resource type：
  - `postgres`
  - `postgres_state_store`
  - `postgres_cursor_store`
  - `postgres_table_queue`
  - `postgres_incremental`
  - `postgres_table_sink`
- 支持 Python API 直接使用 connector、source、sink、state store 和 cursor store。
- 支持 YAML worker 通过安装插件后直接使用 PostgreSQL resource type。
- 提供 focused unit tests 和 PostgreSQL live integration tests。

## 非目标

- 不支持 logical replication、`pgoutput`、`wal2json` 或其它 CDC source。
- 不修改 onestep-web 拖拽配置、credential schema 或 debug/sample data 流程。
- 不改变 core `Source`、`Sink`、`Delivery`、`OneStepApp` API。
- 不改变 control-plane protocol、reporter payload 字段或 remote-control 行为。
- 不迁移或重构现有 MySQL 插件。

## 包结构

```text
plugins/onestep-postgres/
  pyproject.toml
  README.md
  src/onestep_postgres/
    __init__.py
    connector.py
    resources.py
    state_sqlalchemy.py
  tests/
    test_postgres_plugin.py
    test_postgres_table_queue.py
    test_postgres_incremental.py
    test_postgres_table_sink.py
    test_state_sqlalchemy.py
    integration/
      test_postgres_live.py
```

`connector.py` contains PostgreSQL runtime implementations. `resources.py` contains YAML build and strict validation handlers. `state_sqlalchemy.py` reuses the SQLAlchemy-backed state/cursor store shape from the MySQL plugin, with PostgreSQL-safe table creation and statements where needed.

`__init__.py` exports the public Python API and the resource entry point target:

```python
from .resources import register_resources as register
```

## Dependencies

The plugin depends on:

- `onestep>=1.4.2`
- `SQLAlchemy>=2.0.0`
- `psycopg[binary]>=3.2.0`

The root workspace adds `plugins/onestep-postgres` as a member and adds workspace source metadata for `onestep-postgres`. The core optional extras add:

```toml
postgres = [
    "onestep-postgres>=0.1.0",
]
```

The `all`, `dev`, and `integration` extras include the PostgreSQL plugin so repo-level development environments can run the plugin tests without manual package installation.

## YAML Resource Types

Example worker wiring:

```yaml
resources:
  pg:
    type: postgres
    dsn: "${POSTGRES_DSN}"

  cursor:
    type: postgres_cursor_store
    connector: pg

  users_source:
    type: postgres_incremental
    connector: pg
    table: users
    key: id
    cursor: [updated_at, id]
    state: cursor

  users_sink:
    type: postgres_table_sink
    connector: pg
    table: dw_users
    mode: upsert
    keys: [id]
```

`postgres` accepts `dsn`, optional `name`, and connection pool options already supported by the MySQL connector where they map cleanly to SQLAlchemy.

`postgres_state_store` and `postgres_cursor_store` accept `connector`, optional `table`, and optional `name`. Defaults should match the MySQL plugin naming style, using PostgreSQL-compatible DDL.

`postgres_incremental` accepts `connector`, `table`, `key`, `cursor`, optional `state`, optional `batch_size`, optional `where`, optional `columns`, and optional `name`.

`postgres_table_queue` accepts the same table queue fields as MySQL where the semantics are portable, including claim status fields, max attempts, visibility timeout, batch size, payload columns, and ordering. It uses PostgreSQL row locking for concurrency.

`postgres_table_sink` accepts `connector`, `table`, `mode`, optional `keys`, optional `columns`, optional `name`, and insert/upsert options. `mode` supports `insert` and `upsert`.

## Runtime Behavior

`PostgresConnector` wraps a SQLAlchemy engine. It should expose small helpers for creating connections and executing transaction-scoped operations, following the MySQL connector style.

`PostgresIncrementalSource` polls rows ordered by cursor fields. Cursor comparisons use tuple-style ordering semantics where PostgreSQL supports them, with deterministic tie-breaking through the configured `key` or final cursor component. The source persists cursor state only after the delivery is acknowledged.

`PostgresTableQueueSource` claims available rows in a transaction using:

```sql
SELECT ...
FOR UPDATE SKIP LOCKED
```

The claim updates status, attempt count, lock metadata, and visibility timestamp in the same transaction. `ack()`, `retry()`, and `fail()` update the claimed row according to the existing table queue contract.

`PostgresTableSink` inserts mapped payload rows. In `upsert` mode it uses:

```python
sqlalchemy.dialects.postgresql.insert(table).on_conflict_do_update(...)
```

`keys` are required for upsert mode and map to `index_elements`. Non-key columns are updated from `excluded`.

## Strict Validation

YAML handlers should reject unknown fields in strict mode and keep errors tied to the resource path, such as `resources.users_sink`.

Validation requirements:

- `postgres.dsn` must be a non-empty string.
- Source and sink resource references must resolve to a `PostgresConnector`.
- State and cursor store references must expose the expected store capability.
- `postgres_incremental.cursor` must be a non-empty string list.
- `postgres_table_sink.mode` must be `insert` or `upsert`.
- `postgres_table_sink.keys` is required and non-empty when `mode: upsert`.
- Numeric limits such as `batch_size`, `max_attempts`, and visibility timeout must be positive where applicable.

## Control Plane Impact

No control-plane coordination is required for the first version. The plugin only adds runtime resource types and does not change reporter payload fields, task lifecycle events, WebSocket protocol behavior, runtime identity, or remote task-control behavior.

Topology display can rely on existing generic source/sink descriptors. Friendly labels for `postgres_*` resource types can be added later as a web/control-plane enhancement if needed.

## Testing

Focused unit tests cover:

- entry point registration and YAML resource construction
- strict validation failures for missing and invalid fields
- incremental cursor query construction and cursor persistence timing
- table queue claim, ack, retry, fail, and skip-locked behavior shape
- table sink insert and PostgreSQL upsert statement behavior
- SQLAlchemy state and cursor store behavior

Integration tests use Docker PostgreSQL and are marked `integration`. They cover:

- connecting through a real PostgreSQL DSN
- creating state/cursor tables
- reading incremental rows across multiple polls
- claiming table queue rows concurrently without duplicate deliveries
- inserting and upserting sink rows into a real table

Default validation for development:

```bash
uv run --all-packages python -m pytest -q plugins/onestep-postgres/tests
```

Live validation when PostgreSQL infrastructure is available:

```bash
uv run --all-packages python -m pytest -q -m integration plugins/onestep-postgres/tests/integration
```

## Release Impact

The new plugin starts at version `0.1.0`. Adding the workspace member and optional extras requires updating `uv.lock`.

Publishing `onestep-postgres` is independent from core unless the release also changes core package behavior. If the core package is released at the same time, follow the existing onestep release rules for core version, changelog, lockfile, and tag.
