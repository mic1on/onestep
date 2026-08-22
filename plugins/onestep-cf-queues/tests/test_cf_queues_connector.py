from __future__ import annotations

import asyncio
import base64
import json

from onestep import ConnectorOperation, ConnectorOperationError, Envelope
from onestep_cf_queues import CFQueuesConnector
from onestep_cf_queues.connector import _decode_message_body


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class FakeCFClient:
    """Minimal stand-in for the Cloudflare Queues HTTP API over httpx."""

    def __init__(self) -> None:
        self.available: list[dict] = []
        self.inflight: dict[str, dict] = {}
        self.acked: list[str] = []
        self.retried: list[dict] = []
        self.sent: list[dict] = []
        self.pull_calls: list[dict] = []
        self._counter = 0
        self.closed = False

    def enqueue(self, body, *, lease_prefix: str = "lease") -> str:
        self._counter += 1
        lease_id = f"{lease_prefix}-{self._counter}"
        message = {
            "body": body,
            "id": f"id-{self._counter}",
            "timestamp_ms": 1689615013586,
            "attempts": 1,
            "metadata": {"CF-Content-Type": "json"},
            "lease_id": lease_id,
        }
        self.available.append(message)
        return lease_id

    async def request(self, method, url, *, headers=None, content=None):
        body = json.loads(content.decode("utf-8")) if content else {}
        if url.endswith("/messages/pull"):
            self.pull_calls.append(body)
            take = body.get("batch_size", 5)
            messages = []
            while self.available and len(messages) < take:
                message = self.available.pop(0)
                self.inflight[message["lease_id"]] = message
                messages.append(message)
            return FakeResponse(
                200,
                {
                    "success": True,
                    "errors": [],
                    "messages": [],
                    "result": {
                        "message_backlog_count": len(self.available),
                        "messages": messages,
                    },
                },
            )
        if url.endswith("/messages/ack"):
            for entry in body.get("acks", []):
                lease_id = entry["lease_id"]
                self.acked.append(lease_id)
                self.inflight.pop(lease_id, None)
            for entry in body.get("retries", []):
                self.retried.append(entry)
                message = self.inflight.pop(entry["lease_id"], None)
                if message is not None:
                    self.available.append(message)
            return FakeResponse(200, {"success": True, "errors": [], "result": {}})
        if url.endswith("/messages"):
            self.sent.append(body)
            return FakeResponse(200, {"success": True, "errors": []})
        return FakeResponse(404, {"success": False, "errors": ["not found"]})

    async def aclose(self):
        self.closed = True


def _connector(client: FakeCFClient) -> CFQueuesConnector:
    return CFQueuesConnector(account_id="acct-1", api_token="secret-token", client=client)


def test_cf_queue_fetch_is_cancel_safe():
    queue = _connector(FakeCFClient()).queue("q1")
    assert queue.fetch_is_cancel_safe is True


def test_cf_queue_pull_decodes_body_and_injects_metadata():
    async def scenario():
        client = FakeCFClient()
        client.enqueue('{"body": {"value": 1}}')
        queue = _connector(client).queue("q1", ack_flush_interval_s=0)

        deliveries = await queue.fetch(5)
        assert len(deliveries) == 1
        delivery = deliveries[0]
        assert delivery.payload == {"value": 1}
        cf_meta = delivery.envelope.meta["cf_queues"]
        assert cf_meta["id"] == "id-1"
        assert cf_meta["attempts"] == 1
        assert cf_meta["metadata"] == {"CF-Content-Type": "json"}
        # lease_id must not leak onto the envelope metadata.
        assert "lease_id" not in cf_meta

    asyncio.run(scenario())


def test_cf_queue_ack_acknowledges_lease():
    async def scenario():
        client = FakeCFClient()
        client.enqueue('{"body": 1}')
        queue = _connector(client).queue("q1", ack_flush_interval_s=0)

        delivery = (await queue.fetch(5))[0]
        await delivery.ack()

        assert client.acked == ["lease-1"]

    asyncio.run(scenario())


