from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from onestep import (
    ControlPlaneReporter,
    ControlPlaneReporterConfig,
    ControlPlaneWsSender,
    ControlPlaneWsTransport,
    OneStepApp,
    build_control_plane_http_base_url,
    build_control_plane_ws_url,
)


def _make_config() -> ControlPlaneReporterConfig:
    return ControlPlaneReporterConfig(
        base_url="https://control-plane.example.com",
        token="secret-token",
        environment="prod",
        service_name="billing-sync",
        node_name="vm-prod-3",
        deployment_version="1.0.0+c435c99",
        instance_id=UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df"),
        heartbeat_interval_s=3600.0,
        metrics_interval_s=3600.0,
        event_flush_interval_s=3600.0,
        event_batch_size=10,
        timeout_s=3.0,
    )


@dataclass
class FakeWsConnection:
    sent_messages: list[dict[str, object]] = field(default_factory=list)
    initial_messages: list[dict[str, object]] = field(default_factory=list)
    closed: bool = False
    _recv_queue: asyncio.Queue[str] | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        return None

    def _ensure_recv_queue(self) -> asyncio.Queue[str]:
        if self._recv_queue is None:
            self._recv_queue = asyncio.Queue()
            for message in self.initial_messages:
                self._recv_queue.put_nowait(json.dumps(message))
            self.initial_messages.clear()
        return self._recv_queue

    async def send(self, message: str) -> None:
        self.sent_messages.append(json.loads(message))

    async def recv(self) -> str:
        return await self._ensure_recv_queue().get()

    async def close(self) -> None:
        self.closed = True

    def queue_message(self, message: dict[str, object]) -> None:
        self._ensure_recv_queue().put_nowait(json.dumps(message))


def test_build_control_plane_urls_handle_prefixed_ws_endpoints() -> None:
    assert (
        build_control_plane_ws_url("https://control-plane.example.com/control")
        == "wss://control-plane.example.com/control/api/v1/agents/ws"
    )
    assert (
        build_control_plane_ws_url("wss://control-plane.example.com/control/api/v1/agents/ws")
        == "wss://control-plane.example.com/control/api/v1/agents/ws"
    )
    assert (
        build_control_plane_http_base_url("wss://control-plane.example.com/control/api/v1/agents/ws")
        == "https://control-plane.example.com/control"
    )


def test_ws_transport_connect_sends_hello_and_parses_hello_ack() -> None:
    connection = FakeWsConnection(
        initial_messages=[
            {
                "type": "hello_ack",
                "message_id": "msg_server_1",
                "sent_at": "2026-03-17T12:00:00Z",
                "payload": {
                    "session_id": "sess_server_1",
                    "protocol_version": "1",
                    "heartbeat_interval_s": 30,
                    "accepted_capabilities": [
                        "telemetry.sync",
                        "telemetry.heartbeat",
                    ],
                    "server_time": "2026-03-17T12:00:00Z",
                },
            }
        ]
    )
    connect_calls: list[tuple[str, dict[str, str], list[str]]] = []

    async def connect_factory(url: str, headers: dict[str, str], subprotocols: list[str]):
        connect_calls.append((url, headers, subprotocols))
        return connection

    transport = ControlPlaneWsTransport(
        base_url="https://control-plane.example.com",
        token="secret-token",
        connect_factory=connect_factory,
    )

    async def scenario():
        hello_ack = await transport.connect(
            service={
                "name": "billing-sync",
                "environment": "prod",
                "node_name": "vm-prod-3",
                "instance_id": UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df"),
                "deployment_version": "1.0.0+c435c99",
            },
            runtime={
                "onestep_version": "1.0.0",
                "python_version": "3.12.3",
                "hostname": "host-a",
                "pid": 12345,
                "started_at": datetime(2026, 3, 17, 11, 58, tzinfo=timezone.utc),
            },
        )
        await transport.close()
        return hello_ack

    hello_ack = asyncio.run(scenario())

    assert connect_calls == [
        (
            "wss://control-plane.example.com/api/v1/agents/ws",
            {"Authorization": "Bearer secret-token"},
            ["onestep-agent.v1"],
        )
    ]
    assert connection.sent_messages[0]["type"] == "hello"
    assert connection.sent_messages[0]["payload"]["protocol_version"] == "1"
    assert connection.sent_messages[0]["payload"]["service"]["name"] == "billing-sync"
    assert hello_ack.session_id == "sess_server_1"
    assert hello_ack.protocol_version == "1"
    assert hello_ack.heartbeat_interval_s == 30
    assert connection.closed is True


