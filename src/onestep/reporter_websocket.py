"""
WebSocket-based Control Plane Reporter.

Enables bidirectional communication between Worker and Control Plane:
- Worker initiates connection (no NAT traversal needed)
- Worker pushes: heartbeat, metrics, events, sync
- Control Plane pushes: commands (pause, resume, scale, etc.)

Usage:
    from onestep import OneStepApp, WebSocketReporter, WebSocketReporterConfig

    config = WebSocketReporterConfig(
        ws_url="wss://control-plane.example.com/ws/agents",
        token="your-token",
    )
    
    app = OneStepApp("my-service", reporter=WebSocketReporter(config))
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable
from uuid import UUID, uuid4

from .events import TaskEvent

if TYPE_CHECKING:
    from .app import OneStepApp

try:
    import websockets
    from websockets.asyncio.client import ClientConnection
except ImportError:
    websockets = None
    ClientConnection = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentCommand(str, Enum):
    """Commands that Control Plane can send to Worker."""
    PAUSE = "pause"              # Pause all tasks
    RESUME = "resume"            # Resume all tasks
    PAUSE_TASK = "pause_task"    # Pause specific task: {"task": "task_name"}
    RESUME_TASK = "resume_task"  # Resume specific task
    SCALE = "scale"              # Adjust concurrency: {"task": "name", "concurrency": 5}
    SHUTDOWN = "shutdown"        # Graceful shutdown
    SYNC = "sync"                # Request immediate sync
    PING = "ping"                # Health check


class AgentState(str, Enum):
    """Worker state reported to Control Plane."""
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"


@dataclass
class WebSocketReporterConfig:
    """Configuration for WebSocket reporter."""
    ws_url: str                      # WebSocket URL: wss://host/ws/agents
    token: str                       # Authentication token
    environment: str = "dev"
    service_name: str | None = None
    instance_id: UUID = field(default_factory=uuid4)
    
    # Timing
    reconnect_delay_s: float = 1.0     # Initial reconnect delay
    reconnect_backoff_factor: float = 2.0  # Exponential backoff
    max_reconnect_delay_s: float = 60.0
    heartbeat_interval_s: float = 30.0
    metrics_interval_s: float = 30.0
    
    # Connection
    ping_interval_s: float = 20.0
    ping_timeout_s: float = 10.0
    
    # Buffering
    event_batch_size: int = 100
    event_flush_interval_s: float = 5.0
    
    def __post_init__(self) -> None:
        self.ws_url = self.ws_url.strip().rstrip("/")
        if not self.ws_url.startswith(("ws://", "wss://")):
            raise ValueError("ws_url must start with ws:// or wss://")
        if not self.token:
            raise ValueError("token must not be empty")


@dataclass
class _OutboundMessage:
    """Message queued for sending to Control Plane."""
    type: str
    payload: dict[str, Any]
    sequence: int


class WebSocketReporter:
    """WebSocket-based reporter with bidirectional communication.
    
    Features:
    - Automatic reconnection with exponential backoff
    - Message queueing during disconnection
    - Command handling from Control Plane
    - Graceful degradation when connection fails
    """
    
    def __init__(self, config: WebSocketReporterConfig) -> None:
        if websockets is None:
            raise RuntimeError(
                "WebSocketReporter requires websockets. "
                "Install with: pip install 'onestep[websocket]'"
            )
        
        self.config = config
        self._logger = logging.getLogger(f"onestep.ws_reporter.{config.service_name}")
        
        # State
        self._app: OneStepApp | None = None
        self._state = AgentState.STARTING
        self._sequence = 0
        self._connected = False
        self._reconnect_delay = config.reconnect_delay_s
        
        # Connection
        self._ws: ClientConnection | None = None
        self._connection_task: asyncio.Task[None] | None = None
        self._receive_task: asyncio.Task[None] | None = None
        
        # Message queue
        self._outbound: asyncio.Queue[_OutboundMessage] = asyncio.Queue()
        self._pending_events: list[dict[str, Any]] = []
        
        # Background tasks
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._metrics_task: asyncio.Task[None] | None = None
        self._flush_task: asyncio.Task[None] | None = None
        
        # Command handlers
        self._command_handlers: dict[AgentCommand, Callable[[dict], None]] = {
            AgentCommand.PAUSE: self._handle_pause,
            AgentCommand.RESUME: self._handle_resume,
            AgentCommand.PAUSE_TASK: self._handle_pause_task,
            AgentCommand.RESUME_TASK: self._handle_resume_task,
            AgentCommand.SCALE: self._handle_scale,
            AgentCommand.SHUTDOWN: self._handle_shutdown,
            AgentCommand.SYNC: self._handle_sync,
            AgentCommand.PING: self._handle_ping,
        }
        
        # State change callbacks
        self._on_state_change: Callable[[AgentState], None] | None = None
    
    def attach(self, app: "OneStepApp") -> None:
        """Attach reporter to app (called during app initialization)."""
        self._app = app
        if self.config.service_name is None:
            self.config.service_name = app.name
    
    async def start(self) -> None:
        """Start the WebSocket connection and background tasks."""
        if self._connection_task is not None:
            return
        
        self._state = AgentState.STARTING
        self._connection_task = asyncio.create_task(self._connection_loop())
        
        # Start background tasks
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._metrics_task = asyncio.create_task(self._metrics_loop())
        self._flush_task = asyncio.create_task(self._flush_loop())
    
    async def stop(self) -> None:
        """Stop the reporter and close connection."""
        self._state = AgentState.STOPPING
        
        # Cancel background tasks
        for task in [self._heartbeat_task, self._metrics_task, self._flush_task]:
            if task is not None:
                task.cancel()
        
        # Close connection
        if self._ws is not None:
            await self._ws.close()
        
        if self._connection_task is not None:
            self._connection_task.cancel()
        
        self._state = AgentState.STOPPED
    
    async def emit_event(self, event: TaskEvent) -> None:
        """Queue an event for sending to Control Plane."""
        payload = self._build_event_payload(event)
        self._pending_events.append(payload)
        
        # Flush if batch is full
        if len(self._pending_events) >= self.config.event_batch_size:
            await self._flush_events()
    
    # ==================== Connection Management ====================
    
    async def _connection_loop(self) -> None:
        """Main connection loop with automatic reconnection."""
        while True:
            try:
                await self._connect()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.warning(
                    f"WebSocket connection failed: {e}. "
                    f"Reconnecting in {self._reconnect_delay:.1f}s"
                )
            
            # Exponential backoff
            await asyncio.sleep(self._reconnect_delay)
            self._reconnect_delay = min(
                self._reconnect_delay * self.config.reconnect_backoff_factor,
                self.config.max_reconnect_delay_s
            )
    
    async def _connect(self) -> None:
        """Establish WebSocket connection."""
        headers = {
            "Authorization": f"Bearer {self.config.token}",
            "X-Agent-ID": str(self.config.instance_id),
            "X-Service-Name": self.config.service_name or "unknown",
            "X-Environment": self.config.environment,
        }
        
        self._logger.info(f"Connecting to {self.config.ws_url}")
        
        async with websockets.connect(
            self.config.ws_url,
            additional_headers=headers,
            ping_interval=self.config.ping_interval_s,
            ping_timeout=self.config.ping_timeout_s,
        ) as ws:
            self._ws = ws
            self._connected = True
            self._reconnect_delay = self.config.reconnect_delay_s
            self._state = AgentState.RUNNING
            
            self._logger.info("WebSocket connected")
            
            # Send initial sync
            await self._send_sync()
            
            # Start receiving messages
            await self._receive_loop()
    
    async def _receive_loop(self) -> None:
        """Receive and process messages from Control Plane."""
        try:
            async for message in self._ws:
                try:
                    data = json.loads(message)
                    await self._handle_message(data)
                except json.JSONDecodeError:
                    self._logger.warning(f"Invalid JSON message: {message[:100]}")
                except Exception as e:
                    self._logger.exception(f"Error handling message: {e}")
        except websockets.ConnectionClosed:
            self._logger.warning("WebSocket connection closed")
        finally:
            self._connected = False
            self._ws = None
    
    async def _handle_message(self, data: dict[str, Any]) -> None:
        """Handle a message from Control Plane."""
        msg_type = data.get("type")
        
        if msg_type == "command":
            command_str = data.get("command")
            params = data.get("params", {})
            
            try:
                command = AgentCommand(command_str)
            except ValueError:
                self._logger.warning(f"Unknown command: {command_str}")
                return
            
            handler = self._command_handlers.get(command)
            if handler:
                self._logger.info(f"Received command: {command.value}")
                handler(params)
            else:
                self._logger.warning(f"No handler for command: {command.value}")
        
        elif msg_type == "ack":
            # Acknowledgment of our message
            self._logger.debug(f"Message acknowledged: seq={data.get('sequence')}")
        
        else:
            self._logger.debug(f"Unknown message type: {msg_type}")
    
    # ==================== Command Handlers ====================
    
    def _handle_pause(self, params: dict) -> None:
        """Pause all tasks."""
        if self._app:
            self._app.pause()
            self._state = AgentState.PAUSED
            self._logger.info("All tasks paused by control plane")
    
    def _handle_resume(self, params: dict) -> None:
        """Resume all tasks."""
        if self._app:
            self._app.resume()
            self._state = AgentState.RUNNING
            self._logger.info("All tasks resumed by control plane")
    
    def _handle_pause_task(self, params: dict) -> None:
        """Pause a specific task."""
        task_name = params.get("task")
        if self._app and task_name:
            # Find and pause the task
            for task in self._app.tasks:
                if task.name == task_name:
                    task.pause()
                    self._logger.info(f"Task {task_name} paused by control plane")
                    break
    
    def _handle_resume_task(self, params: dict) -> None:
        """Resume a specific task."""
        task_name = params.get("task")
        if self._app and task_name:
            for task in self._app.tasks:
                if task.name == task_name:
                    task.resume()
                    self._logger.info(f"Task {task_name} resumed by control plane")
                    break
    
    def _handle_scale(self, params: dict) -> None:
        """Adjust task concurrency."""
        task_name = params.get("task")
        concurrency = params.get("concurrency")
        if self._app and task_name and concurrency:
            for task in self._app.tasks:
                if task.name == task_name:
                    task.concurrency = concurrency
                    self._logger.info(
                        f"Task {task_name} scaled to concurrency={concurrency}"
                    )
                    break
    
    def _handle_shutdown(self, params: dict) -> None:
        """Initiate graceful shutdown."""
        if self._app:
            self._logger.info("Shutdown requested by control plane")
            asyncio.create_task(self._app.shutdown())
    
    def _handle_sync(self, params: dict) -> None:
        """Request immediate sync."""
        asyncio.create_task(self._send_sync())
    
    def _handle_ping(self, params: dict) -> None:
        """Respond to health check."""
        self._send_immediate("pong", {"state": self._state.value})
    
    # ==================== Message Sending ====================
    
    async def _send(self, msg_type: str, payload: dict[str, Any]) -> None:
        """Queue a message for sending."""
        self._sequence += 1
        msg = _OutboundMessage(
            type=msg_type,
            payload=payload,
            sequence=self._sequence,
        )
        await self._outbound.put(msg)
    
    def _send_immediate(self, msg_type: str, payload: dict[str, Any]) -> None:
        """Send a message immediately (non-blocking, may fail if disconnected)."""
        if self._connected and self._ws is not None:
            self._sequence += 1
            data = {
                "type": msg_type,
                "sequence": self._sequence,
                "timestamp": _utcnow().isoformat(),
                "payload": payload,
            }
            asyncio.create_task(self._ws.send(json.dumps(data)))
    
    async def _send_sync(self) -> None:
        """Send full sync to Control Plane."""
        if self._app is None:
            return
        
        payload = {
            "service": self._service_descriptor(),
            "state": self._state.value,
            "tasks": self._build_tasks_descriptor(),
        }
        await self._send("sync", payload)
    
    async def _send_heartbeat(self) -> None:
        """Send heartbeat to Control Plane."""
        payload = {
            "service": self._service_descriptor(),
            "state": self._state.value,
            "health": self._build_health_descriptor(),
        }
        await self._send("heartbeat", payload)
    
    async def _send_metrics(self) -> None:
        """Send metrics to Control Plane."""
        payload = {
            "service": self._service_descriptor(),
            "metrics": self._build_metrics_descriptor(),
        }
        await self._send("metrics", payload)
    
    async def _flush_events(self) -> None:
        """Flush pending events."""
        if not self._pending_events:
            return
        
        events = self._pending_events[:]
        self._pending_events.clear()
        
        payload = {
            "service": self._service_descriptor(),
            "events": events,
        }
        await self._send("events", payload)
    
    # ==================== Background Loops ====================
    
    async def _heartbeat_loop(self) -> None:
        """Periodic heartbeat."""
        while True:
            await asyncio.sleep(self.config.heartbeat_interval_s)
            try:
                await self._send_heartbeat()
            except Exception:
                pass  # Connection loop handles reconnection
    
    async def _metrics_loop(self) -> None:
        """Periodic metrics."""
        while True:
            await asyncio.sleep(self.config.metrics_interval_s)
            try:
                await self._send_metrics()
            except Exception:
                pass
    
    async def _flush_loop(self) -> None:
        """Periodic event flush."""
        while True:
            await asyncio.sleep(self.config.event_flush_interval_s)
            try:
                await self._flush_events()
            except Exception:
                pass
    
    # ==================== Descriptor Builders ====================
    
    def _service_descriptor(self) -> dict[str, Any]:
        return {
            "name": self.config.service_name,
            "instance_id": str(self.config.instance_id),
            "environment": self.config.environment,
        }
    
    def _build_tasks_descriptor(self) -> list[dict[str, Any]]:
        if self._app is None:
            return []
        
        tasks = []
        for task in self._app.tasks:
            tasks.append({
                "name": task.name,
                "source": task.source.name if task.source else None,
                "concurrency": task.concurrency,
                "timeout_s": task.timeout_s,
            })
        return tasks
    
    def _build_health_descriptor(self) -> dict[str, Any]:
        if self._app is None:
            return {"healthy": True}
        
        return {
            "healthy": not self._app.is_stopping,
            "stopping": self._app.is_stopping,
        }
    
    def _build_metrics_descriptor(self) -> dict[str, Any]:
        # TODO: Implement actual metrics collection
        return {}
    
    def _build_event_payload(self, event: TaskEvent) -> dict[str, Any]:
        return {
            "event_id": str(uuid4()),
            "kind": event.kind.value,
            "task_name": event.task,
            "occurred_at": event.emitted_at.isoformat(),
            "attempts": event.attempts,
            "duration_ms": int(event.duration_s * 1000) if event.duration_s else None,
            "meta": dict(event.meta),
        }
    
    # ==================== Outbound Processing ====================
    
    async def _process_outbound(self) -> None:
        """Process outbound message queue."""
        while True:
            msg = await self._outbound.get()
            
            if not self._connected or self._ws is None:
                continue  # Drop message if disconnected (could queue instead)
            
            try:
                data = {
                    "type": msg.type,
                    "sequence": msg.sequence,
                    "timestamp": _utcnow().isoformat(),
                    "payload": msg.payload,
                }
                await self._ws.send(json.dumps(data))
            except Exception as e:
                self._logger.warning(f"Failed to send message: {e}")


__all__ = ["WebSocketReporter", "WebSocketReporterConfig", "AgentCommand", "AgentState"]