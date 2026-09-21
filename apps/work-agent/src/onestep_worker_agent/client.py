from __future__ import annotations

import asyncio
import json
import platform
import random
import sys
import time
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse, urlunparse
from uuid import UUID, uuid4

import httpx
import websockets

from onestep_worker_agent import __version__
from onestep_worker_agent.config import AgentConfig
from onestep_worker_agent.identity import AgentIdentity
from onestep_worker_agent.packages import extract_package
from onestep_worker_agent.supervisor import (
    DeploymentSpec,
    InstallError,
    SubprocessSupervisor,
    resolve_onestep_executable,
)

# Reconnect policy for the control session.
#
# A failed session retries on a capped exponential budget, and the actual delay
# is drawn uniformly from that budget so a fleet of agents that lost the plane
# at the same moment does not retry in lockstep. A session that stayed up for
# `_STABLE_CONNECTION_SECONDS` counts as healthy and resets the budget, so a
# long-lived agent reconnects promptly after a one-off blip instead of waiting
# out a delay inflated by an outage that is already over.
_BASE_BACKOFF_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 30.0
_STABLE_CONNECTION_SECONDS = 30.0


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
    rng: random.Random | None = None,
) -> None:
    ws_url = _worker_agent_ws_url(config.plane_url)
    headers = {"Authorization": f"Bearer {identity.connection_token}"}
    # Full jitter over the current attempt budget. Agents that lost their
    # session at the same moment (e.g. one plane restart) spread their retries
    # instead of reconnecting in lockstep; the escalating budget keeps the
    # mean delay growing so a down plane is not hammered in a tight loop.
    # `rng` is injectable so tests can pin the jitter instead of sampling it.
    jitter_source = rng if rng is not None else random.Random()
    async with httpx.AsyncClient(base_url=config.plane_url, timeout=60.0) as http_client:
        # Control-plane reliability depends on the agent reconnecting so the
        # plane can re-dispatch pending commands on each new `hello`. Keep
        # reconnecting with capped exponential backoff until shut down.
        attempt_backoff = _BASE_BACKOFF_SECONDS
        while True:
            session_started_at = _monotonic()
            try:
                await _run_one_session(
                    ws_url=ws_url,
                    headers=headers,
                    http_client=http_client,
                    config=config,
                    identity=identity,
                    supervisor=supervisor,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any session failure should retry
                # A session that stayed up long enough was healthy, so an
                # earlier outage must not keep inflating the next retry.
                if _monotonic() - session_started_at >= _STABLE_CONNECTION_SECONDS:
                    attempt_backoff = _BASE_BACKOFF_SECONDS
                delay = _jittered_backoff_delay(attempt_backoff, jitter_source)
                print(
                    "onestep-worker-agent: control session ended: "
                    f"{exc}; reconnecting in {delay:.1f}s"
                )
                await asyncio.sleep(delay)
                attempt_backoff = min(attempt_backoff * 2, _MAX_BACKOFF_SECONDS)
            else:
                # Clean session end (e.g. server-initiated close without error):
                # the plane ended the session on purpose, so reconnect promptly
                # from the base budget rather than escalating the retry.
                attempt_backoff = _BASE_BACKOFF_SECONDS
                delay = _jittered_backoff_delay(attempt_backoff, jitter_source)
                print(
                    "onestep-worker-agent: control session closed; "
                    f"reconnecting in {delay:.1f}s"
                )
                await asyncio.sleep(delay)


def _monotonic() -> float:
    """Monotonic seconds, behind a seam so tests can pin session lifetimes."""
    return time.monotonic()


def _jittered_backoff_delay(budget: float, rng: random.Random) -> float:
    """Return a full-jitter reconnect delay within the current attempt budget.

    The whole budget is randomized (`[0, budget]`) rather than nudged by a
    small percentage: that is what spreads a fleet of agents that all lost
    their session at the same instant. `budget` is the capped exponential
    value, so the upper bound is still respected.
    """
    return rng.uniform(0.0, budget)


async def _supervise_session(
    receive_loop: Coroutine[Any, Any, None],
    heartbeat_loop: Coroutine[Any, Any, None],
) -> None:
    """Run a session's receive loop and heartbeat sender as one supervised unit.

    The session ends when either coroutine finishes:

    * the receive loop ends normally (the plane sent a close frame) -> return,
      so the caller takes its clean-close reconnect path;
    * the heartbeat sender raises (e.g. the socket died between heartbeats) ->
      re-raise, so the caller takes the same reconnect path as a dropped
      receive loop. This is the supervision a bare ``create_task`` lacked: a
      heartbeat failure used to leave the receive loop waiting forever.

    Whichever coroutine finishes first, its companion is cancelled and awaited
    before this coroutine returns or raises, so a session never leaves an
    orphan task behind and never leaves a task exception unretrieved.
    """
    tasks = [asyncio.create_task(receive_loop), asyncio.create_task(heartbeat_loop)]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        # Checked in a fixed order (receive, then heartbeat) so that when both
        # fail together the reported cause is stable rather than set-ordered.
        for task in tasks:
            if task.cancelled() or not task.done():
                continue
            failure = task.exception()
            if failure is not None:
                raise failure
    finally:
        # Also runs when this coroutine is cancelled: cancel whatever is still
        # running and await it, so every task is finished and its exception
        # retrieved before the session is left.
        await _settle_session_tasks(tasks)


async def _settle_session_tasks(tasks: list[asyncio.Task[None]]) -> None:
    """Cancel and await every session task, then re-raise a pending cancellation.

    A cancellation can land while the session is being settled (the agent is
    being shut down). The tasks are cancelled at that point, so keep driving
    them to completion instead of returning early and leaking an orphan, then
    re-raise so the cancellation still reaches the caller.
    """
    for task in tasks:
        if not task.done():
            task.cancel()
    cancellation: asyncio.CancelledError | None = None
    for task in tasks:
        while not task.done():
            try:
                # asyncio.wait never cancels the tasks it waits on, so it
                # observes a cancellation aimed at the agent without
                # abandoning the companion tasks.
                await asyncio.wait({task})
            except asyncio.CancelledError as exc:
                cancellation = exc
                task.cancel()
        # Reading each result marks a failure raised during settling as
        # retrieved, so it cannot be reported as "exception was never
        # retrieved" after the session is gone.
        if not task.cancelled():
            task.exception()
    if cancellation is not None:
        raise cancellation


async def _run_one_session(
    *,
    ws_url: str,
    headers: dict[str, str],
    http_client: httpx.AsyncClient,
    config: AgentConfig,
    identity: AgentIdentity,
    supervisor: SubprocessSupervisor,
) -> None:
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
        # The plane replies with hello_ack carrying the heartbeat interval it
        # expects us to honor. Fall back to 30s if the field is missing/invalid
        # so we still stay alive against an older or stricter server.
        heartbeat_interval = _parse_hello_ack(await websocket.recv(), default=30)

        async def heartbeat_loop() -> None:
            while True:
                await asyncio.sleep(heartbeat_interval)
                await websocket.send(
                    json.dumps(
                        build_heartbeat_message(
                            worker_agent_id=identity.worker_agent_id,
                            used_slots=supervisor.used_slots,
                            running_deployments=supervisor.running_deployments(),
                        )
                    )
                )

        async def receive_loop() -> None:
            async for raw_message in websocket:
                message = json.loads(raw_message)
                message_type = message.get("type")
                # The plane sends `error` frames for protocol violations (e.g.
                # hello_required, unsupported_message_type). Surface them in the
                # log so the operator can tell a forced-close reconnect from a
                # network blip, rather than silently dropping the frame.
                if message_type == "error":
                    _handle_error_message(message)
                    continue
                await handle_control_message(
                    websocket=websocket,
                    http_client=http_client,
                    config=config,
                    identity=identity,
                    supervisor=supervisor,
                    message=message,
                )

        # Both loops belong to the same session: whichever ends or fails first
        # ends the session, and the other is cancelled and awaited.
        await _supervise_session(receive_loop(), heartbeat_loop())


def _parse_hello_ack(raw_message: str | bytes, *, default: int) -> int:
    try:
        payload = json.loads(raw_message)
        interval = payload.get("payload", {}).get("heartbeat_interval_s")
    except (json.JSONDecodeError, AttributeError):
        return default
    if isinstance(interval, bool) or not isinstance(interval, int) or interval < 1:
        return default
    return interval


def _handle_error_message(message: dict[str, object]) -> None:
    payload = message.get("payload")
    if not isinstance(payload, dict):
        print("onestep-worker-agent: received error frame from control plane")
        return
    code = payload.get("code", "unknown")
    detail = payload.get("message", "")
    close_connection = payload.get("close_connection")
    suffix = " (connection will be closed)" if close_connection else ""
    print(f"onestep-worker-agent: control plane error: {code}: {detail}{suffix}")


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
    env = _deployment_env(args.get("env"), config=config, identity=identity)
    params = _object_dict(args.get("params"))
    credential_refs = _string_list(args.get("credential_refs"))
    timeout_s = _payload_timeout_s(payload)

    if stop_existing_first:
        await supervisor.stop(deployment_id, grace_seconds=timeout_s)

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
        await _send_deployment_event(
            websocket,
            deployment_id=deployment_id,
            event_type="preparing",
            observed_status="preparing",
            message="downloading workflow package",
            payload={
                "download_url": download_url,
                # Record the call-time params and credential refs the plane
                # dispatches. They are NOT injected into the subprocess yet:
                # onestep core has no runtime params entrypoint and the plane
                # does not deliver credential values. Capturing them here keeps
                # the deployment timeline auditable until those land.
                "params": params,
                "credential_refs": credential_refs,
            },
        )
        response = await http_client.get(
            download_url,
            headers={"Authorization": f"Bearer {identity.connection_token}"},
        )
        response.raise_for_status()
        extract_package(response.content, package_checksum, package_dir)
        venv_dir = supervisor.venv_path_for(package_checksum)
        onestep_executable = resolve_onestep_executable(venv_dir)
        install_mode = SubprocessSupervisor.detect_install_mode(package_dir) or "runtime"
        await _send_deployment_event(
            websocket,
            deployment_id=deployment_id,
            event_type="installing",
            observed_status="installing",
            message="installing runtime dependencies into virtualenv",
            payload={
                "mode": install_mode,
                "package_checksum": package_checksum,
            },
        )
        try:
            await supervisor.install(
                DeploymentSpec(
                    deployment_id=deployment_id,
                    worker_agent_id=str(identity.worker_agent_id),
                    runtime_instance_id=runtime_instance_id,
                    package_dir=package_dir,
                    entrypoint=entrypoint,
                    env=env,
                    venv_dir=venv_dir,
                    onestep_executable=onestep_executable,
                ),
                mode=install_mode,
                timeout_s=timeout_s,
            )
        except InstallError:
            raise  # surfaced to the outer handler as a failed deployment
        await _send_deployment_event(
            websocket,
            deployment_id=deployment_id,
            event_type="checking",
            observed_status="checking",
            message="running onestep check",
            payload={"entrypoint": entrypoint},
        )
        spec = DeploymentSpec(
            deployment_id=deployment_id,
            worker_agent_id=str(identity.worker_agent_id),
            runtime_instance_id=runtime_instance_id,
            package_dir=package_dir,
            entrypoint=entrypoint,
            env=env,
            venv_dir=venv_dir,
            onestep_executable=onestep_executable,
        )
        check_returncode = await _run_check(supervisor, spec, timeout_s)
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
        await _send_deployment_event(
            websocket,
            deployment_id=deployment_id,
            event_type="failed",
            observed_status="failed",
            message=str(exc),
            payload={"error_code": exc.__class__.__name__},
        )
        await _send_command_result(
            websocket,
            command_id=command_id,
            status="failed",
            error_code=exc.__class__.__name__,
            error_message=str(exc),
        )
        return

    await _send_deployment_event(
        websocket,
        deployment_id=deployment_id,
        event_type="running",
        observed_status="running",
        message="deployment started",
        payload={"runtime_instance_id": runtime_instance_id},
    )
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
    timeout_s = _payload_timeout_s(payload)
    await _send_command_ack(websocket, command_id=command_id, status="accepted")
    try:
        await _send_deployment_event(
            websocket,
            deployment_id=deployment_id,
            event_type="stopping",
            observed_status="stopping",
            message="stopping deployment process",
        )
        returncode = await supervisor.stop(deployment_id, grace_seconds=timeout_s)
    except Exception as exc:
        await _send_deployment_event(
            websocket,
            deployment_id=deployment_id,
            event_type="failed",
            observed_status="failed",
            message=str(exc),
            payload={"error_code": exc.__class__.__name__},
        )
        await _send_command_result(
            websocket,
            command_id=command_id,
            status="failed",
            error_code=exc.__class__.__name__,
            error_message=str(exc),
        )
        return

    await _send_deployment_event(
        websocket,
        deployment_id=deployment_id,
        event_type="stopped",
        observed_status="stopped",
        message="deployment stopped",
        payload={"returncode": returncode},
    )
    await _send_command_result(
        websocket,
        command_id=command_id,
        status="succeeded",
        result={"returncode": returncode},
    )


async def _send_deployment_event(
    websocket,
    *,
    deployment_id: str,
    event_type: str,
    observed_status: str,
    message: str,
    payload: dict[str, object] | None = None,
) -> None:
    await websocket.send(
        json.dumps(
            {
                "type": "deployment_event",
                "message_id": _message_id(),
                "sent_at": _sent_at(),
                "payload": {
                    "deployment_id": deployment_id,
                    "event_type": event_type,
                    "observed_status": observed_status,
                    "message": message,
                    "payload": payload or {},
                },
            }
        )
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


def _deployment_env(
    value: object,
    *,
    config: AgentConfig,
    identity: AgentIdentity,
) -> dict[str, str]:
    env = _string_dict(value)
    env.setdefault("ONESTEP_CONTROL_PLANE_URL", config.plane_url)
    env.setdefault("ONESTEP_CONTROL_PLANE_TOKEN", identity.connection_token)
    return env


def _object_dict(value: object) -> dict[str, object]:
    """Coerce a free-form mapping (e.g. params) without stringifying values.

    Unlike _string_dict, this preserves nested types so the recorded event
    payload stays faithful to what the plane dispatched.
    """
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _payload_timeout_s(payload: dict[str, object]) -> float | None:
    """Read the command-level timeout_s the control plane sets on every command.

    Returns None when absent so callers keep their previous default; the plane
    schema guarantees an int >= 1 when present (WorkerAgentCommandPayload).
    """
    raw = payload.get("timeout_s")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    return value if value >= 1 else None


async def _run_check(
    supervisor: SubprocessSupervisor,
    spec: DeploymentSpec,
    timeout_s: float | None,
) -> int:
    try:
        return await supervisor.check(spec, timeout_s=timeout_s)
    except TimeoutError:
        return 124  # mirrors the conventional `timeout(1)` exit status


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
