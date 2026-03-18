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
    MemoryQueue,
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
        max_pending_events=100,
        max_pending_metric_batches=10,
        timeout_s=3.0,
        reconnect_base_delay_s=0.01,
        reconnect_max_delay_s=0.02,
    )


@dataclass
class FakeWsConnection:
    sent_messages: list[dict[str, object]] = field(default_factory=list)
    initial_messages: list[dict[str, object]] = field(default_factory=list)
    closed: bool = False
    _recv_queue: asyncio.Queue[object] | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        return None

    def _ensure_recv_queue(self) -> asyncio.Queue[object]:
        if self._recv_queue is None:
            self._recv_queue = asyncio.Queue()
            for message in self.initial_messages:
                self._recv_queue.put_nowait(json.dumps(message))
            self.initial_messages.clear()
        return self._recv_queue

    async def send(self, message: str) -> None:
        self.sent_messages.append(json.loads(message))

    async def recv(self) -> str:
        message = await self._ensure_recv_queue().get()
        if isinstance(message, Exception):
            raise message
        assert isinstance(message, str)
        return message

    async def close(self) -> None:
        self.closed = True

    def queue_message(self, message: dict[str, object]) -> None:
        self._ensure_recv_queue().put_nowait(json.dumps(message))

    def queue_error(self, error: Exception) -> None:
        self._ensure_recv_queue().put_nowait(error)


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


def test_ws_sender_advertises_restart_and_drain_only_after_binding_runtime() -> None:
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
                        "command.shutdown",
                        "command.restart",
                        "command.drain",
                        "command.pause_task",
                        "command.resume_task",
                        "command.sync_now",
                        "command.flush_metrics",
                        "command.flush_events",
                    ],
                    "server_time": "2026-03-17T12:00:00Z",
                },
            }
        ]
    )

    async def connect_factory(url: str, headers: dict[str, str], subprotocols: list[str]):
        return connection

    transport = ControlPlaneWsTransport(
        base_url="https://control-plane.example.com",
        token="secret-token",
        connect_factory=connect_factory,
    )
    sender = ControlPlaneWsSender(_make_config(), transport=transport)
    app = OneStepApp("billing-sync")
    reporter = ControlPlaneReporter(_make_config(), sender=sender)
    reporter.attach(app)

    async def scenario() -> None:
        await app.startup()
        await app.shutdown()

    asyncio.run(scenario())

    hello_message = connection.sent_messages[0]
    assert hello_message["type"] == "hello"
    capabilities = hello_message["payload"]["capabilities"]
    assert "command.restart" in capabilities
    assert "command.drain" in capabilities
    assert "command.pause_task" in capabilities
    assert "command.resume_task" in capabilities
    assert "command.discard_dead_letters" not in capabilities
    assert "command.replay_dead_letters" not in capabilities


def test_ws_sender_advertises_dead_letter_replay_only_for_compatible_tasks() -> None:
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
                        "command.discard_dead_letters",
                        "command.replay_dead_letters",
                    ],
                    "server_time": "2026-03-17T12:00:00Z",
                },
            }
        ]
    )

    async def connect_factory(url: str, headers: dict[str, str], subprotocols: list[str]):
        return connection

    transport = ControlPlaneWsTransport(
        base_url="https://control-plane.example.com",
        token="secret-token",
        connect_factory=connect_factory,
    )
    sender = ControlPlaneWsSender(_make_config(), transport=transport)
    app = OneStepApp("billing-sync")
    source = MemoryQueue("sync-users.incoming", batch_size=1, poll_interval_s=0.01)
    dead = MemoryQueue("sync-users.dead", batch_size=1, poll_interval_s=0.01)
    reporter = ControlPlaneReporter(_make_config(), sender=sender)
    reporter.attach(app)

    @app.task(name="sync_users", source=source, dead_letter=dead)
    async def sync_users(ctx, item):
        return item

    async def scenario() -> None:
        await app.startup()
        await app.shutdown()

    asyncio.run(scenario())

    hello_message = connection.sent_messages[0]
    capabilities = hello_message["payload"]["capabilities"]
    assert "command.discard_dead_letters" in capabilities
    assert "command.replay_dead_letters" in capabilities


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
    assert isinstance(result_message["payload"]["duration_ms"], int)


