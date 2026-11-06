from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import UUID

from onestep import MemoryQueue, OneStepApp
from onestep_control_plane import ControlPlaneReporter, ControlPlaneWsSender

from control_plane_testkit import RecordingTransport, make_config


def test_reconnect_keeps_instance_identity_and_advances_sequences(tmp_path) -> None:
    async def run_once(session_id: str):
        transport = RecordingTransport(session_ids=[session_id])
        app = OneStepApp("billing-sync")
        config = make_config(instance_id=None, state_dir=str(tmp_path))
        sender = ControlPlaneWsSender(config, transport=transport)
        reporter = ControlPlaneReporter(config, sender=sender)
        reporter.attach(app)
        await app.startup()
        await reporter.send_sync_now()
        await reporter.send_heartbeat_now()
        connect_service, connect_runtime = transport.connect_calls[0]
        sequences = {
            channel: [
                payload["sequence"]
                for sent_channel, payload in transport.send_calls
                if sent_channel == channel
            ]
            for channel in ("sync", "heartbeat")
        }
        active_session_id = reporter.session_id
        await app.shutdown()
        return connect_service, connect_runtime, sequences, active_session_id

    first_service, first_runtime, first_sequences, first_session_id = asyncio.run(
        run_once("sess_first")
    )
    second_service, second_runtime, second_sequences, second_session_id = asyncio.run(
        run_once("sess_second")
    )

    assert first_service["instance_id"] == second_service["instance_id"]
    assert first_runtime["started_at"] != second_runtime["started_at"]
    assert first_session_id == "sess_first"
    assert second_session_id == "sess_second"
    assert second_sequences["sync"][0] > first_sequences["sync"][-1]
    assert second_sequences["heartbeat"][0] > first_sequences["heartbeat"][-1]


@dataclass
class ServerSession:
    """Server-side view of one WS session.

    ``persisted_topology_hash`` is what the control plane actually stored. It
    stays ``None`` when a sync frame reached the wire but the server never
    handled it, which is the exact state #194 leaves behind.
    """

    session_id: str
    persisted_topology_hash: str | None = None


@dataclass
class DropBeforePersistTransport:
    """Transport that reproduces the #194 failure mode.

    ``drop_connection()`` keeps the transport looking connected but makes the
    next frame fail with a broken pipe, exactly like a socket that died after
    the previous write. That drives the sender's real recovery path: the failed
    send is retried on a freshly connected session.

    The first ``lost_sync_count`` sync frames are dropped after reaching the
    wire, standing for "sent but never persisted".
    """

    session_ids: list[str]
    connect_calls: int = 0
    send_calls: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    server_sessions: list[ServerSession] = field(default_factory=list)
    connected: bool = False
    _session_index: int = 0
    _session_id: str | None = None
    _lost_sync_count: int = 1
    _fail_next_send: bool = False
    reconnects: int = 0

    @property
    def session_id(self) -> str | None:
        return self._session_id

    async def connect(self, *, service: dict[str, Any], runtime: dict[str, Any]) -> None:
        self.connected = True
        self.connect_calls += 1
        if self.connect_calls > 1:
            self.reconnects += 1
        if self._session_index < len(self.session_ids):
            self._session_id = self.session_ids[self._session_index]
        else:
            self._session_id = f"sess_{self._session_index + 1}"
        self._session_index += 1
        self.server_sessions.append(ServerSession(session_id=self._session_id))

    async def send_telemetry(self, channel: str, body: dict[str, Any]) -> None:
        if self._fail_next_send:
            self._fail_next_send = False
            self.connected = False
            raise RuntimeError("broken pipe")
        assert self.server_sessions, "telemetry sent without a server session"
        session = self.server_sessions[-1]
        self.send_calls.append((session.session_id, channel, dict(body)))
        if channel != "sync":
            return
        if self._lost_sync_count > 0:
            # The frame reached the wire; the server never persisted it.
            self._lost_sync_count -= 1
            return
        session.persisted_topology_hash = str(body["app"]["topology_hash"])

    async def close(self) -> None:
        self.connected = False

    def drop_connection(self) -> None:
        """Kill the socket without the server having persisted anything more."""
        self._fail_next_send = True


def _build_app() -> OneStepApp:
    app = OneStepApp("billing-sync")

    @app.task(source=MemoryQueue("incoming"), emit=MemoryQueue("processed"))
    async def sync_users(ctx, payload):  # noqa: ANN001 - test task signature
        return payload

    return app


def _topology_hash_of(payload: dict[str, Any]) -> str:
    return str(payload["app"]["topology_hash"])


def _sync_payloads(transport: DropBeforePersistTransport) -> list[dict[str, Any]]:
    return [body for _, channel, body in transport.send_calls if channel == "sync"]


