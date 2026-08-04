import asyncio
from types import SimpleNamespace

from onestep import ConnectorOperation, ConnectorOperationError
from onestep_rabbitmq import RabbitMQConnector
import onestep_rabbitmq.connector as rabbitmq_module


class FakeIncomingMessage:
    def __init__(self, queue, body):
        self._queue = queue
        self.body = body
        self.acked = False
        self.nacked = False
        self.rejected = False

    async def ack(self):
        self.acked = True

    async def nack(self, requeue=True):
        self.nacked = requeue
        if requeue:
            self._queue.put(FakeIncomingMessage(self._queue, self.body))

    async def reject(self, requeue=False):
        self.rejected = not requeue


class FakeQueue:
    def __init__(self, name):
        self.name = name
        self.messages = []
        self.bindings = []
        self.consumer_callback = None
        self.consumer_tag = None
        self.consume_calls = 0
        self.cancelled_consumer_tags = []
        self.cancel_error = None
        self.durable = None
        self.auto_delete = None
        self.exclusive = None
        self.arguments = None

    async def get(self, fail=False, timeout=None):
        raise AssertionError("RabbitMQ sources must use basic.consume, not basic.get")

    async def consume(self, callback):
        self.consume_calls += 1
        self.consumer_callback = callback
        self.consumer_tag = f"consumer-{self.consume_calls}"
        pending, self.messages = self.messages, []
        for message in pending:
            asyncio.create_task(callback(message))
        return self.consumer_tag

    async def cancel(self, consumer_tag):
        self.cancelled_consumer_tags.append(consumer_tag)
        if self.cancel_error is not None:
            raise self.cancel_error
        self.consumer_callback = None
        self.consumer_tag = None

    def put(self, message):
        if self.consumer_callback is None:
            self.messages.append(message)
            return
        asyncio.create_task(self.consumer_callback(message))

    async def bind(self, exchange, routing_key=None, arguments=None, timeout=None):
        self.bindings.append(
            {
                "exchange": exchange,
                "routing_key": routing_key,
                "arguments": arguments,
            }
        )


class FakeExchange:
    def __init__(self, name, queue_registry):
        self.name = name
        self.queue_registry = queue_registry
        self.published = []

    async def publish(self, message, routing_key):
        self.published.append((message, routing_key))
        if self.name == "__default__":
            queue = self.queue_registry.setdefault(routing_key, FakeQueue(routing_key))
            queue.put(FakeIncomingMessage(queue, message.body))
            return
        for queue in self.queue_registry.values():
            for binding in queue.bindings:
                if binding["exchange"] is self and binding["routing_key"] == routing_key:
                    queue.put(FakeIncomingMessage(queue, message.body))


class FakeChannel:
    def __init__(self, queue_registry, exchange_registry):
        self.queue_registry = queue_registry
        self.exchange_registry = exchange_registry
        self.default_exchange = FakeExchange("__default__", queue_registry)
        self.close_callbacks = FakeCallbackCollection(self)
        self.prefetch_count = None
        self.closed = False

    async def set_qos(self, prefetch_count):
        self.prefetch_count = prefetch_count

    async def declare_queue(self, name, durable, auto_delete, exclusive, arguments):
        queue = self.queue_registry.setdefault(name, FakeQueue(name))
        queue.durable = durable
        queue.auto_delete = auto_delete
        queue.exclusive = exclusive
        queue.arguments = arguments
        return queue

    async def declare_exchange(self, name, exchange_type, durable, auto_delete, arguments):
        exchange = self.exchange_registry.get(name)
        if exchange is None:
            exchange = FakeExchange(name, self.queue_registry)
            self.exchange_registry[name] = exchange
        exchange.exchange_type = exchange_type
        exchange.durable = durable
        exchange.auto_delete = auto_delete
        exchange.arguments = arguments
        return exchange

    async def close(self):
        self.closed = True
        await self.close_callbacks(None)


class FakeCallbackCollection:
    def __init__(self, sender):
        self.sender = sender
        self.callbacks = []

    def add(self, callback):
        self.callbacks.append(callback)

    async def __call__(self, exc):
        for callback in self.callbacks:
            await callback(self.sender, exc)


class FakeConnection:
    def __init__(self):
        self.queue_registry = {}
        self.exchange_registry = {}
        self.channels = []
        self.closed = False

    async def channel(self, publisher_confirms=False):
        channel = FakeChannel(self.queue_registry, self.exchange_registry)
        self.channels.append((channel, publisher_confirms))
        return channel

    async def close(self):
        self.closed = True


class FakeMessage:
    def __init__(self, **kwargs):
        self.body = kwargs["body"]
        self.kwargs = kwargs


async def fake_connect_robust(url, **kwargs):
    return FakeConnection()


