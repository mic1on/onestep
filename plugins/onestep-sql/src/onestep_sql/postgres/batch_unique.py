"""PostgreSQL uniqueness table indexes, including predicates and NULL rules."""

import sqlalchemy as sa

from onestep_sql._shared.batch_unique import copy_predicate, nulls_not_distinct


def unique_ddl(conn, table, shadow, rules):
    for number, (_, obj, columns) in enumerate(rules):
        options = {"postgresql_where": copy_predicate(obj, "postgresql", table, shadow)}
        if nulls_not_distinct(obj):
            options["postgresql_nulls_not_distinct"] = True
        sa.Index(
            f"{shadow.name}_{number}",
            *(shadow.c[col] for col in columns),
            unique=True,
            **options,
        )
    create = [
        sa.schema.CreateTable(shadow),
        *(sa.schema.CreateIndex(index) for index in shadow.indexes),
    ]
    return create, sa.schema.DropTable(shadow, if_exists=True), True
