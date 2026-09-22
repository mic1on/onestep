"""MySQL temporary uniqueness table DDL (no implicit commit)."""

import sqlalchemy as sa


def unique_ddl(conn, table, shadow, rules):
    for option in ("charset", "collate"):
        value = table.dialect_options["mysql"].get(option)
        if value:
            shadow.dialect_options["mysql"][option] = value
    quote = conn.dialect.identifier_preparer.quote
    definitions = []
    for _, obj, columns in rules:
        lengths = (
            obj.dialect_options["mysql"].get("length")
            if isinstance(obj, sa.Index)
            else None
        )
        parts = []
        for column in columns:
            length = lengths.get(column) if isinstance(lengths, dict) else lengths
            parts.append(quote(column) + (f"({int(length)})" if length else ""))
        definitions.append(f"UNIQUE ({', '.join(parts)})")
    # CREATE INDEX on even a temporary table can implicitly commit. All
    # unique keys must be inline in CREATE TEMPORARY TABLE instead.
    ddl = str(sa.schema.CreateTable(shadow).compile(dialect=conn.dialect))
    closing = ddl.rfind(")")
    ddl = ddl[:closing] + ", " + ", ".join(definitions) + ddl[closing:]
    drop = "DROP TEMPORARY TABLE IF EXISTS " + quote(shadow.name)
    return [ddl], drop, False
