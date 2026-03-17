from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Protocol
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

from .reporter import ControlPlaneReporterConfig

if TYPE_CHECKING:
    from .app import OneStepApp
    from .reporter import ControlPlaneReporter

WS_AGENT_SUBPROTOCOL = "onestep-agent.v1"
WS_PROTOCOL_VERSION = "1"
DEFAULT_AGENT_CAPABILITIES = [
    "telemetry.sync",
    "telemetry.heartbeat",
    "telemetry.metrics",
    "telemetry.events",
    "command.ping",
    "command.shutdown",
    "command.sync_now",
    "command.flush_metrics",
    "command.flush_events",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _parse_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _normalize_control_plane_base_path(path: str) -> str:
    normalized = path.rstrip("/")
    if normalized.endswith("/api/v1/agents/ws"):
        return normalized[: -len("/api/v1/agents/ws")]
    return normalized


def build_control_plane_http_base_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    scheme_map = {
        "http": "http",
        "https": "https",
        "ws": "http",
        "wss": "https",
    }
    if parsed.scheme not in scheme_map:
        raise ValueError(f"unsupported control plane URL scheme: {parsed.scheme}")
    path = _normalize_control_plane_base_path(parsed.path)
    if path and not path.startswith("/"):
        path = f"/{path}"
    return urlunsplit((scheme_map[parsed.scheme], parsed.netloc, path, "", ""))


def build_control_plane_ws_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    scheme_map = {
        "http": "ws",
        "https": "wss",
        "ws": "ws",
        "wss": "wss",
    }
    if parsed.scheme not in scheme_map:
        raise ValueError(f"unsupported control plane URL scheme: {parsed.scheme}")
    prefix = _normalize_control_plane_base_path(parsed.path)
    if prefix:
        path = f"{prefix}/api/v1/agents/ws"
    else:
        path = "/api/v1/agents/ws"
    if not path.startswith("/"):
        path = f"/{path}"
    return urlunsplit((scheme_map[parsed.scheme], parsed.netloc, path, parsed.query, parsed.fragment))


def _next_message_id() -> str:
    return f"msg_{uuid4().hex}"


class AgentProtocolError(RuntimeError):
    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class AgentHelloAck:
    session_id: str
    protocol_version: str
    heartbeat_interval_s: int
    accepted_capabilities: list[str]
    server_time: datetime


@dataclass(frozen=True)
class AgentCommand:
    command_id: str
    kind: str
    args: dict[str, Any]
    timeout_s: int
    created_at: datetime


class AgentCommandHandler(Protocol):
    def supports_command(self, kind: str) -> bool: ...

    async def handle_command(self, command: AgentCommand) -> dict[str, Any] | None: ...


class WsConnection(Protocol):
    async def send(self, message: str) -> None: ...

    async def recv(self) -> str: ...

    async def close(self) -> None: ...


WsConnectFactory = Callable[[str, dict[str, str], list[str]], Awaitable[WsConnection]]


class _WebsocketsConnectionAdapter:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def send(self, message: str) -> None:
        await self._connection.send(message)

    async def recv(self) -> str:
        return await self._connection.recv()

    async def close(self) -> None:
        await self._connection.close()


async def _default_connect_factory(
    url: str,
    headers: dict[str, str],
    subprotocols: list[str],
) -> WsConnection:
    try:
        from websockets.asyncio.client import connect as ws_connect

        connection = await ws_connect(
            url,
            additional_headers=headers,
            subprotocols=subprotocols,
        )
    except ImportError:
        try:
            from websockets.client import connect as ws_connect
        except ImportError as exc:  # pragma: no cover - exercised via dependency error path
            raise RuntimeError(
                "websockets is required for WS control plane transport; install it before "
                "using ControlPlaneWsTransport"
            ) from exc
        connection = await ws_connect(
            url,
            extra_headers=headers,
            subprotocols=subprotocols,
        )
    return _WebsocketsConnectionAdapter(connection)


class ControlPlaneWsTransport:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        protocol_version: str = WS_PROTOCOL_VERSION,
        capabilities: list[str] | None = None,
        connect_factory: WsConnectFactory | None = None,
        command_handler: AgentCommandHandler | None = None,
    ) -> None:
        self._logger = logging.getLogger("onestep.control_plane.ws")
        self._ws_url = build_control_plane_ws_url(base_url)
        self._token = token.strip()
        if not self._token:
            raise ValueError("control plane token must not be empty")
        self._protocol_version = protocol_version
        self._capabilities = list(capabilities or DEFAULT_AGENT_CAPABILITIES)
        self._connect_factory = connect_factory or _default_connect_factory
        self._command_handler = command_handler
        self._connection: WsConnection | None = None
        self._hello_ack: AgentHelloAck | None = None
        self._receive_task: asyncio.Task[None] | None = None
        self._send_lock: asyncio.Lock | None = None
        self._command_receipts: dict[str, dict[str, dict[str, Any]]] = {}

    @property
    def connected(self) -> bool:
        return self._connection is not None

    @property
    def hello_ack(self) -> AgentHelloAck | None:
        return self._hello_ack

    def set_command_handler(self, handler: AgentCommandHandler | None) -> None:
        self._command_handler = handler

    async def connect(
        self,
        *,
        service: dict[str, Any],
        runtime: dict[str, Any],
    ) -> AgentHelloAck:
        if self._connection is not None and self._hello_ack is not None:
            return self._hello_ack

        self._logger.info("connecting to control plane WS", extra={"ws_url": self._ws_url})
        connection = await self._connect_factory(
            self._ws_url,
            {"Authorization": f"Bearer {self._token}"},
            [WS_AGENT_SUBPROTOCOL],
        )
        self._connection = connection
        try:
            hello_message = self._build_message(
                "hello",
                {
                    "protocol_version": self._protocol_version,
                    "capabilities": self._capabilities,
                    "service": service,
                    "runtime": runtime,
                },
            )
            await self._send_message(hello_message)
            server_response = json.loads(await connection.recv())
            if server_response.get("type") == "error":
                payload = server_response.get("payload", {})
                raise AgentProtocolError(
                    code=str(payload.get("code", "protocol_error")),
                    message=str(payload.get("message", "server rejected hello")),
                )
            if server_response.get("type") != "hello_ack":
                raise AgentProtocolError(
                    code="unexpected_server_message",
                    message=f"expected hello_ack, got {server_response.get('type')}",
                )

            payload = server_response.get("payload", {})
            self._hello_ack = AgentHelloAck(
                session_id=str(payload["session_id"]),
                protocol_version=str(payload["protocol_version"]),
                heartbeat_interval_s=int(payload["heartbeat_interval_s"]),
                accepted_capabilities=[str(value) for value in payload["accepted_capabilities"]],
                server_time=_parse_datetime(str(payload["server_time"])),
            )
            self._logger.info(
                "control plane WS connected",
                extra={
                    "ws_url": self._ws_url,
                    "session_id": self._hello_ack.session_id,
                },
            )
            self._receive_task = asyncio.create_task(
                self._receive_loop(),
                name="onestep-control-plane-ws-recv",
            )
            return self._hello_ack
        except Exception:
            await connection.close()
            self._connection = None
            self._hello_ack = None
            raise

    async def send_telemetry(self, channel: str, body: dict[str, Any]) -> None:
        if self._connection is None:
            raise RuntimeError("WS transport must connect before sending telemetry")
        await self._send_message(
            self._build_message(
                "telemetry",
                {
                    "channel": channel,
                    "body": body,
                },
            )
        )

    async def close(self) -> None:
        receive_task = self._receive_task
        self._receive_task = None
        if receive_task is not None:
            receive_task.cancel()
            if receive_task is not asyncio.current_task():
                try:
                    await receive_task
                except asyncio.CancelledError:
                    pass
        if self._connection is None:
            self._hello_ack = None
            return
        self._logger.info("closing control plane WS", extra={"ws_url": self._ws_url})
        await self._connection.close()
        self._connection = None
        self._hello_ack = None

    async def _receive_loop(self) -> None:
        connection = self._connection
        if connection is None:
            return
        while True:
            try:
                server_response = json.loads(await connection.recv())
            except asyncio.CancelledError:
                raise
            except Exception:
                return
            message_type = server_response.get("type")
            if message_type == "command":
                await self._handle_command_message(server_response)
            elif message_type == "error":
                payload = server_response.get("payload", {})
                if payload.get("close_connection"):
                    return

    async def _handle_command_message(self, message: dict[str, Any]) -> None:
        payload = message.get("payload", {})
        command = AgentCommand(
            command_id=str(payload["command_id"]),
            kind=str(payload["kind"]),
            args=dict(payload.get("args") or {}),
            timeout_s=int(payload["timeout_s"]),
            created_at=_parse_datetime(str(payload["created_at"])),
        )
        previous_receipts = self._command_receipts.get(command.command_id)
        if previous_receipts is not None:
            await self._send_message(previous_receipts["ack"])
            result_message = previous_receipts.get("result")
            if result_message is not None:
                await self._send_message(result_message)
            return

        handler = self._command_handler
        if handler is None or not handler.supports_command(command.kind):
            reject_message = self._build_command_ack_message(
                command.command_id,
                status="rejected",
                error_code="unsupported_command",
                error_message=f"command kind {command.kind} is not supported by this agent",
            )
            self._command_receipts[command.command_id] = {"ack": reject_message}
            await self._send_message(reject_message)
            return

        accept_message = self._build_command_ack_message(
            command.command_id,
            status="accepted",
        )
        self._command_receipts[command.command_id] = {"ack": accept_message}
        await self._send_message(accept_message)
        asyncio.create_task(
            self._execute_command(command, handler),
            name=f"onestep-control-plane-command-{command.kind}",
        )

    async def _execute_command(
        self,
        command: AgentCommand,
        handler: AgentCommandHandler,
    ) -> None:
        started_at = _utcnow()
        try:
            result = await handler.handle_command(command)
        except Exception as exc:
            finished_at = _utcnow()
            result_message = self._build_command_result_message(
                command.command_id,
                status="failed",
                finished_at=finished_at,
                error_code="command_execution_failed",
                error_message=str(exc),
                duration_ms=max(int((finished_at - started_at).total_seconds() * 1000), 0),
            )
        else:
            finished_at = _utcnow()
            result_message = self._build_command_result_message(
                command.command_id,
                status="succeeded",
                finished_at=finished_at,
                result=result or {"ok": True},
                duration_ms=max(int((finished_at - started_at).total_seconds() * 1000), 0),
            )
        self._command_receipts.setdefault(command.command_id, {})["result"] = result_message
        await self._send_message(result_message)

    async def _send_message(self, message: dict[str, Any]) -> None:
        if self._connection is None:
            raise RuntimeError("WS transport is not connected")
        if self._send_lock is None:
            self._send_lock = asyncio.Lock()
        async with self._send_lock:
            await self._connection.send(self._encode_message(message))

    def _build_command_ack_message(
        self,
        command_id: str,
        *,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "command_id": command_id,
            "status": status,
            "received_at": _utcnow(),
        }
        if error_code is not None:
            payload["error_code"] = error_code
        if error_message is not None:
            payload["error_message"] = error_message
        return self._build_message("command_ack", payload)

    def _build_command_result_message(
        self,
        command_id: str,
        *,
        status: str,
        finished_at: datetime,
        result: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "command_id": command_id,
            "status": status,
            "finished_at": finished_at,
        }
        if result is not None:
            payload["result"] = result
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        if error_code is not None:
            payload["error_code"] = error_code
        if error_message is not None:
            payload["error_message"] = error_message
        return self._build_message("command_result", payload)

    def _build_message(self, message_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": message_type,
            "message_id": _next_message_id(),
            "sent_at": _utcnow(),
            "payload": payload,
        }

    def _encode_message(self, message: dict[str, Any]) -> str:
        return json.dumps(message, default=_json_default)


