# onestep-sql

Canonical, unified MySQL **and** PostgreSQL connector plugin for
[onestep](https://github.com/mic1on/onestep).

This package merges the previously separate `onestep-mysql` and
`onestep-postgres` distributions behind a single entry point and a single
namespace (`onestep_sql`). It is introduced incrementally per
[the consolidation design](../../docs/superpowers/specs/2026-08-20-onestep-sql-consolidation-design.md)
(tracking issue #133).

## Status

**Phase 1 — canonical package without changing consumers.**

* `onestep-sql[mysql]`, `[postgres]`, and `[all]` build, discover their
  resources, and pass their suites.
* `onestep_sql.mysql` and `onestep_sql.postgres` carry the copied, unchanged
  implementations of the legacy packages.
* The legacy `onestep-mysql` / `onestep-postgres` distributions still exist and
  remain the recommended install until the later phases switch first-party
  consumers over (Phase 3) and update the docs (Phase 4).

## Install

```bash
pip install "onestep-sql[all]"      # MySQL + PostgreSQL
pip install "onestep-sql[mysql]"    # MySQL only
pip install "onestep-sql[postgres]" # PostgreSQL only
```

## Usage

The package registers all 14 YAML resource types through a single entry point
(`sql` in the `onestep.resources` group). No import is required to use the
types in a YAML pipeline — onestep discovers them automatically.

For programmatic access to the connector classes:

```python
from onestep_sql.mysql import MySQLConnector, BinlogSource
from onestep_sql.postgres import PostgresConnector, PostgresExecutionSource
```

## What is NOT changing

* The 14 YAML type names (`mysql_*`, `postgres_*`) and their catalog roles,
  fields, defaults, and connector boundaries are unchanged.
* `mysql_binlog` stays MySQL-only; `postgres_execution_source` / tracked
  execution stays PostgreSQL-only.
* See the design doc for the full non-goals and the phased rollout plan.
