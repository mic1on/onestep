from __future__ import annotations

import asyncio

from onestep import ControlPlaneReporter, OneStepApp

from control_plane_testkit import SenderRecorder, make_config


def test_runtime_descriptor_keeps_instance_id_stable_but_refreshes_process_facts(
    monkeypatch,
    tmp_path,
) -> None:
    async def run_once(pid: int):
        recorder = SenderRecorder()
        app = OneStepApp("billing-sync")
        reporter = ControlPlaneReporter(
            make_config(instance_id=None, state_dir=str(tmp_path)),
            sender=recorder,
        )
        reporter.attach(app)
        monkeypatch.setattr("onestep.reporter.os.getpid", lambda: pid)
        await app.startup()
        heartbeat_payload = next(payload for channel, payload in recorder.calls if channel == "heartbeat")
        await app.shutdown()
        return heartbeat_payload

    first_payload = asyncio.run(run_once(101))
    second_payload = asyncio.run(run_once(202))

    assert first_payload["service"]["instance_id"] == second_payload["service"]["instance_id"]
    assert first_payload["runtime"]["started_at"] != second_payload["runtime"]["started_at"]
    assert first_payload["runtime"]["pid"] == 101
    assert second_payload["runtime"]["pid"] == 202
