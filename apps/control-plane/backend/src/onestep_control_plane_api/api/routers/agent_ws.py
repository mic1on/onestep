from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, status
from pydantic import ValidationError
from sqlalchemy import update
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.agent_command_service import (
    build_command_message,
    get_command_by_id,
    get_command_capability,
    list_redeliverable_commands_for_instance,
    mark_command_dispatched,
    reject_redelivery_for_unsupported_capability,
)
from onestep_control_plane_api.api.agent_connection_registry import agent_connection_registry
from onestep_control_plane_api.api.agent_ingestion_service import (
    ingest_events_request,
    ingest_heartbeat_request,
    ingest_metrics_request,
    ingest_sync_request,
)
from onestep_control_plane_api.api.common import (
    as_utc,
    ensure_instance_stub,
    ensure_service,
    utcnow,
)
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
    require_websocket_ingest_token,
)
from onestep_control_plane_api.api.ui_event_stream import publish_ui_stream_event
from onestep_control_plane_api.db.models import AgentCommand, AgentSession
from onestep_control_plane_api.db.session import get_db_session

router = APIRouter(prefix="/api/v1/agents", tags=["agent-ws"])

SUPPORTED_PROTOCOL_VERSION = "1"
SUPPORTED_CAPABILITIES = frozenset(
    {
        "telemetry.sync",
        "telemetry.heartbeat",
        "telemetry.metrics",
        "telemetry.events",
        "command.ping",
        "command.shutdown",
        "command.restart",
        "command.drain",
        "command.pause_task",
        "command.resume_task",
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
    now = utcnow()
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


def _handle_hello(
    db: Session,
    *,
    message: AgentHelloMessage,
    connected_at,
) -> AgentHelloAckMessage:
    if message.payload.protocol_version != SUPPORTED_PROTOCOL_VERSION:
        raise ValueError(
            f"protocol_version={message.payload.protocol_version} is not supported"
        )

    accepted_capabilities = _accepted_capabilities(message.payload.capabilities)
    service = ensure_service(db, message.payload.service, update_existing_version=True)
    instance = ensure_instance_stub(db, service=service, identity=message.payload.service)
    instance.service = service
    instance.node_name = message.payload.service.node_name
    instance.hostname = message.payload.runtime.hostname
    instance.pid = message.payload.runtime.pid
    instance.deployment_version = message.payload.service.deployment_version
    instance.onestep_version = message.payload.runtime.onestep_version
    instance.python_version = message.payload.runtime.python_version
    instance.started_at = as_utc(message.payload.runtime.started_at)
    instance.last_seen_at = connected_at

    session_id = _new_session_id()
    db.execute(
        update(AgentSession)
        .where(
            AgentSession.instance_id == message.payload.service.instance_id,
            AgentSession.status == "active",
        )
        .values(
            status="superseded",
            superseded_at=connected_at,
            disconnected_at=connected_at,
            updated_at=connected_at,
        )
    )
    db.add(
        AgentSession(
            session_id=session_id,
            service=service,
            instance_id=message.payload.service.instance_id,
            protocol_version=message.payload.protocol_version,
            status="active",
            capabilities_json=list(message.payload.capabilities),
            accepted_capabilities_json=accepted_capabilities,
            connected_at=connected_at,
            last_hello_at=connected_at,
            last_message_at=connected_at,
        )
    )
    db.commit()
    publish_ui_stream_event("sessions")

    return AgentHelloAckMessage(
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


def _mark_session_message(db: Session, session_id: str, occurred_at) -> None:
    db.execute(
        update(AgentSession)
        .where(AgentSession.session_id == session_id)
        .values(last_message_at=occurred_at, updated_at=occurred_at)
    )
    db.commit()


def _close_session(db: Session, session_id: str, *, disconnected_at) -> None:
    db.execute(
        update(AgentSession)
        .where(AgentSession.session_id == session_id, AgentSession.status == "active")
        .values(
            status="disconnected",
            disconnected_at=disconnected_at,
            last_message_at=disconnected_at,
            updated_at=disconnected_at,
        )
    )
    db.commit()


async def _send_loop(
    websocket: WebSocket,
    send_queue: asyncio.Queue[dict[str, object]],
) -> None:
    while True:
        message = await send_queue.get()
        await websocket.send_json(message)


async def _enqueue_pending_commands(
    db: Session,
    *,
    instance_id: UUID,
    session_id: str,
    accepted_capabilities: list[str],
    send_queue: asyncio.Queue[dict[str, object]],
) -> None:
    for command in list_redeliverable_commands_for_instance(db, instance_id=instance_id):
        required_capability = get_command_capability(command.kind)
        if required_capability not in accepted_capabilities:
            reject_redelivery_for_unsupported_capability(
                db,
                command=command,
                capability=required_capability,
            )
            publish_ui_stream_event("commands")
            continue
        command = mark_command_dispatched(
            db,
            command=command,
            session_id=session_id,
        )
        publish_ui_stream_event("commands")
        await send_queue.put(build_command_message(command).model_dump(mode="json"))


def _resolve_command_for_session(
    db: Session,
    *,
    context: _ConnectionContext,
    command_id: str,
) -> AgentCommand | None:
    command = get_command_by_id(db, command_id=command_id)
    if command is None or command.instance_id != context.instance_id:
        return None
    return command


def _handle_command_ack(
    db: Session,
    *,
    context: _ConnectionContext,
    message: AgentCommandAckMessage,
    received_at,
) -> bool:
    command = _resolve_command_for_session(
        db,
        context=context,
        command_id=message.payload.command_id,
    )
    if command is None:
        return False
    if command.finished_at is not None or command.ack_status is not None:
        return True

    command.session_id = context.session_id
    command.ack_status = message.payload.status
    command.acked_at = as_utc(message.payload.received_at)
    command.updated_at = received_at
    if message.payload.status == "accepted":
        command.status = "accepted"
    else:
        command.status = "rejected"
        command.finished_at = as_utc(message.payload.received_at)
        command.error_code = message.payload.error_code
        command.error_message = message.payload.error_message
    db.commit()
    publish_ui_stream_event("commands")
    return True


def _handle_command_result(
    db: Session,
    *,
    context: _ConnectionContext,
    message: AgentCommandResultMessage,
    received_at,
) -> str:
    command = _resolve_command_for_session(
        db,
        context=context,
        command_id=message.payload.command_id,
    )
    if command is None:
        return "unknown"
    if command.finished_at is not None:
        return "duplicate"

    command.session_id = context.session_id
    command.status = message.payload.status
    command.finished_at = as_utc(message.payload.finished_at)
    command.result_json = message.payload.result
    command.duration_ms = message.payload.duration_ms
    command.error_code = message.payload.error_code
    command.error_message = message.payload.error_message
    if command.ack_status is None:
        command.ack_status = "accepted"
        command.acked_at = received_at
    command.updated_at = received_at
    db.commit()
    publish_ui_stream_event("commands")
    return "ok"


@router.websocket("/ws")
async def agent_ws(
    websocket: WebSocket,
    auth: WebSocketIngestAuth = Depends(require_websocket_ingest_token),
    db: Session = Depends(get_db_session),
) -> None:
    await websocket.accept(subprotocol=auth.accepted_subprotocol)
    send_queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    send_task = asyncio.create_task(_send_loop(websocket, send_queue))
    context: _ConnectionContext | None = None
    try:
        while True:
            raw_message = await websocket.receive_text()
            now = utcnow()
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
                    hello_ack = _handle_hello(db, message=hello, connected_at=now)
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
                    db,
                    instance_id=context.instance_id,
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
                        ingest_sync_request(db, request, received_at=now)
                    elif telemetry.payload.channel == "heartbeat":
                        request = HeartbeatIngestRequest.model_validate(telemetry.payload.body)
                        ingest_heartbeat_request(db, request, received_at=now)
                    elif telemetry.payload.channel == "metrics":
                        request = MetricsIngestRequest.model_validate(telemetry.payload.body)
                        ingest_metrics_request(db, request, received_at=now)
                    elif telemetry.payload.channel == "events":
                        request = EventsIngestRequest.model_validate(telemetry.payload.body)
                        ingest_events_request(db, request, received_at=now)
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

                _mark_session_message(db, context.session_id, now)
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
                if not _handle_command_ack(
                    db,
                    context=context,
                    message=command_ack,
                    received_at=now,
                ):
                    await _send_error(
                        websocket,
                        code="unknown_command",
                        message=f"command_id={command_ack.payload.command_id} was not found",
                        close_connection=False,
                    )
                    continue
                _mark_session_message(db, context.session_id, now)
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
                result = _handle_command_result(
                    db,
                    context=context,
                    message=command_result,
                    received_at=now,
                )
                if result == "unknown":
                    await _send_error(
                        websocket,
                        code="unknown_command",
                        message=f"command_id={command_result.payload.command_id} was not found",
                        close_connection=False,
                    )
                    continue
                if result == "duplicate":
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
                _mark_session_message(db, context.session_id, now)
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
            await agent_connection_registry.unregister(
                instance_id=context.instance_id,
                session_id=context.session_id,
            )
            _close_session(db, context.session_id, disconnected_at=utcnow())
            publish_ui_stream_event("sessions")
