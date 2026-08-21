---
title: Migrate to onestep-sql
outline: deep
---

# Migrate to onestep-sql

`onestep-sql` is the canonical distribution package for MySQL and PostgreSQL
(tracking issue #133,
[design document](../../superpowers/specs/2026-08-20-onestep-sql-consolidation-design.md)).
It consolidates the previously separate `onestep-mysql` and `onestep-postgres`
packages into one package, one namespace (`onestep_sql`), and a single shared
SQL behaviour implementation.

This page explains how to migrate from the legacy packages to `onestep-sql`,
and how existing installs keep working under the new version.

## Do You Need To Migrate Immediately

No. The legacy `onestep-mysql` / `onestep-postgres` packages are still
published as thin forwarding shims, with a compatibility window of at least
"six months or two feature releases, whichever is later":

- `pip install onestep-mysql` still installs and automatically pulls in
  `onestep-sql[mysql,sqlite]`.
- Import paths such as `from onestep_mysql import MySQLConnector` remain
  object-identity compatible.
- All 14 YAML resource type names (`mysql_*`, `postgres_*`) are unchanged.
- The legacy shims no longer declare their own `onestep.resources` entry
  points; resource registration is handled uniformly by the single `sql`
  entry point of `onestep-sql`, so installing old and new packages together
  never double-registers.

New deployments and new documentation examples should use `onestep-sql`
directly.

## Installation

| Scenario | Legacy Command | New Command (Recommended) |
| --- | --- | --- |
| MySQL | `pip install onestep-mysql` | `pip install 'onestep-sql[mysql]'` |
| PostgreSQL | `pip install onestep-postgres` | `pip install 'onestep-sql[postgres]'` |
| Both | Install both legacy packages separately | `pip install 'onestep-sql[mysql,postgres]'` |
| Via core extra | `pip install 'onestep[mysql]'` | Unchanged (the extra now resolves to `onestep-sql`) |
| All connectors | `pip install 'onestep[all]'` | Unchanged |

The core extras `onestep[mysql]`, `onestep[postgres]`, `onestep[sql]`,
`onestep[all]`, `onestep[dev]`, and `onestep[integration]` now all resolve to
`onestep-sql`; no changes to `pip install 'onestep[...]'` lines in your
`pyproject.toml` are needed.

## Python Imports

New code should import from the canonical namespace:

```python
# MySQL
from onestep_sql.mysql import MySQLConnector, BinlogSource, TableSink

# PostgreSQL
from onestep_sql.postgres import PostgresConnector, PostgresExecutionSource
```

Legacy import paths remain compatible (the forwarding shims guarantee object
identity):

```python
# Still works, equivalent to the imports above
from onestep_mysql import MySQLConnector, BinlogSource, TableSink
from onestep_postgres import PostgresConnector, PostgresExecutionSource
```

## YAML Configuration

**No changes needed.** All YAML resource type names, fields, defaults,
catalog roles, and connector boundaries are unchanged. `onestep-sql`
registers all 14 types through its single `sql` entry point, which the YAML
loader discovers automatically.

```yaml
resources:
  db:
    type: mysql          # name unchanged
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

## Backend-Specific Capability Boundaries

The consolidation does not treat MySQL and PostgreSQL as interchangeable
backends:

- `mysql_binlog` always remains MySQL-only (depends on the synchronous
  `mysql-replication` library).
- `postgres_execution_source` / tracked execution always remains
  PostgreSQL-only (depends on PostgreSQL transaction/lock/lease semantics).

Neither appears in the other backend's namespace.

## Worker Image

The `onestep-worker` image already ships the `onestep-sql`, `onestep-mysql`,
and `onestep-postgres` packages. `onestep[all]` now resolves MySQL/PostgreSQL
dependencies through `onestep-sql`; no changes to worker YAML or mounted
`requirements.txt` files are needed.

## Migration Checklist

- [ ] New deployments use `pip install 'onestep-sql[mysql]'` / `'onestep-sql[postgres]'`.
- [ ] New code imports from `onestep_sql.mysql` / `onestep_sql.postgres`.
- [ ] Existing `pip install onestep-mysql` / `onestep-postgres` installs still work.
- [ ] Existing `from onestep_mysql import ...` / `from onestep_postgres import ...` imports still work.
- [ ] YAML resource type names, fields, and defaults are unchanged.
- [ ] `pip check` passes with no version conflicts.
- [ ] Installing old and new packages in the same environment does not double-register resource types (guaranteed by the single `onestep-sql` entry point).

## References

- [Design document](../../superpowers/specs/2026-08-20-onestep-sql-consolidation-design.md)
- [MySQL connector](/en/broker/mysql)
- [PostgreSQL connector](/en/broker/postgres)
- [PostgreSQL Tracked Execution](/en/broker/postgres-execution)
- [CHANGELOG](https://github.com/mic1on/onestep/blob/main/CHANGELOG.md)
