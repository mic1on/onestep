from __future__ import annotations

from typing import Any

from .reporter_registry import missing_control_plane_plugin_error

_EXPORTS = {
    "AgentCommand",
    "AgentHelloAck",
    "AgentProtocolError",
    "ControlPlaneWsSender",
    "ControlPlaneWsTransport",
    "DEFAULT_AGENT_CAPABILITIES",
    "WS_AGENT_SUBPROTOCOL",
    "WS_PROTOCOL_VERSION",
    "build_control_plane_http_base_url",
    "build_control_plane_ws_url",
}


def _load_control_plane_attr(name: str) -> Any:
    try:
        import onestep_control_plane
    except ImportError as exc:
        raise missing_control_plane_plugin_error() from exc
    return getattr(onestep_control_plane, name)


def __getattr__(name: str) -> Any:
    if name in _EXPORTS:
        return _load_control_plane_attr(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = sorted(_EXPORTS)
