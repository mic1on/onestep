"""HTTP command work units must yield during DB waits and close before delivery."""

from __future__ import annotations

import ast
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from conftest import shared_cache_async_url
from onestep_control_plane_api.api import routers
from onestep_control_plane_api.api.agent_connection_registry import agent_connection_registry
from onestep_control_plane_api.api.worker_agent_connection_registry import (
    worker_agent_connection_registry,
)
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db import session as db_session_module
from onestep_control_plane_api.db.models import (
    AgentCommand,
    WorkerAgentCommand,
    WorkerDeployment,
    WorkerDeploymentEvent,
    WorkflowPackage,
)
from onestep_control_plane_api.main import app
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import AsyncAdaptedQueuePool
from test_command_rbac import seed_service_and_instance
from test_worker_agent_api import register_worker_agent, upload_workflow_package
from test_workers_api import _create_worker_payload, _hello_message, _upload_handler_package


@dataclass(frozen=True)
class CommandRoute:
    path: str
    payload: dict
    connection_id: UUID
    worker: bool
    first_table: str
    source_surface: str | None = None

    @property
    def registry(self):
        return worker_agent_connection_registry if self.worker else agent_connection_registry

    @property
    def connection_key(self):
        return "worker_agent_id" if self.worker else "instance_id"


@pytest.fixture(params=["instance", "service", "task", "create", "stop", "restart", "worker"])
def command_route(request, client, db_session, worker_agent_registration_token):
    kind = request.param
    if kind in {"instance", "service", "task"}:
        instance = seed_service_and_instance(db_session)
        instance.app_snapshot_json = {
            "task_control_states": [{"task_name": "sync", "supported_commands": ["pause_task"]}]
        }
        db_session.commit()
        payload = {"kind": "ping", "reason": "async route regression"}
        path = f"/api/v1/instances/{instance.instance_id}/commands"
        surface = "instance_detail"
        if kind != "instance":
            payload.update(
                target_mode="selected_instances", target_instance_ids=[str(instance.instance_id)]
            )
            path = "/api/v1/services/billing-sync/commands?environment=prod"
            surface = "service_detail_fanout"
        if kind == "task":
            payload["kind"] = "pause_task"
            path = "/api/v1/services/billing-sync/tasks/sync/commands?environment=prod"
            surface = "task_detail"
        return CommandRoute(
            path,
            payload,
            instance.instance_id,
            False,
            "instances" if kind == "instance" else "services",
            surface,
        )

    registration = register_worker_agent(client, worker_agent_registration_token)
    worker_agent_id = UUID(registration["worker_agent_id"])
    if kind == "worker":
        package = _upload_handler_package(client)
        payload = _create_worker_payload()
        payload["handler_package_id"] = package["package_id"]
        worker = client.post("/api/v1/workers", json=payload)
        assert worker.status_code == 200
        return CommandRoute(
            f"/api/v1/workers/{worker.json()['id']}/deploy",
            {"worker_agent_id": str(worker_agent_id)},
            worker_agent_id,
            True,
            "workers",
        )

    package = upload_workflow_package(client)
    payload = {
        "workflow_package_id": package["package_id"],
        "worker_agent_id": str(worker_agent_id),
    }
    path = "/api/v1/worker-deployments"
    if kind != "create":
        deployment = client.post(path, json={**payload, "desired_status": "stopped"})
        assert deployment.status_code == 200
        path += f"/{deployment.json()['deployment_id']}/{kind}"
        payload = {}
    return CommandRoute(
        path,
        payload,
        worker_agent_id,
        True,
        "worker_agents" if kind == "create" else "worker_deployments",
    )


@contextmanager
def connected(client, route, queue):
    async def register():
        await route.registry.register(
            **{route.connection_key: route.connection_id},
            session_id="sess_active_1",
            send_queue=queue,
        )

    async def unregister():
        await route.registry.unregister(
            **{route.connection_key: route.connection_id},
            session_id="sess_active_1",
        )

    client.portal.call(register)
    try:
        yield
    finally:
        client.portal.call(unregister)


async def post_command(route):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        return await client.post(route.path, json=route.payload)


def test_async_router_functions_do_not_accept_sync_sessions():
    offenders = []
    for path in Path(routers.__file__).parent.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            annotations = " ".join(ast.unparse(arg.annotation) for arg in args if arg.annotation)
            defaults = " ".join(
                ast.unparse(value)
                for value in [*node.args.defaults, *node.args.kw_defaults]
                if value is not None
            )
            if "Session" in annotations.replace("AsyncSession", "") or "get_db_session" in defaults:
                offenders.append(f"{path.name}:{node.lineno} {node.name}")
    assert offenders == []


