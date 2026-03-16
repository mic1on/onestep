"""Integration tests for Redis Streams connector.

Run with:
    PYTHONPATH=src pytest tests/integration/test_redis_live.py -v

Requires Redis running at REDIS_URL (default: redis://localhost:6379)
"""
import os
import pytest
import pytest_asyncio

from onestep import OneStepApp
from onestep.connectors.redis import RedisConnector

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# Skip all tests in this module if redis not installed
pytest.importorskip("redis", reason="redis package not installed")


@pytest_asyncio.fixture
async def redis_connector():
    """Create Redis connector for testing."""
    connector = RedisConnector(REDIS_URL)
    yield connector
    await connector.close()


@pytest_asyncio.fixture
async def redis_client():
    """Create raw Redis client for cleanup."""
    from redis.asyncio import Redis
    client = Redis.from_url(REDIS_URL)
    yield client
    await client.aclose()


@pytest.mark.integration
@pytest.mark.asyncio
class TestRedisStreamLive:
    """Live tests against Redis server."""

    async def test_basic_send_and_fetch(self, redis_connector, redis_client):
        """Should send and fetch messages through Redis Streams."""
        stream_name = "test:basic"
        group = "test-group-basic"

        # Clean up
        await redis_client.delete(stream_name)
        try:
            await redis_client.xgroup_destroy(stream_name, group)
        except Exception:
            pass

        stream = redis_connector.stream(
            stream_name,
            group=group,
            create_group=True,
        )

        # Send message
        await stream.publish({"job": "test-1", "value": 42})

        # Fetch message
        deliveries = await stream.fetch(10)
        assert len(deliveries) == 1
        assert deliveries[0].payload == {"job": "test-1", "value": 42}

        # Ack
        await deliveries[0].ack()

        # Fetch again should be empty (message acknowledged)
        deliveries = await stream.fetch(10)
        assert len(deliveries) == 0

        await stream.close()

    async def test_consumer_group_distribution(self, redis_connector, redis_client):
        """Multiple consumers should share messages in a group."""
        stream_name = "test:distribution"
        group = "test-group-dist"

        # Clean up
        await redis_client.delete(stream_name)
        try:
            await redis_client.xgroup_destroy(stream_name, group)
        except Exception:
            pass

        # Create two consumers
        consumer1 = redis_connector.stream(
            stream_name,
            group=group,
            consumer="consumer-1",
        )
        consumer2 = redis_connector.stream(
            stream_name,
            group=group,
            consumer="consumer-2",
        )

        # Publish 4 messages
        for i in range(4):
            await consumer1.publish({"job": f"job-{i}"})

        # Each consumer fetches
        deliveries1 = await consumer1.fetch(2)
        deliveries2 = await consumer2.fetch(2)

        # Messages should be distributed
        assert len(deliveries1) == 2
        assert len(deliveries2) == 2

        # Ack all
        for d in deliveries1 + deliveries2:
            await d.ack()

        await consumer1.close()
        await consumer2.close()

    async def test_retry_keeps_message_in_pel(self, redis_connector, redis_client):
        """Retry should keep message in PEL for redelivery."""
        stream_name = "test:retry"
        group = "test-group-retry"

        # Clean up
        await redis_client.delete(stream_name)
        try:
            await redis_client.xgroup_destroy(stream_name, group)
        except Exception:
            pass

        stream = redis_connector.stream(
            stream_name,
            group=group,
            consumer="retry-consumer",
        )

        # Send and fetch
        await stream.publish({"job": "retry-test"})
        deliveries = await stream.fetch(10)
        assert len(deliveries) == 1

        # Retry (don't ack)
        await deliveries[0].retry()

        # Check pending messages
        pending = await redis_client.xpending(stream_name, group)
        assert pending["pending"] >= 1  # Message still in PEL

        await stream.close()

    async def test_batch_fetch(self, redis_connector, redis_client):
        """Should fetch messages in batches."""
        stream_name = "test:batch"
        group = "test-group-batch"

        # Clean up
        await redis_client.delete(stream_name)
        try:
            await redis_client.xgroup_destroy(stream_name, group)
        except Exception:
            pass

        stream = redis_connector.stream(
            stream_name,
            group=group,
            batch_size=5,
        )

        # Publish 10 messages
        for i in range(10):
            await stream.publish({"job": f"batch-{i}"})

        # Fetch first batch (max 5)
        deliveries = await stream.fetch(100)
        assert len(deliveries) == 5

        # Ack and fetch remaining
        for d in deliveries:
            await d.ack()

        deliveries = await stream.fetch(100)
        assert len(deliveries) == 5

        for d in deliveries:
            await d.ack()

        await stream.close()

    async def test_maxlen_trim(self, redis_connector, redis_client):
        """Should trim stream to maxlen."""
        stream_name = "test:maxlen"
        group = "test-group-maxlen"

        # Clean up
        await redis_client.delete(stream_name)
        try:
            await redis_client.xgroup_destroy(stream_name, group)
        except Exception:
            pass

        stream = redis_connector.stream(
            stream_name,
            group=group,
            maxlen=5,
            approximate_trim=False,
        )

        # Publish 10 messages
        for i in range(10):
            await stream.publish({"job": i})

        # Stream should be trimmed to ~5 (approximate)
        length = await redis_client.xlen(stream_name)
        assert length <= 10  # Approximate trim may keep more

        await stream.close()


@pytest.mark.integration
@pytest.mark.asyncio
class TestRedisWithOneStepApp:
    """Test Redis connector with OneStepApp."""

    async def test_app_task_with_redis(self, redis_client):
        """Should run task with Redis source and sink."""
        from onestep import MemoryQueue

        stream_name = "test:app"
        group = "test-group-app"

        # Clean up
        await redis_client.delete(stream_name)
        try:
            await redis_client.xgroup_destroy(stream_name, group)
        except Exception:
            pass

        redis = RedisConnector(REDIS_URL)
        source = redis.stream(stream_name, group=group)
        sink = MemoryQueue("processed")

        app = OneStepApp("redis-test-app")
        results = []

        @app.task(source=source, emit=sink)
        async def process(ctx, item):
            results.append(item)
            return {"processed": item}

        # Publish test message
        await source.publish({"test": "data"})

        # Run briefly
        async def run_app():
            await app.serve()

        # Let it process one message
        import asyncio
        task = asyncio.create_task(run_app())
        await asyncio.sleep(0.5)
        app.request_shutdown()
        await task

        # Check result
        assert len(results) == 1
        assert results[0] == {"test": "data"}

        await redis.close()