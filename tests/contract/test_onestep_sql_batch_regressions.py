"""Behavior regressions for #221–#224; live dialects opt in via their DSNs."""

from __future__ import annotations

import os
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
import sqlalchemy as sa

from onestep.envelope import Envelope
from onestep.resilience import ConnectorErrorKind, ConnectorOperationError


@pytest_asyncio.fixture(params=["sqlite", "mysql", "postgres"])
async def database(request):
    backend = request.param
    if backend == "sqlite":
        from onestep_sql.sqlite import SQLiteConnector

        connector = SQLiteConnector(":memory:")
    else:
        dsn = os.getenv(f"ONESTEP_{backend.upper()}_DSN")
        if not dsn:
            pytest.skip(f"ONESTEP_{backend.upper()}_DSN not set")
        if backend == "mysql":
            from onestep_sql.mysql import MySQLConnector

            connector = MySQLConnector(dsn)
        else:
            from onestep_sql.postgres import PostgresConnector

            connector = PostgresConnector(dsn)
    metadata = sa.MetaData()
    try:
        yield backend, connector, metadata
    finally:
        async with connector.engine.begin() as conn:
            await conn.run_sync(metadata.drop_all)
        await connector.close()


def table_for(database, *columns, **kwargs):
    return sa.Table("batch_reg_" + uuid4().hex[:12], database[2], *columns, **kwargs)


async def setup(database, table, rows=()):
    async with database[1].engine.begin() as conn:
        await conn.run_sync(database[2].create_all)
        if rows:
            await conn.execute(table.insert(), list(rows))


async def contents(database, table):
    async with database[1].engine.connect() as conn:
        return [
            dict(row)
            for row in (
                await conn.execute(sa.select(table).order_by(table.c.id))
            ).mappings()
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["update", "upsert"])
async def test_skip_null_is_decided_before_native_json_binding(database, mode):
    table = table_for(
        database,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("data", sa.JSON),
        sa.Column("v", sa.String(80)),
    )
    await setup(database, table, [{"id": 1, "data": {"keep": 1}, "v": "old"}])
    sink = database[1].table_sink(
        table=table.name,
        mode=mode,
        keys=("id",),
        update_columns=({"name": "data", "policy": "skip_null"}, "v"),
    )
    await sink.send(Envelope(body=[{"id": 1, "data": None, "v": "new"}]))
    assert (await contents(database, table))[0] == {
        "id": 1,
        "data": {"keep": 1},
        "v": "new",
    }


@pytest.mark.asyncio
async def test_update_typed_values_null_keys_and_key_whitelist(database):
    table = table_for(
        database,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("k", sa.String(20)),
        sa.Column("data", sa.JSON),
        sa.Column("amount", sa.Numeric(10, 2)),
        sa.Column("_onestep_key_0", sa.String(20)),
    )
    await setup(
        database,
        table,
        [
            {
                "id": 1,
                "k": None,
                "data": {},
                "amount": Decimal("1.00"),
                "_onestep_key_0": "old",
            }
        ],
    )
    sink = database[1].table_sink(
        table=table.name,
        mode="update",
        keys=("id", "k"),
        update_columns=(
            "id",
            {"name": "data", "policy": "skip_null"},
            "amount",
            "_onestep_key_0",
        ),
    )
    await sink.send(
        Envelope(
            body=[
                {
                    "id": 1,
                    "k": None,
                    "data": {"new": [1]},
                    "amount": Decimal("2.25"),
                    "_onestep_key_0": "new",
                }
            ]
        )
    )
    row = (await contents(database, table))[0]
    assert row == {
        "id": 1,
        "k": None,
        "data": {"new": [1]},
        "amount": Decimal("2.25"),
        "_onestep_key_0": "new",
    }


@pytest.mark.asyncio
async def test_alternating_masks_preserve_repeated_key_order(database):
    table = table_for(
        database,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("v", sa.String(20)),
        sa.Column("note", sa.String(20)),
    )
    await setup(database, table, [{"id": 1, "v": "old", "note": "old"}])
    sink = database[1].table_sink(
        table=table.name,
        mode="update",
        keys=("id",),
        update_columns=({"name": "v", "policy": "skip_null"}, "note"),
    )
    await sink.send(
        Envelope(
            body=[
                {"id": 1, "v": "a", "note": "first"},
                {"id": 1, "v": None, "note": "second"},
                {"id": 1, "v": "b", "note": "third"},
            ]
        )
    )
    assert (await contents(database, table))[0] == {"id": 1, "v": "b", "note": "third"}


