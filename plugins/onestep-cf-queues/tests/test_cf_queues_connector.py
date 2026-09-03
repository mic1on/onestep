from __future__ import annotations

import asyncio
import base64
import json

import pytest

from onestep import ConnectorOperation, ConnectorOperationError, Envelope
from onestep.resilience import ConnectorErrorKind, is_retryable_connector_error
from onestep_cf_queues import CFQueuesConnector
from onestep_cf_queues.connector import CFQueuesDelivery, _decode_message_body


class SDKMessage:
    """Mimics cloudflare.types.queues.MessagePullResponse.Message."""

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


class FakeMessages:
    """Stand-in for client.queues.messages.* on the async SDK."""

    def __init__(self, parent: "FakeCFClient") -> None:
        self._p = parent

    async def pull(self, queue_id, *, account_id, batch_size=5, visibility_timeout_ms=None):
        self._p.pull_calls.append(
            {
                "queue_id": queue_id,
                "account_id": account_id,
                "batch_size": batch_size,
                "visibility_timeout_ms": visibility_timeout_ms,
            }
        )
        messages = []
        while self._p.available and len(messages) < batch_size:
            message = self._p.available.pop(0)
            self._p.inflight[message.lease_id] = message
            messages.append(message)
        return SDKPullResponse(messages)

    async def ack(self, queue_id, *, account_id, acks=None, retries=None):
        for entry in acks or []:
            lease_id = entry["lease_id"]
            self._p.acked.append(lease_id)
            self._p.inflight.pop(lease_id, None)
        for entry in retries or []:
            self._p.retried.append(entry)
            message = self._p.inflight.pop(entry["lease_id"], None)
            if message is not None:
                self._p.available.append(message)
        return None

    async def push(self, queue_id, *, account_id, body=None, content_type=None, delay_seconds=None):
        self._p.sent.append({"body": body, "content_type": content_type})
        return None


class FakeQueues:
    def __init__(self, parent: "FakeCFClient") -> None:
        self.messages = FakeMessages(parent)


class FakeCFClient:
    """Minimal stand-in for cloudflare.AsyncCloudflare."""

    def __init__(self) -> None:
        self.available: list[SDKMessage] = []
        self.inflight: dict[str, SDKMessage] = {}
        self.acked: list[str] = []
        self.retried: list[dict] = []
        self.sent: list[dict] = []
        self.pull_calls: list[dict] = []
        self._counter = 0
        self.closed = False
        self.queues = FakeQueues(self)

    def enqueue(self, body, *, lease_prefix: str = "lease") -> str:
        self._counter += 1
        lease_id = f"{lease_prefix}-{self._counter}"
        self.available.append(
            SDKMessage(
                id=f"id-{self._counter}",
                body=body,
                timestamp_ms=1689615013586,
                attempts=1,
                metadata={"CF-Content-Type": "json"},
                lease_id=lease_id,
            )
        )
        return lease_id

    async def close(self):
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


def test_cf_queue_pull_forwards_account_and_visibility():
    async def scenario():
        client = FakeCFClient()
        client.enqueue('{"body": 1}')
        queue = _connector(client).queue(
            "q1", visibility_timeout_ms=45000, ack_flush_interval_s=0
        )

        await queue.fetch(5)

        call = client.pull_calls[0]
        assert call["account_id"] == "acct-1"
        assert call["queue_id"] == "q1"
        assert call["visibility_timeout_ms"] == 45000

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


def test_cf_queue_sdk_transport_error_is_normalized():
    async def scenario():
        class ErrorMessages(FakeMessages):
            async def pull(self, queue_id, *, account_id, batch_size=5, visibility_timeout_ms=None):
                raise ConnectionError("network down")

        client = FakeCFClient()
        client.queues.messages = ErrorMessages(client)
        queue = _connector(client).queue("q1", ack_flush_interval_s=0)

        try:
            await queue.fetch(5)
        except ConnectorOperationError as exc:
            assert exc.operation is ConnectorOperation.FETCH
            assert exc.backend == "cf_queues"
            return
        raise AssertionError("expected ConnectorOperationError")

    asyncio.run(scenario())


def _sdk_exception(kind: str, status: int | None = None) -> Exception:
    """Build a real cloudflare SDK exception of the requested kind."""
    httpx = pytest.importorskip("httpx")
    cloudflare = pytest.importorskip("cloudflare")

    request = httpx.Request("POST", "https://api.cloudflare.com")
    if kind == "timeout":
        return cloudflare.APITimeoutError(request=request)
    if kind == "status":
        response = httpx.Response(status, request=request)
        return cloudflare.APIStatusError("api status", response=response, body=None)
    if kind == "api_error":
        return cloudflare.APIError("bare api error", request, body=None)
    if kind == "response_validation":
        return cloudflare.APIResponseValidationError(
            httpx.Response(200, request=request), body=None
        )
    raise AssertionError(f"unknown sdk exception kind: {kind}")


