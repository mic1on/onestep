from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from onestep import ControlPlaneReporterConfig

FIXED_INSTANCE_ID = UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df")


def make_config(**overrides: Any) -> ControlPlaneReporterConfig:
    kwargs: dict[str, Any] = {
        "base_url": "https://control-plane.example.com",
        "token": "secret-token",
        "environment": "prod",
        "service_name": "billing-sync",
        "node_name": "vm-prod-3",
        "deployment_version": "1.0.0+c435c99",
        "instance_id": FIXED_INSTANCE_ID,
        "heartbeat_interval_s": 3600.0,
        "metrics_interval_s": 3600.0,
        "event_flush_interval_s": 3600.0,
        "event_batch_size": 10,
        "max_pending_events": 100,
        "max_pending_metric_batches": 10,
        "timeout_s": 3.0,
        "reconnect_base_delay_s": 0.01,
        "reconnect_max_delay_s": 0.02,
    }
    kwargs.update(overrides)
    return ControlPlaneReporterConfig(**kwargs)


@dataclass
class SenderRecorder:
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def __call__(self, channel: str, payload: dict[str, Any]) -> None:
        self.calls.append((channel, payload))


@dataclass
class RecordingTransport:
    session_ids: list[str]
    connected: bool = False
    connect_calls: list[tuple[dict[str, Any], dict[str, Any]]] = field(default_factory=list)
    send_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    _session_index: int = 0
    _session_id: str | None = None

    @property
    def session_id(self) -> str | None:
        return self._session_id

    async def connect(self, *, service: dict[str, Any], runtime: dict[str, Any]) -> None:
        self.connected = True
        self.connect_calls.append((dict(service), dict(runtime)))
        if self._session_index < len(self.session_ids):
            self._session_id = self.session_ids[self._session_index]
        else:
            self._session_id = f"sess_{self._session_index + 1}"
        self._session_index += 1

    async def send_telemetry(self, channel: str, body: dict[str, Any]) -> None:
        self.send_calls.append((channel, dict(body)))

    async def close(self) -> None:
        self.connected = False
