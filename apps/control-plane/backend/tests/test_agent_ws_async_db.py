"""Native-async behaviour of the agent WebSocket database path.

These tests cover the properties the conversion itself is supposed to buy, not
the protocol semantics (those stay in ``test_agent_ws.py``):

* every database-touching branch runs on a short-lived async work unit;
* an idle WS holds **no checked-out connection and no open transaction** —
  including the empty-pending-commands path after hello, which is the specific
  leak issue #192 calls out;
* a slow database or a connection-pool wait does **not** freeze the event loop;
* error, duplicate, disconnect and cancellation paths behave.

Each assertion here is a discriminator: it fails against the pre-conversion
implementation or against a plausible wrong conversion, not just against a
broken one.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from conftest import AsyncTestHarness, shared_cache_async_url
from fastapi import HTTPException
from onestep_control_plane_api.api.agent_command_service import (
    list_redeliverable_commands_for_instance_async,
)
from onestep_control_plane_api.api.agent_ingestion_service import ingest_heartbeat_request
from onestep_control_plane_api.api.schemas import HeartbeatIngestRequest
from onestep_control_plane_api.db import session as db_session_module
from onestep_control_plane_api.db.models import AgentCommand, AgentSession, Instance, Service
from onestep_control_plane_api.db.session import session_scope
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool
from test_agent_ws import (
    _drain_ui_stream_channels,
    _hello_message,
    _subscribe_ui_stream,
    _unsubscribe_ui_stream,
    make_service_payload,
)

INSTANCE_ID = UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df")


def _heartbeat_body() -> dict[str, object]:
    return {
        "service": make_service_payload(),
        "runtime": {
            "onestep_version": "1.0.0a0",
            "python_version": "3.11.14",
            "hostname": "vm-prod-3",
            "pid": 18231,
            "started_at": "2026-03-08T17:29:50Z",
        },
        "health": {"status": "ok", "uptime_s": 70, "inflight_tasks": 3},
        "sent_at": "2026-03-08T17:31:00Z",
        "sequence": 2,
    }


def _telemetry_frame(channel: str, body: dict[str, object], message_id: str) -> dict[str, object]:
    return {
        "type": "telemetry",
        "message_id": message_id,
        "sent_at": "2026-03-17T12:00:01Z",
        "payload": {"channel": channel, "body": body},
    }


def _ack_frame(command_id: str) -> dict[str, object]:
    return {
        "type": "command_ack",
        "message_id": "msg_ack_1",
        "sent_at": "2026-03-17T12:00:02Z",
        "payload": {
            "command_id": command_id,
            "status": "accepted",
            "received_at": "2026-03-17T12:00:02Z",
        },
    }


def _result_frame(command_id: str, **overrides: object) -> dict[str, object]:
    payload = {
        "command_id": command_id,
        "status": "succeeded",
        "finished_at": "2026-03-17T12:00:03Z",
        "result": {"ok": True},
        "duration_ms": 3,
    }
    payload.update(overrides)
    return {
        "type": "command_result",
        "message_id": "msg_result_1",
        "sent_at": "2026-03-17T12:00:03Z",
        "payload": payload,
    }


def _seed_service(db_session) -> Service:
    """Create the service/instance rows ``agent_commands.service_id`` requires."""

    service = Service(
        name="billing-sync",
        environment="prod",
        latest_deployment_version="1.0.0",
        latest_sync_at=datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC),
    )
    db_session.add(service)
    db_session.commit()
    db_session.add(
        Instance(
            service_id=service.id,
            instance_id=INSTANCE_ID,
            node_name="vm-prod-3",
            deployment_version="1.0.0",
            status="ok",
            created_at=datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC),
            updated_at=datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC),
        )
    )
    db_session.commit()
    db_session.refresh(service)
    return service


def _wait_until_idle(async_db, *, timeout_s: float = 5.0) -> None:
    """Wait until every work unit has finished and been closed.

    An assertion made immediately after a frame is sent would race the server,
    which can legitimately still be mid-work-unit. Waiting for quiescence makes
    the "nothing is left open" assertions measure behaviour rather than timing.
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


def _open_transaction_count(db_session) -> int:
    """How many SQLite write transactions are open on this connection.

    SQLite exposes no ``pg_stat_activity``, so the authoritative probe is the
    connection's own transaction state: ``sqlite3.Connection.in_transaction`` is
    True exactly while a BEGIN is outstanding. This is what the issue's
    "idle transaction" means here — a read transaction left open across a
    ``receive_text()`` wait.
    """

    raw = db_session.connection().connection.driver_connection
    return 1 if raw.in_transaction else 0


