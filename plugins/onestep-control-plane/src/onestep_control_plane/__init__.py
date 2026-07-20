from __future__ import annotations

from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version as _package_version
from typing import Any

from onestep.reporter_registry import ReporterRegistry, ReporterSpecHandler

from .reporter import ControlPlaneReporter, ControlPlaneReporterConfig
from .ws import (
    AgentCommand,
    AgentHelloAck,
    AgentProtocolError,
    ControlPlaneWsSender,
    ControlPlaneWsTransport,
    DEFAULT_AGENT_CAPABILITIES,
    WS_AGENT_SUBPROTOCOL,
    WS_PROTOCOL_VERSION,
    build_control_plane_http_base_url,
    build_control_plane_ws_url,
)

try:
    __version__ = _package_version("onestep-control-plane")
except PackageNotFoundError:  # pragma: no cover - local source tree before install
    __version__ = "dev"

_CONTROL_PLANE_REPORTER_FIELDS = frozenset({"type", "base_url", "token", "service_name"})


def register(registry: ReporterRegistry) -> None:
    registry.register_reporter_type(
        ReporterSpecHandler(
            type="control_plane",
            allowed_fields=_CONTROL_PLANE_REPORTER_FIELDS,
            build=_build_control_plane_reporter,
        )
    )


def _build_control_plane_reporter(
    ctx: Any,
    spec: Mapping[str, Any],
) -> ControlPlaneReporter:
    config = ControlPlaneReporterConfig.from_env(
        app_name=ctx.app.name,
        base_url=ctx.optional_string(spec, "base_url"),
        token=ctx.optional_string(spec, "token"),
        service_name=ctx.optional_string(spec, "service_name"),
    )
    return ControlPlaneReporter(config)


__all__ = [
    "AgentCommand",
    "AgentHelloAck",
    "AgentProtocolError",
    "ControlPlaneReporter",
    "ControlPlaneReporterConfig",
    "ControlPlaneWsSender",
    "ControlPlaneWsTransport",
    "DEFAULT_AGENT_CAPABILITIES",
    "WS_AGENT_SUBPROTOCOL",
    "WS_PROTOCOL_VERSION",
    "__version__",
    "build_control_plane_http_base_url",
    "build_control_plane_ws_url",
    "register",
]
