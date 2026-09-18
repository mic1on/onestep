"""Shared table_queue YAML lint for the onestep-sql backends (issue #180).

Every table_queue backend applies ``claim`` while fetching, which moves rows
out of the ``where`` candidate set; every failure path (``fail_row`` /
``retry_row`` / ``release_row``) routes through
``update_row(row_ref, nack)``, which is a no-op for an empty mapping. With
``claim`` non-empty and ``nack`` empty or missing, failed rows therefore
stick in the claimed state forever — neither retried nor marked failed.

:func:`warn_empty_nack_with_claim` reports that configuration footgun as a
warning from the YAML resource ``build`` hook, so ``onestep check`` (both the
default and ``--strict`` load paths), the ``onestep build`` pre-build check
and ``onestep run`` startup all surface it. It never raises: the
configuration is valid, just risky. ``ack`` is intentionally not inspected
because empty ``ack`` has a deliberate design (updating the business columns
themselves is the completion marker).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

_LOGGER = logging.getLogger("onestep")


def warn_empty_nack_with_claim(
    *,
    field: str,
    claim: Mapping[str, Any],
    nack: Mapping[str, Any] | None,
) -> None:
    """Warn when ``claim`` is non-empty but ``nack`` is empty or missing.

    A missing ``nack`` is equivalent to an empty one: every backend's
    ``table_queue()`` factory coerces it with ``nack=dict(nack or {})``.
    """
    if claim and not (nack or {}):
        _LOGGER.warning(
            "%s: nack is empty while claim is set: failed rows will neither "
            "be retried nor marked failed; set nack fields (usually "
            "reverting the claim columns) if retry is intended.",
            field,
        )