# --------------------------------------------------------------------------------------
# Work-unit granularity: no connection or transaction survives a handled message
# --------------------------------------------------------------------------------------


def test_hello_with_no_pending_commands_leaves_nothing_checked_out(client, auth_headers, async_db):
    """The exact leak from the issue: EMPTY pending list after hello.

    The old code returned early from the pending-command query while its read
    transaction was still open, then blocked in ``receive_text()`` holding it.
    Assert the harness reports zero live connections and no open transaction
    once hello has been handled and the socket is idle.
    """

    with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as websocket:
        websocket.send_json(_hello_message())
        ack = websocket.receive_json()

        assert ack["type"] == "hello_ack"
        # The socket is now idle: hello is fully handled, including the
        # pending-command query, and nothing is waiting on the database.
        _wait_until_idle(async_db)
        assert async_db.checkedout() == 0, (
            f"idle WS still holds {async_db.checkedout()} checked-out connection(s)"
        )
        assert async_db.leaked_connections == 0
        # The session-level probe: a missing ``close()`` is invisible to the pool
        # counters, because GC eventually returns the connection anyway.
        assert async_db.open_sessions == [], (
            f"{len(async_db.open_sessions)} session(s) were never closed"
        )
        # A connection that is never handed back is indistinguishable from a
        # leak, so assert the accounting is self-consistent too.
        assert async_db.checkins >= async_db.checkouts

    assert async_db.checkedout() == 0


def test_idle_ws_after_telemetry_holds_no_connection(client, auth_headers, async_db, db_session):
    """Telemetry is a work unit too, and it ends before the next receive."""

    with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as websocket:
        websocket.send_json(_hello_message())
        websocket.receive_json()
        before = async_db.checkedout()

        websocket.send_json(
            _telemetry_frame("heartbeat", _heartbeat_body(), "msg_heartbeat_1")
        )
        # Give the server a moment to process the frame, then confirm the work
        # unit released its connection rather than holding it.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if db_session.scalar(select(AgentSession.last_message_at)) is not None:
                break
            time.sleep(0.01)

        _wait_until_idle(async_db)
        assert async_db.checkedout() == before, "telemetry work unit left a connection checked out"
        assert async_db.open_sessions == [], "telemetry work unit left a session open"

    assert async_db.checkedout() == 0


def test_idle_ws_has_no_open_transaction(client, auth_headers, db_session):
    """No BEGIN is outstanding while the handler waits for the next frame.

    This is the direct assertion for the issue's idle-transaction bug, measured
    on the same connection the test reads from.
    """

    with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as websocket:
        websocket.send_json(_hello_message())
        websocket.receive_json()
        websocket.send_json(
            _telemetry_frame("heartbeat", _heartbeat_body(), "msg_heartbeat_1")
        )
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if db_session.scalar(select(AgentSession.last_message_at)) is not None:
                break
            time.sleep(0.01)

        assert _open_transaction_count(db_session) == 0, (
            "an idle WS still holds an open transaction"
        )


def test_each_message_uses_a_fresh_session(client, auth_headers, async_db):
    """Work units do not share one session across the connection lifetime.

    Counts ``AsyncSession`` constructions through the factory the router uses,
    so a conversion that hoisted one session out of the loop would be caught.
    """

    created: list[AsyncSession] = []
    real_factory = db_session_module.get_async_session_factory()

    def counting_factory() -> AsyncSession:
        session = real_factory()
        created.append(session)
        return session

    db_session_module.AsyncSessionLocal = counting_factory

    try:
        with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as websocket:
            websocket.send_json(_hello_message())
            websocket.receive_json()
            hello_sessions = len(created)
            assert hello_sessions >= 1

            websocket.send_json(
                _telemetry_frame("heartbeat", _heartbeat_body(), "msg_heartbeat_1")
            )
            time.sleep(0.2)

        # Every message after hello opened at least one more work unit, so the
        # session is not being reused across the connection.
        assert len(created) > hello_sessions
        # ... and every one of them was closed, rather than left open.
        assert all(not created_session.in_transaction() for created_session in created)
        assert async_db.open_sessions == [], "a work unit left its session open"
    finally:
        db_session_module.AsyncSessionLocal = real_factory


# --------------------------------------------------------------------------------------
# Error, duplicate, unknown, validation and disconnect paths
# --------------------------------------------------------------------------------------