async def wait_for_consumer(queue):
    while queue._consumer_tag is None:
        await asyncio.sleep(0)


def test_rabbitmq_queue_send_fetch_retry_fail_and_exchange_binding(monkeypatch):
    fake_driver = SimpleNamespace(
        connect_robust=fake_connect_robust,
        Message=FakeMessage,
        DeliveryMode=SimpleNamespace(PERSISTENT="persistent"),
    )
    monkeypatch.setattr(rabbitmq_module, "aio_pika", fake_driver)

    async def scenario():
        connector = RabbitMQConnector("amqp://guest:guest@localhost/")
        queue = connector.queue("jobs", prefetch=5, poll_interval_s=0.01)

        await queue.publish({"value": 1}, meta={"source": "test"}, attempts=2)
        batch = await queue.fetch(1)
        assert len(batch) == 1
        assert batch[0].payload == {"value": 1}
        assert batch[0].envelope.meta == {"source": "test"}
        assert batch[0].envelope.attempts == 2

        original = batch[0]._message
        await batch[0].retry()
        assert original.nacked is True

        redelivery = await queue.fetch(1)
        assert len(redelivery) == 1
        await redelivery[0].ack()
        assert redelivery[0]._message.acked is True

        await queue.publish({"value": 2})
        failed = await queue.fetch(1)
        assert len(failed) == 1
        await failed[0].fail(RuntimeError("boom"))
        assert failed[0]._message.rejected is True

        events = connector.queue(
            "jobs_worker",
            exchange="jobs.events",
            routing_key="jobs.created",
            bind_arguments={"x-match": "all"},
            exclusive=True,
        )
        await events.open()

        receive_channel = events._receive_channel
        publish_channel = events._publish_channel
        assert receive_channel is not None
        assert publish_channel is not None
        assert receive_channel.queue_registry["jobs_worker"].exclusive is True
        assert receive_channel.queue_registry["jobs_worker"].bindings[0]["routing_key"] == "jobs.created"
        assert receive_channel.queue_registry["jobs_worker"].bindings[0]["arguments"] == {"x-match": "all"}
        assert "jobs.events" in receive_channel.exchange_registry

        await events.publish({"event": "created"})
        routed = await events.fetch(1)
        assert len(routed) == 1
        assert routed[0].payload == {"event": "created"}

        await events.close()
        await queue.close()
        await connector.close()

    asyncio.run(scenario())


def test_rabbitmq_fetch_uses_one_long_lived_consumer(monkeypatch):
    fake_driver = SimpleNamespace(
        connect_robust=fake_connect_robust,
        Message=FakeMessage,
        DeliveryMode=SimpleNamespace(PERSISTENT="persistent"),
    )
    monkeypatch.setattr(rabbitmq_module, "aio_pika", fake_driver)

    async def scenario():
        connector = RabbitMQConnector("amqp://guest:guest@localhost/")
        queue = connector.queue("jobs", prefetch=5, batch_size=3, poll_interval_s=0.5)

        waiting_fetch = asyncio.create_task(queue.fetch(3))

        async def wait_for_consumer():
            while queue._queue is None or queue._queue.consume_calls == 0:
                await asyncio.sleep(0)

        await asyncio.wait_for(wait_for_consumer(), timeout=0.1)
        broker_queue = queue._queue

        await queue.publish({"value": 1})
        first_batch = await asyncio.wait_for(waiting_fetch, timeout=0.1)
        assert [delivery.payload for delivery in first_batch] == [{"value": 1}]
        await first_batch[0].release_unstarted()
        released = await queue.fetch(1)
        assert [delivery.payload for delivery in released] == [{"value": 1}]
        await released[0].ack()

        await queue.publish({"value": 2})
        await queue.publish({"value": 3})
        second_batch = await queue.fetch(3)
        assert [delivery.payload for delivery in second_batch] == [
            {"value": 2},
            {"value": 3},
        ]
        assert broker_queue.consume_calls == 1

        await queue.close()
        assert broker_queue.cancelled_consumer_tags == ["consumer-1"]
        await connector.close()

    asyncio.run(scenario())


def test_rabbitmq_empty_fetch_is_cancel_safe(monkeypatch):
    fake_driver = SimpleNamespace(
        connect_robust=fake_connect_robust,
        Message=FakeMessage,
        DeliveryMode=SimpleNamespace(PERSISTENT="persistent"),
    )
    monkeypatch.setattr(rabbitmq_module, "aio_pika", fake_driver)

    async def scenario():
        connector = RabbitMQConnector("amqp://guest:guest@localhost/")
        queue = connector.queue("jobs", poll_interval_s=10.0)

        fetch = asyncio.create_task(queue.fetch(1))
        await asyncio.wait_for(wait_for_consumer(queue), timeout=0.1)
        broker_queue = queue._queue
        fetch.cancel()
        await asyncio.gather(fetch, return_exceptions=True)

        assert fetch.cancelled()
        assert queue._consumer_tag is None
        assert broker_queue.cancelled_consumer_tags == ["consumer-1"]
        assert queue._opened is False
        assert connector._ref_count == 0
        await queue.close()
        await connector.close()

    asyncio.run(scenario())


