from __future__ import annotations

import asyncio
import json

from control_plane_testkit import SenderRecorder, make_config
from onestep import HttpSink, MemoryQueue, OneStepApp
from onestep_control_plane import ControlPlaneReporter


def test_reporter_describes_http_sink_kind_and_redacts_config() -> None:
    recorder = SenderRecorder()
    app = OneStepApp("billing-sync")
    sink = HttpSink(
        "notify",
        url="https://user:password@example.com/hooks?token=secret-token&safe=1#fragment",
        headers={
            "Authorization": "Bearer secret-token",
            "X-Api-Key": "secret-token",
        },
        params={"token": "secret-token"},
        body={"token": "secret-token", "id": "{{ body.id }}"},
        timeout_s=2.5,
        success_statuses=[202],
    )

    @app.task(source=MemoryQueue("incoming"), emit=sink)
    async def forward(ctx, payload):
        return payload

    reporter = ControlPlaneReporter(make_config(), sender=recorder)
    reporter.attach(app)

    async def scenario() -> None:
        await reporter.send_sync_now()

    asyncio.run(scenario())

    sync_payload = next(payload for channel, payload in recorder.calls if channel == "sync")
    emit = sync_payload["app"]["tasks"][0]["emit"][0]
    assert emit == {
        "kind": "http_sink",
        "name": "notify",
        "config": {
            "url": "https://<redacted>@example.com/hooks",
            "method": "POST",
            "headers": {
                "Authorization": "<redacted>",
                "X-Api-Key": "<redacted>",
            },
            "params": {
                "token": "<redacted>",
            },
            "body": "<redacted>",
            "timeout_s": 2.5,
            "success_statuses": [202],
        },
    }
    assert "secret-token" not in json.dumps(sync_payload["app"])
