"""Agent control WebSocket.

Database access convention
--------------------------
Every message is handled as one or more *short database work units*. Each work
unit opens its own :class:`AsyncSession` through :func:`session_scope`, commits
or rolls back, and closes before the handler awaits the next
``websocket.receive_text()``. Nothing is shared across the connection lifetime.

That is the point of this change. The previous implementation took a single
synchronous ``Session`` as a FastAPI dependency and held it for the whole
connection, so the read transaction opened by the hello path stayed open while
the handler blocked in ``receive_text()``: an idle agent held a checked-out
connection and an idle transaction indefinitely, and every slow query blocked
the event loop for every other connection. The ``hello`` path with an EMPTY
pending-command list was the worst case, because the early return left the read
transaction open with nothing to commit.

Plain data, not ORM instances
-----------------------------
Work units return dataclasses and scalars. An ORM instance whose attribute is
read after its transaction closed would trigger implicit IO or raise, so
service functions convert before the boundary.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, status
from pydantic import ValidationError

from onestep_control_plane_api.api.agent_command_service import (
    PendingCommandDelivery,
    apply_command_ack,
    apply_command_result,
    build_command_message_from_delivery,
    get_command_capability,
    list_redeliverable_commands_for_instance_async,
    mark_command_dispatched_async,
    reject_redelivery_for_unsupported_capability_async,
)
from onestep_control_plane_api.api.agent_connection_registry import agent_connection_registry
from onestep_control_plane_api.api.agent_ingestion_service import (
    ingest_events_request,
    ingest_heartbeat_request,
    ingest_metrics_request,
    ingest_sync_request,
)
from onestep_control_plane_api.api.agent_session_service import (
    OpenedAgentSession,
    close_agent_session,
    mark_session_message,
    open_agent_session,
)
from onestep_control_plane_api.api.common import as_utc
from onestep_control_plane_api.api.common import utcnow as _utcnow
from onestep_control_plane_api.api.schemas import (
    AgentCommandAckMessage,
    AgentCommandResultMessage,
    AgentErrorMessage,
    AgentErrorPayload,
    AgentHelloAckMessage,
    AgentHelloAckPayload,
    AgentHelloMessage,
    AgentTelemetryMessage,
    AgentWsEnvelope,
    EventsIngestRequest,
    HeartbeatIngestRequest,
    MetricsIngestRequest,
    SyncIngestRequest,
)
from onestep_control_plane_api.api.security import (
    WebSocketIngestAuth,
    require_async_websocket_ingest_token,
)
from onestep_control_plane_api.api.ui_event_stream import publish_ui_stream_event
from onestep_control_plane_api.db.session import session_scope

router = APIRouter(prefix="/api/v1/agents", tags=["agent-ws"])

SUPPORTED_PROTOCOL_VERSION = "1"
SUPPORTED_CAPABILITIES = frozenset(
    {
        "telemetry.sync",
        "telemetry.heartbeat",
        "telemetry.metrics",
        "telemetry.custom_metrics",
        "telemetry.events",
        "command.ping",
        "command.shutdown",
        "command.restart",
        "command.drain",
        "command.pause_task",
        "command.resume_task",
        "command.restart_task",
        "command.discard_dead_letters",
        "command.replay_dead_letters",
        "command.run_task_once",
        "command.sync_now",
        "command.flush_metrics",
        "command.flush_events",
    }
)


@dataclass
class _ConnectionContext:
    session_id: str
    instance_id: UUID


def _new_message_id() -> str:
    return f"msg_{uuid.uuid4().hex}"


def _new_session_id() -> str:
    return f"sess_{uuid.uuid4().hex}"


def _accepted_capabilities(capabilities: list[str]) -> list[str]:
    return [capability for capability in capabilities if capability in SUPPORTED_CAPABILITIES]


def _build_error_message(
    *,
    code: str,
    message: str,
    close_connection: bool,
) -> AgentErrorMessage:
    now = _utcnow()
    return AgentErrorMessage(
        type="error",
        message_id=_new_message_id(),
        sent_at=now,
        payload=AgentErrorPayload(
            code=code,
            message=message,
            close_connection=close_connection,
        ),
    )


async def _send_error(
    websocket: WebSocket,
    *,
    code: str,
    message: str,
    close_connection: bool,
) -> None:
    error_message = _build_error_message(
        code=code,
        message=message,
        close_connection=close_connection,
    )
    await websocket.send_json(error_message.model_dump(mode="json"))
    if close_connection:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=message)


async def _handle_hello(
    *,
    message: AgentHelloMessage,
    connected_at,
) -> tuple[OpenedAgentSession, list[PendingCommandDelivery], AgentHelloAckMessage]:
    """Register the hello AND list its redeliverable commands in ONE work unit.

    Why they share a work unit
    --------------------------
    Splitting them would change behaviour. The synchronous original held a
    single session with an open transaction from the hello insert through the
    pending-command listing, so a command created by a concurrent HTTP request
    in between was either fully invisible to the listing or fully visible, never
    half-observed. With two separate async work units, a command created in the
    gap is picked up by the listing AND immediately pushed by
    ``create_instance_command`` to the live connection — so the agent receives
    it twice.

    Doing both in one work unit keeps the original atomicity: the listing sees
    exactly the commands that existed when the session row was created.

    The work unit still ends before the caller sends the ack or waits for the
    next frame, so the idle-transaction leak the issue calls out is fixed — and
    it is fixed for the EMPTY pending list too, which is the case the old early
    return left dangling.
    """

    if message.payload.protocol_version != SUPPORTED_PROTOCOL_VERSION:
        raise ValueError(
            f"protocol_version={message.payload.protocol_version} is not supported"
        )

    accepted_capabilities = _accepted_capabilities(message.payload.capabilities)
    session_id = _new_session_id()

    with anyio.CancelScope(shield=True):
        async with session_scope() as session:
            opened = await open_agent_session(
                session,
                message=message,
                session_id=session_id,
                accepted_capabilities=accepted_capabilities,
                connected_at=connected_at,
            )
            pending = await list_redeliverable_commands_for_instance_async(
                session, instance_id=message.payload.service.instance_id
            )
    publish_ui_stream_event("sessions")

    hello_ack = AgentHelloAckMessage(
        type="hello_ack",
        message_id=_new_message_id(),
        sent_at=connected_at,
        payload=AgentHelloAckPayload(
            session_id=session_id,
            protocol_version=SUPPORTED_PROTOCOL_VERSION,
            heartbeat_interval_s=30,
            accepted_capabilities=accepted_capabilities,
            server_time=connected_at,
        ),
    )
    return opened, pending, hello_ack


async def _enqueue_pending_commands(
    *,
    pending: list[PendingCommandDelivery],
    session_id: str,
    accepted_capabilities: list[str],
    send_queue: asyncio.Queue[dict[str, object]],
) -> None:
    """Redeliver already-listed commands, one work unit per command.

    The listing itself happened in :func:`_handle_hello`; this only dispatches
    or rejects what it returned, which is why it never touches the listing
    query.

    Each dispatch or rejection is its own work unit, so a slow command never
    holds a transaction across the ``send_queue.put`` that follows.
    """

    for delivery in pending:
        required_capability = get_command_capability(delivery.kind)
        if required_capability not in accepted_capabilities:
            with anyio.CancelScope(shield=True):
                async with session_scope() as session:
                    await reject_redelivery_for_unsupported_capability_async(
                        session,
                        command_id=delivery.command_id,
                        capability=required_capability,
                    )
            publish_ui_stream_event("commands")
            continue

        with anyio.CancelScope(shield=True):
            async with session_scope() as session:
                dispatched = await mark_command_dispatched_async(
                    session,
                    command_id=delivery.command_id,
                    session_id=session_id,
                )
        publish_ui_stream_event("commands")
        await send_queue.put(
            build_command_message_from_delivery(dispatched).model_dump(mode="json")
        )


async def _send_loop(
    websocket: WebSocket,
    send_queue: asyncio.Queue[dict[str, object]],
) -> None:
    while True:
        message = await send_queue.get()
        await websocket.send_json(message)


@router.websocket("/ws")
async def agent_ws(
    websocket: WebSocket,
    auth: WebSocketIngestAuth = Depends(require_async_websocket_ingest_token),
) -> None:
    await websocket.accept(subprotocol=auth.accepted_subprotocol)
    send_queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    send_task = asyncio.create_task(_send_loop(websocket, send_queue))
    context: _ConnectionContext | None = None
    try:
        while True:
            raw_message = await websocket.receive_text()
            now = _utcnow()
            try:
                envelope = AgentWsEnvelope.model_validate_json(raw_message)
            except ValidationError:
                await _send_error(
                    websocket,
                    code="invalid_message",
                    message="incoming frame does not match the protocol envelope",
                    close_connection=True,
                )
                return

            if envelope.type == "hello":
                try:
                    hello = AgentHelloMessage.model_validate_json(raw_message)
                    _opened, pending_commands, hello_ack = await _handle_hello(
                        message=hello, connected_at=now
                    )
                except ValidationError:
                    await _send_error(
                        websocket,
                        code="invalid_message",
                        message="hello payload is invalid",
                        close_connection=True,
                    )
                    return
                except ValueError as exc:
                    await _send_error(
                        websocket,
                        code="unsupported_protocol_version",
                        message=str(exc),
                        close_connection=True,
                    )
                    return

                context = _ConnectionContext(
                    session_id=hello_ack.payload.session_id,
                    instance_id=hello.payload.service.instance_id,
                )
                await agent_connection_registry.register(
                    instance_id=context.instance_id,
                    session_id=context.session_id,
                    send_queue=send_queue,
                )
                await websocket.send_json(hello_ack.model_dump(mode="json"))
                await _enqueue_pending_commands(
                    pending=pending_commands,
                    session_id=context.session_id,
                    accepted_capabilities=hello_ack.payload.accepted_capabilities,
                    send_queue=send_queue,
                )
                continue

            if context is None:
                await _send_error(
                    websocket,
                    code="session_not_initialized",
                    message="hello must be sent before other message types",
                    close_connection=True,
                )
                return

            if envelope.type == "telemetry":
                try:
                    telemetry = AgentTelemetryMessage.model_validate_json(raw_message)
                except ValidationError:
                    await _send_error(
                        websocket,
                        code="invalid_message",
                        message="telemetry payload is invalid",
                        close_connection=False,
                    )
                    continue

                try:
                    if telemetry.payload.channel == "sync":
                        request = SyncIngestRequest.model_validate(telemetry.payload.body)
                        await _ingest(
                            ingest_sync_request, request, received_at=now
                        )
                    elif telemetry.payload.channel == "heartbeat":
                        request = HeartbeatIngestRequest.model_validate(telemetry.payload.body)
                        await _ingest(
                            ingest_heartbeat_request, request, received_at=now
                        )
                    elif telemetry.payload.channel == "metrics":
                        request = MetricsIngestRequest.model_validate(telemetry.payload.body)
                        await _ingest(
                            ingest_metrics_request, request, received_at=now
                        )
                    elif telemetry.payload.channel == "events":
                        request = EventsIngestRequest.model_validate(telemetry.payload.body)
                        await _ingest(
                            ingest_events_request, request, received_at=now
                        )
                    else:  # pragma: no cover - guarded by schema literal
                        await _send_error(
                            websocket,
                            code="invalid_telemetry_channel",
                            message=(
                                "telemetry channel "
                                f"{telemetry.payload.channel} is not supported"
                            ),
                            close_connection=False,
                        )
                        continue
                except ValidationError:
                    await _send_error(
                        websocket,
                        code="invalid_message",
                        message="telemetry body does not match the expected channel schema",
                        close_connection=False,
                    )
                    continue

                await _touch_session(context.session_id, now)
                continue

            if envelope.type == "command_ack":
                try:
                    command_ack = AgentCommandAckMessage.model_validate_json(raw_message)
                except ValidationError:
                    await _send_error(
                        websocket,
                        code="invalid_message",
                        message="command_ack payload is invalid",
                        close_connection=False,
                    )
                    continue
                outcome = await _run_command_ack(context, command_ack, received_at=now)
                if outcome == "unchanged":
                    continue
                if outcome == "unknown":
                    await _send_error(
                        websocket,
                        code="unknown_command",
                        message=f"command_id={command_ack.payload.command_id} was not found",
                        close_connection=False,
                    )
                    continue
                await _touch_session(context.session_id, now)
                continue

            if envelope.type == "command_result":
                try:
                    command_result = AgentCommandResultMessage.model_validate_json(raw_message)
                except ValidationError:
                    await _send_error(
                        websocket,
                        code="invalid_message",
                        message="command_result payload is invalid",
                        close_connection=False,
                    )
                    continue
                outcome = await _run_command_result(context, command_result, received_at=now)
                if outcome == "unknown":
                    await _send_error(
                        websocket,
                        code="unknown_command",
                        message=f"command_id={command_result.payload.command_id} was not found",
                        close_connection=False,
                    )
                    continue
                if outcome == "duplicate":
                    await _send_error(
                        websocket,
                        code="duplicate_command_result",
                        message=(
                            f"command_id={command_result.payload.command_id} already has "
                            "a terminal result"
                        ),
                        close_connection=False,
                    )
                    continue
                await _touch_session(context.session_id, now)
                continue

            await _send_error(
                websocket,
                code="invalid_message",
                message=f"message type {envelope.type} is not supported",
                close_connection=False,
            )
    except WebSocketDisconnect:
        return
    finally:
        send_task.cancel()
        try:
            await send_task
        except asyncio.CancelledError:
            pass
        if context is not None:
            # Shielded: unregistering and the disconnect work unit must complete
            # even when the enclosing task was already cancelled, or a
            # disconnected agent keeps an active session and a live registry
            # entry forever. See ``_close_connection_session``.
            with anyio.CancelScope(shield=True):
                await agent_connection_registry.unregister(
                    instance_id=context.instance_id,
                    session_id=context.session_id,
                )
                await _close_connection_session(context.session_id)
            publish_ui_stream_event("sessions")


async def _ingest(ingest_fn, request, *, received_at) -> None:
    """Run one telemetry ingest as its own short work unit.

    Shielded so the work unit cannot be cut in half: a cancellation that arrives
    while the insert is in flight would otherwise roll back a frame the agent
    has already been told (implicitly, by the next successful frame) was
    accepted. See :func:`_close_connection_session` for the full reasoning.
    """

    with anyio.CancelScope(shield=True):
        async with session_scope() as session:
            await ingest_fn(session, request, received_at=received_at)


async def _touch_session(session_id: str, occurred_at) -> None:
    """Record that a session delivered a message, as its own work unit."""

    with anyio.CancelScope(shield=True):
        async with session_scope() as session:
            await mark_session_message(session, session_id, occurred_at)


async def _close_connection_session(session_id: str) -> None:
    """Mark the session disconnected during cleanup, as its own work unit.

    Runs inside :func:`anyio.CancelScope` with ``shield=True``. The enclosing
    task is frequently already cancelled by the time cleanup runs — Starlette's
    test client cancels the websocket scope as soon as the ``with`` block exits,
    and a real disconnect or server shutdown can do the same. Without the
    shield, the first ``await`` inside the work unit would raise
    ``CancelledError`` and the session would stay ``active`` forever: a
    disconnect that is never recorded. Shielding lets this one work unit commit
    or roll back and release its connection, then the cancellation resumes.
    """

    with anyio.CancelScope(shield=True):
        async with session_scope() as session:
            await close_agent_session(session, session_id, disconnected_at=_utcnow())


async def _run_command_ack(
    context: _ConnectionContext,
    message: AgentCommandAckMessage,
    *,
    received_at,
) -> str:
    """Apply a ``command_ack`` as one work unit and return its outcome."""

    with anyio.CancelScope(shield=True):
        async with session_scope() as session:
            outcome = await apply_command_ack(
            session,
            instance_id=context.instance_id,
            session_id=context.session_id,
            command_id=message.payload.command_id,
            ack_status=message.payload.status,
            acked_at=as_utc(message.payload.received_at),
            received_at=received_at,
            error_code=message.payload.error_code,
            error_message=message.payload.error_message,
        )
    if outcome == "ok":
        publish_ui_stream_event("commands")
    return outcome


async def _run_command_result(
    context: _ConnectionContext,
    message: AgentCommandResultMessage,
    *,
    received_at,
) -> str:
    """Apply a ``command_result`` as one work unit and return its outcome."""

    with anyio.CancelScope(shield=True):
        async with session_scope() as session:
            outcome = await apply_command_result(
            session,
            instance_id=context.instance_id,
            session_id=context.session_id,
            command_id=message.payload.command_id,
            status_value=message.payload.status,
            finished_at=as_utc(message.payload.finished_at),
            result_json=message.payload.result,
            duration_ms=message.payload.duration_ms,
            received_at=received_at,
            error_code=message.payload.error_code,
            error_message=message.payload.error_message,
        )
    if outcome == "ok":
        publish_ui_stream_event("commands")
    return outcome