def test_rabbitmq_discards_stale_buffer_when_receive_channel_closes(monkeypatch):
    fake_driver = SimpleNamespace(
        connect_robust=fake_connect_robust,
        Message=FakeMessage,
        DeliveryMode=SimpleNamespace(PERSISTENT="persistent"),
    )
    monkeypatch.setattr(rabbitmq_module, "aio_pika", fake_driver)

    async def scenario():
        connector = RabbitMQConnector("amqp://guest:guest@localhost/")
        queue = connector.queue("jobs")
        await queue.open()
        messages, _ = queue._ensure_consumer_state()
        broker_queue = queue._queue
        messages.put_nowait(FakeIncomingMessage(broker_queue, b"stale"))

        await queue._receive_channel.close_callbacks(ConnectionError("closed"))

        assert messages.empty()
        await queue.close()
        await connector.close()

    asyncio.run(scenario())


def test_rabbitmq_cancel_failure_resets_transport(monkeypatch):
    fake_driver = SimpleNamespace(
        connect_robust=fake_connect_robust,
        Message=FakeMessage,
        DeliveryMode=SimpleNamespace(PERSISTENT="persistent"),
    )
    monkeypatch.setattr(rabbitmq_module, "aio_pika", fake_driver)

    async def scenario():
        connector = RabbitMQConnector("amqp://guest:guest@localhost/")
        queue = connector.queue("jobs")
        fetch = asyncio.create_task(queue.fetch(1))
        await asyncio.wait_for(wait_for_consumer(queue), timeout=0.1)
        queue._queue.cancel_error = ConnectionError("connection closed")

        fetch.cancel()
        await asyncio.gather(fetch, return_exceptions=True)

        assert fetch.cancelled()
        assert queue._opened is False
        assert queue._receive_channel is None
        assert connector._ref_count == 0
        await connector.close()

    asyncio.run(scenario())


def test_rabbitmq_queue_open_maps_connection_errors_and_releases_reference(monkeypatch):
    class BrokenConnection(FakeConnection):
        async def channel(self, publisher_confirms=False):
            raise RuntimeError("connection closed")

    async def broken_connect_robust(url, **kwargs):
        return BrokenConnection()

    fake_driver = SimpleNamespace(
        connect_robust=broken_connect_robust,
        Message=FakeMessage,
        DeliveryMode=SimpleNamespace(PERSISTENT="persistent"),
    )
    monkeypatch.setattr(rabbitmq_module, "aio_pika", fake_driver)

    async def scenario():
        connector = RabbitMQConnector("amqp://guest:guest@localhost/")
        queue = connector.queue("jobs", poll_interval_s=0.01)

        try:
            await queue.open()
        except ConnectorOperationError as exc:
            assert exc.operation is ConnectorOperation.OPEN
            assert connector._ref_count == 0
        else:
            raise AssertionError("expected ConnectorOperationError")

    asyncio.run(scenario())


def test_rabbitmq_open_failure_does_not_leak_url_credentials(monkeypatch):
    """connect_robust errors embed the full amqp URL; it must be scrubbed."""
    import traceback

    secret_url = "amqp://writer:supersecret@rabbitmq.internal:5672//"

    async def leaking_connect_robust(url, **kwargs):
        raise ConnectionError(f"could not connect to {url}: ACCESS_REFUSED")

    fake_driver = SimpleNamespace(connect_robust=leaking_connect_robust)
    monkeypatch.setattr(rabbitmq_module, "aio_pika", fake_driver)

    async def scenario():
        connector = RabbitMQConnector(secret_url)
        queue = connector.queue("jobs", poll_interval_s=0.01)
        try:
            await queue.open()
        except ConnectorOperationError as error:
            return error
        raise AssertionError("expected ConnectorOperationError")

    error = asyncio.run(scenario())
    # The public ``cause`` must not carry credentials.
    assert "supersecret" not in str(error.cause)
    assert "writer:supersecret" not in str(error.cause)
    assert "<redacted>" in str(error.cause)
    # The original secret-bearing exception must not be chained.
    assert error.__cause__ is None
    assert error.__suppress_context__ is True
    # The formatted traceback (reported by the runtime) stays clean too.
    traceback_text = "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )
    assert "supersecret" not in traceback_text
    assert "writer:supersecret" not in traceback_text