def test_ws_sender_executes_restart_command_and_reports_partial_completion() -> None:
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
                        "command.restart",
                    ],
                    "server_time": "2026-03-17T12:00:00Z",
                },
            }
        ]
    )

    async def connect_factory(url: str, headers: dict[str, str], subprotocols: list[str]):
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
                "message_id": "msg_command_restart",
                "sent_at": "2026-03-17T12:00:02Z",
                "payload": {
                    "command_id": "cmd_restart",
                    "kind": "restart",
                    "args": {},
                    "timeout_s": 10,
                    "created_at": "2026-03-17T12:00:02Z",
                },
            }
        )
        await asyncio.sleep(0.05)
        await app.shutdown()

    asyncio.run(scenario())

    ack_message = next(message for message in connection.sent_messages if message["type"] == "command_ack")
    result_message = next(
        message for message in connection.sent_messages if message["type"] == "command_result"
    )
    assert ack_message["payload"]["status"] == "accepted"
    assert result_message["payload"]["status"] == "succeeded"
    assert result_message["payload"]["result"] == {
        "operation": "restart",
        "requested": True,
        "completion": "partial",
        "restart_requested": True,
        "shutdown_requested": True,
        "supervisor_handoff_required": True,
    }


def test_ws_sender_executes_drain_command_and_reports_complete_when_idle() -> None:
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
                        "command.drain",
                    ],
                    "server_time": "2026-03-17T12:00:00Z",
                },
            }
        ]
    )

    async def connect_factory(url: str, headers: dict[str, str], subprotocols: list[str]):
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
                "message_id": "msg_command_drain",
                "sent_at": "2026-03-17T12:00:02Z",
                "payload": {
                    "command_id": "cmd_drain",
                    "kind": "drain",
                    "args": {},
                    "timeout_s": 10,
                    "created_at": "2026-03-17T12:00:02Z",
                },
            }
        )
        await asyncio.sleep(0.05)
        app.request_shutdown()
        await app.shutdown()

    asyncio.run(scenario())

    ack_message = next(message for message in connection.sent_messages if message["type"] == "command_ack")
    result_message = next(
        message for message in connection.sent_messages if message["type"] == "command_result"
    )
    assert ack_message["payload"]["status"] == "accepted"
    assert result_message["payload"]["status"] == "succeeded"
    assert result_message["payload"]["result"] == {
        "operation": "drain",
        "requested": True,
        "completion": "complete",
        "drained": True,
        "accepting_new_work": False,
        "runner_count": 0,
        "parked_runner_count": 0,
        "fetching_runner_count": 0,
        "inflight_task_count": 0,
    }


def test_ws_sender_executes_pause_task_and_resume_task_commands() -> None:
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
                        "command.pause_task",
                        "command.resume_task",
                    ],
                    "server_time": "2026-03-17T12:00:00Z",
                },
            }
        ]
    )

    async def connect_factory(url: str, headers: dict[str, str], subprotocols: list[str]):
        return connection

    transport = ControlPlaneWsTransport(
        base_url="https://control-plane.example.com",
        token="secret-token",
        connect_factory=connect_factory,
    )
    app = OneStepApp("billing-sync")
    source = MemoryQueue("sync-users.incoming", batch_size=1, poll_interval_s=0.01)
    sink = MemoryQueue("sync-users.processed", batch_size=1, poll_interval_s=0.01)
    started = asyncio.Event()
    release = asyncio.Event()
    sender = ControlPlaneWsSender(_make_config(), transport=transport)
    reporter = ControlPlaneReporter(_make_config(), sender=sender)
    reporter.attach(app)

    @app.task(name="sync_users", source=source, emit=sink, concurrency=1)
    async def sync_users(ctx, item):
        if item["value"] == 1:
            started.set()
            await release.wait()
        if item["value"] == 2:
            ctx.app.request_shutdown()
        return {"value": item["value"]}

    async def scenario() -> None:
        await source.publish({"value": 1})
        await source.publish({"value": 2})
        serve_task = asyncio.create_task(app.serve())
        await asyncio.wait_for(started.wait(), timeout=1.0)
        connection.queue_message(
            {
                "type": "command",
                "message_id": "msg_command_pause_task",
                "sent_at": "2026-03-17T12:00:02Z",
                "payload": {
                    "command_id": "cmd_pause_task",
                    "kind": "pause_task",
                    "args": {"task_name": "sync_users"},
                    "timeout_s": 30,
                    "created_at": "2026-03-17T12:00:02Z",
                },
            }
        )
        await asyncio.sleep(0.02)
        release.set()
        for _ in range(50):
            if any(
                message["type"] == "command_result"
                and message["payload"]["command_id"] == "cmd_pause_task"
                for message in connection.sent_messages
            ):
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("pause_task command_result was not sent")

        connection.queue_message(
            {
                "type": "command",
                "message_id": "msg_command_resume_task",
                "sent_at": "2026-03-17T12:00:03Z",
                "payload": {
                    "command_id": "cmd_resume_task",
                    "kind": "resume_task",
                    "args": {"task_name": "sync_users"},
                    "timeout_s": 30,
                    "created_at": "2026-03-17T12:00:03Z",
                },
            }
        )
        await asyncio.wait_for(serve_task, timeout=1.0)

    asyncio.run(scenario())

    pause_result = next(
        message
        for message in connection.sent_messages
        if message["type"] == "command_result"
        and message["payload"]["command_id"] == "cmd_pause_task"
    )
    resume_result = next(
        message
        for message in connection.sent_messages
        if message["type"] == "command_result"
        and message["payload"]["command_id"] == "cmd_resume_task"
    )
    assert pause_result["payload"]["status"] == "succeeded"
    assert pause_result["payload"]["result"] == {
        "operation": "pause_task",
        "task_name": "sync_users",
        "requested": True,
        "completion": "complete",
        "paused": True,
        "accepting_new_work": False,
        "runner_count": 1,
        "parked_runner_count": 1,
        "fetching_runner_count": 0,
        "inflight_task_count": 0,
    }
    assert resume_result["payload"]["status"] == "succeeded"
    assert resume_result["payload"]["result"]["operation"] == "resume_task"
    assert resume_result["payload"]["result"]["task_name"] == "sync_users"
    assert resume_result["payload"]["result"]["requested"] is True
    assert resume_result["payload"]["result"]["completion"] == "complete"
    assert resume_result["payload"]["result"]["paused"] is False
    assert resume_result["payload"]["result"]["accepting_new_work"] is True
    assert resume_result["payload"]["result"]["runner_count"] == 1
    assert resume_result["payload"]["result"]["parked_runner_count"] == 0