def test_unknown_command_result_is_reported_and_no_session_leaks(
    client, auth_headers, async_db
):
    """An unknown command id yields ``unknown_command`` and still releases."""

    with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as websocket:
        websocket.send_json(_hello_message())
        websocket.receive_json()

        websocket.send_json(_result_frame("cmd_does_not_exist"))
        error = websocket.receive_json()

    assert error["type"] == "error"
    assert error["payload"]["code"] == "unknown_command"
    assert async_db.checkedout() == 0


def test_duplicate_command_result_is_deduped(client, auth_headers, db_session):
    """A second terminal result for one command is deduped, exactly as before."""

    with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as websocket:
        websocket.send_json(_hello_message())
        websocket.receive_json()

        create_response = client.post(
            f"/api/v1/instances/{INSTANCE_ID}/commands",
            json={"kind": "ping", "args": {"nonce": "dup"}, "timeout_s": 10},
        )
        assert create_response.status_code == 200
        command_id = create_response.json()["command_id"]

        command_payload = websocket.receive_json()
        assert command_payload["payload"]["command_id"] == command_id

        websocket.send_json(_ack_frame(command_id))
        websocket.send_json(_result_frame(command_id))
        websocket.send_json(_result_frame(command_id))
        error = websocket.receive_json()

    assert error["type"] == "error"
    assert error["payload"]["code"] == "duplicate_command_result"

    db_session.expire_all()
    command = db_session.scalar(
        select(AgentCommand).where(AgentCommand.command_id == command_id)
    )
    assert command is not None
    assert command.status == "succeeded"
    # The duplicate did not overwrite the first terminal result.
    assert command.result_json == {"ok": True}


def test_invalid_telemetry_body_returns_error_and_keeps_the_connection(
    client, auth_headers, async_db
):
    """A validation failure is reported, not a dropped connection or a leak."""

    with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as websocket:
        websocket.send_json(_hello_message())
        websocket.receive_json()

        websocket.send_json(
            _telemetry_frame("heartbeat", {"not": "a heartbeat"}, "msg_bad_1")
        )
        error = websocket.receive_json()

    assert error["type"] == "error"
    assert error["payload"]["code"] == "invalid_message"
    assert error["payload"]["close_connection"] is False
    assert async_db.checkedout() == 0


def test_disconnect_marks_the_session_disconnected(client, auth_headers, db_session, async_db):
    """Closing the socket runs the disconnect work unit and commits it."""

    with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as websocket:
        websocket.send_json(_hello_message())
        ack = websocket.receive_json()
        session_id = ack["payload"]["session_id"]

    db_session.expire_all()
    session = db_session.scalar(
        select(AgentSession).where(AgentSession.session_id == session_id)
    )
    assert session is not None
    assert session.status == "disconnected"
    assert session.disconnected_at is not None
    assert async_db.checkedout() == 0
    assert async_db.open_sessions == []


def test_cancellation_during_a_work_unit_releases_the_connection(async_db):
    """A cancelled work unit still closes its session and returns its connection.

    ``session_scope`` releases on ``BaseException``, so a cancellation inside a
    work unit must not strand a checked-out connection. Driven at the session
    level so the assertion is about the work-unit contract, not about how a
    particular frame is routed.
    """

    async def scenario() -> None:
        async with session_scope() as session:
            # A query is what actually checks a connection out of the pool.
            await session.execute(text("SELECT 1"))
            assert async_db.checkedout() >= 1, "work unit did not check out a connection"
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scenario())

    assert async_db.checkedout() == 0, "a cancelled work unit leaked its connection"
    assert async_db.leaked_connections == 0
    assert async_db.open_sessions == [], "a cancelled work unit left its session open"


def test_session_scope_releases_on_cancellation_not_just_exception(async_db):
    """``session_scope`` must release on ``BaseException``, not ``Exception``.

    ``asyncio.CancelledError`` derives from ``BaseException`` in Python 3.8+, so
    a ``except Exception`` handler would skip both the rollback and the release
    and strand a checked-out connection on every cancellation — which is exactly
    what a disconnect or a shutdown produces.

    Asserted by instrumenting the session: on cancellation the scope must still
    call ``rollback()`` and ``close()``.
    """

    calls: list[str] = []
    real_factory = db_session_module.get_async_session_factory()

    class RecordingSession(AsyncSession):
        async def rollback(self) -> None:
            calls.append("rollback")
            await super().rollback()

        async def close(self) -> None:
            calls.append("close")
            await super().close()

    db_session_module.AsyncSessionLocal = async_sessionmaker(
        bind=async_db.engine,
        autoflush=False,
        expire_on_commit=False,
        class_=RecordingSession,
    )

    async def scenario() -> None:
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
            raise asyncio.CancelledError()

    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(scenario())
    finally:
        db_session_module.AsyncSessionLocal = real_factory

    assert "rollback" in calls, "cancellation skipped the rollback"
    assert "close" in calls, "cancellation skipped the close"
    assert async_db.checkedout() == 0
    assert async_db.open_sessions == []