def test_cf_queue_retry_marks_lease_with_delay():
    async def scenario():
        client = FakeCFClient()
        client.enqueue('{"body": 1}')
        queue = _connector(client).queue("q1", ack_flush_interval_s=0)

        delivery = (await queue.fetch(5))[0]
        await delivery.retry(delay_s=600)

        assert client.retried == [{"lease_id": "lease-1", "delay_seconds": 600}]

    asyncio.run(scenario())


def test_cf_queue_release_unstarted_retries_immediately():
    async def scenario():
        client = FakeCFClient()
        client.enqueue('{"body": 1}')
        queue = _connector(client).queue("q1", ack_flush_interval_s=0)

        delivery = (await queue.fetch(5))[0]
        await delivery.release_unstarted()

        assert client.retried == [{"lease_id": "lease-1", "delay_seconds": 0}]

    asyncio.run(scenario())


def test_cf_queue_fail_leave_does_nothing():
    async def scenario():
        client = FakeCFClient()
        client.enqueue('{"body": 1}')
        queue = _connector(client).queue("q1", on_fail="leave", ack_flush_interval_s=0)

        delivery = (await queue.fetch(5))[0]
        await delivery.fail(RuntimeError("boom"))

        assert client.acked == []
        assert client.retried == []

    asyncio.run(scenario())


def test_cf_queue_fail_ack_drops_message():
    async def scenario():
        client = FakeCFClient()
        client.enqueue('{"body": 1}')
        queue = _connector(client).queue("q1", on_fail="ack", ack_flush_interval_s=0)

        delivery = (await queue.fetch(5))[0]
        await delivery.fail(RuntimeError("boom"))

        assert client.acked == ["lease-1"]

    asyncio.run(scenario())


def test_cf_queue_send_publishes_encoded_envelope():
    async def scenario():
        client = FakeCFClient()
        queue = _connector(client).queue("q1", ack_flush_interval_s=0)

        await queue.send(Envelope(body={"job": 1}))

        assert len(client.sent) == 1
        published = client.sent[0]
        decoded = json.loads(published["body"])
        assert decoded["body"] == {"job": 1}

    asyncio.run(scenario())


def test_cf_queue_batches_acks_into_single_request():
    async def scenario():
        client = FakeCFClient()
        for _ in range(3):
            client.enqueue('{"body": 1}')
        queue = _connector(client).queue("q1", ack_flush_interval_s=0.05)

        deliveries = await queue.fetch(5)
        for delivery in deliveries:
            await delivery.ack()
        await queue.flush_acks()

        assert sorted(client.acked) == ["lease-1", "lease-2", "lease-3"]

    asyncio.run(scenario())


def test_cf_queue_rejects_invalid_batch_size():
    connector = _connector(FakeCFClient())
    for invalid in (0, 101):
        try:
            connector.queue("q1", batch_size=invalid)
        except ValueError:
            continue
        raise AssertionError(f"batch_size={invalid} should be rejected")


def test_cf_queue_rejects_invalid_on_fail():
    connector = _connector(FakeCFClient())
    try:
        connector.queue("q1", on_fail="explode")
    except ValueError:
        return
    raise AssertionError("invalid on_fail should be rejected")


def test_decode_message_body_handles_base64_json():
    encoded = base64.b64encode(b'{"body": {"a": 1}}').decode("ascii")
    envelope = _decode_message_body(encoded)
    assert envelope.body == {"a": 1}


def test_decode_message_body_handles_structured_dict():
    envelope = _decode_message_body({"body": {"a": 1}, "attempts": 3})
    assert envelope.body == {"a": 1}
    assert envelope.attempts == 3


def test_cf_queue_http_error_is_normalized():
    async def scenario():
        class ErrorClient(FakeCFClient):
            async def request(self, method, url, *, headers=None, content=None):
                if url.endswith("/messages/pull"):
                    return FakeResponse(429, text="rate limited")
                return await super().request(method, url, headers=headers, content=content)

        client = ErrorClient()
        queue = _connector(client).queue("q1", ack_flush_interval_s=0)

        try:
            await queue.fetch(5)
        except ConnectorOperationError as exc:
            assert exc.operation is ConnectorOperation.FETCH
            assert exc.backend == "cf_queues"
            return
        raise AssertionError("expected ConnectorOperationError")

    asyncio.run(scenario())