def assert_committed_delivery(message, route, test_engine, async_db):
    assert async_db.open_sessions == []
    assert async_db.checkedout() == 0
    # An independent connection can only observe committed dispatch records.
    with Session(test_engine) as db:
        command_id = message["payload"]["command_id"]
        model = WorkerAgentCommand if route.worker else AgentCommand
        command = db.scalar(
            select(model).where(
                model.command_id == (UUID(command_id) if route.worker else command_id)
            )
        )
        assert command is not None
        assert command.status == "dispatched"
        assert command.session_id == "sess_active_1"
        assert command.dispatched_at is not None
        if route.worker:
            events = db.scalars(
                select(WorkerDeploymentEvent).where(
                    WorkerDeploymentEvent.deployment_id == command.deployment_id,
                )
            ).all()
            for event_type in ("command_created", "command_dispatched"):
                assert any(
                    row.event_type == event_type and row.payload_json["command_id"] == command_id
                    for row in events
                )
            assert (
                db.scalar(
                    select(WorkerDeployment).where(
                        WorkerDeployment.deployment_id == command.deployment_id
                    )
                )
                is not None
            )
        else:
            assert command.source_surface == route.source_surface
            assert command.reason == route.payload["reason"]


def test_dispatch_is_committed_and_closed_before_enqueue(
    command_route,
    client,
    test_engine,
    async_db,
):
    messages = []

    class ProbeQueue:
        async def put(self, message):
            assert_committed_delivery(message, command_route, test_engine, async_db)
            messages.append(message)

    with connected(client, command_route, ProbeQueue()):
        response = client.post(command_route.path, json=command_route.payload)
    assert response.status_code == 200, response.text
    assert len(messages) == 1


def database_state(test_engine):
    with Session(test_engine) as db:
        return (
            [
                db.scalar(select(func.count()).select_from(model))
                for model in (
                    AgentCommand,
                    WorkerAgentCommand,
                    WorkerDeploymentEvent,
                    WorkflowPackage,
                )
            ],
            list(
                db.execute(
                    select(
                        WorkerDeployment.deployment_id,
                        WorkerDeployment.desired_status,
                        WorkerDeployment.observed_status,
                    )
                ).all()
            ),
        )


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
def test_failure_after_flush_rolls_back_entire_request(
    command_route,
    client,
    test_engine,
    async_db,
    monkeypatch,
    failure,
):
    before = database_state(test_engine)
    archives = set(Path(settings.worker_package_storage_dir).glob("*.zip"))
    original_flush = AsyncSession.flush

    async def failing_flush(session, *args, **kwargs):
        has_command = any(
            isinstance(row, (AgentCommand, WorkerAgentCommand)) for row in session.new
        )
        await original_flush(session, *args, **kwargs)
        if has_command:
            raise failure("injected failure after writing command")

    monkeypatch.setattr(AsyncSession, "flush", failing_flush)
    queue = asyncio.Queue()

    async def run():
        with pytest.raises(failure):
            await post_command(command_route)

    with connected(client, command_route, queue):
        client.portal.call(run)
    assert database_state(test_engine) == before
    assert set(Path(settings.worker_package_storage_dir).glob("*.zip")) == archives
    assert queue.empty()
    assert async_db.open_sessions == []
    assert async_db.checkedout() == 0


def test_waiting_for_delivery_holds_no_database_resources(
    command_route,
    client,
    test_engine,
    async_db,
):
    waiting = threading.Event()
    finished = threading.Event()
    messages = []

    class BlockedQueue:
        async def put(self, message):
            assert_committed_delivery(message, command_route, test_engine, async_db)
            messages.append(message)
            waiting.set()
            await asyncio.Event().wait()

    async def run():
        try:
            await post_command(command_route)
        finally:
            finished.set()

    with connected(client, command_route, BlockedQueue()):
        request = client.portal.start_task_soon(run)
        try:
            assert waiting.wait(3)
            assert not request.done()
            assert async_db.open_sessions == []
            assert async_db.checkedout() == 0
            assert client.get("/healthz").status_code == 200
        finally:
            request.cancel()
            assert finished.wait(3)
    # A cancellation during delivery preserves the durable command for redelivery.
    assert_committed_delivery(messages[0], command_route, test_engine, async_db)


