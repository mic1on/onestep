from __future__ import annotations

import asyncio
import json
import platform
import sys
from datetime import UTC, datetime
from urllib.parse import urlparse, urlunparse
from uuid import UUID, uuid4

import httpx
import websockets

from onestep_worker_agent import __version__
from onestep_worker_agent.config import AgentConfig
from onestep_worker_agent.identity import AgentIdentity
from onestep_worker_agent.supervisor import SubprocessSupervisor


def _message_id() -> str:
    return f"msg_{uuid4().hex}"


def _sent_at() -> str:
    return datetime.now(UTC).isoformat()


def _worker_agent_ws_url(plane_url: str) -> str:
    parsed = urlparse(plane_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse((scheme, parsed.netloc, "/api/v1/worker-agents/ws", "", "", ""))


async def register_agent(config: AgentConfig) -> AgentIdentity:
    async with httpx.AsyncClient(base_url=config.plane_url, timeout=30.0) as client:
        response = await client.post(
            "/api/v1/worker-agents/register",
            json={
                "registration_token": config.registration_token,
                "display_name": config.display_name,
                "agent_version": __version__,
                "onestep_version": None,
                "python_version": platform.python_version(),
                "execution_mode": "subprocess",
                "max_concurrent_deployments": config.max_concurrent_deployments,
                "labels": {},
                "capabilities": ["deployment.start", "deployment.stop", "deployment.restart"],
                "platform": {
                    "system": platform.system(),
                    "machine": platform.machine(),
                    "python_executable": sys.executable,
                },
            },
        )
        response.raise_for_status()
        payload = response.json()
        return AgentIdentity(
            worker_agent_id=UUID(payload["worker_agent_id"]),
            connection_token=payload["connection_token"],
        )


def build_hello_message(
    *,
    worker_agent_id: UUID,
    max_concurrent_deployments: int,
    used_slots: int,
    running_deployments: list[str],
) -> dict[str, object]:
    return {
        "type": "hello",
        "message_id": _message_id(),
        "sent_at": _sent_at(),
        "payload": {
            "protocol_version": "1",
            "worker_agent_id": str(worker_agent_id),
            "capabilities": ["deployment.start", "deployment.stop", "deployment.restart"],
            "max_concurrent_deployments": max_concurrent_deployments,
            "used_slots": used_slots,
            "running_deployments": running_deployments,
        },
    }


def build_heartbeat_message(
    *,
    worker_agent_id: UUID,
    used_slots: int,
    running_deployments: list[str],
) -> dict[str, object]:
    return {
        "type": "heartbeat",
        "message_id": _message_id(),
        "sent_at": _sent_at(),
        "payload": {
            "worker_agent_id": str(worker_agent_id),
            "used_slots": used_slots,
            "running_deployments": running_deployments,
            "recent_errors": [],
        },
    }


async def _connect_ws(url: str, headers: dict[str, str]):
    try:
        return await websockets.connect(url, additional_headers=headers)
    except TypeError:
        return await websockets.connect(url, extra_headers=headers)


async def run_control_loop(
    *,
    config: AgentConfig,
    identity: AgentIdentity,
    supervisor: SubprocessSupervisor,
) -> None:
    ws_url = _worker_agent_ws_url(config.plane_url)
    headers = {"Authorization": f"Bearer {identity.connection_token}"}
    async with await _connect_ws(ws_url, headers) as websocket:
        await websocket.send(
            json.dumps(
                build_hello_message(
                    worker_agent_id=identity.worker_agent_id,
                    max_concurrent_deployments=config.max_concurrent_deployments,
                    used_slots=supervisor.used_slots,
                    running_deployments=supervisor.running_deployments(),
                )
            )
        )
        await websocket.recv()

        async def heartbeat_loop() -> None:
            while True:
                await asyncio.sleep(30)
                await websocket.send(
                    json.dumps(
                        build_heartbeat_message(
                            worker_agent_id=identity.worker_agent_id,
                            used_slots=supervisor.used_slots,
                            running_deployments=supervisor.running_deployments(),
                        )
                    )
                )

        heartbeat_task = asyncio.create_task(heartbeat_loop())
        try:
            async for raw_message in websocket:
                message = json.loads(raw_message)
                if message.get("type") == "command":
                    await _ack_unsupported_command(websocket, message)
        finally:
            heartbeat_task.cancel()


async def _ack_unsupported_command(websocket, message: dict[str, object]) -> None:
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return
    command_id = payload.get("command_id")
    kind = payload.get("kind", "unknown")
    await websocket.send(
        json.dumps(
            {
                "type": "command_ack",
                "message_id": _message_id(),
                "sent_at": _sent_at(),
                "payload": {
                    "command_id": command_id,
                    "status": "rejected",
                    "error_code": "unsupported_command",
                    "error_message": f"command kind {kind} is not implemented yet",
                },
            }
        )
    )
