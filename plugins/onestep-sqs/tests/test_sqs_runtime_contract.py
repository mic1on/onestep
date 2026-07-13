from __future__ import annotations

import asyncio
import threading

from onestep import OneStepApp
from onestep_sqs import SQSConnector


class BlockingReceiveSQSClient:
    def __init__(self) -> None:
        self.receive_started = threading.Event()
        self.release_receive = threading.Event()
        self.visibility_changes: list[tuple[str, str, int]] = []

    def receive_message(self, **kwargs):
        self.receive_started.set()
        if not self.release_receive.wait(timeout=1.0):
            raise TimeoutError("test did not release the SQS long poll")
        return {
            "Messages": [
                {
                    "MessageId": "msg-long-poll",
                    "ReceiptHandle": "rh-long-poll",
                    "Body": '{"value": 1}',
                }
            ]
        }

    def change_message_visibility(self, QueueUrl, ReceiptHandle, VisibilityTimeout):
        self.visibility_changes.append((QueueUrl, ReceiptHandle, VisibilityTimeout))


def test_shutdown_waits_for_sqs_long_poll_and_releases_unstarted_delivery() -> None:
    async def scenario() -> None:
        client = BlockingReceiveSQSClient()
        connector = SQSConnector(region_name="ap-southeast-1", client=client)
        queue = connector.queue(
            "https://sqs.ap-southeast-1.amazonaws.com/123456789/jobs",
            wait_time_s=20,
            delete_flush_interval_s=0,
        )
        app = OneStepApp("sqs-long-poll-shutdown", shutdown_timeout_s=1.0)
        handled: list[dict[str, int]] = []

        @app.task(source=queue, concurrency=1)
        async def consume(ctx, item):
            handled.append(item)

        serve_task = asyncio.create_task(app.serve())
        try:
            await _wait_for_thread_event(client.receive_started)
            app.request_shutdown()
            await asyncio.sleep(0.02)
            assert serve_task.done() is False

            client.release_receive.set()
            await asyncio.wait_for(serve_task, timeout=1.0)
        finally:
            client.release_receive.set()
            if not serve_task.done():
                app.request_shutdown()
            await asyncio.gather(serve_task, return_exceptions=True)

        assert handled == []
        assert client.visibility_changes == [(queue.url, "rh-long-poll", 0)]

    asyncio.run(scenario())


async def _wait_for_thread_event(event: threading.Event) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while not event.is_set():
        if loop.time() >= deadline:
            raise TimeoutError("SQS receive_message did not start")
        await asyncio.sleep(0.001)
