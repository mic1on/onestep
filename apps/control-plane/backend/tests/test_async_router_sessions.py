from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from uuid import UUID

import httpx
from onestep_control_plane_api.api.agent_connection_registry import (
    LiveAgentConnection,
    agent_connection_registry,
)
from onestep_control_plane_api.api.security import require_console_auth
from onestep_control_plane_api.auth.service import LocalIdentity
from onestep_control_plane_api.db.models import AgentCommand
from onestep_control_plane_api.main import app
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from test_command_rbac import seed_service_and_instance


def test_async_router_functions_do_not_accept_sync_sessions() -> None:
    router_dir = Path(__file__).parents[1] / "src" / "onestep_control_plane_api" / "api" / "routers"
    violations: list[str] = []

    for path in sorted(router_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            if node.args.vararg is not None:
                arguments.append(node.args.vararg)
            if node.args.kwarg is not None:
                arguments.append(node.args.kwarg)
            for argument in arguments:
                annotation = argument.annotation
                if annotation is None:
                    continue
                rendered = ast.unparse(annotation)
                if rendered == "Session" or rendered.endswith(".Session"):
                    violations.append(f"{path.name}:{node.name}({argument.arg}: {rendered})")

    assert not violations, (
        "async router functions must own native async work units and cannot "
        "accept a synchronous SQLAlchemy Session: " + ", ".join(violations)
    )


def test_instance_command_commits_and_closes_before_enqueue(
    client, db_session, test_engine, async_db, monkeypatch
) -> None:
    instance = seed_service_and_instance(db_session)
    identity = LocalIdentity(user_id="operator", username="operator", roles=("operator",))
    monkeypatch.setitem(app.dependency_overrides, require_console_auth, lambda: identity)

    class CommitProbeQueue:
        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        async def put(self, message: dict[str, object]) -> None:
            command_id = message["payload"]["command_id"]
            assert async_db.open_sessions == []
            with Session(test_engine) as independent_session:
                command = independent_session.scalar(
                    select(AgentCommand).where(AgentCommand.command_id == command_id)
                )
                assert command is not None
                assert command.status == "dispatched"
                assert command.session_id == "sess_active_1"
            self.messages.append(message)

    queue = CommitProbeQueue()
    connection = LiveAgentConnection(
        instance_id=instance.instance_id,
        session_id="sess_active_1",
        send_queue=queue,  # type: ignore[arg-type]
    )

    async def get_connection(_instance_id: UUID) -> LiveAgentConnection:
        return connection

    monkeypatch.setattr(agent_connection_registry, "get", get_connection)
    response = client.post(
        f"/api/v1/instances/{instance.instance_id}/commands",
        json={"kind": "ping", "args": {"nonce": "commit-probe"}, "timeout_s": 10},
    )

    assert response.status_code == 200
    assert len(queue.messages) == 1
    assert async_db.open_sessions == []


def test_command_route_yields_event_loop_while_database_is_slow(client, monkeypatch) -> None:
    original_scalar = AsyncSession.scalar
    scalar_calls = 0

    async def delayed_scalar(self, *args, **kwargs):
        nonlocal scalar_calls
        scalar_calls += 1
        await asyncio.sleep(0.03)
        return await original_scalar(self, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "scalar", delayed_scalar)

    async def exercise_route() -> tuple[httpx.Response, int]:
        ticks = 0
        finished = asyncio.Event()

        async def ticker() -> None:
            nonlocal ticks
            while not finished.is_set():
                ticks += 1
                await asyncio.sleep(0.001)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as async_client:
            ticking = asyncio.create_task(ticker())
            try:
                response = await async_client.post(
                    f"/api/v1/instances/{UUID(int=0)}/commands",
                    json={"kind": "ping", "args": {}, "timeout_s": 10},
                )
            finally:
                finished.set()
                await ticking
        return response, ticks

    response, ticks = asyncio.run(exercise_route())

    assert response.status_code == 404
    assert scalar_calls >= 1
    assert ticks >= 10
