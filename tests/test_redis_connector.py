"""Unit tests for Redis Streams connector."""
import pytest
from unittest.mock import AsyncMock, patch

from onestep.connectors.redis import RedisConnector, RedisStreamDelivery


class TestRedisConnector:
    """Tests for RedisConnector."""

    def test_stream_creation(self):
        """Should create RedisStreamQueue with correct parameters."""
        connector = RedisConnector("redis://localhost")
        stream = connector.stream(
            "test-stream",
            group="test-group",
            consumer="test-consumer",
            batch_size=50,
            poll_interval_s=0.5,
        )

        assert stream.name == "test-stream"
        assert stream.group == "test-group"
        assert stream.consumer == "test-consumer"
        assert stream.batch_size == 50
        assert stream.poll_interval_s == 0.5

    def test_stream_defaults(self):
        """Should use sensible defaults for stream parameters."""
        connector = RedisConnector("redis://localhost")
        stream = connector.stream("test-stream")

        assert stream.group == "onestep"
        assert stream.consumer is not None  # Auto-generated
        assert stream.batch_size == 100
        assert stream.poll_interval_s == 1.0
        assert stream.block_ms == 1000

    def test_stream_custom_block_ms(self):
        """Should allow custom block_ms override."""
        connector = RedisConnector("redis://localhost")
        stream = connector.stream("test-stream", block_ms=5000)

        assert stream.block_ms == 5000


class TestRedisStreamDelivery:
    """Tests for RedisStreamDelivery."""

    def test_delivery_properties(self):
        """Should expose envelope properties."""
        from onestep.envelope import Envelope

        envelope = Envelope(body={"key": "value"}, meta={"source": "test"}, attempts=1)
        delivery = RedisStreamDelivery(
            redis=None,
            stream="test",
            group="test-group",
            message_id="123-0",
            envelope=envelope,
        )

        assert delivery.payload == {"key": "value"}
        assert delivery.envelope.meta == {"source": "test"}
        assert delivery.envelope.attempts == 1


