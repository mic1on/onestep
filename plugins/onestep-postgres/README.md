# onestep-postgres

PostgreSQL connector plugin for onestep.

Install it with:

```bash
pip install onestep-postgres
```

YAML resources are available after the plugin is installed:

```yaml
resources:
  pg:
    type: postgres
    dsn: "${POSTGRES_DSN}"

  cursor:
    type: postgres_cursor_store
    connector: pg

  users:
    type: postgres_incremental
    connector: pg
    table: users
    key: id
    cursor: [updated_at, id]
    state: cursor

  processed:
    type: postgres_table_sink
    connector: pg
    table: processed_users
    mode: upsert
    keys: [id]
```

The first version supports table queues, incremental polling, table sinks, and
SQLAlchemy-backed state/cursor stores. It does not support PostgreSQL logical
replication or CDC.
