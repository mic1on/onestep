"""Backend-agnostic validation shared by every tracked-execution source.

The option surface of a tracked-execution source (``namespace`` /
``task_names`` / ``batch_size`` / ``poll_interval_s`` / ``lease_duration_s`` /
``heartbeat_interval_s`` / ``worker_id``) is pure parameter validation with no
dialect semantics whatsoever: the same fields, the same order, the same
messages and the same boundaries apply to every backend. Phase 1 of the MySQL
tracked execution backend therefore lifted it out of the PostgreSQL source
module, and Phase 3's ``onestep_sql.mysql`` source consumes this same object.

Living here, rather than inside
:mod:`onestep_sql._shared.execution.source`, keeps the validator free of the
source layer's heavier imports (``onestep.connectors.base``,
``onestep.resilience``): the YAML resource-catalog validator imports it on the
resource-registration path, where those are not otherwise needed. It must
never import a concrete backend -- routing MySQL through
``onestep_sql.postgres`` would invert the dependency direction and break the
"no cross-backend dependency" rule (design §12.3).
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def _validate_execution_source_options(
    *,
    namespace: str,
    task_names: Sequence[str],
    batch_size: int,
    poll_interval_s: float,
    lease_duration_s: float,
    heartbeat_interval_s: float,
    worker_id: str,
    field_prefix: str = "",
) -> tuple[str, ...]:
    prefix = f"{field_prefix}." if field_prefix else ""
    if not isinstance(namespace, str) or not namespace.strip() or len(namespace.strip()) > 255:
        raise ValueError(f"{prefix}namespace must be non-empty and <= 255 characters")
    if not isinstance(task_names, Sequence) or isinstance(task_names, (str, bytes)):
        raise TypeError(f"{prefix}task_names must be a sequence of strings")
    normalized_tasks = tuple(
        task.strip() if isinstance(task, str) else task for task in task_names
    )
    if not normalized_tasks or any(
        not isinstance(task, str) or not task or len(task) > 255
        for task in normalized_tasks
    ):
        raise ValueError(f"{prefix}task_names must be non-empty strings <= 255 characters")
    if len(normalized_tasks) != 1:
        raise ValueError(f"{prefix}task_names must contain exactly one task name")
    if len(set(normalized_tasks)) != len(normalized_tasks):
        raise ValueError(f"{prefix}task_names must be unique")
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
        raise ValueError(f"{prefix}batch_size must be >= 1")
    for name, value in (
        ("poll_interval_s", poll_interval_s),
        ("lease_duration_s", lease_duration_s),
        ("heartbeat_interval_s", heartbeat_interval_s),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError(f"{prefix}{name} must be a finite number")
    if poll_interval_s <= 0:
        raise ValueError(f"{prefix}poll_interval_s must be > 0")
    if lease_duration_s <= 0:
        raise ValueError(f"{prefix}lease_duration_s must be > 0")
    if (
        heartbeat_interval_s <= 0
        or (
            heartbeat_interval_s > lease_duration_s / 3
            and not math.isclose(heartbeat_interval_s, lease_duration_s / 3)
        )
    ):
        raise ValueError(
            f"{prefix}heartbeat_interval_s must be > 0 and <= "
            f"{prefix}lease_duration_s / 3"
        )
    if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id.strip()) > 255:
        raise ValueError(f"{prefix}worker_id must be non-empty and <= 255 characters")
    return normalized_tasks


__all__ = ["_validate_execution_source_options"]