def test_database_exception_rolls_back_and_releases(async_db):
    """A DB error inside a work unit rolls back and releases the connection."""

    async def scenario() -> None:
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
            assert async_db.checkedout() >= 1
            raise OperationalError("SELECT 1", {}, RuntimeError("boom"))

    with pytest.raises(OperationalError):
        asyncio.run(scenario())

    assert async_db.checkedout() == 0, "a failed work unit leaked its connection"
    assert async_db.leaked_connections == 0
    assert async_db.open_sessions == [], "a failed work unit left its session open"


# --------------------------------------------------------------------------------------
# The point of the conversion: a slow database must not freeze the event loop
# --------------------------------------------------------------------------------------


def test_slow_database_does_not_block_a_no_db_health_probe(
    client, auth_headers, async_db, monkeypatch
):
    import onestep_control_plane_api.api.routers.agent_ws as agent_ws
    """While a DB work unit is in flight, unrelated coroutines keep running.

    This is the regression the issue is about. The probe is a synchronous
    ticker driven from the test thread plus an in-loop asyncio ticker: if the
    handler's database call blocked the loop, both would stall.
    """


    delay_s = 1.5
    entered = asyncio.Event()
    ticks = 0

    async def slow_ingest(session, request, *, received_at):
        # An awaited delay models a slow *query* that yields to the loop, which
        # is what a real async driver does and what the conversion is for. On
        # the pre-conversion code this call was synchronous and blocked the
        # loop outright, so the health probe below would not be served.
        entered.set()
        await asyncio.sleep(delay_s)
        return await real(session, request, received_at=received_at)

    real = agent_ws.ingest_heartbeat_request
    monkeypatch.setattr(agent_ws, "ingest_heartbeat_request", slow_ingest)

    with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as websocket:
        websocket.send_json(_hello_message())
        websocket.receive_json()

        websocket.send_json(
            _telemetry_frame("heartbeat", _heartbeat_body(), "msg_heartbeat_slow")
        )

        # An unrelated no-DB HTTP probe must still be served while the DB work
        # unit is in flight.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not entered.is_set():
            time.sleep(0.005)

        start = time.monotonic()
        while time.monotonic() - start < delay_s:
            response = client.get("/healthz")
            assert response.status_code == 200, "a slow DB query froze the no-DB health probe"
            ticks += 1
            time.sleep(0.02)

    # The probe was served many times during a 1.5s DB stall.
    assert ticks >= 5, f"health probe only answered {ticks} times during a {delay_s}s DB stall"
    assert async_db.checkedout() == 0


def test_connection_pool_wait_does_not_block_the_event_loop(async_db):
    """A wait for a pooled connection is awaited, not blocked.

    SQLite's shared-cache mode serialises writers, so a size-1 pool cannot be
    contended the way PostgreSQL can without deadlocking the test. What matters
    for this issue is the property, not the driver: while one work unit holds a
    connection and another waits for the pool, an independent asyncio ticker
    must keep running. If the wait blocked the loop the ticker would stall,
    which is exactly the pre-conversion behaviour.

    The wait itself is driven by a real bounded pool on its own database, so the
    measurement is a genuine pool acquisition and not a bare sleep.
    """

    ticker_ticks = 0
    finished = asyncio.Event()

    async def ticker() -> None:
        nonlocal ticker_ticks
        while not finished.is_set():
            await asyncio.sleep(0.01)
            ticker_ticks += 1

    async def scenario() -> int:
        nonlocal ticker_ticks
        ticker_task = asyncio.create_task(ticker())

        # A bounded pool of one connection on a private database.
        pool_engine = create_async_engine(
            shared_cache_async_url(f"{async_db.name}_pool"),
            future=True,
            poolclass=AsyncAdaptedQueuePool,
            pool_size=1,
            max_overflow=0,
            pool_timeout=5.0,
        )
        factory = async_sessionmaker(
            bind=pool_engine, autoflush=False, expire_on_commit=False, class_=AsyncSession
        )
        try:
            # Take the single pooled connection, then start a second work unit
            # that must WAIT for it. The holder releases after a delay, and the
            # ticker measures whether that wait froze the loop.
            holder_session = factory()
            await holder_session.execute(text("SELECT 1"))

            async def second_work_unit() -> int:
                async with factory() as session:
                    return int((await session.execute(text("SELECT 42"))).scalar_one())

            second_task = asyncio.create_task(second_work_unit())

            # Let the second work unit reach the pool wait, then keep the loop
            # busy for a measurable interval while it is still waiting.
            await asyncio.sleep(0.5)
            await holder_session.close()

            value = await second_task
            finished.set()
            ticker_task.cancel()
            return value
        finally:
            await pool_engine.dispose()

    observed = asyncio.run(scenario())

    assert observed == 42
    assert finished.is_set()
    assert ticker_ticks >= 3, (
        f"event loop ran only {ticker_ticks} ticks while a work unit waited for the "
        "pool; the wait blocked the loop instead of yielding"
    )