async def _wait_for(predicate: Callable[[], bool], *, timeout: float = 2.0) -> None:
    """Poll ``predicate``; the sender reconnects from its own background task."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        if predicate():
            return
        if loop.time() >= deadline:
            raise AssertionError(f"condition not met within {timeout}s")
        await asyncio.sleep(0.01)


def test_reconnect_restores_topology_without_manual_sync_now() -> None:
    """#194 acceptance: after reconnect, the current topology is restored.

    Session 1 sends the topology and then the connection dies before the server
    persists it. The reporter already recorded that hash, so pre-fix the dedupe
    in ``_send_sync`` suppresses every later send and the server never converges
    without a manual ``sync_now``. Post-fix, reconnecting re-arms the topology
    on the new session and the server converges on its own.
    """
    transport = DropBeforePersistTransport(session_ids=["sess_1", "sess_2"])
    app = _build_app()
    config = make_config(instance_id=UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df"))
    sender = ControlPlaneWsSender(config, transport=transport)
    reporter = ControlPlaneReporter(config, sender=sender)
    reporter.attach(app)

    async def scenario() -> None:
        await app.startup()
        await _wait_for(lambda: bool(_sync_payloads(transport)))
        sent_hash = _topology_hash_of(_sync_payloads(transport)[0])
        # Sent, but the server did not persist it.
        assert transport.server_sessions[0].persisted_topology_hash is None

        # The socket dies. A heartbeat drives the sender's recovery path; no
        # manual sync_now is ever called.
        transport.drop_connection()
        await reporter._safe_send_heartbeat()
        await _wait_for(lambda: transport.connect_calls >= 2)
        await _wait_for(
            lambda: transport.server_sessions[-1].persisted_topology_hash == sent_hash
        )
        await app.shutdown()

    asyncio.run(asyncio.wait_for(scenario(), timeout=10.0))

    final_session = transport.server_sessions[-1]
    assert final_session.session_id == "sess_2"
    assert final_session.persisted_topology_hash == _topology_hash_of(
        _sync_payloads(transport)[0]
    ), "the server must hold the current topology after reconnect"
    session_2_sends = [
        channel for session_id, channel, _ in transport.send_calls if session_id == "sess_2"
    ]
    assert "sync" in session_2_sends, "the new session must receive the topology"


def test_stable_session_does_not_resend_unchanged_topology() -> None:
    """Dedupe inside one stable session is unchanged."""
    transport = DropBeforePersistTransport(session_ids=["sess_1"], _lost_sync_count=0)
    app = _build_app()
    config = make_config(instance_id=UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df"))
    sender = ControlPlaneWsSender(config, transport=transport)
    reporter = ControlPlaneReporter(config, sender=sender)
    reporter.attach(app)

    async def scenario() -> None:
        await app.startup()
        await reporter.send_sync_now()
        await reporter._safe_send_sync()
        await reporter._safe_send_sync()
        await app.shutdown()

    asyncio.run(asyncio.wait_for(scenario(), timeout=10.0))

    assert len(_sync_payloads(transport)) == 1, "an unchanged topology must not be re-sent"
    assert transport.connect_calls == 1


def test_repeated_reconnects_send_bounded_topology_resyncs() -> None:
    """Repeated reconnects resend once per session, never unbounded."""
    transport = DropBeforePersistTransport(
        session_ids=[f"sess_{index}" for index in range(1, 7)],
        _lost_sync_count=0,
    )
    app = _build_app()
    config = make_config(instance_id=UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df"))
    sender = ControlPlaneWsSender(config, transport=transport)
    reporter = ControlPlaneReporter(config, sender=sender)
    reporter.attach(app)

    async def scenario() -> None:
        await app.startup()
        await _wait_for(lambda: len(_sync_payloads(transport)) == 1)
        for _ in range(4):
            expected_reconnects = transport.reconnects + 1
            transport.drop_connection()
            await reporter._safe_send_heartbeat()
            await _wait_for(lambda: transport.reconnects >= expected_reconnects)
            await _wait_for(lambda: len(_sync_payloads(transport)) >= transport.connect_calls)
        await app.shutdown()

    asyncio.run(asyncio.wait_for(scenario(), timeout=10.0))

    assert transport.connect_calls == 5
    sync_sends = _sync_payloads(transport)
    # One sync per session: the startup sync plus one resync per reconnect.
    assert len(sync_sends) == transport.connect_calls
    assert len(sync_sends) <= 5, "resends must be bounded by the reconnect count"


def test_topology_change_during_reconnect_is_delivered() -> None:
    """A topology that changes while disconnected is delivered on reconnect."""
    transport = DropBeforePersistTransport(session_ids=["sess_1", "sess_2"], _lost_sync_count=0)
    app = _build_app()
    config = make_config(instance_id=UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df"))
    sender = ControlPlaneWsSender(config, transport=transport)
    reporter = ControlPlaneReporter(config, sender=sender)
    reporter.attach(app)

    async def scenario() -> None:
        await app.startup()
        await _wait_for(lambda: bool(_sync_payloads(transport)))
        first_hash = _topology_hash_of(_sync_payloads(transport)[0])
        await _wait_for(
            lambda: transport.server_sessions[0].persisted_topology_hash == first_hash
        )

        transport.drop_connection()

        @app.task(source=MemoryQueue("later"), emit=MemoryQueue("later_out"))
        async def added_task(ctx, payload):  # noqa: ANN001 - test task signature
            return payload

        await reporter._safe_send_heartbeat()
        await _wait_for(lambda: transport.connect_calls >= 2)
        await _wait_for(
            lambda: transport.server_sessions[-1].persisted_topology_hash
            not in (None, first_hash)
        )
        await app.shutdown()

    asyncio.run(asyncio.wait_for(scenario(), timeout=10.0))

    final_session = transport.server_sessions[-1]
    assert final_session.session_id == "sess_2"
    assert final_session.persisted_topology_hash != _topology_hash_of(_sync_payloads(transport)[0])
