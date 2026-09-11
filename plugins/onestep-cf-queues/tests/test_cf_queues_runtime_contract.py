from __future__ import annotations

import asyncio

from onestep import OneStepApp
from onestep.testing import (
    AcknowledgedSinkHarness,
    ClaimedSourceHarness,
    StopControl,
    run_acknowledged_sink_contract,
    run_claimed_source_stop_contract,
)
from onestep_cf_queues import CFQueuesConnector


class SDKMessage:
    def __init__(self, **kwargs) -> None:
        self.id = kwargs.get("id")
        self.body = kwargs.get("body")
        self.lease_id = kwargs.get("lease_id")
        self.timestamp_ms = kwargs.get("timestamp_ms")
        self.attempts = kwargs.get("attempts")
        self.metadata = kwargs.get("metadata")


class SDKPullResponse:
    def __init__(self, messages) -> None:
        self.messages = messages
        self.message_backlog_count = 0
        self.metadata = None


class BlockingSendMessages:
    """messages.* where push (publish) blocks until released."""

    def __init__(self, parent) -> None:
        self._p = parent

    async def pull(self, queue_id, *, account_id, batch_size=5, visibility_timeout_ms=None):
        return SDKPullResponse([])

    async def ack(self, queue_id, *, account_id, acks=None, retries=None):
        return None

    async def push(self, queue_id, *, account_id, body=None, content_type=None, delay_seconds=None):
        self._p.send_started.set()
        await self._p.release_send.wait()
        self._p.sent.append({"body": body})
        return None


class BlockingSendClient:
    def __init__(self) -> None:
        self.send_started = asyncio.Event()
        self.release_send = asyncio.Event()
        self.sent: list[dict] = []
        self.queues = type("Q", (), {"messages": BlockingSendMessages(self)})()

    async def close(self):
        return None


def test_runtime_ack_follows_cf_queues_publish_acknowledgement() -> None:
    async def scenario() -> None:
        client = BlockingSendClient()
        connector = CFQueuesConnector(
            account_id="acct-1", api_token="secret-token", client=client
        )
        sink = connector.queue("q-out", ack_flush_interval_s=0)

        harness = AcknowledgedSinkHarness(
            sink=sink,
            wait_for_send_started=client.send_started.wait,
            release_send=client.release_send.set,
        )
        await run_acknowledged_sink_contract(harness, body={"value": 1})

    asyncio.run(scenario())


class BlockingPullMessages:
    """messages.* where pull blocks until released, then serves one message."""

    def __init__(self, parent) -> None:
        self._p = parent

    async def pull(self, queue_id, *, account_id, batch_size=5, visibility_timeout_ms=None):
        self._p.pull_started.set()
        await self._p.release_pull.wait()
        if self._p.served:
            return SDKPullResponse([])
        self._p.served = True
        return SDKPullResponse(
            [
                SDKMessage(
                    id="id-1",
                    body='{"body": {"value": 1}}',
                    timestamp_ms=1,
                    attempts=1,
                    metadata={},
                    lease_id="lease-1",
                )
            ]
        )

    async def ack(self, queue_id, *, account_id, acks=None, retries=None):
        for entry in retries or []:
            self._p.retried.append(entry)
        return None

    async def push(self, queue_id, *, account_id, body=None, content_type=None, delay_seconds=None):
        return None


class BlockingPullClient:
    def __init__(self) -> None:
        self.pull_started = asyncio.Event()
        self.release_pull = asyncio.Event()
        self.retried: list[dict] = []
        self.served = False
        self.queues = type("Q", (), {"messages": BlockingPullMessages(self)})()

    async def close(self):
        return None


def test_stop_controls_release_fetched_unstarted_cf_delivery() -> None:
    async def scenario() -> None:
        client = BlockingPullClient()
        connector = CFQueuesConnector(
            account_id="acct-1", api_token="secret-token", client=client
        )
        source = connector.queue("q-in", ack_flush_interval_s=0)
        # The claimed-source contract verifies the release path when intake
        # stops before handling; force the non-cancel-safe branch to exercise it.
        source.fetch_is_cancel_safe = False

        harness = ClaimedSourceHarness(
            source=source,
            wait_for_fetch_started=client.pull_started.wait,
            release_fetch=client.release_pull.set,
            assert_released=lambda: _assert_released(client),
        )
        await run_claimed_source_stop_contract(harness, StopControl.SHUTDOWN)

    asyncio.run(scenario())


def _assert_released(client: BlockingPullClient) -> None:
    assert any(entry.get("lease_id") == "lease-1" for entry in client.retried), (
        "expected the unstarted delivery to be released via a retry"
    )
