from __future__ import annotations

import asyncio

from onestep import ControlPlaneReporter, OneStepApp

from control_plane_testkit import SenderRecorder, make_config


def test_sync_sequence_continues_across_forced_sync_and_restart(tmp_path) -> None:
    async def run_once(send_forced_sync: bool) -> list[int]:
        recorder = SenderRecorder()
        app = OneStepApp("billing-sync")
        reporter = ControlPlaneReporter(
            make_config(instance_id=None, state_dir=str(tmp_path)),
            sender=recorder,
        )
        reporter.attach(app)
        await app.startup()
        if send_forced_sync:
            await reporter.send_sync_now()
        await app.shutdown()
        return [payload["sequence"] for channel, payload in recorder.calls if channel == "sync"]

    first_sequences = asyncio.run(run_once(send_forced_sync=True))
    second_sequences = asyncio.run(run_once(send_forced_sync=False))

    assert first_sequences == [1, 2]
    assert second_sequences[0] == 3