@pytest.mark.asyncio
async def test_unique_nulls_remain_distinct(database):
    table = table_for(
        database,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(80), unique=True),
    )
    await setup(database, table)
    sink = database[1].table_sink(
        table=table.name, mode="upsert", keys=("id",), batch_size=1
    )
    await sink.send(Envelope(body=[{"id": 1, "email": None}, {"id": 2, "email": None}]))
    assert len(await contents(database, table)) == 2


@pytest.mark.asyncio
async def test_partial_unique_index_evaluates_its_predicate(database):
    if database[0] == "mysql":
        pytest.skip("MySQL has no partial indexes")
    table = table_for(
        database,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(80)),
        sa.Column("active", sa.Boolean),
    )
    sa.Index(
        "uq_" + table.name,
        table.c.email,
        unique=True,
        sqlite_where=table.c.active.is_(True),
        postgresql_where=table.c.active.is_(True),
    )
    await setup(database, table)
    sink = database[1].table_sink(
        table=table.name, mode="upsert", keys=("id",), batch_size=1
    )
    await sink.send(
        Envelope(
            body=[
                {"id": 1, "email": "a", "active": False},
                {"id": 2, "email": "a", "active": False},
            ]
        )
    )
    with pytest.raises(ConnectorOperationError) as error:
        await sink.send(
            Envelope(
                body=[
                    {"id": 3, "email": "b", "active": True},
                    {"id": 4, "email": "b", "active": True},
                ]
            )
        )
    assert error.value.kind is ConnectorErrorKind.PERMANENT
    assert [r["id"] for r in await contents(database, table)] == [1, 2]


@pytest.mark.asyncio
async def test_collation_conflict_rejected_across_chunks(database):
    if database[0] == "postgres":
        pytest.skip("PostgreSQL NULL/predicate rules are covered separately")
    collation = "NOCASE" if database[0] == "sqlite" else "utf8mb4_0900_ai_ci"
    table = table_for(
        database,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(80, collation=collation), unique=True),
    )
    await setup(database, table)
    sink = database[1].table_sink(
        table=table.name, mode="upsert", keys=("id",), batch_size=1
    )
    with pytest.raises(ConnectorOperationError) as error:
        await sink.send(
            Envelope(body=[{"id": 1, "email": "Alice"}, {"id": 2, "email": "alice"}])
        )
    assert error.value.kind is ConnectorErrorKind.PERMANENT
    assert await contents(database, table) == []
    # A rejected validation must leave the pooled connection usable.
    await sink.send(
        Envelope(body=[{"id": 1, "email": "Alice"}, {"id": 2, "email": "Bob"}])
    )
    assert len(await contents(database, table)) == 2


@pytest.mark.asyncio
async def test_nulls_not_distinct_is_not_treated_as_ordinary_unique(database):
    if database[0] != "postgres":
        pytest.skip("PostgreSQL 15+ only")
    table = table_for(
        database,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(80)),
        sa.UniqueConstraint("email", postgresql_nulls_not_distinct=True),
    )
    await setup(database, table)
    sink = database[1].table_sink(table=table.name, mode="upsert", keys=("id",))
    with pytest.raises(ConnectorOperationError):
        await sink.send(
            Envelope(body=[{"id": 1, "email": None}, {"id": 2, "email": None}])
        )
    assert await contents(database, table) == []


@pytest.mark.asyncio
async def test_sqlite_default_only_batch_preserves_count(database):
    if database[0] != "sqlite":
        pytest.skip("SQLite DEFAULT VALUES regression")
    table = table_for(
        database,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("v", sa.Integer, server_default="7"),
    )
    await setup(database, table)
    sink = database[1].table_sink(table=table.name, mode="insert", batch_size=2)
    await sink.send(Envelope(body=[{}, {}, {}]))
    assert await contents(database, table) == [
        {"id": 1, "v": 7},
        {"id": 2, "v": 7},
        {"id": 3, "v": 7},
    ]


@pytest.mark.asyncio
async def test_later_chunk_failure_rolls_back_after_unique_validation(database):
    table = table_for(
        database,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(80), unique=True),
        sa.Column("v", sa.String(80), nullable=False),
    )
    await setup(database, table)
    sink = database[1].table_sink(
        table=table.name, mode="upsert", keys=("id",), batch_size=1
    )
    with pytest.raises(Exception):
        await sink.send(
            Envelope(
                body=[
                    {"id": 1, "email": "a", "v": "ok"},
                    {"id": 2, "email": "b", "v": None},
                ]
            )
        )
    assert await contents(database, table) == []


