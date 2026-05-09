from __future__ import annotations

import asyncio

from onestep import ControlPlaneReporter, ControlPlaneWsSender, OneStepApp

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
            channel: [payload["sequence"] for sent_channel, payload in transport.send_calls if sent_channel == channel]
            for channel in ("sync", "heartbeat")
        }
        active_session_id = reporter.session_id
        await app.shutdown()
        return connect_service, connect_runtime, sequences, active_session_id

    first_service, first_runtime, first_sequences, first_session_id = asyncio.run(run_once("sess_first"))
    second_service, second_runtime, second_sequences, second_session_id = asyncio.run(run_once("sess_second"))

    assert first_service["instance_id"] == second_service["instance_id"]
    assert first_runtime["started_at"] != second_runtime["started_at"]
    assert first_session_id == "sess_first"
    assert second_session_id == "sess_second"
    assert second_sequences["sync"][0] > first_sequences["sync"][-1]
    assert second_sequences["heartbeat"][0] > first_sequences["heartbeat"][-1]
