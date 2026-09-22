"""Batch-local uniqueness checks using the database's own index semantics.

The temporary table contains only input values: no target data, defaults,
sequences, triggers or foreign keys. It is not a concurrency/target-row check.
"""

from __future__ import annotations

import re
from uuid import uuid4

try:
    import sqlalchemy as sa
    from sqlalchemy.sql import visitors
except ImportError:  # optional SQLAlchemy dependency
    sa = None
    visitors = None


def unique_rules(table):
    """Do not collapse indexes by columns: predicates/NULL rules can differ."""
    rules = []
    if table.primary_key.columns:
        rules.append(
            ("PRIMARY KEY", table.primary_key, tuple(table.primary_key.columns.keys()))
        )
    for obj in [*table.constraints, *table.indexes]:
        if isinstance(obj, sa.UniqueConstraint) or (
            isinstance(obj, sa.Index) and obj.unique
        ):
            rules.append(
                (
                    f"unique index/constraint {obj.name!r}",
                    obj,
                    tuple(obj.columns.keys()),
                )
            )
    return rules


def predicate(obj, dialect):
    return (
        obj.dialect_options[dialect].get("where") if isinstance(obj, sa.Index) else None
    )


def copy_predicate(obj, dialect, table, shadow):
    where = predicate(obj, dialect)
    if where is None:
        return None
    return visitors.replacement_traverse(
        sa.text(where) if isinstance(where, str) else where,
        {},
        lambda node: (
            shadow.c.get(node.name)
            if isinstance(node, sa.Column) and node.table is table
            else None
        ),
    )


def nulls_not_distinct(obj):
    return bool(obj.dialect_options["postgresql"].get("nulls_not_distinct"))


def exact_conflicts(sink, rows, table):
    """Cheap rejection of exact duplicates; leave predicates to the database."""
    if len(rows) < 2:
        return
    for label, obj, columns in unique_rules(table):
        if not columns or not set(columns) <= rows[0].keys():
            continue
        if (
            predicate(obj, "sqlite") is not None
            or predicate(obj, "postgresql") is not None
        ):
            continue
        seen = set()
        for index, row in enumerate(rows):
            value = tuple(row[column] for column in columns)
            if any(item is None for item in value) and not nulls_not_distinct(obj):
                continue
            try:
                duplicate = value in seen
                seen.add(value)
            except TypeError:
                # A typed database check handles values whose Python equality
                # or hashing is not the SQL type's equality.
                continue
            if duplicate:
                if set(columns) == set(sink.keys):
                    message = f"batch item {index} duplicates earlier keys {value!r}"
                else:
                    message = f"batch item {index} collides with an earlier row on {label} ({', '.join(columns)})={value!r}"
                raise sink._batch_payload_error(
                    message
                    + "; batch upserts refuse order-dependent last-wins conflicts. Deduplicate upstream."
                )


async def validate_unique_rows(sink, conn, rows, table):
    if sink.mode != "upsert" or len(rows) < 2:
        return
    dialect = conn.dialect.name
    rules = [
        rule
        for rule in unique_rules(table)
        if rule[2] and set(rule[2]) <= rows[0].keys()
    ]
    if not rules:
        return
    # Integer keys with actual int values have identical equality in Python
    # and these SQL dialects. This common path needs neither DDL nor a query.
    if all(
        predicate(obj, dialect) is None
        and all(isinstance(table.c[col].type, sa.Integer) for col in cols)
        and all(
            row[col] is None or type(row[col]) is int for row in rows for col in cols
        )
        for _, obj, cols in rules
    ):
        return

    used = set().union(*(set(cols) for _, _, cols in rules))
    for _, obj, _ in rules:
        where = predicate(obj, dialect)
        if where is not None:
            # Reflection supplies SQL text, not a bound expression tree.
            # Include predicate inputs without copying defaults or sequences.
            referenced = {
                c.name
                for c in table.c
                if re.search(r"(?<!\w)" + re.escape(c.name) + r"(?!\w)", str(where))
            }
            missing = referenced - rows[0].keys()
            if missing:
                raise sink._batch_payload_error(
                    f"batch uniqueness predicate needs explicit input columns: {', '.join(sorted(missing))}"
                )
            used.update(referenced)
    shadow = sa.Table(
        "_onestep_batch_" + uuid4().hex,
        sa.MetaData(),
        *(sa.Column(c.name, c.type, nullable=True) for c in table.c if c.name in used),
        prefixes=["TEMPORARY"],
    )
    create, drop, transactional_ddl = await sink._batch_unique_ddl(
        conn, table, shadow, rules
    )

    async def execute_ddl(statement):
        if isinstance(statement, str):
            return await conn.exec_driver_sql(statement)
        return await conn.execute(statement)

    # The nested transaction also restores PostgreSQL after an integrity
    # error. MySQL temporary DDL is not rolled back, so always drop explicitly.
    created = False
    try:
        async with conn.begin_nested():
            for statement in create:
                await execute_ddl(statement)
                created = True
            insert = shadow.insert()
            for chunk in sink._batch_chunks(rows):
                # One executemany per chunk, not one execution per row: the
                # shadow table has no defaults, sequences, triggers or target
                # data, so both spellings insert the same values and let the
                # database decide conflicts, but the row-at-a-time form costs
                # one network round trip per row (a 2216-row batch spent 136s
                # on a 61ms link against 0.5s here; issue #228).
                #
                # Pass a list of parameter dicts rather than one statement with
                # inline ``VALUES``: MySQL's packet guard measures an
                # executemany one row at a time, but an inline multi-row
                # statement as a whole. The guard raises a PERMANENT error
                # instead of splitting, so the inline spelling would reject
                # batches whose real write path succeeds by splitting.
                await conn.execute(
                    insert, [{col: row[col] for col in used} for row in chunk]
                )
            await execute_ddl(drop)
            created = False
    except sa.exc.IntegrityError as exc:
        raise sink._batch_payload_error(
            "batch rows collide under the database's unique-index rules; deduplicate upstream"
        ) from exc
    finally:
        if created and not transactional_ddl:
            await execute_ddl(drop)


# All three connector classes support SQLite DSNs; share that fallback.
async def sqlite_unique_ddl(conn, table, shadow, rules):
    quote = conn.dialect.identifier_preparer.quote
    indexes = {}
    for info in (
        await conn.exec_driver_sql(f"PRAGMA index_list({quote(table.name)})")
    ).mappings():
        if info["unique"]:
            parts = list(
                (
                    await conn.exec_driver_sql(
                        f"PRAGMA index_xinfo({quote(info['name'])})"
                    )
                ).mappings()
            )
            indexes[info["name"]] = [(p["name"], p["coll"]) for p in parts if p["key"]]
    for number, (_, obj, columns) in enumerate(rules):
        parts = indexes.get(obj.name)
        if parts is None:
            parts = next(
                (
                    parts
                    for parts in indexes.values()
                    if tuple(p[0] for p in parts) == columns
                ),
                [],
            )
        collations = dict(parts)
        expressions = [
            shadow.c[col].collate(collations[col])
            if collations.get(col)
            else shadow.c[col]
            for col in columns
        ]
        sa.Index(
            f"{shadow.name}_{number}",
            *expressions,
            unique=True,
            sqlite_where=copy_predicate(obj, "sqlite", table, shadow),
        )
    create = [
        sa.schema.CreateTable(shadow),
        *(sa.schema.CreateIndex(index) for index in shadow.indexes),
    ]
    return create, sa.schema.DropTable(shadow, if_exists=True), True
