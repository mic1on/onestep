"""Check the actual asyncmy text-protocol query before sending it."""

from __future__ import annotations

from contextlib import asynccontextmanager

import sqlalchemy as sa


class _PacketTooLarge(Exception):
    pass


@asynccontextmanager
async def packet_guard(sink, conn):
    limit = int(
        (await conn.exec_driver_sql("SELECT @@SESSION.max_allowed_packet")).scalar_one()
    )
    raw = await conn.get_raw_connection()
    driver = raw.driver_connection
    # asyncmy keeps the negotiated Python codec here (e.g. utf8 for utf8mb4).
    encoding = driver._encoding

    def check_packet(_conn, cursor, statement, parameters, _context, executemany):
        # This event runs AFTER SQLAlchemy's type processors (including JSON),
        # and mogrify uses this connection's escaping/NO_BACKSLASH_ESCAPES mode.
        if executemany:
            # asyncmy merges INSERT parameters after this event. Bound its
            # encoded SQL, not just the individual rows checked below. Reserve
            # one byte for COM_QUERY and stay strictly below MySQL's limit.
            cursor._cursor.max_stmt_length = min(
                cursor._cursor.max_stmt_length, limit - 2
            )
        for values in parameters if executemany else [parameters]:
            query = cursor._cursor.mogrify(statement, values)
            size = len(query.encode(encoding, "surrogateescape")) + 1  # COM_QUERY
            if size >= limit:
                raise _PacketTooLarge(
                    f"encoded query is {size} bytes; max_allowed_packet is {limit}"
                )

    sa.event.listen(conn.sync_connection, "before_cursor_execute", check_packet)
    try:
        yield
    except _PacketTooLarge as exc:
        raise sink._batch_payload_error(
            f"MySQL batch row does not fit max_allowed_packet ({exc}); reduce the row payload or increase the server limit"
        ) from exc
    finally:
        sa.event.remove(conn.sync_connection, "before_cursor_execute", check_packet)


async def execute_batch(sink, conn, rows, table, candidates):
    async def execute_chunk(chunk):
        statement, parameters = sink._build_batch_statements(chunk, table, candidates)[
            0
        ]
        try:
            result = (
                await conn.execute(statement)
                if parameters is None
                else await conn.execute(statement, parameters)
            )
            return result.rowcount or 0
        except _PacketTooLarge as exc:
            if len(chunk) == 1:
                raise sink._batch_payload_error(
                    f"MySQL batch contains a row that cannot fit in max_allowed_packet ({exc}); "
                    "reduce the row payload or increase the server limit"
                ) from exc
            middle = len(chunk) // 2
            return await execute_chunk(chunk[:middle]) + await execute_chunk(
                chunk[middle:]
            )

    matched = 0
    for chunk, _, _ in sink._batch_groups(rows, candidates):
        matched += await execute_chunk(chunk)
    return matched
