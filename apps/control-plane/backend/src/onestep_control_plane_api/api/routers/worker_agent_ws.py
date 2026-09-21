"""Worker-agent control WebSocket.

Database access convention
--------------------------
Every message is handled as one or more *short database work units*, exactly
like the agent WebSocket (issue #192). Each work unit opens its own
:class:`~sqlalchemy.ext.asyncio.AsyncSession` through
:func:`~onestep_control_plane_api.db.session.session_scope`, commits or rolls
back, and closes before the handler awaits the next
``websocket.receive_text()``. Nothing is shared across the connection lifetime.

The previous implementation took a synchronous ``Session`` as a FastAPI
dependency and held it for the whole connection, calling synchronous service
functions inside the async message loop: every slow query blocked the event
loop, and an idle worker held a checked-out connection and an open transaction
for as long as the socket stayed open. Both properties are fixed here.

Plain data, not ORM instances
-----------------------------
The pending-command listing returns dataclasses
(:class:`PendingWorkerCommandDelivery`); the outbound ``command`` frame is
built from them after the transaction has closed, never from ORM attributes.

Transaction ownership
---------------------
The ``*_async`` service functions are each one whole work unit: they do not
commit on their own — the enclosing ``session_scope`` does (#206 lesson). The
synchronous bodies they share with legacy callers run through
``AsyncSession.run_sync`` inside the caller's transaction.

Cancellation
------------
Work units run inside :func:`anyio.CancelScope` with ``shield=True`` so a
cancellation cannot cut a commit in half, and the disconnect cleanup in the
``finally`` block is shielded too: a cancelled handler must still mark its
session disconnected and unregister, or a dropped worker stays ``active`` and
registered forever.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, status
from pydantic import ValidationError

from onestep_control_plane_api.api.common import utcnow
from onestep_control_plane_api.api.schemas import (
    AgentErrorPayload,
    WorkerAgentCommandAckMessage,
    WorkerAgentCommandResultMessage,
    WorkerAgentErrorMessage,
    WorkerAgentHeartbeatMessage,
    WorkerAgentHelloMessage,
    WorkerAgentWsEnvelope,
    WorkerDeploymentEventMessage,
)
from onestep_control_plane_api.api.security import require_websocket_worker_agent_connection
from onestep_control_plane_api.api.worker_agent_connection_registry import (
    worker_agent_connection_registry,
)
from onestep_control_plane_api.api.worker_agent_service import (
    PendingWorkerCommandDelivery,
    apply_worker_agent_heartbeat_async,
    apply_worker_deployment_event_async,
    build_worker_agent_command_message_from_delivery,
    close_worker_agent_session_async,
    get_worker_agent_command_capability,
    handle_worker_agent_command_ack_async,
    handle_worker_agent_command_result_async,
    handle_worker_agent_hello_async,
    list_redeliverable_worker_agent_commands_async,
    mark_worker_agent_command_dispatched_async,
    mark_worker_agent_session_message_async,
    reject_worker_agent_command_without_delivery_async,
)
from onestep_control_plane_api.db.models import WorkerAgent
from onestep_control_plane_api.db.session import session_scope

router = APIRouter(prefix="/api/v1/worker-agents", tags=["worker-agent-ws"])


@dataclass(frozen=True)
class _ConnectionContext:
    worker_agent_id: UUID
    session_id: str


def _build_error_message(
    *,
    code: str,
    message: str,
    close_connection: bool,
) -> WorkerAgentErrorMessage:
    now = utcnow()
    return WorkerAgentErrorMessage(
        type="error",
        message_id=f"msg_{now.timestamp():.0f}",
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


async def _send_loop(
    websocket: WebSocket,
    send_queue: asyncio.Queue[dict[str, object]],
) -> None:
    while True:
        message = await send_queue.get()
        await websocket.send_json(message)


@router.websocket("/ws")
async def worker_agent_ws(
    websocket: WebSocket,
    worker_agent: WorkerAgent = Depends(require_websocket_worker_agent_connection),
) -> None:
    await websocket.accept()
    send_queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    send_task = asyncio.create_task(_send_loop(websocket, send_queue))
    context: _ConnectionContext | None = None
    try:
        while True:
            raw_message = await websocket.receive_text()
            now = utcnow()
            try:
                envelope = WorkerAgentWsEnvelope.model_validate_json(raw_message)
            except ValidationError:
                await _send_error(
                    websocket,
                    code="invalid_envelope",
                    message="message envelope is invalid",
                    close_connection=False,
                )
                continue

            if context is None:
                if envelope.type != "hello":
                    await _send_error(
                        websocket,
                        code="hello_required",
                        message="first worker-agent websocket message must be hello",
                        close_connection=True,
                    )
                    return
                try:
                    hello = WorkerAgentHelloMessage.model_validate_json(raw_message)
                    ack, pending = await _handle_hello(
                        worker_agent_id=worker_agent.worker_agent_id,
                        message=hello,
                        connected_at=now,
                    )
                except (ValidationError, ValueError) as exc:
                    await _send_error(
                        websocket,
                        code="invalid_hello",
                        message=str(exc),
                        close_connection=True,
                    )
                    return

                context = _ConnectionContext(
                    worker_agent_id=worker_agent.worker_agent_id,
                    session_id=ack.payload.session_id,
                )
                await worker_agent_connection_registry.register(
                    worker_agent_id=context.worker_agent_id,
                    session_id=context.session_id,
                    send_queue=send_queue,
                )
                await websocket.send_json(ack.model_dump(mode="json"))
                await _enqueue_pending_commands(
                    pending=pending,
                    worker_agent_id=worker_agent.worker_agent_id,
                    session_id=context.session_id,
                    accepted_capabilities=ack.payload.accepted_capabilities,
                    send_queue=send_queue,
                )
                continue

            await _touch_session(context.session_id, now)
            if envelope.type == "heartbeat":
                try:
                    heartbeat = WorkerAgentHeartbeatMessage.model_validate_json(raw_message)
                    await _apply_heartbeat(
                        worker_agent_id=worker_agent.worker_agent_id,
                        session_id=context.session_id,
                        message=heartbeat,
                        received_at=now,
                    )
                except (ValidationError, ValueError) as exc:
                    await _send_error(
                        websocket,
                        code="invalid_heartbeat",
                        message=str(exc),
                        close_connection=True,
                    )
                    return
                continue

            if envelope.type == "deployment_event":
                try:
                    deployment_event = WorkerDeploymentEventMessage.model_validate_json(
                        raw_message
                    )
                except ValidationError:
                    await _send_error(
                        websocket,
                        code="invalid_deployment_event",
                        message="deployment_event payload is invalid",
                        close_connection=False,
                    )
                    continue
                applied = await _apply_deployment_event(
                    worker_agent_id=worker_agent.worker_agent_id,
                    message=deployment_event,
                    received_at=now,
                )
                if not applied:
                    await _send_error(
                        websocket,
                        code="unknown_deployment",
                        message=(
                            f"worker deployment {deployment_event.payload.deployment_id} "
                            "was not found for this worker agent"
                        ),
                        close_connection=False,
                    )
                continue

            if envelope.type == "command_ack":
                try:
                    command_ack = WorkerAgentCommandAckMessage.model_validate_json(
                        raw_message
                    )
                except ValidationError:
                    await _send_error(
                        websocket,
                        code="invalid_command_ack",
                        message="command_ack payload is invalid",
                        close_connection=False,
                    )
                    continue
                applied = await _run_command_ack(
                    worker_agent_id=worker_agent.worker_agent_id,
                    session_id=context.session_id,
                    message=command_ack,
                    received_at=now,
                )
                if not applied:
                    await _send_error(
                        websocket,
                        code="unknown_command",
                        message=(
                            f"worker-agent command {command_ack.payload.command_id} "
                            "was not found"
                        ),
                        close_connection=False,
                    )
                continue

            if envelope.type == "command_result":
                try:
                    command_result = WorkerAgentCommandResultMessage.model_validate_json(
                        raw_message
                    )
                except ValidationError:
                    await _send_error(
                        websocket,
                        code="invalid_command_result",
                        message="command_result payload is invalid",
                        close_connection=False,
                    )
                    continue
                result_status = await _run_command_result(
                    worker_agent_id=worker_agent.worker_agent_id,
                    session_id=context.session_id,
                    message=command_result,
                    received_at=now,
                )
                if result_status == "unknown":
                    await _send_error(
                        websocket,
                        code="unknown_command",
                        message=(
                            f"worker-agent command {command_result.payload.command_id} "
                            "was not found"
                        ),
                        close_connection=False,
                    )
                continue

            await _send_error(
                websocket,
                code="unsupported_message_type",
                message=f"worker-agent message type {envelope.type} is not supported yet",
                close_connection=False,
            )
    except WebSocketDisconnect:
        pass
    finally:
        send_task.cancel()
        if context is not None:
            # Shielded: unregistering and the disconnect work unit must complete
            # even when the enclosing task was already cancelled, or a
            # disconnected worker keeps an active session and a live registry
            # entry forever.
            with anyio.CancelScope(shield=True):
                await worker_agent_connection_registry.unregister(
                    worker_agent_id=context.worker_agent_id,
                    session_id=context.session_id,
                )
                await _close_connection_session(
                    worker_agent_id=context.worker_agent_id,
                    session_id=context.session_id,
                )


async def _handle_hello(
    *,
    worker_agent_id: UUID,
    message: WorkerAgentHelloMessage,
    connected_at,
) -> tuple[object, list[PendingWorkerCommandDelivery]]:
    """Register the hello AND list its redeliverable commands in ONE work unit.

    The original synchronous implementation held a single session from the
    hello writes through the pending-command listing, so the listing saw
    exactly the commands that existed when the session row was created.
    Doing both in one work unit keeps that atomicity: a command created in the
    gap would otherwise be picked up by the listing AND immediately pushed by
    the HTTP dispatch path, making the worker receive it twice.

    The work unit still ends before the caller sends the ack or waits for the
    next frame, so no transaction is held across a network wait.
    """

    with anyio.CancelScope(shield=True):
        async with session_scope() as session:
            ack = await handle_worker_agent_hello_async(
                session,
                worker_agent_id=worker_agent_id,
                message=message,
                connected_at=connected_at,
            )
            pending = await list_redeliverable_worker_agent_commands_async(
                session, worker_agent_id=worker_agent_id
            )
    return ack, pending


async def _enqueue_pending_commands(
    *,
    pending: list[PendingWorkerCommandDelivery],
    worker_agent_id: UUID,
    session_id: str,
    accepted_capabilities: list[str],
    send_queue: asyncio.Queue[dict[str, object]],
) -> None:
    """Redeliver already-listed commands, one work unit per command.

    The listing itself happened inside the hello's work unit; this only
    dispatches or rejects what it returned. Each dispatch or rejection is its
    own work unit, so a slow command never holds a transaction across the
    ``send_queue.put`` that follows.
    """

    for delivery in pending:
        required_capability = get_worker_agent_command_capability(delivery.kind)
        if required_capability is not None and required_capability not in accepted_capabilities:
            with anyio.CancelScope(shield=True):
                async with session_scope() as session:
                    await reject_worker_agent_command_without_delivery_async(
                        session,
                        command_id=delivery.command_id,
                        error_code="unsupported_capability",
                        error_message=(
                            f"worker agent {worker_agent_id} does not advertise "
                            f"capability {required_capability}"
                        ),
                    )
            continue

        with anyio.CancelScope(shield=True):
            async with session_scope() as session:
                await mark_worker_agent_command_dispatched_async(
                    session,
                    command_id=delivery.command_id,
                    session_id=session_id,
                )
        # Queued only after the dispatch work unit committed, so the worker
        # never receives a command whose dispatch row is not yet durable.
        await send_queue.put(
            build_worker_agent_command_message_from_delivery(delivery).model_dump(
                mode="json"
            )
        )


async def _touch_session(session_id: str, occurred_at) -> None:
    """Record that a worker session delivered a message, as its own work unit."""

    with anyio.CancelScope(shield=True):
        async with session_scope() as session:
            await mark_worker_agent_session_message_async(
                session, session_id=session_id, occurred_at=occurred_at
            )


async def _apply_heartbeat(
    *,
    worker_agent_id: UUID,
    session_id: str,
    message: WorkerAgentHeartbeatMessage,
    received_at,
) -> None:
    """Apply one heartbeat as its own work unit."""

    with anyio.CancelScope(shield=True):
        async with session_scope() as session:
            await apply_worker_agent_heartbeat_async(
                session,
                worker_agent_id=worker_agent_id,
                session_id=session_id,
                message=message,
                received_at=received_at,
            )


async def _apply_deployment_event(
    *,
    worker_agent_id: UUID,
    message: WorkerDeploymentEventMessage,
    received_at,
) -> bool:
    """Record one deployment event as its own work unit."""

    with anyio.CancelScope(shield=True):
        async with session_scope() as session:
            return await apply_worker_deployment_event_async(
                session,
                worker_agent_id=worker_agent_id,
                message=message,
                received_at=received_at,
            )


async def _run_command_ack(
    *,
    worker_agent_id: UUID,
    session_id: str,
    message: WorkerAgentCommandAckMessage,
    received_at,
) -> bool:
    """Apply one ``command_ack`` as its own work unit and return its outcome."""

    with anyio.CancelScope(shield=True):
        async with session_scope() as session:
            return await handle_worker_agent_command_ack_async(
                session,
                worker_agent_id=worker_agent_id,
                session_id=session_id,
                message=message,
                received_at=received_at,
            )


async def _run_command_result(
    *,
    worker_agent_id: UUID,
    session_id: str,
    message: WorkerAgentCommandResultMessage,
    received_at,
) -> str:
    """Apply one ``command_result`` as its own work unit and return its outcome."""

    with anyio.CancelScope(shield=True):
        async with session_scope() as session:
            return await handle_worker_agent_command_result_async(
                session,
                worker_agent_id=worker_agent_id,
                session_id=session_id,
                message=message,
                received_at=received_at,
            )


async def _close_connection_session(*, worker_agent_id: UUID, session_id: str) -> None:
    """Mark the session disconnected during cleanup, as its own work unit.

    Runs inside :func:`anyio.CancelScope` with ``shield=True``. The enclosing
    task is frequently already cancelled by the time cleanup runs — Starlette's
    test client cancels the websocket scope as soon as the ``with`` block
    exits, and a real disconnect or server shutdown can do the same. Without
    the shield, the first ``await`` inside the work unit would raise
    ``CancelledError`` and the session would stay ``active`` forever.
    """

    with anyio.CancelScope(shield=True):
        async with session_scope() as session:
            await close_worker_agent_session_async(
                session,
                worker_agent_id=worker_agent_id,
                session_id=session_id,
                disconnected_at=utcnow(),
            )
