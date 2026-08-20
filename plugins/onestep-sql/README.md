# onestep-sql

Canonical, unified MySQL **and** PostgreSQL connector plugin for
[onestep](https://github.com/mic1on/onestep).

This package merges the previously separate `onestep-mysql` and
`onestep-postgres` distributions behind a single entry point and a single
namespace (`onestep_sql`). It is introduced incrementally per
[the consolidation design](../../docs/superpowers/specs/2026-08-20-onestep-sql-consolidation-design.md)
(tracking issue #133).

## Status

**Phase 3 — canonical distribution; legacy packages are thin forwarding shims.**

* `onestep-sql[mysql]`, `[postgres]`, `[sqlite]`, and `[all]` build, discover
  their resources, and pass their suites.
* `onestep_sql.mysql` and `onestep_sql.postgres` carry the canonical
  implementations; shared SQL behaviour lives once in `onestep_sql._shared`.
* The root `onestep` extras (`mysql`, `postgres`, `sql`, `all`, `dev`,
  `integration`) resolve through `onestep-sql`.
* The legacy `onestep-mysql` / `onestep-postgres` distributions remain
  available as thin forwarding shims without their own resource entry points;
  existing `pip install onestep-mysql` and `from onestep_mysql import ...`
  imports keep working unchanged.
* All 14 YAML resource type names, catalog roles, fields, defaults, and
  connector boundaries are unchanged.

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
