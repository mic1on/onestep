# onestep-mysql

MySQL connector plugin for `onestep`.

```bash
pip install onestep-mysql
```

The package registers these YAML resource types through the `onestep.resources`
entry point:

- `mysql`
- `mysql_state_store`
- `mysql_cursor_store`
- `mysql_table_queue`
- `mysql_incremental`
- `mysql_binlog`
- `mysql_table_sink`

Python usage:

```python
from onestep_mysql import MySQLConnector
```

SQLAlchemy database operations use `AsyncEngine` and async drivers. Existing
`mysql://` and `mysql+pymysql://` DSNs are accepted and are automatically
adapted to the `asyncmy` dialect. SQLite is supported in tests and local
development through `aiosqlite`. The binlog reader remains isolated behind a
thread boundary because `mysql-replication` is a synchronous library.

Production `mysql_incremental` sources should bind a durable
`mysql_cursor_store` with an explicit stable `state_key`. Retries redeliver the
same logical row with incremented attempts and pause later SQL reads across the
gap. Concurrent acknowledgements advance only a contiguous prefix and are
coalesced into cursor-store commit waves.
