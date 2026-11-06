"""Lazy re-exports for the ``ops`` package.

The six names in ``__all__`` are resolved on first attribute access through the
module-level ``__getattr__`` (PEP 562), mirroring how
:mod:`onestep_control_plane_api.db.session` exposes ``async_engine`` /
``AsyncSessionLocal`` lazily.

Why lazy: eager re-exports made ``import onestep_control_plane_api.ops`` pull
in ``ops.readiness``, which (before #216) imported the background workers at
module level -- ``workers.notification_scanner`` imports
``api.notification_service`` and ``db.session``, and importing ``db.session``
creates the process-wide synchronous engine whose factory imports
``ops.observability`` mid-import. That closed the cycle

    ops/__init__ -> ops/readiness -> workers/notification_scanner
        -> api.notification_service / db.session -> ops.observability
        -> ops/__init__ (partially initialized)

and blew up with an ``ImportError`` whenever ``ops`` was imported first
(documented in the latency diagnostics runbook; #215 worked around it with an
import-time skip gate in ``db.session``, retired in #216 now that these lazy
exports break the cycle at its ``ops/__init__`` edge). With the lazy exports,
importing ``ops.observability`` (or any other ``ops`` submodule) no longer
loads ``readiness``, the workers, or the ``api`` package, and
``from onestep_control_plane_api.ops import build_readiness_report`` still
works -- ``__getattr__`` resolves the name on access.
"""

__all__ = [
    "BackgroundTaskReadinessState",
    "RetentionRunReport",
    "RetentionTableReport",
    "build_default_background_task_states",
    "build_readiness_report",
    "run_retention",
]

_READINESS_EXPORTS = (
    "BackgroundTaskReadinessState",
    "build_default_background_task_states",
    "build_readiness_report",
)
_RETENTION_EXPORTS = (
    "RetentionRunReport",
    "RetentionTableReport",
    "run_retention",
)


def __getattr__(name: str) -> object:
    if name in _READINESS_EXPORTS:
        from onestep_control_plane_api.ops import readiness

        return getattr(readiness, name)
    if name in _RETENTION_EXPORTS:
        from onestep_control_plane_api.ops import retention

        return getattr(retention, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