@pytest.mark.asyncio
async def test_unique_validation_costs_chunks_not_rows(database):
    """#228: the shadow-table preflight must not spend one round trip per row.

    The check runs in the database, so it cannot be bypassed — but filling the
    temporary table one row per execution made a 2216-row string-key batch wait
    2216 round trips (136s on a 61ms link). String keys never reach the integer
    fast path, so this is the common case for business-key upserts.

    Asserting on the statement count keeps the regression deterministic: it is
    independent of link latency and machine speed, unlike a wall-clock bound.
    """
    table = table_for(
        database,
        sa.Column("device_key", sa.String(64), primary_key=True),
        sa.Column("v", sa.Integer),
    )
    await setup(database, table)
    sink = database[1].table_sink(
        table=table.name, mode="upsert", keys=("device_key",), batch_size=1000
    )

    inserts: list[tuple[bool, str]] = []

    def _listen(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT"):
            inserts.append((executemany, statement))

    engine = database[1].engine
    sa.event.listen(engine.sync_engine, "before_cursor_execute", _listen)
    try:
        rows = [{"device_key": f"2408600{i:05d}", "v": i} for i in range(60)]
        await sink.send(Envelope(body=rows))
    finally:
        sa.event.remove(engine.sync_engine, "before_cursor_execute", _listen)

    shadow = [item for item in inserts if "_onestep_batch_" in item[1]]
    # One executemany fills the whole shadow table; the row-at-a-time form
    # would issue one INSERT per row and fail this assertion.
    assert len(shadow) == 1, f"{len(shadow)} shadow INSERTs for 60 rows"
    # The merged form must stay an executemany: an inline multi-row VALUES is
    # measured whole by MySQL's packet guard, which raises PERMANENT instead of
    # splitting, turning a slow batch into a rejected one.
    assert shadow[0][0], "the shadow insert must use executemany, not inline VALUES"
    async with engine.connect() as conn:
        written = (
            await conn.execute(sa.select(sa.func.count()).select_from(table))
        ).scalar_one()
    assert written == 60


@pytest.mark.asyncio
async def test_mysql_encoded_packet_split_and_single_row_rejection(database):
    if (
        database[0] != "mysql"
        or os.getenv("ONESTEP_TEST_ALLOW_GLOBAL_MYSQL_SETTINGS") != "1"
    ):
        pytest.skip("requires explicitly opted-in isolated MySQL server")
    connector = database[1]
    async with connector.engine.begin() as conn:
        original = (
            await conn.exec_driver_sql("SELECT @@GLOBAL.max_allowed_packet")
        ).scalar_one()
        await conn.exec_driver_sql("SET GLOBAL max_allowed_packet = 4194304")
    await connector.engine.dispose()  # session limit is fixed at connect time
    try:
        table = table_for(
            database,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("data", sa.JSON),
        )
        await setup(database, table)
        sink = connector.table_sink(table=table.name, mode="insert")
        await sink.send(
            Envelope(
                body=[{"id": i, "data": {"text": "中" * 200000}} for i in range(1, 5)]
            )
        )
        assert len(await contents(database, table)) == 4
        with pytest.raises(ConnectorOperationError) as error:
            await sink.send(
                Envelope(
                    body=[
                        {"id": 5, "data": {"text": "small"}},
                        {"id": 6, "data": {"text": "x" * 4195328}},
                    ]
                )
            )
        assert error.value.kind is ConnectorErrorKind.PERMANENT
        assert "max_allowed_packet" in str(error.value)
        assert len(await contents(database, table)) == 4  # earlier chunk rolled back
        await sink.send(Envelope(body=[{"id": 7, "data": {"text": "usable"}}]))
        assert len(await contents(database, table)) == 5
    finally:
        async with connector.engine.begin() as conn:
            await conn.exec_driver_sql(
                f"SET GLOBAL max_allowed_packet = {int(original)}"
            )
        await connector.engine.dispose()


@pytest.mark.asyncio
async def test_skip_null_does_not_fire_column_specific_trigger(database):
    if database[0] == "mysql":
        pytest.skip("MySQL has no UPDATE OF column triggers")
    table = table_for(
        database,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("v", sa.String(80)),
        sa.Column("note", sa.String(80)),
    )
    audit = table_for(database, sa.Column("id", sa.Integer, primary_key=True))
    await setup(database, table, [{"id": 1, "v": "keep", "note": "old"}])
    function = "fn_" + table.name
    async with database[1].engine.begin() as conn:
        if database[0] == "sqlite":
            await conn.exec_driver_sql(
                f"CREATE TRIGGER tr_{table.name} AFTER UPDATE OF v ON {table.name} BEGIN INSERT INTO {audit.name} VALUES (1); END"
            )
        else:
            await conn.exec_driver_sql(
                f"CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN INSERT INTO {audit.name} VALUES (1); RETURN NEW; END $$"
            )
            await conn.exec_driver_sql(
                f"CREATE TRIGGER tr_{table.name} AFTER UPDATE OF v ON {table.name} FOR EACH ROW EXECUTE FUNCTION {function}()"
            )
    try:
        for mode in ("update", "upsert"):
            sink = database[1].table_sink(
                table=table.name,
                mode=mode,
                keys=("id",),
                update_columns=({"name": "v", "policy": "skip_null"}, "note"),
            )
            await sink.send(Envelope(body=[{"id": 1, "v": None, "note": mode}]))
            assert (await contents(database, table))[0]["v"] == "keep"
            assert await contents(database, audit) == []
    finally:
        if database[0] == "postgres":
            async with database[1].engine.begin() as conn:
                await conn.exec_driver_sql(
                    f"DROP TRIGGER tr_{table.name} ON {table.name}"
                )
                await conn.exec_driver_sql(f"DROP FUNCTION {function}()")


@pytest.mark.asyncio
async def test_sqlite_collated_primary_key_is_checked(database):
    if database[0] != "sqlite":
        pytest.skip("SQLite reflected PK collation regression")
    table = table_for(
        database,
        sa.Column("id", sa.String(30, collation="NOCASE"), primary_key=True),
        sa.Column("v", sa.String(20)),
    )
    await setup(database, table)
    sink = database[1].table_sink(
        table=table.name, mode="upsert", keys=("id",), batch_size=1
    )
    with pytest.raises(ConnectorOperationError):
        await sink.send(
            Envelope(body=[{"id": "A", "v": "one"}, {"id": "a", "v": "two"}])
        )
    assert await contents(database, table) == []


@pytest.mark.asyncio
async def test_mysql_table_default_collation_and_prefix_index(database):
    if database[0] != "mysql":
        pytest.skip("MySQL table collation and index prefix")
    table = table_for(
        database,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(80)),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    sa.Index("uq_" + table.name, table.c.email, unique=True, mysql_length=3)
    await setup(database, table)
    sink = database[1].table_sink(
        table=table.name, mode="upsert", keys=("id",), batch_size=1
    )
    with pytest.raises(ConnectorOperationError):
        await sink.send(
            Envelope(
                body=[{"id": 1, "email": "ABC-one"}, {"id": 2, "email": "abc-two"}]
            )
        )
    assert await contents(database, table) == []


@pytest.mark.asyncio
async def test_postgres_nondeterministic_collation(database):
    if database[0] != "postgres":
        pytest.skip("PostgreSQL ICU collation")
    connector = database[1]
    name = "coll_" + uuid4().hex[:12]
    async with connector.engine.begin() as conn:
        icu = (
            await conn.exec_driver_sql(
                "SELECT EXISTS(SELECT 1 FROM pg_collation WHERE collprovider = 'i')"
            )
        ).scalar_one()
        if not icu:
            pytest.skip("server built without ICU")
        await conn.exec_driver_sql(
            f"CREATE COLLATION {name} (provider = icu, locale = 'und-u-ks-level2', deterministic = false)"
        )
    table = table_for(
        database,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(80, collation=name), unique=True),
    )
    try:
        await setup(database, table)
        sink = connector.table_sink(
            table=table.name, mode="upsert", keys=("id",), batch_size=1
        )
        with pytest.raises(ConnectorOperationError):
            await sink.send(
                Envelope(
                    body=[{"id": 1, "email": "Alice"}, {"id": 2, "email": "alice"}]
                )
            )
        assert await contents(database, table) == []
    finally:
        async with connector.engine.begin() as conn:
            await conn.run_sync(lambda sync: table.drop(sync, checkfirst=True))
            await conn.exec_driver_sql(f"DROP COLLATION {name}")
