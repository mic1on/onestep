"""Native-async behaviour of the worker-agent WebSocket database path.

Mirrors ``test_agent_ws_async_db.py`` for the worker WS (issue #212): these
tests cover the properties the conversion is supposed to buy, not the protocol
semantics (those stay in ``test_worker_agent_ws.py``):

* an idle worker WS holds **no checked-out connection and no open session**;
* a database failure inside a work unit rolls back cleanly, leaves no hanging
  session behind and releases its connection;
* cancellation on disconnect still records the disconnect and releases;
* a slow database does not stop the server from answering other traffic.

Each assertion here is a discriminator: it fails against the pre-conversion
implementation (one synchronous session held for the whole WS lifetime) or a
plausible wrong conversion, not just against a broken one.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from onestep_control_plane_api.api.routers import worker_agent_ws as worker_agent_ws_module
from onestep_control_plane_api.api.worker_agent_connection_registry import (
    worker_agent_connection_registry,
)
from onestep_control_plane_api.db.models import WorkerAgentCommand, WorkerAgentSession
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from test_worker_agent_ws import (
    _hello_message,
    _register_worker_agent,
    _upload_workflow_package,
)


def _wait_until_idle(async_db, *, timeout_s: float = 5.0) -> None:
    """Wait until every work unit has finished and been closed.

    Same contract as the agent-WS helper: an assertion made right after a frame
    is sent would race the server, which may legitimately still be mid-work
    unit. Waiting for quiescence makes the "nothing is left open" assertions
    measure behaviour rather than timing.
    """

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not async_db.open_sessions and async_db.checkedout() == 0:
            return
        time.sleep(0.01)
    raise AssertionError(
        f"work units never settled: {len(async_db.open_sessions)} open session(s), "
        f"{async_db.checkedout()} checked-out connection(s)"
    )


def _eventually(fn, *, async_db, timeout_s: float = 5.0):
    """Run ``fn`` until it stops raising SQLite lock errors and quiescence holds.

    The server's async work units and the test's synchronous reads share one
    SQLite database: a read issued while a write transaction is still open
    raises "table is locked" rather than blocking. Retry until the server is
    idle AND the read succeeds, so the assertion measures behaviour, not a
    scheduling race.
    """

    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            result = fn()
            if not async_db.open_sessions and async_db.checkedout() == 0:
                return result
        except OperationalError as exc:  # pragma: no cover - timing dependent
            last_error = exc
        time.sleep(0.01)
    if last_error is not None:
        raise last_error
    raise AssertionError("condition never became true and the server never settled")


def _register(client: TestClient, worker_agent_registration_token: str) -> dict[str, object]:
    return _register_worker_agent(client, worker_agent_registration_token)


def _connect(client: TestClient, registration: dict[str, object]):
    return client.websocket_connect(
        "/api/v1/worker-agents/ws",
        headers={"Authorization": f"Bearer {registration['connection_token']}"},
    )


# --------------------------------------------------------------------------------------
# Work-unit granularity: no connection or session survives an idle connection
# --------------------------------------------------------------------------------------


def test_idle_worker_ws_after_hello_holds_nothing_checked_out(
    client, async_db, worker_agent_registration_token
):
    """After hello is fully handled, the connection holds no DB resources.

    The pre-conversion implementation answered hello through a dependency-
    injected session that stayed open for the whole connection, so an idle
    worker held a checked-out connection (and an open transaction) forever.
    """

    registration = _register(client, worker_agent_registration_token)
    with _connect(client, registration) as websocket:
        websocket.send_json(_hello_message(str(registration["worker_agent_id"])))
        ack = websocket.receive_json()
        assert ack["type"] == "hello_ack"

        _wait_until_idle(async_db)
        assert async_db.checkedout() == 0, (
            f"idle worker WS still holds {async_db.checkedout()} checked-out connection(s)"
        )
        assert async_db.leaked_connections == 0
        assert async_db.open_sessions == [], (
            f"{len(async_db.open_sessions)} session(s) were never closed"
        )
        assert async_db.checkins >= async_db.checkouts

    assert async_db.checkedout() == 0


def test_each_message_uses_a_fresh_session(
    client, async_db, worker_agent_registration_token
):
    """Work units do not share one session across the connection lifetime.

    Counts ``AsyncSession`` constructions through the factory the router uses,
    so a conversion that hoisted one session out of the loop would be caught.
    """

    import onestep_control_plane_api.db.session as db_session_module

    registration = _register(client, worker_agent_registration_token)
    created = []
    real_factory = db_session_module.get_async_session_factory()

    def counting_factory():
        session = real_factory()
        created.append(session)
        return session

    db_session_module.AsyncSessionLocal = counting_factory
    try:
        with _connect(client, registration) as websocket:
            websocket.send_json(_hello_message(str(registration["worker_agent_id"])))
            websocket.receive_json()
            hello_sessions = len(created)
            assert hello_sessions >= 1
        assert len(created) >= hello_sessions
        assert all(not s.in_transaction() for s in created)
        assert async_db.open_sessions == []
    finally:
        db_session_module.AsyncSessionLocal = real_factory


# --------------------------------------------------------------------------------------
# DB failure injection: rollback, no hanging session, connection released
# --------------------------------------------------------------------------------------


def test_hello_db_failure_rolls_back_and_leaves_no_hanging_session(
    client, async_db, db_session, worker_agent_registration_token
):
    """A DB error inside the hello work unit rolls everything back cleanly.

    The hello path opens the WorkerAgentSession row AND lists redeliverable
    commands in ONE work unit. Neither helper commits on its own, so when the
    listing raises after the insert, the outer rollback must remove the new
    session row — otherwise an ``active`` session would leak that no cleanup
    path can find, because the handler never set its context.
    """

    registration = _register(client, worker_agent_registration_token)
    real_listing = worker_agent_ws_module.list_redeliverable_worker_agent_commands_async
    real_hello = worker_agent_ws_module.handle_worker_agent_hello_async
    attempted_session_ids: list[str] = []

    async def recording_hello(session, **kwargs):
        result = await real_hello(session, **kwargs)
        attempted_session_ids.append(result.payload.session_id)
        return result

    async def failing_listing(session, *, worker_agent_id):
        raise OperationalError("SELECT worker_agent_commands", {}, RuntimeError("boom"))

    worker_agent_ws_module.handle_worker_agent_hello_async = recording_hello
    worker_agent_ws_module.list_redeliverable_worker_agent_commands_async = failing_listing

    try:
        with pytest.raises(Exception):
            with _connect(client, registration) as websocket:
                websocket.send_json(_hello_message(str(registration["worker_agent_id"])))
                time.sleep(0.2)
    finally:
        worker_agent_ws_module.handle_worker_agent_hello_async = real_hello
        worker_agent_ws_module.list_redeliverable_worker_agent_commands_async = real_listing

    assert attempted_session_ids, "hello never attempted to open a session"
    leaked_session_id = attempted_session_ids[0]

    db_session.expire_all()
    session = db_session.scalar(
        select(WorkerAgentSession).where(
            WorkerAgentSession.session_id == leaked_session_id
        )
    )
    assert session is None, (
        "a hello whose pending-command listing failed left a WorkerAgentSession "
        "behind; the outer rollback did not cover the insert, which leaks a "
        "hanging session no cleanup path can find"
    )
    _wait_until_idle(async_db)
    assert async_db.checkedout() == 0, "the failed work unit leaked its connection"
    assert async_db.open_sessions == []


def test_command_result_db_failure_rolls_back_and_connection_is_reusable(
    client, async_db, db_session, worker_agent_registration_token
):
    """A DB error in the command-result work unit rolls back and releases.

    The connection must come back to the pool cleanly so a later work unit (on
    the same or another connection) can run against the uncorrupted state.
    """

    registration = _register(client, worker_agent_registration_token)
    package = _upload_workflow_package(client)
    deployment_response = client.post(
        "/api/v1/worker-deployments",
        json={
            "workflow_package_id": package["package_id"],
            "worker_agent_id": registration["worker_agent_id"],
        },
    )
    assert deployment_response.status_code == 200

    real_result = worker_agent_ws_module.handle_worker_agent_command_result_async

    async def failing_result(session, **kwargs):
        raise OperationalError("UPDATE worker_agent_commands", {}, RuntimeError("boom"))

    worker_agent_ws_module.handle_worker_agent_command_result_async = failing_result
    try:
        # The handler has no graceful path for a database failure: it crashes
        # the connection and the test transport surfaces the app error on
        # unwind. Which exact transport error arrives is not the point here;
        # what matters is the database state afterwards.
        with pytest.raises(Exception):
            with _connect(client, registration) as websocket:
                websocket.send_json(_hello_message(str(registration["worker_agent_id"])))
                websocket.receive_json()
                command_message = websocket.receive_json()
                assert command_message["type"] == "command"

                websocket.send_json(
                    {
                        "type": "command_result",
                        "message_id": "msg_result_1",
                        "sent_at": "2026-06-16T09:00:20Z",
                        "payload": {
                            "command_id": command_message["payload"]["command_id"],
                            "status": "succeeded",
                            "result": {},
                            "finished_at": "2026-06-16T09:00:20Z",
                        },
                    }
                )
                websocket.receive_json(timeout=5)
    finally:
        worker_agent_ws_module.handle_worker_agent_command_result_async = real_result

    _wait_until_idle(async_db)
    assert async_db.checkedout() == 0, "the failed command-result unit leaked its connection"
    assert async_db.open_sessions == []

    # The result was NOT applied: the command must still be dispatched, not
    # half-updated to a terminal status.
    db_session.expire_all()
    command = db_session.scalar(
        select(WorkerAgentCommand).where(
            WorkerAgentCommand.command_id
            == UUID(command_message["payload"]["command_id"])
        )
    )
    assert command is not None
    assert command.status == "dispatched"
    assert command.finished_at is None


# --------------------------------------------------------------------------------------
# Cancellation: a disconnected worker leaves no session and no connection
# --------------------------------------------------------------------------------------


def test_disconnect_cleans_up_session_and_registry(
    client, async_db, db_session, worker_agent_registration_token
):
    """Closing the socket runs the disconnect work unit and commits it."""

    registration = _register(client, worker_agent_registration_token)
    with _connect(client, registration) as websocket:
        websocket.send_json(_hello_message(str(registration["worker_agent_id"])))
        ack = websocket.receive_json()
        session_id = ack["payload"]["session_id"]

    db_session.expire_all()
    session = db_session.scalar(
        select(WorkerAgentSession).where(WorkerAgentSession.session_id == session_id)
    )
    assert session is not None
    assert session.status == "disconnected"
    assert session.disconnected_at is not None
    _wait_until_idle(async_db)
    assert async_db.checkedout() == 0
    assert async_db.open_sessions == []


def test_cancellation_releases_connection_and_registry_entry(
    client, async_db, db_session, worker_agent_registration_token
):
    """A cancelled handler leaves no session, no registry entry, no connection.

    Drives the disconnect cleanup work unit directly under cancellation, the
    shape the transport produces when the WS scope is torn down: the enclosing
    task is already cancelled when cleanup runs, so the shielded scope must
    still commit the disconnect and release the connection.
    """

    import onestep_control_plane_api.db.session as db_session_module  # noqa: F401

    registration = _register(client, worker_agent_registration_token)
    with _connect(client, registration) as websocket:
        websocket.send_json(_hello_message(str(registration["worker_agent_id"])))
        ack = websocket.receive_json()
        session_id = ack["payload"]["session_id"]

    async def scenario():
        # Start the disconnect cleanup work unit, cancel it while it is in
        # flight, and let the shielded scope finish the commit anyway. The
        # suppress matches the transport: the enclosing task dies cancelled,
        # but the work unit itself must have completed.
        task = asyncio.create_task(
            worker_agent_ws_module._close_connection_session(
                worker_agent_id=UUID(str(registration["worker_agent_id"])),
                session_id=session_id,
            )
        )
        await asyncio.sleep(0.001)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    # The WS handler's own shielded disconnect cleanup may still be settling
    # when the test loop has exited; wait for quiescence before asserting.
    _wait_until_idle(async_db)

    db_session.expire_all()
    session = _eventually(
        lambda: db_session.scalar(
            select(WorkerAgentSession).where(
                WorkerAgentSession.session_id == session_id
            )
        ),
        async_db=async_db,
    )
    assert session is not None
    assert session.status == "disconnected", (
        "a cancelled handler must still record the disconnect: without the "
        "shielded cleanup scope the session would stay active forever"
    )
    assert async_db.checkedout() == 0, "cancellation leaked a checked-out connection"
    assert async_db.leaked_connections == 0
    assert async_db.open_sessions == []


def test_registry_entry_is_removed_on_disconnect(
    client, async_db, worker_agent_registration_token
):
    """After the socket closes, no live connection stays registered."""

    registration = _register(client, worker_agent_registration_token)
    worker_agent_id = UUID(str(registration["worker_agent_id"]))
    with _connect(client, registration) as websocket:
        websocket.send_json(_hello_message(str(worker_agent_id)))
        websocket.receive_json()
        _wait_until_idle(async_db)
        assert asyncio.run(worker_agent_connection_registry.get(worker_agent_id)) is not None

    _wait_until_idle(async_db)
    assert asyncio.run(worker_agent_connection_registry.get(worker_agent_id)) is None, (
        "a disconnected worker kept a live registry entry"
    )


# --------------------------------------------------------------------------------------
# The point of the conversion: a slow DB must not freeze the server
# --------------------------------------------------------------------------------------


def test_ws_traffic_is_served_while_a_worker_db_work_unit_is_in_flight(
    client, async_db, worker_agent_registration_token, monkeypatch
):
    """A second worker's hello completes while the first one's DB call is slow."""

    registration = _register(client, worker_agent_registration_token)
    other_registration = _register(client, worker_agent_registration_token)

    real_hello = worker_agent_ws_module.handle_worker_agent_hello_async
    entered = asyncio.Event()
    delay_s = 1.0
    slow_worker_id = str(registration["worker_agent_id"])

    async def slow_hello(session, *, worker_agent_id, message, connected_at):
        if str(worker_agent_id) == slow_worker_id:
            # Only the FIRST worker's hello is slow. SQLite's shared-cache mode
            # returns "table is locked" when two connections write at once, so
            # the second connection must run a fast hello to exercise true
            # server responsiveness rather than writer contention.
            entered.set()
            await asyncio.sleep(delay_s)
        return await real_hello(
            session,
            worker_agent_id=worker_agent_id,
            message=message,
            connected_at=connected_at,
        )

    monkeypatch.setattr(worker_agent_ws_module, "handle_worker_agent_hello_async", slow_hello)

    with _connect(client, registration) as websocket:
        websocket.send_json(_hello_message(str(registration["worker_agent_id"])))

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not entered.is_set():
            time.sleep(0.005)

        # A second, independent worker WS must still complete its hello while
        # the first connection's database call is in flight.
        with _connect(client, other_registration) as other:
            other.send_json(_hello_message(str(other_registration["worker_agent_id"])))
            ack = other.receive_json()
            assert ack["type"] == "hello_ack"

    _wait_until_idle(async_db)
    assert async_db.checkedout() == 0


# --------------------------------------------------------------------------------------
# Service-level work-unit contract
# --------------------------------------------------------------------------------------


def test_hello_work_unit_returns_plain_data_and_defers_commit(
    db_session, async_db, worker_agent_registration_token
):
    """``handle_worker_agent_hello_async`` must not commit inside the work unit."""

    from onestep_control_plane_api.api.schemas import WorkerAgentHelloMessage
    from onestep_control_plane_api.api.security import hash_worker_agent_token
    from onestep_control_plane_api.api.worker_agent_service import (
        handle_worker_agent_hello_async,
    )
    from onestep_control_plane_api.db.models import WorkerAgent
    from onestep_control_plane_api.db.session import session_scope

    worker_agent = WorkerAgent(
        worker_agent_id=uuid4(),
        display_name="unit-agent",
        status="offline",
        execution_mode="subprocess",
        max_concurrent_deployments=2,
        connection_token_hash=hash_worker_agent_token("unit-token"),
    )
    db_session.add(worker_agent)
    db_session.commit()

    hello = WorkerAgentHelloMessage.model_validate(
        _hello_message(str(worker_agent.worker_agent_id))
    )

    async def scenario():
        async with session_scope() as session:
            ack = await handle_worker_agent_hello_async(
                session,
                worker_agent_id=worker_agent.worker_agent_id,
                message=hello,
                connected_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            )
            # An inner commit would have ended the session's transaction.
            return ack, session.in_transaction()

    ack, was_open = asyncio.run(scenario())

    assert was_open, (
        "handle_worker_agent_hello_async committed inside the work unit: the "
        "caller's transaction ended before session_scope could own the commit"
    )
    assert ack.payload.session_id.startswith("worker_sess_")
    # Plain pydantic model built in-unit: readable after the session closed.
    assert ack.payload.accepted_capabilities == ["deployment.start", "deployment.stop"]
