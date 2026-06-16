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
    handle_worker_agent_hello,
    mark_worker_agent_session_message,
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
