"""Shared single-transaction row completion for table-queue deliveries.

Issue #181: the public API of every table-queue delivery used to be two
independent transactions — ``update_current_row(values)`` writing the business
columns and ``ack()`` writing the ack columns. A crash between them left the
row updated but unacknowledged, which is one root of the at-least-once
window. ``complete(values)`` merges the two writes into ONE transaction
(``engine.begin()`` around both UPDATE statements), keeping the delivery
shape identical across the mysql, postgres and sqlite backends.

This module keeps exactly one copy of that machinery:

* :class:`TableQueueCompleteMixin` — the delivery-level ``complete(values)``
  API plus the ``_complete_ack_applied`` flag that lets the host backend's
  ``ack()`` skip the now-redundant second ack UPDATE (the executor still
  calls ``ack()`` after a successful handler; the flag is the delivery-local
  half of the issue's optional optimization and involves no executor
  changes — the skipped UPDATE was idempotent anyway);
* :func:`complete_table_queue_row` — the SQL: resolve the reflected table
  BEFORE opening the transaction, then write the business columns and the ack
  columns as two UPDATE statements inside a single ``engine.begin()`` block.

Deadlock note (issue #181): the backend connectors' ``_table()`` reflection
helper takes its own pooled connection while holding its asyncio cache lock
on a cache miss. Resolving the table object *before* ``engine.begin()`` —
the same ordering every backend's ``_update_row`` already uses — keeps the
transaction from ever nesting that second connection, and by the time a
delivery exists the table is cache-warm anyway because ``fetch()`` resolved
it before claiming.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

try:
    import sqlalchemy as sa
except ImportError:  # pragma: no cover - exercised when optional deps are missing
    sa = None


class TableQueueCompleteMixin:
    """Single-transaction ``complete(values)`` for table-queue deliveries.

    Hosts must provide ``self._source`` (a table-queue source exposing
    ``complete_row(row_ref, values)``), ``self._row_ref`` and the delivery
    ``self.envelope``. After a successful ``complete()`` the
    ``_complete_ack_applied`` flag is set so the host's ``ack()`` can return
    early instead of re-issuing the (idempotent) ack UPDATE.
    """

    _complete_ack_applied: bool = False

    async def complete(self, values: Mapping[str, Any]) -> None:
        """Write business columns and ack columns in one atomic transaction.

        The envelope body mirrors the business payload exactly like
        ``update_current_row`` does; ack columns are queue bookkeeping and
        never leak into the body.
        """
        payload = dict(values)
        await self._source.complete_row(self._row_ref, payload)  # type: ignore[attr-defined]
        self._complete_ack_applied = True
        body = self.envelope.body
        if isinstance(body, dict):
            body.update(payload)


async def complete_table_queue_row(
    *,
    connector: Any,
    row_ref: Any,
    values: Mapping[str, Any],
    ack: Mapping[str, Any],
) -> None:
    """Apply ``values`` then ``ack`` to the referenced row in ONE transaction.

    Both UPDATE statements run inside a single ``engine.begin()`` block, so a
    failure anywhere between them rolls the row back to its pre-call state.
    Empty mappings skip their own statement: an empty ``ack`` degenerates to
    the plain ``update_current_row`` behaviour, an empty ``values`` to a
    plain ack, and both empty is a no-op.
    """
    payload = dict(values)
    ack_payload = dict(ack)
    if not payload and not ack_payload:
        return
    # Resolve the reflected table BEFORE opening the transaction: a cache
    # miss inside engine.begin() would take a second pooled connection while
    # the transaction holds the first one.
    table = await connector._table(row_ref.table)
    key_column = table.c[row_ref.key]
    async with connector.engine.begin() as conn:
        if payload:
            await conn.execute(
                sa.update(table).where(key_column == row_ref.key_value).values(**payload)
            )
        if ack_payload:
            await conn.execute(
                sa.update(table).where(key_column == row_ref.key_value).values(**ack_payload)
            )