@pytest.mark.parametrize(
    ("exception_kind", "http_status"),
    [
        ("timeout", None),
        ("status", 429),
        ("status", 503),
        ("api_error", None),
        ("response_validation", None),
    ],
)
def test_cf_queue_real_sdk_fetch_errors_normalize_retryable(exception_kind, http_status):
    """P0-1/P0-2 regression: real SDK exceptions must normalize to retryable
    ConnectorOperationError instead of escaping raw and killing the worker."""

    async def scenario():
        sdk_exc = _sdk_exception(exception_kind, http_status)

        class ErrorMessages(FakeMessages):
            async def pull(self, queue_id, *, account_id, batch_size=5, visibility_timeout_ms=None):
                raise sdk_exc

        client = FakeCFClient()
        client.queues.messages = ErrorMessages(client)
        queue = _connector(client).queue("q1", ack_flush_interval_s=0)

        try:
            await queue.fetch(5)
        except ConnectorOperationError as exc:
            assert exc.operation is ConnectorOperation.FETCH
            assert exc.kind in {
                ConnectorErrorKind.DISCONNECTED,
                ConnectorErrorKind.TRANSIENT,
                ConnectorErrorKind.THROTTLED,
            }
            assert is_retryable_connector_error(exc)
            assert "secret-token" not in str(exc)
            return
        raise AssertionError("expected ConnectorOperationError")

    asyncio.run(scenario())


def test_cf_queue_real_sdk_status_error_403_is_not_retryable():
    async def scenario():
        sdk_exc = _sdk_exception("status", 403)

        class ErrorMessages(FakeMessages):
            async def pull(self, queue_id, *, account_id, batch_size=5, visibility_timeout_ms=None):
                raise sdk_exc

        client = FakeCFClient()
        client.queues.messages = ErrorMessages(client)
        queue = _connector(client).queue("q1", ack_flush_interval_s=0)

        try:
            await queue.fetch(5)
        except ConnectorOperationError as exc:
            assert exc.kind is ConnectorErrorKind.MISCONFIGURED
            assert not is_retryable_connector_error(exc)
            return
        raise AssertionError("expected ConnectorOperationError")

    asyncio.run(scenario())


def test_cf_queue_worker_loop_survives_consecutive_sdk_fetch_failures():
    """Regression for the runner-facing contract: while the SDK keeps raising
    real Cloudflare errors on pull, the worker loop must keep retrying with
    backoff (retryable ConnectorOperationError) instead of a raw exception
    propagating out and killing the process."""
    pytest.importorskip("cloudflare")

    async def scenario():
        attempts = 0

        class AlwaysFailingMessages(FakeMessages):
            async def pull(self, queue_id, *, account_id, batch_size=5, visibility_timeout_ms=None):
                nonlocal attempts
                attempts += 1
                raise _sdk_exception("api_error")

        client = FakeCFClient()
        client.queues.messages = AlwaysFailingMessages(client)
        queue = _connector(client).queue("q1", ack_flush_interval_s=0)
        queue.poll_interval_s = 0

        for _ in range(5):
            try:
                await queue.fetch(5)
            except ConnectorOperationError as exc:
                # Same contract TaskRunner._resolve_fetch_task relies on:
                # retryable -> _handle_source_fetch_error + keep looping.
                assert is_retryable_connector_error(exc)
            else:
                raise AssertionError("expected ConnectorOperationError")

        assert attempts == 5

    asyncio.run(scenario())


class FailingAckMessages(FakeMessages):
    """Messages API whose ack endpoint raises an SDK status error."""

    def __init__(self, parent: "FakeCFClient", status: int = 503) -> None:
        super().__init__(parent)
        self._status = status
        self.ack_calls = 0

    async def ack(self, queue_id, *, account_id, acks=None, retries=None):
        self.ack_calls += 1
        raise _sdk_exception("status", self._status)


def test_cf_queue_flush_normalizes_sdk_ack_errors():
    """Issue #150 #1: flush-path errors must be normalized so the runtime sees
    ConnectorOperationError diagnostics (backend/operation/kind) instead of a
    raw APIStatusError escaping delivery.ack()."""

    async def scenario():
        client = FakeCFClient()
        client.enqueue('{"body": 1}')
        client.queues.messages = FailingAckMessages(client)
        queue = _connector(client).queue("q1", ack_flush_interval_s=0)

        delivery = (await queue.fetch(5))[0]
        try:
            await delivery.ack()
        except ConnectorOperationError as exc:
            assert exc.operation is ConnectorOperation.ACK
            assert exc.backend == "cf_queues"
            assert exc.kind is ConnectorErrorKind.TRANSIENT
            assert is_retryable_connector_error(exc)
            # The failed entry stays staged so a later flush retries it.
            assert queue._pending_acks == [{"lease_id": "lease-1"}]
            return
        raise AssertionError("expected ConnectorOperationError")

    asyncio.run(scenario())


