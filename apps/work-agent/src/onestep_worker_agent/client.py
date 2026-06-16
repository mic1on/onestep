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
from onestep_worker_agent.packages import extract_package
from onestep_worker_agent.supervisor import DeploymentSpec, SubprocessSupervisor


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
    async with httpx.AsyncClient(base_url=config.plane_url, timeout=60.0) as http_client:
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
                    await handle_control_message(
                        websocket=websocket,
                        http_client=http_client,
                        config=config,
                        identity=identity,
                        supervisor=supervisor,
                        message=message,
                    )
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


async def handle_control_message(
    *,
    websocket,
    http_client: httpx.AsyncClient,
    config: AgentConfig,
    identity: AgentIdentity,
    supervisor: SubprocessSupervisor,
    message: dict[str, object],
) -> None:
    if message.get("type") != "command":
        return
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return
    kind = payload.get("kind")
    if kind == "start_deployment":
        await _handle_start_deployment_command(
            websocket=websocket,
            http_client=http_client,
            config=config,
            identity=identity,
            supervisor=supervisor,
            payload=payload,
            stop_existing_first=False,
        )
        return
    if kind == "stop_deployment":
        await _handle_stop_deployment_command(
            websocket=websocket,
            supervisor=supervisor,
            payload=payload,
        )
        return
    if kind == "restart_deployment":
        await _handle_start_deployment_command(
            websocket=websocket,
            http_client=http_client,
            config=config,
            identity=identity,
            supervisor=supervisor,
            payload=payload,
            stop_existing_first=True,
        )
        return
    else:
        await _ack_unsupported_command(websocket, message)


async def _handle_start_deployment_command(
    *,
    websocket,
    http_client: httpx.AsyncClient,
    config: AgentConfig,
    identity: AgentIdentity,
    supervisor: SubprocessSupervisor,
    payload: dict[str, object],
    stop_existing_first: bool,
) -> None:
    command_id = str(payload.get("command_id") or "")
    args = payload.get("args")
    if not command_id or not isinstance(args, dict):
        return

    deployment_id = _required_string(args, "deployment_id")
    package_checksum = _required_string(args, "package_checksum")
    download_url = _required_string(args, "download_url")
    entrypoint = _required_string(args, "entrypoint")
    env = _string_dict(args.get("env"))

    if stop_existing_first:
        await supervisor.stop(deployment_id)

    try:
        supervisor.reserve_slot(deployment_id)
    except RuntimeError as exc:
        await _send_command_ack(
            websocket,
            command_id=command_id,
            status="rejected",
            error_code="no_slots_available",
            error_message=str(exc),
        )
        return

    await _send_command_ack(websocket, command_id=command_id, status="accepted")
    package_dir = config.work_dir / "deployments" / deployment_id / "package"
    runtime_instance_id = str(uuid4())
    try:
        response = await http_client.get(
            download_url,
            headers={"Authorization": f"Bearer {identity.connection_token}"},
        )
        response.raise_for_status()
        extract_package(response.content, package_checksum, package_dir)
        spec = DeploymentSpec(
            deployment_id=deployment_id,
            worker_agent_id=str(identity.worker_agent_id),
            runtime_instance_id=runtime_instance_id,
            package_dir=package_dir,
            entrypoint=entrypoint,
            env=env,
        )
        check_returncode = await supervisor.check(spec)
        if check_returncode != 0:
            supervisor.release_slot(deployment_id)
            await _send_command_result(
                websocket,
                command_id=command_id,
                status="failed",
                error_code="check_failed",
                error_message=f"onestep check exited with code {check_returncode}",
            )
            return
        await supervisor.start(spec)
    except Exception as exc:
        supervisor.release_slot(deployment_id)
        await _send_command_result(
            websocket,
            command_id=command_id,
            status="failed",
            error_code=exc.__class__.__name__,
            error_message=str(exc),
        )
        return

    await _send_command_result(
        websocket,
        command_id=command_id,
        status="succeeded",
        result={"runtime_instance_id": runtime_instance_id},
    )


async def _handle_stop_deployment_command(
    *,
    websocket,
    supervisor: SubprocessSupervisor,
    payload: dict[str, object],
) -> None:
    command_id = str(payload.get("command_id") or "")
    args = payload.get("args")
    if not command_id or not isinstance(args, dict):
        return

    deployment_id = _required_string(args, "deployment_id")
    await _send_command_ack(websocket, command_id=command_id, status="accepted")
    try:
        returncode = await supervisor.stop(deployment_id)
    except Exception as exc:
        await _send_command_result(
            websocket,
            command_id=command_id,
            status="failed",
            error_code=exc.__class__.__name__,
            error_message=str(exc),
        )
        return

    await _send_command_result(
        websocket,
        command_id=command_id,
        status="succeeded",
        result={"returncode": returncode},
    )


def _required_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"args.{key} is required")
    return value


def _string_dict(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}


async def _send_command_ack(
    websocket,
    *,
    command_id: str,
    status: str,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    await websocket.send(
        json.dumps(
            {
                "type": "command_ack",
                "message_id": _message_id(),
                "sent_at": _sent_at(),
                "payload": {
                    "command_id": command_id,
                    "status": status,
                    "error_code": error_code,
                    "error_message": error_message,
                },
            }
        )
    )


async def _send_command_result(
    websocket,
    *,
    command_id: str,
    status: str,
    result: dict[str, object] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    await websocket.send(
        json.dumps(
            {
                "type": "command_result",
                "message_id": _message_id(),
                "sent_at": _sent_at(),
                "payload": {
                    "command_id": command_id,
                    "status": status,
                    "result": result,
                    "error_code": error_code,
                    "error_message": error_message,
                    "finished_at": _sent_at(),
                },
            }
        )
    )