def test_ws_sender_executes_replay_dead_letters_command() -> None:
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
                        "command.replay_dead_letters",
                    ],
                    "server_time": "2026-03-17T12:00:00Z",
                },
            }
        ]
    )

    async def connect_factory(url: str, headers: dict[str, str], subprotocols: list[str]):
        return connection

    transport = ControlPlaneWsTransport(
        base_url="https://control-plane.example.com",
        token="secret-token",
        connect_factory=connect_factory,
    )
    app = OneStepApp("billing-sync")
    source = MemoryQueue("sync-users.incoming", batch_size=1, poll_interval_s=0.01)
    dead = MemoryQueue("sync-users.dead", batch_size=1, poll_interval_s=0.01)
    sender = ControlPlaneWsSender(_make_config(), transport=transport)
    reporter = ControlPlaneReporter(_make_config(), sender=sender)
    reporter.attach(app)
    replayed_batch: list[object] = []

    @app.task(name="sync_users", source=source, dead_letter=dead)
    async def sync_users(ctx, item):
        return item

    async def scenario() -> None:
        await app.startup()
        await dead.publish(
            {
                "payload": {"value": 1},
                "failure": {
                    "kind": "error",
                    "exception_type": "RuntimeError",
                    "message": "boom",
                    "traceback": "Traceback",
                },
            },
            meta={"original_meta": {"trace_id": "abc"}, "original_attempts": 2},
        )
        connection.queue_message(
            {
                "type": "command",
                "message_id": "msg_command_replay_dead_letters",
                "sent_at": "2026-03-17T12:00:02Z",
                "payload": {
                    "command_id": "cmd_replay_dead_letters",
                    "kind": "replay_dead_letters",
                    "args": {"task_name": "sync_users", "limit": 5},
                    "timeout_s": 30,
                    "created_at": "2026-03-17T12:00:02Z",
                },
            }
        )
        for _ in range(50):
            if any(
                message["type"] == "command_result"
                and message["payload"]["command_id"] == "cmd_replay_dead_letters"
                for message in connection.sent_messages
            ):
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("replay_dead_letters command_result was not sent")
        replayed_batch.extend(await source.fetch(1))
        await app.shutdown()

    asyncio.run(scenario())

    replay_result = next(
        message
        for message in connection.sent_messages
        if message["type"] == "command_result"
        and message["payload"]["command_id"] == "cmd_replay_dead_letters"
    )
    assert replay_result["payload"]["status"] == "succeeded"
    assert replay_result["payload"]["result"] == {
        "operation": "replay_dead_letters",
        "task_name": "sync_users",
        "requested": True,
        "completion": "complete",
        "requested_limit": 5,
        "attempted_count": 1,
        "replayed_count": 1,
        "failed_count": 0,
        "empty": False,
    }

    assert len(replayed_batch) == 1
    assert replayed_batch[0].payload == {"value": 1}
    assert replayed_batch[0].envelope.meta == {"trace_id": "abc"}
    assert replayed_batch[0].envelope.attempts == 2


