import asyncio
from types import SimpleNamespace

from onestep import RabbitMQConnector
import onestep.connectors.rabbitmq as rabbitmq_module


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
            self._queue.messages.append(FakeIncomingMessage(self._queue, self.body))

    async def reject(self, requeue=False):
        self.rejected = not requeue


class FakeQueue:
    def __init__(self, name):
        self.name = name
        self.messages = []
        self.bindings = []

    async def get(self, fail=False, timeout=None):
        if self.messages:
            return self.messages.pop(0)
        if timeout and timeout > 0:
            raise asyncio.TimeoutError
        return None

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
            queue.messages.append(FakeIncomingMessage(queue, message.body))
            return
        for queue in self.queue_registry.values():
            for binding in queue.bindings:
                if binding["exchange"] is self and binding["routing_key"] == routing_key:
                    queue.messages.append(FakeIncomingMessage(queue, message.body))


class FakeChannel:
    def __init__(self, queue_registry, exchange_registry):
        self.queue_registry = queue_registry
        self.exchange_registry = exchange_registry
        self.default_exchange = FakeExchange("__default__", queue_registry)
        self.prefetch_count = None
        self.closed = False

    async def set_qos(self, prefetch_count):
        self.prefetch_count = prefetch_count

    async def declare_queue(self, name, durable, auto_delete, arguments):
        return self.queue_registry.setdefault(name, FakeQueue(name))

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
        )
        await events.open()

        receive_channel = events._receive_channel
        publish_channel = events._publish_channel
        assert receive_channel is not None
        assert publish_channel is not None
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