@pytest.mark.parametrize("wait_kind", ["query", "pool"])
def test_database_waits_leave_http_and_websocket_responsive(
    command_route,
    client,
    async_db,
    monkeypatch,
    worker_agent_registration_token,
    wait_kind,
):
    registration = register_worker_agent(client, worker_agent_registration_token)
    started = threading.Event()
    release = threading.Event()
    first_query = True
    bounded_engine = None
    held_connection = None

    def slow_query():
        started.set()
        assert release.wait(10), "slow query was never released"
        return 1

    def add_slow_function(connection, _record):
        connection.create_function("issue_258_wait", 0, slow_query)

    def delay_first_query(_conn, _cursor, statement, parameters, _context, _executemany):
        nonlocal first_query
        if first_query and f"FROM {command_route.first_table}" in statement:
            first_query = False
            statement = statement.replace("WHERE ", "WHERE issue_258_wait() = 1 AND ", 1)
        return statement, parameters

    with client.websocket_connect(
        "/api/v1/worker-agents/ws",
        headers={"Authorization": f"Bearer {registration['connection_token']}"},
    ) as websocket:
        websocket.send_json(_hello_message(registration["worker_agent_id"]))
        assert websocket.receive_json()["type"] == "hello_ack"
        if wait_kind == "query":
            event.listen(async_db.engine.sync_engine, "connect", add_slow_function)
            event.listen(
                async_db.engine.sync_engine,
                "before_cursor_execute",
                delay_first_query,
                retval=True,
            )
        if wait_kind == "pool":
            bounded_engine = create_async_engine(
                shared_cache_async_url(async_db.name),
                poolclass=AsyncAdaptedQueuePool,
                pool_size=1,
                max_overflow=0,
                pool_timeout=10,
            )
            factory = async_sessionmaker(bounded_engine, expire_on_commit=False, autoflush=False)

            def tracked_factory():
                started.set()
                return factory()

            monkeypatch.setattr(db_session_module, "AsyncSessionLocal", tracked_factory)

            async def hold_connection():
                return await bounded_engine.connect()

            held_connection = client.portal.call(hold_connection)

        with connected(client, command_route, asyncio.Queue()), ThreadPoolExecutor(3) as executor:
            pending = executor.submit(client.post, command_route.path, json=command_route.payload)
            try:
                assert started.wait(3), "HTTP route never reached its database wait"
                assert not pending.done()
                health = executor.submit(client.get, "/healthz")
                assert health.result(timeout=2).status_code == 200
                # Invalid envelopes are answered without DB access, even when the pool is full.
                websocket.send_text("{}")
                frame = executor.submit(websocket.receive_json).result(timeout=2)
                assert frame["payload"]["code"] == "invalid_envelope"
                assert not pending.done(), "the DB wait ended before the responsiveness probes"
            finally:
                release.set()
                if held_connection is not None:
                    client.portal.call(held_connection.close)
            response = pending.result(timeout=5)
            assert response.status_code == 200, response.text
        if bounded_engine is not None:
            assert bounded_engine.pool.checkedout() == 0
            client.portal.call(bounded_engine.dispose)
            monkeypatch.setattr(db_session_module, "AsyncSessionLocal", async_db.factory)
    assert async_db.open_sessions == []
    assert async_db.checkedout() == 0


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
def test_worker_package_flush_failure_removes_archive(
    client,
    test_engine,
    async_db,
    monkeypatch,
    worker_agent_registration_token,
    failure,
):
    registration = register_worker_agent(client, worker_agent_registration_token)
    package = _upload_handler_package(client)
    payload = _create_worker_payload()
    payload["handler_package_id"] = package["package_id"]
    worker = client.post("/api/v1/workers", json=payload).json()
    route = CommandRoute(
        f"/api/v1/workers/{worker['id']}/deploy",
        {"worker_agent_id": registration["worker_agent_id"]},
        UUID(registration["worker_agent_id"]),
        True,
        "workers",
    )
    before = database_state(test_engine)
    archives = set(Path(settings.worker_package_storage_dir).glob("*.zip"))
    original_flush = AsyncSession.flush

    async def failing_flush(session, *args, **kwargs):
        has_package = any(isinstance(row, WorkflowPackage) for row in session.new)
        await original_flush(session, *args, **kwargs)
        if has_package:
            raise failure("injected package flush failure")

    monkeypatch.setattr(AsyncSession, "flush", failing_flush)

    async def run():
        with pytest.raises(failure):
            await post_command(route)

    client.portal.call(run)
    assert database_state(test_engine) == before
    assert set(Path(settings.worker_package_storage_dir).glob("*.zip")) == archives
    assert async_db.open_sessions == []
    assert async_db.checkedout() == 0
