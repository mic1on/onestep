from __future__ import annotations

import asyncio

from onestep import ControlPlaneReporter, OneStepApp

from control_plane_testkit import SenderRecorder, make_config


def test_heartbeat_sequence_continues_after_restart(tmp_path) -> None:
    async def run_once() -> list[int]:
        recorder = SenderRecorder()
        app = OneStepApp("billing-sync")
        reporter = ControlPlaneReporter(
            make_config(instance_id=None, state_dir=str(tmp_path)),
            sender=recorder,
        )
        reporter.attach(app)
        await app.startup()
        await reporter.send_heartbeat_now()
        await app.shutdown()
        return [payload["sequence"] for channel, payload in recorder.calls if channel == "heartbeat"]

    first_sequences = asyncio.run(run_once())
    second_sequences = asyncio.run(run_once())

    assert first_sequences == [1, 2]
    assert second_sequences[0] == 3
