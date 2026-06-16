from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.common import utcnow
from onestep_control_plane_api.api.schemas import (
    AgentErrorPayload,
    WorkerAgentCommandAckMessage,
    WorkerAgentCommandResultMessage,
    WorkerAgentErrorMessage,
    WorkerAgentHeartbeatMessage,
    WorkerAgentHelloMessage,
    WorkerAgentWsEnvelope,
)
from onestep_control_plane_api.api.security import require_websocket_worker_agent_connection
from onestep_control_plane_api.api.worker_agent_connection_registry import (
    worker_agent_connection_registry,
)
from onestep_control_plane_api.api.worker_agent_service import (
    apply_worker_agent_heartbeat,
    close_worker_agent_session,
    dispatch_worker_agent_command,
    get_worker_agent_command_capability,
    handle_worker_agent_command_ack,
    handle_worker_agent_command_result,
    handle_worker_agent_hello,
    list_redeliverable_worker_agent_commands,
    mark_worker_agent_session_message,
    reject_worker_agent_command_without_delivery,
)
from onestep_control_plane_api.db.models import WorkerAgent
from onestep_control_plane_api.db.session import get_db_session

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
    db: Session = Depends(get_db_session),
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
                    ack = handle_worker_agent_hello(
                        db,
                        worker_agent=worker_agent,
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
                    db,
                    worker_agent=worker_agent,
                    session_id=context.session_id,
                    accepted_capabilities=ack.payload.accepted_capabilities,
                    send_queue=send_queue,
                )
                continue

            mark_worker_agent_session_message(db, session_id=context.session_id, occurred_at=now)
            if envelope.type == "heartbeat":
                try:
                    heartbeat = WorkerAgentHeartbeatMessage.model_validate_json(raw_message)
                    apply_worker_agent_heartbeat(
                        db,
                        worker_agent=worker_agent,
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
                if not handle_worker_agent_command_ack(
                    db,
                    worker_agent=worker_agent,
                    session_id=context.session_id,
                    message=command_ack,
                    received_at=now,
                ):
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
                result_status = handle_worker_agent_command_result(
                    db,
                    worker_agent=worker_agent,
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
            await worker_agent_connection_registry.unregister(
                worker_agent_id=context.worker_agent_id,
                session_id=context.session_id,
            )
            close_worker_agent_session(
                db,
                worker_agent_id=context.worker_agent_id,
                session_id=context.session_id,
                disconnected_at=utcnow(),
            )


async def _enqueue_pending_commands(
    db: Session,
    *,
    worker_agent: WorkerAgent,
    session_id: str,
    accepted_capabilities: list[str],
    send_queue: asyncio.Queue[dict[str, object]],
) -> None:
    for command in list_redeliverable_worker_agent_commands(
        db,
        worker_agent_id=worker_agent.worker_agent_id,
    ):
        required_capability = get_worker_agent_command_capability(command.kind)
        if required_capability is not None and required_capability not in accepted_capabilities:
            reject_worker_agent_command_without_delivery(
                db,
                command=command,
                error_code="unsupported_capability",
                error_message=(
                    f"worker agent {worker_agent.worker_agent_id} does not advertise "
                    f"capability {required_capability}"
                ),
            )
            continue
        await dispatch_worker_agent_command(
            db,
            command=command,
            send_queue=send_queue,
            session_id=session_id,
        )