def test_cf_queue_flush_never_loses_pending_entries_on_error():
    """Issue #150 #2 amplification: a failing flush must keep the staged
    entries queued (bounded retry), never silently drop or duplicate them."""

    async def scenario():
        client = FakeCFClient()
        client.enqueue('{"body": 1}')
        client.enqueue('{"body": 2}')
        failing = FailingAckMessages(client)
        client.queues.messages = failing
        queue = _connector(client).queue("q1", ack_flush_interval_s=0)

        deliveries = await queue.fetch(5)
        for delivery in deliveries:
            try:
                await delivery.ack()
            except ConnectorOperationError:
                pass

        # Each ack staged above already attempted an immediate flush (interval
        # 0), plus the explicit retries below.
        attempts_before = failing.ack_calls
        for _ in range(3):
            try:
                await queue.flush_acks()
            except ConnectorOperationError:
                pass
            assert len(queue._pending_acks) == 2

        assert failing.ack_calls == attempts_before + 3

    asyncio.run(scenario())


def test_cf_queue_delivery_with_missing_lease_id_is_rejected():
    """Issue #150 #2: a message without lease_id must fail fast instead of
    staging {"lease_id": null}, which Cloudflare rejects forever."""

    async def scenario():
        client = FakeCFClient()
        queue = _connector(client).queue("q1", ack_flush_interval_s=0)

        bad_message = SDKMessage(id="id-1", body='{"body": 1}', lease_id=None)
        try:
            CFQueuesDelivery(queue, bad_message)
        except ValueError as exc:
            assert "lease_id" in str(exc)
            return
        raise AssertionError("expected ValueError for missing lease_id")

    asyncio.run(scenario())


def test_cf_queue_fetch_skips_messages_without_lease_id():
    """Unusable messages are skipped at fetch time (lease expires and
    Cloudflare redelivers) rather than poisoning the ack staging queue."""

    async def scenario():
        client = FakeCFClient()
        client.enqueue('{"body": 1}')
        queue = _connector(client).queue("q1", ack_flush_interval_s=0)
        client.available.insert(0, SDKMessage(id="id-bad", body='{"body": 0}', lease_id=None))
        client.enqueue('{"body": 2}')

        deliveries = await queue.fetch(5)

        assert [d.envelope.body for d in deliveries] == [1, 2]
        assert queue._pending_acks == []

    asyncio.run(scenario())


def test_cf_queue_stage_methods_reject_empty_lease_id():
    """Defense in depth: direct stage_ack/stage_retry with a bad lease_id must
    raise ValueError before anything is staged or flushed."""

    async def scenario():
        client = FakeCFClient()
        queue = _connector(client).queue("q1", ack_flush_interval_s=0)

        for bad in (None, "", 123):
            for stage in (queue.stage_ack(bad), queue.stage_retry(bad, delay_seconds=1)):
                try:
                    await stage
                except ValueError:
                    pass
                else:
                    raise AssertionError(f"expected ValueError for lease_id={bad!r}")

        assert queue._pending_acks == []
        assert queue._pending_retries == []

    asyncio.run(scenario())


def test_cf_queue_late_ack_after_close_does_not_orphan_flusher():
    """Issue #150 #3: after close(), a late delivery.ack() must not restart an
    unmanaged flusher task. The ack is staged and flushed on the next
    explicit open()/close() cycle."""

    async def scenario():
        client = FakeCFClient()
        client.enqueue('{"body": 1}')
        queue = _connector(client).queue("q1", ack_flush_interval_s=0.05)

        delivery = (await queue.fetch(5))[0]
        await queue.close()
        assert queue._ack_flusher_task is None

        await delivery.ack()

        # No new flusher task was spawned.
        assert queue._ack_flusher_task is None
        # The ack is still staged and survives until the next open cycle.
        assert queue._pending_acks == [{"lease_id": "lease-1"}]
        assert client.acked == []

        await queue.open()
        await queue.flush_acks()
        assert client.acked == ["lease-1"]
        await queue.close()

    asyncio.run(scenario())


def test_cf_queue_pending_entries_survive_loop_change():
    """Issue #150 #3: _ensure_runtime_state must not drop entries staged on a
    previous event loop; only the loop-bound flusher task is reset."""

    client = FakeCFClient()
    queue = _connector(client).queue("q1", ack_flush_interval_s=0)

    # Simulate entries staged on a previous (now-closed) event loop.
    queue._pending_acks = [{"lease_id": "lease-1"}]
    queue._pending_retries = [{"lease_id": "lease-2", "delay_seconds": 1}]
    # Force the loop-identity mismatch that _ensure_runtime_state detects.
    queue._loop = object()
    queue._ack_lock = None

    async def second_loop():
        queue._ensure_runtime_state()
        assert queue._pending_acks == [{"lease_id": "lease-1"}]
        assert queue._pending_retries == [{"lease_id": "lease-2", "delay_seconds": 1}]
        await queue.flush_acks()

    asyncio.run(second_loop())

    assert client.acked == ["lease-1"]
    assert client.retried == [{"lease_id": "lease-2", "delay_seconds": 1}]
    assert queue._pending_acks == []
