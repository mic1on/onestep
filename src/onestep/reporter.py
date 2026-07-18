from __future__ import annotations

from typing import Any

from .reporter_registry import missing_control_plane_plugin_error

_EXPORTS = {"ControlPlaneReporter", "ControlPlaneReporterConfig"}


def _load_control_plane_attr(name: str) -> Any:
    try:
        from onestep_control_plane import ControlPlaneReporter, ControlPlaneReporterConfig
    except ImportError as exc:
        raise missing_control_plane_plugin_error() from exc
    exports = {
        "ControlPlaneReporter": ControlPlaneReporter,
        "ControlPlaneReporterConfig": ControlPlaneReporterConfig,
    }
    return exports[name]


def __getattr__(name: str) -> Any:
    if name in _EXPORTS:
        return _load_control_plane_attr(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = sorted(_EXPORTS)
