"""Shared default incremental state-key derivation for the onestep-sql backends.

Phase 2 of the mysql/postgres consolidation (issue #133, design §3.1) keeps
exactly one copy of ``_default_incremental_state_key``, which previously
existed in parallel in both backend ``connector.py`` modules (see the retired
``scripts/check_plugin_drift.py``). The derived key feeds the at-least-once
cursor state, so its format is stable contract: changing it would orphan
existing persisted cursors.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence


def _default_incremental_state_key(
    *,
    table: str,
    cursor: Sequence[str],
    key: str,
    where: str | None,
) -> str:
    normalized_where = " ".join((where or "").split())
    if normalized_where:
        where_fragment = normalized_where
        if len(where_fragment) > 64:
            where_fragment = f"sha1:{hashlib.sha1(where_fragment.encode('utf-8')).hexdigest()}"
    else:
        where_fragment = "-"
    return f"{table}:{','.join(cursor)}:key={key}:where={where_fragment}"
