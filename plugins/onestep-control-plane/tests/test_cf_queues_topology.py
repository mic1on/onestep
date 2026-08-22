from __future__ import annotations

import asyncio
import json

from control_plane_testkit import SenderRecorder, make_config
from onestep import MemoryQueue, OneStepApp
from onestep.connectors.base import Source
from onestep.envelope import Envelope
from onestep_control_plane import ControlPlaneReporter


class CFQueue(Source):
    """Minimal stand-in mirroring onestep_cf_queues.CFQueue's shape.

    The reporter maps connectors by class name, so this avoids a hard
    onestep-cf-queues test dependency while exercising the cf_queue descriptor
    and its secret-free control_plane_descriptor.
    """

    def __init__(self) -> None:
        super().__init__("queue-xyz")
        self.queue_id = "queue-xyz"
        self.batch_size = 10
        self.visibility_timeout_ms = 30000
        self.poll_interval_s = 1.0
        self.on_fail = "leave"
        self.ack_batch_size = 100
        self.ack_flush_interval_s = 0.5

    async def fetch(self, limit: int):  # pragma: no cover - unused
        return []

    def control_plane_descriptor(self) -> dict:
        return {
            "kind": "cf_queue",
            "name": self.name,
            "config": {
                "queue_id": self.queue_id,
                "batch_size": self.batch_size,
                "visibility_timeout_ms": self.visibility_timeout_ms,
                "poll_interval_s": self.poll_interval_s,
                "on_fail": self.on_fail,
                "ack_batch_size": self.ack_batch_size,
                "ack_flush_interval_s": self.ack_flush_interval_s,
            },
        }


def test_reporter_describes_cf_queue_kind_and_config() -> None:
    recorder = SenderRecorder()
    app = OneStepApp("cf-consumer")
    source = CFQueue()

    @app.task(source=source, emit=MemoryQueue("processed"))
    async def consume(ctx, payload):
        return payload

    reporter = ControlPlaneReporter(make_config(), sender=recorder)
    reporter.attach(app)

    asyncio.run(reporter.send_sync_now())

    sync_payload = next(payload for channel, payload in recorder.calls if channel == "sync")
    described_source = sync_payload["app"]["tasks"][0]["source"]
    assert described_source == {
        "kind": "cf_queue",
        "name": "queue-xyz",
        "config": {
            "queue_id": "queue-xyz",
            "batch_size": 10,
            "visibility_timeout_ms": 30000,
            "poll_interval_s": 1.0,
            "on_fail": "leave",
            "ack_batch_size": 100,
            "ack_flush_interval_s": 0.5,
        },
    }
    # The connector secrets (account_id, api_token) must never appear.
    serialized = json.dumps(sync_payload["app"])
    assert "api_token" not in serialized
    assert "account_id" not in serialized
