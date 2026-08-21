from __future__ import annotations

import asyncio
import json

from control_plane_testkit import SenderRecorder, make_config
from onestep import MemoryQueue, OneStepApp
from onestep.connectors.base import Sink
from onestep.envelope import Envelope
from onestep_control_plane import ControlPlaneReporter


class SNSTopic(Sink):
    """Minimal stand-in mirroring onestep_sqs.SNSTopic's shape.

    The reporter maps connectors by class name, so this avoids a hard
    onestep-sqs test dependency while exercising the sns_topic descriptor.
    """

    def __init__(self) -> None:
        super().__init__("arn:aws:sns:us-east-1:123456789012:events.fifo")
        self.arn = "arn:aws:sns:us-east-1:123456789012:events.fifo"
        self.subject = "onestep-event"
        self.message_group_id = "jobs"
        self.message_attributes = {
            "kind": {"DataType": "String", "StringValue": "job"}
        }
        self.retry_delay_s = 7.5

    async def send(self, envelope: Envelope) -> None:  # pragma: no cover - unused
        return None


def test_reporter_describes_sns_topic_kind_and_config() -> None:
    recorder = SenderRecorder()
    app = OneStepApp("events-fanout")
    sink = SNSTopic()

    @app.task(source=MemoryQueue("incoming"), emit=sink)
    async def forward(ctx, payload):
        return payload

    reporter = ControlPlaneReporter(make_config(), sender=recorder)
    reporter.attach(app)

    asyncio.run(reporter.send_sync_now())

    sync_payload = next(payload for channel, payload in recorder.calls if channel == "sync")
    emit = sync_payload["app"]["tasks"][0]["emit"][0]
    assert emit == {
        "kind": "sns_topic",
        "name": "arn:aws:sns:us-east-1:123456789012:events.fifo",
        "config": {
            "arn": "arn:aws:sns:us-east-1:123456789012:events.fifo",
            "subject": "onestep-event",
            "message_group_id": "jobs",
            "message_attributes": {
                "kind": {"DataType": "String", "StringValue": "job"}
            },
            "retry_delay_s": 7.5,
        },
    }
    assert json.loads(json.dumps(sync_payload["app"]))["tasks"][0]["emit"][0]["kind"] == "sns_topic"