def _endpoint_to_channel(endpoint: str) -> str:
    if endpoint.endswith("/sync"):
        return "sync"
    if endpoint.endswith("/heartbeat"):
        return "heartbeat"
    if endpoint.endswith("/metrics"):
        return "metrics"
    if endpoint.endswith("/events"):
        return "events"
    raise ValueError(f"unsupported control plane endpoint: {endpoint}")


class ControlPlaneWsSender:
    def __init__(
        self,
        config: ControlPlaneReporterConfig,
        *,
        capabilities: list[str] | None = None,
        transport: Any | None = None,
    ) -> None:
        self._transport = transport or ControlPlaneWsTransport(
            base_url=config.base_url,
            token=config.token,
            capabilities=capabilities,
        )
        self._pending_heartbeat: dict[str, Any] | None = None
        self._service_descriptor: dict[str, Any] | None = None
        self._runtime_descriptor: dict[str, Any] | None = None
        self._app: OneStepApp | None = None
        self._reporter: ControlPlaneReporter | None = None
        set_command_handler = getattr(self._transport, "set_command_handler", None)
        if callable(set_command_handler):
            set_command_handler(self)

    async def __call__(self, endpoint: str, payload: dict[str, Any]) -> None:
        channel = _endpoint_to_channel(endpoint)
        self._capture_identity(payload)

        if channel == "heartbeat" and not self._transport.connected:
            self._pending_heartbeat = payload
            return

        await self._ensure_connected()
        await self._transport.send_telemetry(channel, payload)

        if channel == "sync" and self._pending_heartbeat is not None:
            pending_heartbeat = self._pending_heartbeat
            self._pending_heartbeat = None
            await self._transport.send_telemetry("heartbeat", pending_heartbeat)

    async def close(self) -> None:
        close = getattr(self._transport, "close", None)
        if close is None:
            return
        await close()

    def bind_app(self, app: "OneStepApp") -> None:
        self._app = app

    def bind_reporter(self, reporter: "ControlPlaneReporter") -> None:
        self._reporter = reporter

    def supports_command(self, kind: str) -> bool:
        if kind == "ping":
            return True
        if kind == "shutdown":
            return self._app is not None
        if kind in {"sync_now", "flush_metrics", "flush_events"}:
            return self._reporter is not None
        return False

    async def handle_command(self, command: AgentCommand) -> dict[str, Any] | None:
        if command.kind == "ping":
            return {"ok": True, "echo": command.args}
        if command.kind == "shutdown":
            if self._app is None:
                raise RuntimeError("app is not bound")
            self._app.request_shutdown()
            return {"requested": True}
        if self._reporter is None:
            raise RuntimeError("reporter is not bound")
        if command.kind == "sync_now":
            await self._reporter.send_sync_now()
            return {"synced": True}
        if command.kind == "flush_metrics":
            await self._reporter.flush_metrics_now()
            return {"flushed_metrics": True}
        if command.kind == "flush_events":
            await self._reporter.flush_events_now()
            return {"flushed_events": True}
        raise RuntimeError(f"unsupported command kind: {command.kind}")

    def _capture_identity(self, payload: dict[str, Any]) -> None:
        service = payload.get("service")
        runtime = payload.get("runtime")
        if isinstance(service, dict):
            self._service_descriptor = dict(service)
        if isinstance(runtime, dict):
            self._runtime_descriptor = dict(runtime)

    async def _ensure_connected(self) -> None:
        if self._transport.connected:
            return
        if self._service_descriptor is None or self._runtime_descriptor is None:
            raise RuntimeError(
                "WS sender requires service and runtime descriptors before opening a session"
            )
        await self._transport.connect(
            service=self._service_descriptor,
            runtime=self._runtime_descriptor,
        )


__all__ = [
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
]