class TestRedisStreamQueue:
    """Tests for RedisStreamQueue."""

    @pytest.mark.asyncio
    async def test_send_and_fetch_mock(self):
        """Should send and fetch messages using mocked Redis."""
        connector = RedisConnector("redis://localhost")
        stream = connector.stream("test-stream", group="test-group")

        # Mock Redis client
        mock_redis = AsyncMock()
        mock_redis.xgroup_create = AsyncMock()
        mock_redis.xadd = AsyncMock()
        mock_redis.xreadgroup = AsyncMock()
        mock_redis.xack = AsyncMock()
        mock_redis.aclose = AsyncMock()

        # Mock the connector's acquire method
        with patch.object(connector, 'acquire', return_value=mock_redis):
            # Real Redis returns [[stream, []]] when there are no pending messages.
            mock_redis.xreadgroup = AsyncMock(side_effect=[
                [[b"test-stream", []]],
                [[b"test-stream", [
                    (b"123-0", {b"body": b'{"body": {"job": "test"}, "meta": {}, "attempts": 0}'}),
                ]]],
            ])

            # Test open (creates group)
            await stream.open()
            mock_redis.xgroup_create.assert_called_once()

            # Test fetch
            deliveries = await stream.fetch(10)
            assert len(deliveries) == 1
            assert deliveries[0].payload == {"job": "test"}

            # Test ack
            await deliveries[0].ack()
            mock_redis.xack.assert_called_once_with(
                "test-stream", "test-group", b"123-0"
            )

    @pytest.mark.asyncio
    async def test_fetch_empty(self):
        """Should return empty list when no messages."""
        connector = RedisConnector("redis://localhost")
        stream = connector.stream("test-stream")

        mock_redis = AsyncMock()
        mock_redis.xgroup_create = AsyncMock()
        mock_redis.xreadgroup = AsyncMock(side_effect=[
            [[b"test-stream", []]],
            [],
        ])
        mock_redis.aclose = AsyncMock()

        with patch.object(connector, 'acquire', return_value=mock_redis):
            await stream.open()
            deliveries = await stream.fetch(10)
            assert deliveries == []

    @pytest.mark.asyncio
    async def test_send_with_maxlen(self):
        """Should send with maxlen trim option."""
        connector = RedisConnector("redis://localhost")
        stream = connector.stream("test-stream", maxlen=1000)

        mock_redis = AsyncMock()
        mock_redis.xgroup_create = AsyncMock()
        mock_redis.xadd = AsyncMock()
        mock_redis.aclose = AsyncMock()

        with patch.object(connector, 'acquire', return_value=mock_redis):
            await stream.open()
            await stream.publish({"test": "data"})

            # Verify xadd was called with maxlen
            call_kwargs = mock_redis.xadd.call_args[1]
            assert call_kwargs["maxlen"] == 1000
            assert call_kwargs["approximate"] is True

    @pytest.mark.asyncio
    async def test_retry_redelivers_pending_message(self):
        """Should fetch a retried message from the pending list."""
        connector = RedisConnector("redis://localhost")
        stream = connector.stream("test-stream", group="test-group", consumer="consumer-1")

        mock_redis = AsyncMock()
        mock_redis.xgroup_create = AsyncMock()
        mock_redis.xack = AsyncMock()
        mock_redis.aclose = AsyncMock()

        message = [
            [b"test-stream", [
                (b"123-0", {b"body": b'{"body": {"job": "retry"}, "meta": {}, "attempts": 0}'}),
            ]]
        ]
        mock_redis.xreadgroup = AsyncMock(side_effect=[
            [[b"test-stream", []]],
            message,
            message,
        ])

        with patch.object(connector, "acquire", return_value=mock_redis):
            first = await stream.fetch(10)
            assert len(first) == 1
            assert first[0].payload == {"job": "retry"}

            await first[0].retry()

            second = await stream.fetch(10)
            assert len(second) == 1
            assert second[0].payload == {"job": "retry"}

            assert [call.kwargs for call in mock_redis.xreadgroup.call_args_list] == [
                {
                    "groupname": "test-group",
                    "consumername": "consumer-1",
                    "streams": {"test-stream": "0"},
                    "count": 10,
                    "block": 0,
                },
                {
                    "groupname": "test-group",
                    "consumername": "consumer-1",
                    "streams": {"test-stream": ">"},
                    "count": 10,
                    "block": 1000,
                },
                {
                    "groupname": "test-group",
                    "consumername": "consumer-1",
                    "streams": {"test-stream": "0"},
                    "count": 10,
                    "block": 0,
                },
            ]

    @pytest.mark.asyncio
    async def test_fail_acknowledges_and_stops_redelivery(self):
        """Should ack failed messages so they do not reappear from pending."""
        connector = RedisConnector("redis://localhost")
        stream = connector.stream("test-stream", group="test-group", consumer="consumer-1")

        mock_redis = AsyncMock()
        mock_redis.xgroup_create = AsyncMock()
        mock_redis.xack = AsyncMock()
        mock_redis.aclose = AsyncMock()

        message = [
            [b"test-stream", [
                (b"123-0", {b"body": b'{"body": {"job": "fail"}, "meta": {}, "attempts": 0}'}),
            ]]
        ]
        mock_redis.xreadgroup = AsyncMock(side_effect=[
            [[b"test-stream", []]],
            message,
            [[b"test-stream", []]],
            [],
        ])

        with patch.object(connector, "acquire", return_value=mock_redis):
            first = await stream.fetch(10)
            assert len(first) == 1

            await first[0].fail(RuntimeError("boom"))
            mock_redis.xack.assert_called_once_with("test-stream", "test-group", b"123-0")

            second = await stream.fetch(10)
            assert second == []

            assert [call.kwargs for call in mock_redis.xreadgroup.call_args_list] == [
                {
                    "groupname": "test-group",
                    "consumername": "consumer-1",
                    "streams": {"test-stream": "0"},
                    "count": 10,
                    "block": 0,
                },
                {
                    "groupname": "test-group",
                    "consumername": "consumer-1",
                    "streams": {"test-stream": ">"},
                    "count": 10,
                    "block": 1000,
                },
                {
                    "groupname": "test-group",
                    "consumername": "consumer-1",
                    "streams": {"test-stream": "0"},
                    "count": 10,
                    "block": 0,
                },
                {
                    "groupname": "test-group",
                    "consumername": "consumer-1",
                    "streams": {"test-stream": ">"},
                    "count": 10,
                    "block": 1000,
                },
            ]