def test_ws_sender_executes_discard_dead_letters_command() -> None:
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
                        "command.discard_dead_letters",
                    ],
                    "server_time": "2026-03-17T12:00:00Z",
                },
            }
        ]
    )

    async def connect_factory(url: str, headers: dict[str, str], subprotocols: list[str]):
        return connection

    transport = ControlPlaneWsTransport(
        base_url="https://control-plane.example.com",
        token="secret-token",
        connect_factory=connect_factory,
    )
    app = OneStepApp("billing-sync")
    source = MemoryQueue("sync-users.incoming", batch_size=1, poll_interval_s=0.01)
    dead = MemoryQueue("sync-users.dead", batch_size=1, poll_interval_s=0.01)
    sender = ControlPlaneWsSender(_make_config(), transport=transport)
    reporter = ControlPlaneReporter(_make_config(), sender=sender)
    reporter.attach(app)

    @app.task(name="sync_users", source=source, dead_letter=dead)
    async def sync_users(ctx, item):
        return item

    async def scenario() -> None:
        await app.startup()
        await dead.publish(
            {
                "payload": {"value": 1},
                "failure": {
                    "kind": "error",
                    "exception_type": "RuntimeError",
                    "message": "boom",
                    "traceback": "Traceback",
                },
            },
            meta={"original_meta": {"trace_id": "abc"}, "original_attempts": 2},
        )
        connection.queue_message(
            {
                "type": "command",
                "message_id": "msg_command_discard_dead_letters",
                "sent_at": "2026-03-17T12:00:02Z",
                "payload": {
                    "command_id": "cmd_discard_dead_letters",
                    "kind": "discard_dead_letters",
                    "args": {"task_name": "sync_users", "limit": 5},
                    "timeout_s": 30,
                    "created_at": "2026-03-17T12:00:02Z",
                },
            }
        )
        for _ in range(50):
            if any(
                message["type"] == "command_result"
                and message["payload"]["command_id"] == "cmd_discard_dead_letters"
                for message in connection.sent_messages
            ):
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("discard_dead_letters command_result was not sent")
        assert await dead.fetch(1) == []
        await app.shutdown()

    asyncio.run(scenario())

    discard_result = next(
        message
        for message in connection.sent_messages
        if message["type"] == "command_result"
        and message["payload"]["command_id"] == "cmd_discard_dead_letters"
    )
    assert discard_result["payload"]["status"] == "succeeded"
    assert discard_result["payload"]["result"] == {
        "operation": "discard_dead_letters",
        "task_name": "sync_users",
        "requested": True,
        "completion": "complete",
        "requested_limit": 5,
        "attempted_count": 1,
        "discarded_count": 1,
        "failed_count": 0,
        "empty": False,
    }