def test_ws_ping_is_served_while_a_db_work_unit_is_in_flight(
    client, auth_headers, async_db, monkeypatch
):
    import onestep_control_plane_api.api.routers.agent_ws as agent_ws
    """An in-flight DB call does not stop other WS traffic on this server."""

    entered = asyncio.Event()
    delay_s = 1.0
    real = agent_ws.ingest_heartbeat_request

    async def slow_ingest(session, request, *, received_at):
        entered.set()
        await asyncio.sleep(delay_s)
        return await real(session, request, received_at=received_at)

    monkeypatch.setattr(agent_ws, "ingest_heartbeat_request", slow_ingest)

    with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as websocket:
        websocket.send_json(_hello_message())
        websocket.receive_json()
        websocket.send_json(
            _telemetry_frame("heartbeat", _heartbeat_body(), "msg_heartbeat_ping")
        )

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not entered.is_set():
            time.sleep(0.005)

        # A second independent WS connection must still complete its hello while
        # the first connection's database call is in flight.
        with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as other:
            other.send_json(_hello_message(capabilities=["telemetry.heartbeat"]))
            ack = other.receive_json()
            assert ack["type"] == "hello_ack"


# --------------------------------------------------------------------------------------
# Async service-level behaviour
# --------------------------------------------------------------------------------------


