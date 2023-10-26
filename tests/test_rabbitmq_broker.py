from onestep.message import Message

try:
    from collections import Iterable
except ImportError:
    from collections.abc import Iterable
from queue import Queue
import pytest

from onestep.broker.rabbitmq import RabbitMQBroker, RabbitMQConsumer


@pytest.fixture
def broker():
    return RabbitMQBroker(
        "test",
        {
            "host": "localhost",
            "port": 5672,
            "username": "admin",
            "password": "admin",
            "ssl": False,
        }
    )


def test_mq_stream_broker_consume(broker):
    broker.publish('{"a": "b"}')
    consumer = broker.consume()

    assert isinstance(consumer, RabbitMQConsumer)
    result = next(consumer)
    assert isinstance(result, Message)
    assert result.body == {'a': 'b'}
    broker.client.shutdown()


@pytest.fixture
def queue():
    return Queue()
#
#
# def test_redis_consume_message(queue):
#     mock_message = [(b"1", {b"a": b"b"})]
#     queue.put(mock_message)
#     consumer = RedisStreamConsumer(queue)
#     result = next(consumer)
#     assert isinstance(result, Iterable)
#     message = next(result)  # noqa
#     assert message.body == {'a': 'b'}
#     assert message.msg == mock_message[0]
#
#
# def test_redis_consume_multi_messages(broker):
#     broker.prefetch = 2  # mock prefetch
#     broker.publish('{"body": {"a1": "b1"}}')
#     broker.publish('{"body": {"a2": "b2"}}')
#
#     consumer = broker.consume()
#     data = consumer.queue.get()
#     assert len(data) == 2  # Ensure that 2 messages are received
#     broker.client.shutdown()
#
#
# def test_requeue(broker):
#     broker.publish('{"body": {"a1": "b2"}}')
#     consumer = broker.consume()
#
#     assert isinstance(consumer, RedisStreamConsumer)
#     result = next(consumer)
#     assert isinstance(result, Iterable)
#     message = next(result)
#     broker.requeue(message, is_source=True)
#     broker.client.shutdown()