@dataclass
class FakeTransport:
    connected: bool = False
    calls: list[tuple[str, object, object | None]] = field(default_factory=list)

    async def connect(self, *, service: dict[str, object], runtime: dict[str, object]) -> None:
        self.connected = True
        self.calls.append(("connect", dict(service), dict(runtime)))

    async def send_telemetry(self, channel: str, body: dict[str, object]) -> None:
        self.calls.append(("telemetry", channel, body))

    async def close(self) -> None:
        self.connected = False
        self.calls.append(("close", None, None))


def test_ws_sender_buffers_initial_heartbeat_until_sync() -> None:
    fake_transport = FakeTransport()
    app = OneStepApp("billing-sync")
    sender = ControlPlaneWsSender(_make_config(), transport=fake_transport)
    reporter = ControlPlaneReporter(_make_config(), sender=sender)
    reporter.attach(app)

    async def scenario() -> None:
        await app.startup()
        await app.shutdown()

    asyncio.run(scenario())

    assert fake_transport.calls[0][0] == "connect"
    assert fake_transport.calls[1][:2] == ("telemetry", "sync")
    assert fake_transport.calls[2][:2] == ("telemetry", "heartbeat")
    assert fake_transport.calls[-1][0] == "close"

    sync_body = fake_transport.calls[1][2]
    heartbeat_body = fake_transport.calls[2][2]
    assert isinstance(sync_body, dict)
    assert isinstance(heartbeat_body, dict)
    assert sync_body["app"]["name"] == "billing-sync"
    assert heartbeat_body["health"]["status"] == "ok"


def test_ws_sender_executes_sync_now_command_over_active_session() -> None:
    connection = FakeWsConnection(
        initial_messages=[
            {
                "type": "hello_ack",
                "message_id": "msg_server_1",
                "sent_at": "2026-03-17T12:00:00Z",
                "payload": {
                    "session_id": "sess_server_1",
                    "protocol_version": "1",
                    "heartbeat_interval_s": 30,
                    "accepted_capabilities": [
                        "telemetry.sync",
                        "telemetry.heartbeat",
                        "command.sync_now",
                    ],
                    "server_time": "2026-03-17T12:00:00Z",
                },
            }
        ]
    )

    async def connect_factory(url: str, headers: dict[str, str], subprotocols: list[str]):
        assert url == "wss://control-plane.example.com/api/v1/agents/ws"
        assert headers == {"Authorization": "Bearer secret-token"}
        assert subprotocols == ["onestep-agent.v1"]
        return connection

    transport = ControlPlaneWsTransport(
        base_url="https://control-plane.example.com",
        token="secret-token",
        connect_factory=connect_factory,
    )
    app = OneStepApp("billing-sync")
    sender = ControlPlaneWsSender(_make_config(), transport=transport)
    reporter = ControlPlaneReporter(_make_config(), sender=sender)
    reporter.attach(app)

    async def scenario() -> None:
        await app.startup()
        connection.queue_message(
            {
                "type": "command",
                "message_id": "msg_command_1",
                "sent_at": "2026-03-17T12:00:02Z",
                "payload": {
                    "command_id": "cmd_1",
                    "kind": "sync_now",
                    "args": {},
                    "timeout_s": 10,
                    "created_at": "2026-03-17T12:00:02Z",
                },
            }
        )
        await asyncio.sleep(0.05)
        await app.shutdown()

    asyncio.run(scenario())

    message_types = [message["type"] for message in connection.sent_messages]
    assert message_types[0] == "hello"
    assert message_types.count("command_ack") == 1
    assert message_types.count("command_result") == 1
    telemetry_messages = [message for message in connection.sent_messages if message["type"] == "telemetry"]
    assert [message["payload"]["channel"] for message in telemetry_messages].count("sync") >= 2
    ack_message = next(message for message in connection.sent_messages if message["type"] == "command_ack")
    result_message = next(
        message for message in connection.sent_messages if message["type"] == "command_result"
    )
    assert ack_message["payload"]["status"] == "accepted"
    assert result_message["payload"]["status"] == "succeeded"
    assert result_message["payload"]["result"] == {"synced": True}