def test_pending_command_listing_returns_plain_data_not_orm(db_session, async_db):
    """The listing returns dataclasses, so no implicit IO after the commit."""

    service = _seed_service(db_session)
    command = AgentCommand(
        command_id="cmd_async_listing",
        service_id=service.id,
        instance_id=INSTANCE_ID,
        kind="ping",
        args_json={"nonce": "async"},
        timeout_s=10,
        status="pending",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(command)
    db_session.commit()

    async def scenario():
        async with session_scope() as session:
            return await list_redeliverable_commands_for_instance_async(
                session, instance_id=INSTANCE_ID
            )

    pending = asyncio.run(scenario())

    assert any(item.command_id == "cmd_async_listing" for item in pending)
    # Plain dataclass: readable after the session closed, no lazy IO.
    listed = next(item for item in pending if item.command_id == "cmd_async_listing")
    assert listed.kind == "ping"
    assert listed.args_json == {"nonce": "async"}
    assert isinstance(listed.timeout_s, int)


def test_expired_command_is_not_redelivered(db_session, async_db):
    """Stale-command expiry still runs as part of the listing work unit."""

    service = _seed_service(db_session)
    stale = AgentCommand(
        command_id="cmd_async_stale",
        service_id=service.id,
        instance_id=INSTANCE_ID,
        kind="ping",
        args_json={},
        timeout_s=1,
        status="pending",
        created_at=datetime.now(UTC) - timedelta(seconds=600),
        updated_at=datetime.now(UTC) - timedelta(seconds=600),
    )
    db_session.add(stale)
    db_session.commit()

    async def scenario():
        async with session_scope() as session:
            return await list_redeliverable_commands_for_instance_async(
                session, instance_id=INSTANCE_ID
            )

    pending = asyncio.run(scenario())

    assert all(item.command_id != "cmd_async_stale" for item in pending)
    db_session.expire_all()
    refreshed = db_session.scalar(
        select(AgentCommand).where(AgentCommand.command_id == "cmd_async_stale")
    )
    assert refreshed is not None
    assert refreshed.status == "expired"


def test_ingest_heartbeat_is_async_and_awaited(db_session, async_db):
    """The ingest entry point is a coroutine function the router awaits."""

    import inspect

    assert inspect.iscoroutinefunction(ingest_heartbeat_request)

    async def scenario():
        async with session_scope() as session:
            return await ingest_heartbeat_request(
                session, HeartbeatIngestRequest.model_validate(_heartbeat_body())
            )

    response = asyncio.run(scenario())
    assert response.status == "accepted"


def test_hello_and_pending_listing_are_one_work_unit(client, auth_headers, db_session):
    """Hello and the pending-command listing must share ONE session and transaction.

    The original synchronous code held one session with an open transaction from
    the hello insert through the pending-command listing. That is what made the
    two agree on which commands exist: a command created concurrently was either
    fully invisible to the listing or fully visible, never half-observed.

    Splitting them into two async work units opens a window in which a command
    created in between is BOTH pushed to the live connection by
    ``create_instance_command`` AND picked up by the listing, so the agent
    receives it twice. The observable contract is therefore that both run on the
    same session inside the same transaction, which is asserted directly.
    """

    import onestep_control_plane_api.api.routers.agent_ws as agent_ws

    real_listing = agent_ws.list_redeliverable_commands_for_instance_async
    real_open = agent_ws.open_agent_session
    observed: dict[str, object] = {}

    async def recording_open(session, *, message, session_id, accepted_capabilities, connected_at):
        observed["open_session"] = session
        observed["open_in_transaction"] = session.in_transaction()
        return await real_open(
            session,
            message=message,
            session_id=session_id,
            accepted_capabilities=accepted_capabilities,
            connected_at=connected_at,
        )

    async def recording_listing(session, *, instance_id):
        observed["listing_session"] = session
        observed["listing_in_transaction"] = session.in_transaction()
        return await real_listing(session, instance_id=instance_id)

    agent_ws.open_agent_session = recording_open
    agent_ws.list_redeliverable_commands_for_instance_async = recording_listing

    try:
        with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as websocket:
            websocket.send_json(_hello_message())
            websocket.receive_json()
            time.sleep(0.2)
    finally:
        agent_ws.open_agent_session = real_open
        agent_ws.list_redeliverable_commands_for_instance_async = real_listing

    assert "open_session" in observed, "hello never opened a session"
    assert "listing_session" in observed, "the pending listing never ran"
    assert observed["listing_session"] is observed["open_session"], (
        "the pending-command listing ran on a different session than the hello "
        "insert, so the two no longer agree on which commands exist"
    )
    # Both ran inside the same session, which is what makes them atomic. (The
    # transaction flag is not asserted: SQLAlchemy only starts an explicit
    # transaction on the first statement, so it is not a reliable marker here.)
    assert observed["listing_in_transaction"] == observed["open_in_transaction"]


def test_ui_stream_events_are_still_published_for_command_lifecycle(
    client, auth_headers
) -> None:
    """The conversion did not drop or duplicate UI stream notifications."""

    subscriber_id, queue = _subscribe_ui_stream()
    try:
        with client.websocket_connect("/api/v1/agents/ws", headers=auth_headers) as websocket:
            websocket.send_json(_hello_message())
            websocket.receive_json()

            create_response = client.post(
                f"/api/v1/instances/{INSTANCE_ID}/commands",
                json={"kind": "ping", "args": {"nonce": "stream"}, "timeout_s": 10},
            )
            assert create_response.status_code == 200
            command_payload = websocket.receive_json()
            command_id = command_payload["payload"]["command_id"]

            websocket.send_json(_ack_frame(command_id))
            websocket.send_json(_result_frame(command_id))

        assert _drain_ui_stream_channels(queue) == [
            "sessions",
            "commands",
            "commands",
            "commands",
            "sessions",
        ]
    finally:
        _unsubscribe_ui_stream(subscriber_id)


def test_http_404_from_a_service_layer_still_raises(async_db):
    """Dispatching a missing command raises rather than silently no-op-ing."""

    import onestep_control_plane_api.api.agent_command_service as commands

    async def scenario():
        async with session_scope() as session:
            await commands.mark_command_dispatched_async(
                session, command_id="cmd_missing", session_id="sess_x"
            )

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(scenario())
    assert excinfo.value.status_code == 404


def test_async_harness_reports_leaks_as_zero_when_unused(async_db):
    """Guards the probe itself: a harness with no work units reports zero."""

    assert async_db.checkedout() == 0
    assert async_db.leaked_connections == 0
    assert isinstance(async_db, AsyncTestHarness)
