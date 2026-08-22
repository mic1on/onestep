from __future__ import annotations

import asyncio
import json

from onestep import OneStepApp
from onestep.testing import (
    AcknowledgedSinkHarness,
    ClaimedSourceHarness,
    StopControl,
    run_acknowledged_sink_contract,
    run_claimed_source_stop_contract,
)
from onestep_cf_queues import CFQueuesConnector


class FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class BlockingSendClient:
    """Client that blocks on the publish (POST /messages) request."""

    def __init__(self) -> None:
        self.send_started = asyncio.Event()
        self.release_send = asyncio.Event()
        self.sent: list[dict] = []

    async def request(self, method, url, *, headers=None, content=None):
        body = json.loads(content.decode("utf-8")) if content else {}
        if url.endswith("/messages/ack") or url.endswith("/messages/pull"):
            return FakeResponse(200, {"success": True, "errors": [], "result": {}})
        if url.endswith("/messages"):
            self.send_started.set()
            await self.release_send.wait()
            self.sent.append(body)
            return FakeResponse(200, {"success": True, "errors": []})
        return FakeResponse(404, {"success": False, "errors": ["not found"]})

    async def aclose(self):
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


class BlockingPullClient:
    """Client that blocks on the pull request until released."""

    def __init__(self) -> None:
        self.pull_started = asyncio.Event()
        self.release_pull = asyncio.Event()
        self.retried: list[dict] = []
        self._served = False

    async def request(self, method, url, *, headers=None, content=None):
        body = json.loads(content.decode("utf-8")) if content else {}
        if url.endswith("/messages/pull"):
            self.pull_started.set()
            await self.release_pull.wait()
            if self._served:
                messages: list[dict] = []
            else:
                self._served = True
                messages = [
                    {
                        "body": '{"body": {"value": 1}}',
                        "id": "id-1",
                        "timestamp_ms": 1,
                        "attempts": 1,
                        "metadata": {},
                        "lease_id": "lease-1",
                    }
                ]
            return FakeResponse(
                200,
                {"success": True, "errors": [], "result": {"messages": messages}},
            )
        if url.endswith("/messages/ack"):
            for entry in body.get("retries", []):
                self.retried.append(entry)
            return FakeResponse(200, {"success": True, "errors": [], "result": {}})
        return FakeResponse(404, {"success": False, "errors": ["not found"]})

    async def aclose(self):
        return None


def test_stop_controls_release_fetched_unstarted_cf_delivery() -> None:
    async def scenario() -> None:
        client = BlockingPullClient()
        connector = CFQueuesConnector(
            account_id="acct-1", api_token="secret-token", client=client
        )
        source = connector.queue("q-in", ack_flush_interval_s=0)
        # This queue reports a cancel-safe fetch; the claimed-source contract
        # verifies the release path when intake stops before handling.
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