def test_ws_transport_reports_timeout_for_slow_command() -> None:
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
                    "accepted_capabilities": ["command.ping"],
                    "server_time": "2026-03-17T12:00:00Z",
                },
            }
        ]
    )

    class SlowHandler:
        def supports_command(self, kind: str) -> bool:
            return kind == "ping"

        async def handle_command(self, command) -> dict[str, object] | None:
            await asyncio.sleep(0.05)
            return {"ok": True}

    async def connect_factory(url: str, headers: dict[str, str], subprotocols: list[str]):
        assert url == "wss://control-plane.example.com/api/v1/agents/ws"
        assert headers == {"Authorization": "Bearer secret-token"}
        assert subprotocols == ["onestep-agent.v1"]
        return connection

    transport = ControlPlaneWsTransport(
        base_url="https://control-plane.example.com",
        token="secret-token",
        connect_factory=connect_factory,
        command_handler=SlowHandler(),
    )

    async def scenario() -> None:
        await transport.connect(
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
        connection.queue_message(
            {
                "type": "command",
                "message_id": "msg_command_timeout",
                "sent_at": "2026-03-17T12:00:02Z",
                "payload": {
                    "command_id": "cmd_timeout",
                    "kind": "ping",
                    "args": {},
                    "timeout_s": 0,
                    "created_at": "2026-03-17T12:00:02Z",
                },
            }
        )
        await asyncio.sleep(0.05)
        await transport.close()

    asyncio.run(scenario())

    ack_message = next(message for message in connection.sent_messages if message["type"] == "command_ack")
    result_message = next(
        message for message in connection.sent_messages if message["type"] == "command_result"
    )
    assert ack_message["payload"]["status"] == "accepted"
    assert result_message["payload"]["status"] == "timeout"
    assert result_message["payload"]["error_code"] == "command_execution_timeout"
    assert isinstance(result_message["payload"]["duration_ms"], int)


def test_ws_transport_marks_itself_disconnected_after_receive_failure() -> None:
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
                    "accepted_capabilities": ["telemetry.sync"],
                    "server_time": "2026-03-17T12:00:00Z",
                },
            }
        ]
    )

    async def connect_factory(url: str, headers: dict[str, str], subprotocols: list[str]):
        return connection

    transport = ControlPlaneWsTransport(
        base_url="https://control-plane.example.com",
        token="secret-token",
        connect_factory=connect_factory,
    )

    async def scenario() -> None:
        await transport.connect(
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
        connection.queue_error(RuntimeError("socket closed"))
        await asyncio.sleep(0.05)
        assert transport.connected is False
        assert transport.hello_ack is None
        await transport.close()

    asyncio.run(scenario())
    assert connection.closed is True


def test_ws_sender_reconnects_and_retries_current_payload_after_send_failure() -> None:
    @dataclass
    class FlakyTransport:
        connected: bool = False
        connect_calls: int = 0
        send_attempts: list[tuple[str, dict[str, object]]] = field(default_factory=list)

        async def connect(self, *, service: dict[str, object], runtime: dict[str, object]) -> None:
            self.connected = True
            self.connect_calls += 1

        async def send_telemetry(self, channel: str, body: dict[str, object]) -> None:
            self.send_attempts.append((channel, dict(body)))
            if len(self.send_attempts) == 1:
                self.connected = False
                raise RuntimeError("broken pipe")

        async def close(self) -> None:
            self.connected = False

    transport = FlakyTransport()
    sender = ControlPlaneWsSender(_make_config(), transport=transport)
    payload = {
        "service": {
            "name": "billing-sync",
            "environment": "prod",
            "node_name": "vm-prod-3",
            "instance_id": UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df"),
            "deployment_version": "1.0.0+c435c99",
        },
        "runtime": {
            "onestep_version": "1.0.0",
            "python_version": "3.12.3",
            "hostname": "host-a",
            "pid": 12345,
            "started_at": datetime(2026, 3, 17, 11, 58, tzinfo=timezone.utc),
        },
        "app": {"name": "billing-sync", "shutdown_timeout_s": 30.0, "tasks": []},
        "sent_at": datetime(2026, 3, 17, 12, 0, 0, tzinfo=timezone.utc),
        "sequence": 1,
    }

    async def scenario() -> None:
        await sender("sync", payload)
        for _ in range(50):
            if len(transport.send_attempts) >= 2:
                break
            await asyncio.sleep(0.01)
        await sender.close()

    asyncio.run(scenario())

    assert transport.connect_calls == 2
    assert [attempt[0] for attempt in transport.send_attempts] == ["sync", "sync"]


def test_ws_sender_startup_and_shutdown_do_not_block_when_connect_hangs() -> None:
    @dataclass
    class HangingTransport:
        connected: bool = False
        connect_calls: int = 0
        close_calls: int = 0
        _connect_gate: asyncio.Event = field(default_factory=asyncio.Event)

        async def connect(self, *, service: dict[str, object], runtime: dict[str, object]) -> None:
            self.connect_calls += 1
            await self._connect_gate.wait()
            self.connected = True

        async def send_telemetry(self, channel: str, body: dict[str, object]) -> None:
            raise AssertionError("send_telemetry should not be reached while connect is hanging")

        async def close(self) -> None:
            self.close_calls += 1
            self.connected = False

    config = _make_config()
    config.shutdown_flush_timeout_s = 0.01
    transport = HangingTransport()
    app = OneStepApp("billing-sync")
    sender = ControlPlaneWsSender(config, transport=transport)
    reporter = ControlPlaneReporter(config, sender=sender)
    reporter.attach(app)

    async def scenario() -> None:
        await asyncio.wait_for(app.startup(), timeout=0.1)
        await asyncio.wait_for(app.shutdown(), timeout=0.1)

    asyncio.run(scenario())

    assert transport.connect_calls >= 1
    assert transport.close_calls == 1
